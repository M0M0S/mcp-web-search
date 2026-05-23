"""SQLite user store for MCP authorization system.

Provides CRUD operations for users, rate limit snapshots, and token cost
snapshots backed by a SQLite database configured via Settings.KG_DB_PATH.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Final

import structlog

from app.core.config import Settings

logger: structlog.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

_SCHEMA_VERSION: Final[int] = 1

_CREATE_USERS: Final[str] = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    key_id TEXT UNIQUE NOT NULL,
    encrypted_key TEXT,
    key_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled', 'revoked')),
    scopes TEXT NOT NULL DEFAULT '["read"]',
    rate_limits_daily INTEGER NOT NULL DEFAULT 100,
    rate_limits_weekly INTEGER NOT NULL DEFAULT 500,
    rate_limits_monthly INTEGER NOT NULL DEFAULT 2000,
    token_limits_daily INTEGER DEFAULT NULL,
    token_limits_weekly INTEGER DEFAULT NULL,
    token_limits_monthly INTEGER DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT
)
"""

_CREATE_IDX_USERS_KEY_ID: Final[str] = "CREATE INDEX IF NOT EXISTS idx_users_key_id ON users(key_id)"
_CREATE_IDX_USERS_STATUS: Final[str] = "CREATE INDEX IF NOT EXISTS idx_users_status ON users(status)"

_CREATE_SNAPSHOTS: Final[str] = """
CREATE TABLE IF NOT EXISTS rate_limit_snapshots (
    user_id TEXT NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('daily', 'weekly', 'monthly')),
    count INTEGER NOT NULL DEFAULT 0,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (user_id, tier)
)
"""

_CREATE_IDX_SNAPSHOTS_USER: Final[str] = "CREATE INDEX IF NOT EXISTS idx_snapshots_user ON rate_limit_snapshots(user_id)"

_CREATE_TOKEN_SNAPSHOTS: Final[str] = """
CREATE TABLE IF NOT EXISTS token_cost_snapshots (
    user_id TEXT NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('daily', 'weekly', 'monthly')),
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (user_id, tier)
)
"""

_CREATE_IDX_TOKEN_SNAPSHOTS_USER: Final[str] = "CREATE INDEX IF NOT EXISTS idx_token_snapshots_user ON token_cost_snapshots(user_id)"

# ---------------------------------------------------------------------------
# Bounds validation constants
# ---------------------------------------------------------------------------

_RATE_LIMIT_DAILY_MIN: Final[int] = 1
_RATE_LIMIT_DAILY_MAX: Final[int] = 1_000
_RATE_LIMIT_WEEKLY_MIN: Final[int] = 1
_RATE_LIMIT_WEEKLY_MAX: Final[int] = 10_000
_RATE_LIMIT_MONTHLY_MIN: Final[int] = 1
_RATE_LIMIT_MONTHLY_MAX: Final[int] = 100_000

