"""MCP admin tool for user management.

Provides 8 admin-only MCP tool actions:
  create_user, list_users, revoke_user, rotate_key,
  check_limits, check_token_usage, update_limits, update_token_limits.

All actions require "admin" scope via ``require_scopes("admin")``.
Raw API keys are delivered one-time only — never stored in DB or logs.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Any

import structlog

from app.core.config import Settings
from app.core.encryption import encrypt_key, generate_api_key
from app.core.logging import get_logger
from app.core.rate_limiter import _get_pool as _rl_get_pool
from app.core.rate_limiter import check_rate_limit
from app.core.token_cost_tracker import (
    _get_pool as _tc_get_pool,
)
from app.core.token_cost_tracker import (
    check_token_limits,
    get_token_usage,
)
from app.core.user_store import (
    create_user as _store_create_user,
)
from app.core.user_store import (
    list_users as _store_list_users,
)
from app.core.user_store import (
    revoke_user as _store_revoke_user,
)
from app.core.user_store import (
    rotate_key as _store_rotate_key,
)
from app.core.user_store import (
    update_rate_limits as _store_update_rate_limits,
)
from app.core.user_store import (
    update_token_limits as _store_update_token_limits,
)

logger: structlog.BoundLogger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Settings singleton (lazy init, shared across modules)
# ---------------------------------------------------------------------------

_settings: Settings | None = None


def _get_settings() -> Settings:
    """Return module-level Settings singleton (lazy-init, cached)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


# ---------------------------------------------------------------------------
# Bounds validation constants
# ---------------------------------------------------------------------------

_RATE_LIMIT_DAILY_MIN: int = 1
_RATE_LIMIT_DAILY_MAX: int = 1_000
_RATE_LIMIT_WEEKLY_MIN: int = 1
_RATE_LIMIT_WEEKLY_MAX: int = 10_000
_RATE_LIMIT_MONTHLY_MIN: int = 1
_RATE_LIMIT_MONTHLY_MAX: int = 100_000

_TOKEN_LIMIT_DAILY_MIN: int = 0
_TOKEN_LIMIT_DAILY_MAX: int = 10_000_000
_TOKEN_LIMIT_WEEKLY_MIN: int = 0
_TOKEN_LIMIT_WEEKLY_MAX: int = 50_000_000
_TOKEN_LIMIT_MONTHLY_MIN: int = 0
_TOKEN_LIMIT_MONTHLY_MAX: int = 200_000_000

_PAGE_SIZE_MAX: int = 100

# ---------------------------------------------------------------------------
# Audit helpers — token cost data MUST NOT be included
# ---------------------------------------------------------------------------


