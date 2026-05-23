"""Custom token verifier for FastMCP auth integration.

Provides FastMCP-compatible verify_token (returns AccessToken | None) and
validate (returns bool) callables, along with helper functions for building
access tokens, retrieving admin key IDs, and checking user status.

Flow (strict order):
    1. Extract key_id from token (prefix "key_")
    2. Lookup user by key_id in DB (user_store.get_user_by_key_id)
    3. Check Redis rate limits (rate_limiter.check_rate_limit)
       — after DB lookup, before decrypt
    4. Decrypt key from DB (encryption.decrypt_key)
    5. Compare with token using hmac.compare_digest() for constant-time comparison
    6. Check user status (active|disabled|revoked)
    7. Increment rate limit counter (rate_limiter.increment_counter)
    8. Record token cost info (token_cost_tracker.check_token_limits)
    9. Return result

Audit events:
    - invalid_token: key_id not found in DB
    - rate_limit_exceeded: rate limit check returned allowed=False
    - user_disabled: user status is 'disabled' or 'revoked'
    - token_validated: successful validation

Token cost data (input/output tokens) MUST NOT be included in audit logs.
"""

from __future__ import annotations

import hmac
import inspect
from datetime import datetime, timezone
from typing import Any, Final

import structlog

from app.core.config import Settings
from app.core.encryption import decrypt_key
from app.core.rate_limiter import check_rate_limit, increment_counter
from app.core.token_cost_tracker import check_token_limits
from app.core.user_store import get_user_by_id, get_user_by_key_id

logger: structlog.BoundLogger = structlog.get_logger(__name__)

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
# Constants
# ---------------------------------------------------------------------------

_KEY_PREFIX: Final[str] = "key_"
_DEFAULT_TIER: Final[str] = "daily"

# ---------------------------------------------------------------------------
# AccessToken model — FastMCP-compatible structure
# ---------------------------------------------------------------------------


class AccessToken:
    """FastMCP-compatible access token representation.

    Mirrors FastMCP 3.2.4 AccessToken with claims as dict[str, Any].
    """

    __slots__ = ("user_id", "key_id", "scopes", "status", "claims")

    def __init__(
        self,
        user_id: str,
        key_id: str,
        scopes: list[str],
        status: str,
    ) -> None:
        """Initialize AccessToken.

        Args:
            user_id: User identifier (UUID hex string).
            key_id: Key identifier (prefixed with "key_").
            scopes: List of authorized scopes.
            status: User status ('active', 'disabled', 'revoked').
        """
        self.user_id: str = user_id
        self.key_id: str = key_id
        self.scopes: list[str] = scopes
        self.status: str = status
        self.claims: dict[str, Any] = {
            "user_id": user_id,
            "key_id": key_id,
            "scopes": scopes,
            "status": status,
        }

    def __repr__(self) -> str:
        return (
            f"AccessToken(user_id={self.user_id!r}, key_id={self.key_id!r}, "
            f"scopes={self.scopes!r}, status={self.status!r})"
        )


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------


def _audit_invalid_token(key_id: str) -> None:
    """Log audit event for invalid token (key_id not found in DB).

    Args:
        key_id: The key_id extracted from the token.
    """
    logger.info(
        "invalid_token",
        key_id=key_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        result="failure",
    )


def _audit_rate_limit_exceeded(user_id: str, key_id: str, tier: str) -> None:
    """Log audit event for rate limit exceeded.

    Args:
        user_id: User identifier.
        key_id: Key identifier.
        tier: Rate-limit tier that was exceeded.
    """
    logger.info(
        "rate_limit_exceeded",
        user_id=user_id,
        key_id=key_id,
        tier=tier,
        timestamp=datetime.now(timezone.utc).isoformat(),
        result="failure",
    )


def _audit_user_disabled(user_id: str, key_id: str, status: str) -> None:
    """Log audit event for disabled/revoked user.

    Args:
        user_id: User identifier.
        key_id: Key identifier.
        status: User status ('disabled' or 'revoked').
    """
    logger.info(
        "user_disabled",
        user_id=user_id,
        key_id=key_id,
        status=status,
        timestamp=datetime.now(timezone.utc).isoformat(),
        result="failure",
    )