_TOKEN_LIMIT_DAILY_MIN: Final[int] = 0
_TOKEN_LIMIT_DAILY_MAX: Final[int] = 10_000_000
_TOKEN_LIMIT_WEEKLY_MIN: Final[int] = 0
_TOKEN_LIMIT_WEEKLY_MAX: Final[int] = 50_000_000
_TOKEN_LIMIT_MONTHLY_MIN: Final[int] = 0
_TOKEN_LIMIT_MONTHLY_MAX: Final[int] = 200_000_000

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _dict_factory(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    """Row factory that returns dicts keyed by column name."""
    return {column[0]: value for column, value in zip(cursor.description, row)}


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not exist."""
    conn.execute(_CREATE_USERS)
    conn.execute(_CREATE_IDX_USERS_KEY_ID)
    conn.execute(_CREATE_IDX_USERS_STATUS)
    conn.execute(_CREATE_SNAPSHOTS)
    conn.execute(_CREATE_IDX_SNAPSHOTS_USER)
    conn.execute(_CREATE_TOKEN_SNAPSHOTS)
    conn.execute(_CREATE_IDX_TOKEN_SNAPSHOTS_USER)
    conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_db() -> sqlite3.Connection:
    """Initialise the SQLite connection and auto-create schema on first access.

    Returns:
        A sqlite3.Connection configured with dict row factory.
    """
    settings = Settings()
    db_path: str = settings.KG_DB_PATH

    conn = sqlite3.connect(db_path)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    _ensure_schema(conn)
    logger.info("user_store_db_init", db_path=db_path, schema_version=_SCHEMA_VERSION)
    return conn


def create_user(
    name: str,
    rate_limits: dict[str, int] | None = None,
    token_limits: dict[str, int] | None = None,
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    """Create a new user record in the SQLite store.

    Args:
        name: Human-readable user name.
        rate_limits: Optional override for daily/weekly/monthly limits.
        token_limits: Optional override for daily/weekly/monthly token limits.
        scopes: Optional list of authorized scopes (default: ["read"]).

    Returns:
        Dict with user_id, key_id, encrypted_key, status, scopes, rate_limits,
        token_limits, created_at, updated_at.

    Raises:
        ValueError: If rate_limits or token_limits contain invalid values.
        sqlite3.IntegrityError: If key_id collides (should not happen).
    """
    user_id = uuid.uuid4().hex
    key_id = f"key_{uuid.uuid4().hex[:12]}"
    encrypted_key: str | None = None

    # Resolve rate limits with defaults
    rl_daily: int = rate_limits.get("daily", 100) if rate_limits else 100
    rl_weekly: int = rate_limits.get("weekly", 500) if rate_limits else 500
    rl_monthly: int = rate_limits.get("monthly", 2000) if rate_limits else 2000

    # Validate rate limits bounds
    if not (_RATE_LIMIT_DAILY_MIN <= rl_daily <= _RATE_LIMIT_DAILY_MAX):
        raise ValueError(
            f"rate_limits daily={rl_daily} out of bounds "
            f"[{_RATE_LIMIT_DAILY_MIN}, {_RATE_LIMIT_DAILY_MAX}]"
        )
    if not (_RATE_LIMIT_WEEKLY_MIN <= rl_weekly <= _RATE_LIMIT_WEEKLY_MAX):
        raise ValueError(
            f"rate_limits weekly={rl_weekly} out of bounds "
            f"[{_RATE_LIMIT_WEEKLY_MIN}, {_RATE_LIMIT_WEEKLY_MAX}]"
        )
    if not (_RATE_LIMIT_MONTHLY_MIN <= rl_monthly <= _RATE_LIMIT_MONTHLY_MAX):
        raise ValueError(
            f"rate_limits monthly={rl_monthly} out of bounds "
            f"[{_RATE_LIMIT_MONTHLY_MIN}, {_RATE_LIMIT_MONTHLY_MAX}]"
        )

    # Resolve token limits (None means unlimited)
    tl_daily: int | None = token_limits.get("daily") if token_limits else None
    tl_weekly: int | None = token_limits.get("weekly") if token_limits else None
    tl_monthly: int | None = token_limits.get("monthly") if token_limits else None

    if tl_daily is not None and not (_TOKEN_LIMIT_DAILY_MIN <= tl_daily <= _TOKEN_LIMIT_DAILY_MAX):
        raise ValueError(
            f"token_limits daily={tl_daily} out of bounds "
            f"[{_TOKEN_LIMIT_DAILY_MIN}, {_TOKEN_LIMIT_DAILY_MAX}]"
        )
    if tl_weekly is not None and not (_TOKEN_LIMIT_WEEKLY_MIN <= tl_weekly <= _TOKEN_LIMIT_WEEKLY_MAX):
        raise ValueError(
            f"token_limits weekly={tl_weekly} out of bounds "
            f"[{_TOKEN_LIMIT_WEEKLY_MIN}, {_TOKEN_LIMIT_WEEKLY_MAX}]"
        )
    if tl_monthly is not None and not (_TOKEN_LIMIT_MONTHLY_MIN <= tl_monthly <= _TOKEN_LIMIT_MONTHLY_MAX):
        raise ValueError(
            f"token_limits monthly={tl_monthly} out of bounds "
            f"[{_TOKEN_LIMIT_MONTHLY_MIN}, {_TOKEN_LIMIT_MONTHLY_MAX}]"
        )

    ts = _now_iso()
    scopes_json: str = (
        json.dumps(scopes) if scopes else json.dumps(["read"])
    )
    conn = init_db()

    conn.execute(
        """
        INSERT INTO users (
            id, name, key_id, encrypted_key, key_version, status, scopes,
            rate_limits_daily, rate_limits_weekly, rate_limits_monthly,
            token_limits_daily, token_limits_weekly, token_limits_monthly,
            created_at, updated_at, last_used_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            name,
            key_id,
            encrypted_key,
            1,
            "active",
            scopes_json,
            rl_daily,
            rl_weekly,
            rl_monthly,
            tl_daily,
            tl_weekly,
            tl_monthly,
            ts,
            ts,
            None,
        ),
    )
    conn.commit()

    logger.info(
        "user_store_user_created",
        user_id=user_id,
        key_id=key_id,
        name=name,
    )

    return {
        "user_id": user_id,
        "key_id": key_id,
        "encrypted_key": encrypted_key,
        "status": "active",
        "scopes": scopes if scopes else ["read"],
        "rate_limits": {
            "daily": rl_daily,
            "weekly": rl_weekly,
            "monthly": rl_monthly,
        },
        "token_limits": {
            "daily": tl_daily,
            "weekly": tl_weekly,
            "monthly": tl_monthly,
        },
        "created_at": ts,
        "updated_at": ts,
    }