def _audit_event(event: str, user_id: str, user_name: str | None = None) -> None:
    """Log a structured audit event for user management actions.

    Args:
        event: Audit event name (e.g. "user_created", "key_rotated").
        user_id: User identifier (UUID hex string).
        user_name: Human-readable user name (optional).
    """
    logger.info(
        event,
        user_id=user_id,
        user_name=user_name,
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# MCP Tool Actions
# ---------------------------------------------------------------------------


async def create_user(
    name: str,
    rate_limits: dict[str, int] | None = None,
    token_limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Create a new user with a generated API key.

    Generates a raw API key, encrypts it, stores the user in DB,
    and delivers the raw key one-time only. The raw key is never
    stored in DB or logs after delivery.

    Args:
        name: Human-readable user name.
        rate_limits: Optional rate limit overrides (daily/weekly/monthly).
        token_limits: Optional token limit overrides (daily/weekly/monthly).

    Returns:
        Dict with user_id, key_id, raw_key, encrypted_key, status,
        rate_limits, token_limits, created_at.

    Raises:
        ValueError: If bounds validation fails.
    """
    # Generate and encrypt key
    raw_key: str = generate_api_key()
    encrypted_key: str = encrypt_key(raw_key)

    # Create user in DB (DB validates bounds internally)
    user_record: dict[str, Any] = _store_create_user(
        name=name,
        rate_limits=rate_limits,
        token_limits=token_limits,
        encrypted_key=encrypted_key,
    )

    user_id: str = user_record["user_id"]
    key_id: str = user_record["key_id"]

    # Audit event
    _audit_event("user_created", user_id, name)

    # Return with raw_key (one-time delivery) — raw_key is NOT stored
    return {
        "user_id": user_id,
        "key_id": key_id,
        "raw_key": raw_key,
        "encrypted_key": encrypted_key,
        "status": "active",
        "rate_limits": user_record["rate_limits"],
        "token_limits": user_record["token_limits"],
        "created_at": user_record["created_at"],
    }


async def list_users(
    status_filter: str = "all",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Return a paginated list of users with optional status filter.

    Does NOT include encrypted_key in output for security.

    Args:
        status_filter: 'active', 'revoked', or 'all' (default).
        page: 1-based page number (default: 1).
        page_size: Items per page, max 100 (default: 20).

    Returns:
        Dict with users list, total count, page, page_size.

    Raises:
        ValueError: If page_size > 100 or page < 1 or invalid status_filter.
    """
    if page_size > _PAGE_SIZE_MAX:
        raise ValueError(f"page_size={page_size} exceeds maximum {_PAGE_SIZE_MAX}")

    result: dict[str, Any] = _store_list_users(
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )

    # Sanitize output — remove encrypted_key from each user record
    sanitized_users: list[dict[str, Any]] = []
    for user in result["users"]:
        safe_user: dict[str, Any] = {
            k: v for k, v in user.items() if k != "encrypted_key"
        }
        sanitized_users.append(safe_user)

    return {
        "users": sanitized_users,
        "total": result["total"],
        "page": result["page"],
        "page_size": result["page_size"],
    }


async def revoke_user(user_id: str) -> dict[str, Any]:
    """Revoke a user and clear Redis cache for rate/token counters.

    Uses exact key enumeration to avoid pattern collision — keys are
    discovered via ``keys()`` and deleted individually, preventing
    accidental deletion of unrelated keys.

    Args:
        user_id: The user UUID hex.

    Returns:
        Dict with user_id, status, revoked_at.

    Raises:
        ValueError: If user not found.
    """
    # Revoke in DB
    revoked_record: dict[str, Any] = _store_revoke_user(user_id)

    # Delete Redis rate-limit counters for this user (exact key enumeration)
    try:
        rl_pool = _rl_get_pool(_get_settings())
        rl_keys = await asyncio.to_thread(rl_pool.keys, f"rl:{user_id}:*")
        if rl_keys:
            await asyncio.to_thread(rl_pool.delete, *rl_keys)
    except Exception:
        logger.warning("redis_rate_counters_clear_failed", user_id=user_id)

    # Delete Redis token-cost counters for this user (exact key enumeration)
    try:
        tc_pool = _tc_get_pool(_get_settings())
        tc_keys = await asyncio.to_thread(tc_pool.keys, f"tc:{user_id}:*")
        if tc_keys:
            await asyncio.to_thread(tc_pool.delete, *tc_keys)
    except Exception:
        logger.warning("redis_token_counters_clear_failed", user_id=user_id)

    # Audit event
    _audit_event("user_revoked", user_id)

    return {
        "user_id": user_id,
        "status": "revoked",
        "revoked_at": revoked_record.get(
            "updated_at", datetime.datetime.now(datetime.timezone.utc).isoformat()
        ),
    }


async def rotate_key(user_id: str) -> dict[str, Any]:
    """Rotate a user's API key: generate new key, increment key_version.

    The old key is auto-revoked by incrementing key_version.
    New raw key delivered one-time only — never stored in DB or logs.

    Redis keys are indexed by user_id (not key_id) — user_id is unchanged
    during rotation, only key_id/key_version changes. Therefore deletion
    uses exact key enumeration via ``keys()`` to avoid pattern collision.

    Args:
        user_id: The user UUID hex.

    Returns:
        Dict with user_id, new_key_id, raw_key, old_key_id, key_version.

    Raises:
        ValueError: If user not found.
    """
    # Increment key_version and update key_id in DB
    rotation_record: dict[str, Any] = _store_rotate_key(user_id)
    old_key_id: str = rotation_record["old_key_id"]
    new_key_id: str = rotation_record["new_key_id"]
    new_key_version: int = rotation_record["new_key_version"]

    # Generate and encrypt new key
    raw_key: str = generate_api_key()
    new_encrypted_key: str = encrypt_key(raw_key)

    # Delete Redis rate-limit counters for this user (exact key enumeration)
    # Redis keys indexed by user_id, not key_id — user_id unchanged during rotation
    try:
        rl_pool = _rl_get_pool(_get_settings())
        rl_keys = await asyncio.to_thread(rl_pool.keys, f"rl:{user_id}:*")
        if rl_keys:
            await asyncio.to_thread(rl_pool.delete, *rl_keys)
    except Exception:
        logger.warning("redis_rate_counters_clear_failed", user_id=user_id)

    # Delete Redis token-cost counters for this user (exact key enumeration)
    try:
        tc_pool = _tc_get_pool(_get_settings())
        tc_keys = await asyncio.to_thread(tc_pool.keys, f"tc:{user_id}:*")
        if tc_keys:
            await asyncio.to_thread(tc_pool.delete, *tc_keys)
    except Exception:
        logger.warning("redis_token_counters_clear_failed", user_id=user_id)

    # Audit event
    _audit_event("key_rotated", user_id)

    # Return with raw_key (one-time delivery)
    return {
        "user_id": user_id,
        "new_key_id": new_key_id,
        "raw_key": raw_key,
        "old_key_id": old_key_id,
        "key_version": new_key_version,
        "encrypted_key": new_encrypted_key,
    }


async def check_limits(user_id: str) -> dict[str, Any]:
    """Check rate limits and token costs for a user across all tiers.

    Args:
        user_id: The user UUID hex.

    Returns:
        Dict with rate_limits and token_costs per tier (daily/weekly/monthly).
    """
    tiers: list[str] = ["daily", "weekly", "monthly"]

    rate_limits: dict[str, dict[str, int | bool]] = {}
    for tier in tiers:
        rate_limits[tier] = check_rate_limit(user_id, tier)

    token_costs: dict[str, dict[str, int | bool | str | None]] = {}
    for tier in tiers:
        token_costs[tier] = check_token_limits(user_id, tier)

    return {
        "rate_limits": rate_limits,
        "token_costs": token_costs,
    }


async def check_token_usage(user_id: str) -> dict[str, Any]:
    """Check token usage for a user across all tiers.

    Args:
        user_id: The user UUID hex.

    Returns:
        Dict with token_usage per tier (daily/weekly/monthly) containing
        input_tokens, output_tokens, total_tokens.
    """
    tiers: list[str] = ["daily", "weekly", "monthly"]

    token_usage: dict[str, dict[str, int]] = {}
    for tier in tiers:
        token_usage[tier] = get_token_usage(user_id, tier)

    return {
        "token_usage": token_usage,
    }


async def update_limits(
    user_id: str,
    daily: int | None = None,
    weekly: int | None = None,
    monthly: int | None = None,
) -> dict[str, Any]:
    """Update rate limits for a user with bounds validation.

    Bounds:
        daily: [1, 1000]
        weekly: [1, 10000]
        monthly: [1, 100000]

    Args:
        user_id: The user UUID hex.
        daily: New daily limit or None to keep current.
        weekly: New weekly limit or None to keep current.
        monthly: New monthly limit or None to keep current.

    Returns:
        Dict with user_id and updated_rate_limits.

    Raises:
        ValueError: If bounds violated or user not found.
    """
    # Pre-validate bounds (user_store also validates, this is early check)
    if daily is not None and not (
        _RATE_LIMIT_DAILY_MIN <= daily <= _RATE_LIMIT_DAILY_MAX
    ):
        raise ValueError(
            f"rate_limits daily={daily} out of bounds "
            f"[{_RATE_LIMIT_DAILY_MIN}, {_RATE_LIMIT_DAILY_MAX}]"
        )
    if weekly is not None and not (
        _RATE_LIMIT_WEEKLY_MIN <= weekly <= _RATE_LIMIT_WEEKLY_MAX
    ):
        raise ValueError(
            f"rate_limits weekly={weekly} out of bounds "
            f"[{_RATE_LIMIT_WEEKLY_MIN}, {_RATE_LIMIT_WEEKLY_MAX}]"
        )
    if monthly is not None and not (
        _RATE_LIMIT_MONTHLY_MIN <= monthly <= _RATE_LIMIT_MONTHLY_MAX
    ):
        raise ValueError(
            f"rate_limits monthly={monthly} out of bounds "
            f"[{_RATE_LIMIT_MONTHLY_MIN}, {_RATE_LIMIT_MONTHLY_MAX}]"
        )

    updated_record: dict[str, Any] = _store_update_rate_limits(
        user_id=user_id,
        daily=daily,
        weekly=weekly,
        monthly=monthly,
    )

    # Audit event
    _audit_event("limits_updated", user_id)

    return {
        "user_id": user_id,
        "updated_rate_limits": {
            "daily": updated_record["rate_limits"]["daily"],
            "weekly": updated_record["rate_limits"]["weekly"],
            "monthly": updated_record["rate_limits"]["monthly"],
        },
    }


async def update_token_limits(
    user_id: str,
    daily: int | None = None,
    weekly: int | None = None,
    monthly: int | None = None,
) -> dict[str, Any]:
    """Update token limits for a user with bounds validation.

    Bounds:
        daily: [0, 10_000_000] (0 = unlimited)
        weekly: [0, 50_000_000] (0 = unlimited)
        monthly: [0, 200_000_000] (0 = unlimited)

    None means no limit configured (keep current).

    Args:
        user_id: The user UUID hex.
        daily: New daily token limit or None to keep current.
        weekly: New weekly token limit or None to keep current.
        monthly: New monthly token limit or None to keep current.

    Returns:
        Dict with user_id and updated_token_limits.

    Raises:
        ValueError: If bounds violated or user not found.
    """
    # Pre-validate bounds (user_store also validates, this is early check)
    if daily is not None and not (
        _TOKEN_LIMIT_DAILY_MIN <= daily <= _TOKEN_LIMIT_DAILY_MAX
    ):
        raise ValueError(
            f"token_limits daily={daily} out of bounds "
            f"[{_TOKEN_LIMIT_DAILY_MIN}, {_TOKEN_LIMIT_DAILY_MAX}]"
        )
    if weekly is not None and not (
        _TOKEN_LIMIT_WEEKLY_MIN <= weekly <= _TOKEN_LIMIT_WEEKLY_MAX
    ):
        raise ValueError(
            f"token_limits weekly={weekly} out of bounds "
            f"[{_TOKEN_LIMIT_WEEKLY_MIN}, {_TOKEN_LIMIT_WEEKLY_MAX}]"
        )
    if monthly is not None and not (
        _TOKEN_LIMIT_MONTHLY_MIN <= monthly <= _TOKEN_LIMIT_MONTHLY_MAX
    ):
        raise ValueError(
            f"token_limits monthly={monthly} out of bounds "
            f"[{_TOKEN_LIMIT_MONTHLY_MIN}, {_TOKEN_LIMIT_MONTHLY_MAX}]"
        )

    updated_record: dict[str, Any] = _store_update_token_limits(
        user_id=user_id,
        daily=daily,
        weekly=weekly,
        monthly=monthly,
    )

    # Audit event
    _audit_event("token_limits_updated", user_id)

    return {
        "user_id": user_id,
        "updated_token_limits": {
            "daily": updated_record["token_limits"]["daily"],
            "weekly": updated_record["token_limits"]["weekly"],
            "monthly": updated_record["token_limits"]["monthly"],
        },
    }


# ---------------------------------------------------------------------------
# MCP Tool Registration
# ---------------------------------------------------------------------------


def register_user_manage_tools(mcp: Any) -> None:
    """Register all user management MCP tools on the given FastMCP server.

    All tools are admin-only — require_scopes("admin") enforcement.

    Args:
        mcp: FastMCP server instance.
    """
    # Import require_scopes at registration time to avoid circular imports
    from fastmcp.server.auth import require_scopes

    admin_tools = [
        create_user,
        list_users,
        revoke_user,
        rotate_key,
        check_limits,
        check_token_usage,
        update_limits,
        update_token_limits,
    ]

    auth_check = require_scopes("admin")

    for tool in admin_tools:
        mcp.add_tool(tool, auth=auth_check)