def _audit_token_validated(user_id: str, key_id: str) -> None:
    """Log audit event for successful token validation.

    Args:
        user_id: User identifier.
        key_id: Key identifier.
    """
    logger.info(
        "token_validated",
        user_id=user_id,
        key_id=key_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        result="success",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify_token(token: str) -> AccessToken | None:
    """FastMCP-compatible verify_token callable.

    Returns AccessToken with claims if valid, None if invalid.

    claims = {user_id, key_id, scopes, status}

    Flow:
        1. Extract key_id from token (prefix "key_")
        2. Lookup user by key_id in DB
        3. Check rate limits (before decrypt)
        4. Decrypt key from DB
        5. Constant-time comparison with hmac.compare_digest()
        6. Check user status
        7. Increment rate limit counter
        8. Check token cost limits (informational only)
        9. Return AccessToken or None

    FastMCP compatibility:
        - DebugTokenVerifier accepts sync callables via inspect.isawaitable detection
        - validate_token wrapper uses verify_token internally
        - TokenVerifierProtocol requires async verify_token for direct subclass use
        - Current use case: DebugTokenVerifier(validate=validate_token) — sync OK

    Args:
        token: The token string to verify.

    Returns:
        AccessToken with claims if valid, None if invalid.
    """
    # Step 1: Extract key_id from token
    if not token.startswith(_KEY_PREFIX):
        _audit_invalid_token(token)
        return None

    key_id: str = token[len(_KEY_PREFIX) :]

    # Step 2: Lookup user by key_id in DB
    user = get_user_by_key_id(key_id)
    if user is None:
        _audit_invalid_token(key_id)
        return None

    user_id: str = user["user_id"]
    encrypted_key: str | None = user.get("encrypted_key")
    status: str = user.get("status", "active")
    scopes: list[str] = user.get("scopes", [])

    # Step 3: Check rate limits (after DB lookup, before decrypt)
    rate_limit_info = check_rate_limit(user_id, _DEFAULT_TIER)
    if not rate_limit_info["allowed"]:
        _audit_rate_limit_exceeded(user_id, key_id, _DEFAULT_TIER)
        return None

    # Step 4: Decrypt key from DB
    if encrypted_key is None:
        _audit_invalid_token(key_id)
        return None

    try:
        decrypted_key: str = decrypt_key(encrypted_key)
    except ValueError:
        _audit_invalid_token(key_id)
        return None

    # Step 5: Constant-time comparison with hmac.compare_digest()
    if not hmac.compare_digest(decrypted_key, token):
        _audit_invalid_token(key_id)
        return None

    # Step 6: Check user status
    if status in ("disabled", "revoked"):
        _audit_user_disabled(user_id, key_id, status)
        return None

    # Step 7: Increment rate limit counter
    increment_counter(user_id, _DEFAULT_TIER)

    # Step 8: Check token cost limits (informational only)
    token_cost_info = check_token_limits(user_id, _DEFAULT_TIER)

    # Step 9: Return AccessToken
    _audit_token_validated(user_id, key_id)

    access_token = AccessToken(
        user_id=user_id,
        key_id=key_id,
        scopes=scopes,
        status=status,
    )

    # Attach rate limit and token cost info for tool-level use
    access_token.claims["rate_limit_info"] = rate_limit_info
    access_token.claims["token_cost_info"] = token_cost_info

    return access_token


def validate_token(token: str) -> bool:
    """FastMCP-compatible validate callable (returns bool).

    Calls verify_token internally, returns True if AccessToken returned.

    Same flow as verify_token but returns bool only.
    Async-safe (inspect.isawaitable check).

    Args:
        token: The token string to validate.

    Returns:
        True if token is valid, False otherwise.
    """
    # Async-safe: handle if verify_token is awaited
    result = verify_token(token)

    if inspect.isawaitable(result):
        # Should not happen in sync context, but handle gracefully
        raise TypeError(
            "validate_token expects a synchronous verify_token callable. "
            "Use validate_token_async for async contexts."
        )

    return result is not None


def get_auth_context(token: str) -> dict[str, Any] | None:
    """Get full auth context for tool-level use.

    Returns:
        Dict with keys: user_id, key_id, scopes, status, rate_limit_info,
        token_cost_info. None if token invalid.

    Args:
        token: The token string to extract context from.

    Returns:
        Full auth context dict or None if token invalid.
    """
    access_token = verify_token(token)
    if access_token is None:
        return None

    context: dict[str, Any] = {
        "user_id": access_token.user_id,
        "key_id": access_token.key_id,
        "scopes": access_token.scopes,
        "status": access_token.status,
        "rate_limit_info": access_token.claims.get("rate_limit_info", {}),
        "token_cost_info": access_token.claims.get("token_cost_info", {}),
    }

    return context


def create_access_token(
    user_id: str,
    key_id: str,
    scopes: list[str],
    status: str,
) -> dict[str, Any]:
    """Create AccessToken-compatible claims dict.

    claims = {user_id, key_id, scopes, status}
    Used internally to build result for FastMCP.

    Args:
        user_id: User identifier (UUID hex string).
        key_id: Key identifier (prefixed with "key_").
        scopes: List of authorized scopes.
        status: User status ('active', 'disabled', 'revoked').

    Returns:
        Dict with user_id, key_id, scopes, status.
    """
    claims: dict[str, Any] = {
        "user_id": user_id,
        "key_id": key_id,
        "scopes": scopes,
        "status": status,
    }

    return claims


def get_admin_key_ids() -> list[str]:
    """Get admin key_ids from Settings.ADMIN_KEY_IDS.

    Returns list of key_ids with admin scope.

    Returns:
        List of admin key_id strings.
    """
    admin_key_ids: list[str] = _get_settings().ADMIN_KEY_IDS

    return admin_key_ids


def check_user_status(user_id: str) -> str:
    """Check user status from DB.

    Return: 'active', 'disabled', or 'revoked'.

    Args:
        user_id: User identifier (UUID hex string).

    Returns:
        User status string ('active', 'disabled', or 'revoked').
    """
    user = get_user_by_id(user_id)
    if user is None:
        return "revoked"

    status: str = user.get("status", "active")

    return status