def get_user_by_key_id(key_id: str) -> dict[str, Any] | None:
    """Look up a user by their unique key_id.

    Args:
        key_id: The unique key identifier.

    Returns:
        User dict or None if not found.
    """
    conn = init_db()
    row = conn.execute(
        "SELECT * FROM users WHERE key_id = ?", (key_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    """Look up a user by their primary key id.

    Args:
        user_id: The user UUID hex.

    Returns:
        User dict or None if not found.
    """
    conn = init_db()
    row = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def list_users(
    status_filter: str = "all",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Return a paginated list of users with optional status filter.

    Args:
        status_filter: 'all', 'active', 'disabled', or 'revoked'.
        page: 1-based page number.
        page_size: Items per page (max 100).

    Returns:
        Dict with users list, total count, page, page_size.

    Raises:
        ValueError: If page_size > 100 or page < 1.
    """
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if page_size < 1 or page_size > 100:
        raise ValueError(f"page_size must be in [1, 100], got {page_size}")

    offset: int = (page - 1) * page_size

    conn = init_db()

    if status_filter == "all":
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()["COUNT(*)"]
        rows = conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (page_size, offset),
        ).fetchall()
    else:
        valid_statuses = {"active", "disabled", "revoked"}
        if status_filter not in valid_statuses:
            raise ValueError(
                f"status_filter must be one of {valid_statuses}, got '{status_filter}'"
            )
        total = conn.execute(
            "SELECT COUNT(*) FROM users WHERE status = ?", (status_filter,)
        ).fetchone()["COUNT(*)"]
        rows = conn.execute(
            "SELECT * FROM users WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (status_filter, page_size, offset),
        ).fetchall()

    return {
        "users": [_row_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def update_user(user_id: str, **kwargs: Any) -> dict[str, Any]:
    """Update user fields by user_id.

    Supported kwargs: rate_limits, token_limits, name, status, scopes.

    Args:
        user_id: The user UUID hex.
        **kwargs: Fields to update.

    Returns:
        Updated user dict.

    Raises:
        ValueError: If user not found or invalid values provided.
    """
    conn = init_db()

    existing = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if existing is None:
        raise ValueError(f"user_id={user_id} not found")

    updates: dict[str, Any] = {}
    ts = _now_iso()

    if "rate_limits" in kwargs:
        rl = kwargs["rate_limits"]
        if not isinstance(rl, dict):
            raise ValueError("rate_limits must be a dict")
        daily = rl.get("daily", existing["rate_limits_daily"])
        weekly = rl.get("weekly", existing["rate_limits_weekly"])
        monthly = rl.get("monthly", existing["rate_limits_monthly"])

        if not (_RATE_LIMIT_DAILY_MIN <= daily <= _RATE_LIMIT_DAILY_MAX):
            raise ValueError(
                f"rate_limits daily={daily} out of bounds "
                f"[{_RATE_LIMIT_DAILY_MIN}, {_RATE_LIMIT_DAILY_MAX}]"
            )
        if not (_RATE_LIMIT_WEEKLY_MIN <= weekly <= _RATE_LIMIT_WEEKLY_MAX):
            raise ValueError(
                f"rate_limits weekly={weekly} out of bounds "
                f"[{_RATE_LIMIT_WEEKLY_MIN}, {_RATE_LIMIT_WEEKLY_MAX}]"
            )
        if not (_RATE_LIMIT_MONTHLY_MIN <= monthly <= _RATE_LIMIT_MONTHLY_MAX):
            raise ValueError(
                f"rate_limits monthly={monthly} out of bounds "
                f"[{_RATE_LIMIT_MONTHLY_MIN}, {_RATE_LIMIT_MONTHLY_MAX}]"
            )

        updates["rate_limits_daily"] = daily
        updates["rate_limits_weekly"] = weekly
        updates["rate_limits_monthly"] = monthly

    if "token_limits" in kwargs:
        tl = kwargs["token_limits"]
        if not isinstance(tl, dict):
            raise ValueError("token_limits must be a dict")
        daily = tl.get("daily", existing["token_limits_daily"])
        weekly = tl.get("weekly", existing["token_limits_weekly"])
        monthly = tl.get("monthly", existing["token_limits_monthly"])

        if daily is not None and not (_TOKEN_LIMIT_DAILY_MIN <= daily <= _TOKEN_LIMIT_DAILY_MAX):
            raise ValueError(
                f"token_limits daily={daily} out of bounds "
                f"[{_TOKEN_LIMIT_DAILY_MIN}, {_TOKEN_LIMIT_DAILY_MAX}]"
            )
        if weekly is not None and not (_TOKEN_LIMIT_WEEKLY_MIN <= weekly <= _TOKEN_LIMIT_WEEKLY_MAX):
            raise ValueError(
                f"token_limits weekly={weekly} out of bounds "
                f"[{_TOKEN_LIMIT_WEEKLY_MIN}, {_TOKEN_LIMIT_WEEKLY_MAX}]"
            )
        if monthly is not None and not (_TOKEN_LIMIT_MONTHLY_MIN <= monthly <= _TOKEN_LIMIT_MONTHLY_MAX):
            raise ValueError(
                f"token_limits monthly={monthly} out of bounds "
                f"[{_TOKEN_LIMIT_MONTHLY_MIN}, {_TOKEN_LIMIT_MONTHLY_MAX}]"
            )

        updates["token_limits_daily"] = daily
        updates["token_limits_weekly"] = weekly
        updates["token_limits_monthly"] = monthly

    if "name" in kwargs:
        updates["name"] = kwargs["name"]

    if "status" in kwargs:
        valid_statuses = {"active", "disabled", "revoked"}
        if kwargs["status"] not in valid_statuses:
            raise ValueError(
                f"status must be one of {valid_statuses}, got '{kwargs['status']}'"
            )
        updates["status"] = kwargs["status"]

    if "scopes" in kwargs:
        scopes_val = kwargs["scopes"]
        if not isinstance(scopes_val, list):
            raise ValueError("scopes must be a list")
        updates["scopes"] = json.dumps(scopes_val)

    if not updates:
        return _row_to_dict(existing)

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [ts, user_id]

    conn.execute(
        f"UPDATE users SET {set_clause}, updated_at = ? WHERE id = ?",
        values,
    )
    conn.commit()

    updated = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    logger.info(
        "user_store_user_updated",
        user_id=user_id,
        updated_fields=list(updates.keys()),
    )

    return _row_to_dict(updated)


def revoke_user(user_id: str) -> dict[str, Any]:
    """Revoke a user by setting status to 'revoked'.

    Args:
        user_id: The user UUID hex.

    Returns:
        Updated user dict with status='revoked'.

    Raises:
        ValueError: If user not found.
    """
    conn = init_db()

    existing = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if existing is None:
        raise ValueError(f"user_id={user_id} not found")

    ts = _now_iso()
    conn.execute(
        "UPDATE users SET status = 'revoked', updated_at = ? WHERE id = ?",
        (ts, user_id),
    )
    conn.commit()

    updated = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    logger.info("user_store_user_revoked", user_id=user_id)

    return _row_to_dict(updated)


def rotate_key(user_id: str) -> dict[str, Any]:
    """Rotate a user's key version (increment by 1).

    Returns the old key_id so the caller can revoke it if needed.

    Args:
        user_id: The user UUID hex.

    Returns:
        Dict with old_key_id, new_key_id, key_version, user_id.

    Raises:
        ValueError: If user not found.
    """
    conn = init_db()

    existing = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if existing is None:
        raise ValueError(f"user_id={user_id} not found")

    old_key_id: str = existing["key_id"]
    new_key_version: int = existing["key_version"] + 1
    ts = _now_iso()

    conn.execute(
        "UPDATE users SET key_version = ?, updated_at = ? WHERE id = ?",
        (new_key_version, ts, user_id),
    )
    conn.commit()

    logger.info(
        "user_store_key_rotated",
        user_id=user_id,
        old_key_id=old_key_id,
        new_key_version=new_key_version,
    )

    return {
        "user_id": user_id,
        "old_key_id": old_key_id,
        "new_key_version": new_key_version,
        "updated_at": ts,
    }


def update_rate_limits(
    user_id: str,
    daily: int | None = None,
    weekly: int | None = None,
    monthly: int | None = None,
) -> dict[str, Any]:
    """Update rate limits for a user with bounds validation.

    Args:
        user_id: The user UUID hex.
        daily: New daily limit (1-1000) or None to keep current.
        weekly: New weekly limit (1-10000) or None to keep current.
        monthly: New monthly limit (1-100000) or None to keep current.

    Returns:
        Updated user dict.

    Raises:
        ValueError: If user not found or bounds violated.
    """
    conn = init_db()

    existing = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if existing is None:
        raise ValueError(f"user_id={user_id} not found")

    updates: dict[str, int] = {}

    if daily is not None:
        if not (_RATE_LIMIT_DAILY_MIN <= daily <= _RATE_LIMIT_DAILY_MAX):
            raise ValueError(
                f"rate_limits daily={daily} out of bounds "
                f"[{_RATE_LIMIT_DAILY_MIN}, {_RATE_LIMIT_DAILY_MAX}]"
            )
        updates["rate_limits_daily"] = daily

    if weekly is not None:
        if not (_RATE_LIMIT_WEEKLY_MIN <= weekly <= _RATE_LIMIT_WEEKLY_MAX):
            raise ValueError(
                f"rate_limits weekly={weekly} out of bounds "
                f"[{_RATE_LIMIT_WEEKLY_MIN}, {_RATE_LIMIT_WEEKLY_MAX}]"
            )
        updates["rate_limits_weekly"] = weekly

    if monthly is not None:
        if not (_RATE_LIMIT_MONTHLY_MIN <= monthly <= _RATE_LIMIT_MONTHLY_MAX):
            raise ValueError(
                f"rate_limits monthly={monthly} out of bounds "
                f"[{_RATE_LIMIT_MONTHLY_MIN}, {_RATE_LIMIT_MONTHLY_MAX}]"
            )
        updates["rate_limits_monthly"] = monthly

    if not updates:
        return _row_to_dict(existing)

    ts = _now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [ts, user_id]

    conn.execute(
        f"UPDATE users SET {set_clause}, updated_at = ? WHERE id = ?",
        values,
    )
    conn.commit()

    updated = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    logger.info(
        "user_store_rate_limits_updated",
        user_id=user_id,
        updated_fields=list(updates.keys()),
    )

    return _row_to_dict(updated)


def update_token_limits(
    user_id: str,
    daily: int | None = None,
    weekly: int | None = None,
    monthly: int | None = None,
) -> dict[str, Any]:
    """Update token limits for a user with bounds validation.

    0 means unlimited. None means no limit configured (keep current).

    Args:
        user_id: The user UUID hex.
        daily: New daily token limit (0-10_000_000) or None to keep current.
        weekly: New weekly token limit (0-50_000_000) or None to keep current.
        monthly: New monthly token limit (0-200_000_000) or None to keep current.

    Returns:
        Updated user dict.

    Raises:
        ValueError: If user not found or bounds violated.
    """
    conn = init_db()

    existing = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if existing is None:
        raise ValueError(f"user_id={user_id} not found")

    updates: dict[str, int | None] = {}

    if daily is not None:
        if not (_TOKEN_LIMIT_DAILY_MIN <= daily <= _TOKEN_LIMIT_DAILY_MAX):
            raise ValueError(
                f"token_limits daily={daily} out of bounds "
                f"[{_TOKEN_LIMIT_DAILY_MIN}, {_TOKEN_LIMIT_DAILY_MAX}]"
            )
        updates["token_limits_daily"] = daily

    if weekly is not None:
        if not (_TOKEN_LIMIT_WEEKLY_MIN <= weekly <= _TOKEN_LIMIT_WEEKLY_MAX):
            raise ValueError(
                f"token_limits weekly={weekly} out of bounds "
                f"[{_TOKEN_LIMIT_WEEKLY_MIN}, {_TOKEN_LIMIT_WEEKLY_MAX}]"
            )
        updates["token_limits_weekly"] = weekly

    if monthly is not None:
        if not (_TOKEN_LIMIT_MONTHLY_MIN <= monthly <= _TOKEN_LIMIT_MONTHLY_MAX):
            raise ValueError(
                f"token_limits monthly={monthly} out of bounds "
                f"[{_TOKEN_LIMIT_MONTHLY_MIN}, {_TOKEN_LIMIT_MONTHLY_MAX}]"
            )
        updates["token_limits_monthly"] = monthly

    if not updates:
        return _row_to_dict(existing)

    ts = _now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [ts, user_id]

    conn.execute(
        f"UPDATE users SET {set_clause}, updated_at = ? WHERE id = ?",
        values,
    )
    conn.commit()

    updated = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()

    logger.info(
        "user_store_token_limits_updated",
        user_id=user_id,
        updated_fields=list(updates.keys()),
    )

    return _row_to_dict(updated)


def get_rate_limit_snapshots(user_id: str) -> dict[str, Any]:
    """Return current rate limit snapshots per tier for a user.

    Args:
        user_id: The user UUID hex.

    Returns:
        Dict keyed by tier ('daily', 'weekly', 'monthly') with count and
        last_updated values. Missing tiers default to count=0.
    """
    conn = init_db()

    tiers = ["daily", "weekly", "monthly"]
    snapshots: dict[str, dict[str, Any]] = {}

    for tier in tiers:
        row = conn.execute(
            "SELECT count, last_updated FROM rate_limit_snapshots "
            "WHERE user_id = ? AND tier = ?",
            (user_id, tier),
        ).fetchone()
        if row is None:
            snapshots[tier] = {"count": 0, "last_updated": None}
        else:
            snapshots[tier] = {"count": row["count"], "last_updated": row["last_updated"]}

    return snapshots


def get_token_cost_snapshots(user_id: str) -> dict[str, Any]:
    """Return current token cost snapshots per tier for a user.

    Args:
        user_id: The user UUID hex.

    Returns:
        Dict keyed by tier ('daily', 'weekly', 'monthly') with input_tokens,
        output_tokens, total_tokens, and last_updated values.
        Missing tiers default to all zeros.
    """
    conn = init_db()

    tiers = ["daily", "weekly", "monthly"]
    snapshots: dict[str, dict[str, Any]] = {}

    for tier in tiers:
        row = conn.execute(
            "SELECT input_tokens, output_tokens, total_tokens, last_updated "
            "FROM token_cost_snapshots WHERE user_id = ? AND tier = ?",
            (user_id, tier),
        ).fetchone()
        if row is None:
            snapshots[tier] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "last_updated": None,
            }
        else:
            snapshots[tier] = {
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "total_tokens": row["total_tokens"],
                "last_updated": row["last_updated"],
            }

    return snapshots


def sync_rate_limits_to_db(user_id: str, tier: str, count: int) -> None:
    """Upsert a rate limit snapshot for a user.

    Args:
        user_id: The user UUID hex.
        tier: 'daily', 'weekly', or 'monthly'.
        count: Current usage count.

    Raises:
        ValueError: If tier is invalid.
    """
    valid_tiers = {"daily", "weekly", "monthly"}
    if tier not in valid_tiers:
        raise ValueError(f"tier must be one of {valid_tiers}, got '{tier}'")

    ts = _now_iso()
    conn = init_db()

    conn.execute(
        """
        INSERT INTO rate_limit_snapshots (user_id, tier, count, last_updated)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (user_id, tier) DO UPDATE SET
            count = excluded.count,
            last_updated = excluded.last_updated
        """,
        (user_id, tier, count, ts),
    )
    conn.commit()


def sync_token_costs_to_db(
    user_id: str,
    tier: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Upsert a token cost snapshot for a user.

    Args:
        user_id: The user UUID hex.
        tier: 'daily', 'weekly', or 'monthly'.
        input_tokens: Input token count for this period.
        output_tokens: Output token count for this period.

    Raises:
        ValueError: If tier is invalid.
    """
    valid_tiers = {"daily", "weekly", "monthly"}
    if tier not in valid_tiers:
        raise ValueError(f"tier must be one of {valid_tiers}, got '{tier}'")

    total_tokens: int = input_tokens + output_tokens
    ts = _now_iso()
    conn = init_db()

    conn.execute(
        """
        INSERT INTO token_cost_snapshots (user_id, tier, input_tokens, output_tokens, total_tokens, last_updated)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (user_id, tier) DO UPDATE SET
            input_tokens = excluded.input_tokens,
            output_tokens = excluded.output_tokens,
            total_tokens = excluded.total_tokens,
            last_updated = excluded.last_updated
        """,
        (user_id, tier, input_tokens, output_tokens, total_tokens, ts),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Normalise a raw row dict into the standard user response format.

    Ensures rate_limits and token_limits are returned as nested dicts.
    """
    return {
        "user_id": row["id"],
        "name": row["name"],
        "key_id": row["key_id"],
        "encrypted_key": row["encrypted_key"],
        "key_version": row["key_version"],
        "status": row["status"],
        "rate_limits": {
            "daily": row["rate_limits_daily"],
            "weekly": row["rate_limits_weekly"],
            "monthly": row["rate_limits_monthly"],
        },
        "token_limits": {
            "daily": row["token_limits_daily"],
            "weekly": row["token_limits_weekly"],
            "monthly": row["token_limits_monthly"],
        },
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_used_at": row["last_used_at"],
    }
