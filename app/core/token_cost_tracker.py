"""Token cost tracking for MCP authorization system.

Manages per-user token usage counters across tiers (daily/weekly/monthly).
Primary storage: Redis atomic counters. Fallback: SQLite token_cost_snapshots
table. Enforcement is informational only — produces warnings in tool responses,
never hard-blocks execution.
"""

from __future__ import annotations

import asyncio
import datetime
import sqlite3
import time
from typing import Final, cast

import redis
import redis.asyncio as redis_async
import structlog

from app.core.config import Settings
from app.core.rate_limiter import get_tier_ttl as _rl_get_tier_ttl
from app.core.user_store import get_user_by_id

logger = structlog.get_logger("token_cost_tracker")

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

REDIS_KEY_PREFIX: Final[str] = "tc:"

REDIS_TIMEOUT: Final[float] = 2.0
REDIS_HEALTH_CHECK_INTERVAL: Final[float] = 30.0

# Token limit bounds (0 = unlimited explicitly, None = no limit configured)
_TOKEN_LIMIT_DAILY_MIN: Final[int] = 0
_TOKEN_LIMIT_DAILY_MAX: Final[int] = 10_000_000
_TOKEN_LIMIT_WEEKLY_MIN: Final[int] = 0
_TOKEN_LIMIT_WEEKLY_MAX: Final[int] = 50_000_000
_TOKEN_LIMIT_MONTHLY_MIN: Final[int] = 0
_TOKEN_LIMIT_MONTHLY_MAX: Final[int] = 200_000_000

# ---------------------------------------------------------------------------
# Redis connection pool (lazy init, shared with rate_limiter pattern)
# ---------------------------------------------------------------------------

_redis_pool: redis.Redis | None = None
_redis_async_pool: redis_async.Redis | None = None
_redis_available: bool = True
_redis_last_check: float = 0.0

# Whitelist mapping for safe column selection in SQL queries
_COLUMN_WHITELIST: Final[dict[str, str]] = {
    "input": "input_tokens",
    "output": "output_tokens",
}


def _get_pool(settings: Settings) -> redis.Redis:
    """Return (or create) the sync Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=REDIS_TIMEOUT,
            socket_connect_timeout=REDIS_TIMEOUT,
            health_check_interval=REDIS_HEALTH_CHECK_INTERVAL,
        )
    return _redis_pool


def _get_async_pool(settings: Settings) -> redis_async.Redis:
    """Return (or create) the async Redis connection pool."""
    global _redis_async_pool
    if _redis_async_pool is None:
        _redis_async_pool = redis_async.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=REDIS_TIMEOUT,
            socket_connect_timeout=REDIS_TIMEOUT,
            health_check_interval=REDIS_HEALTH_CHECK_INTERVAL,
        )
    return _redis_async_pool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redis_key(user_id: str, tier: str) -> str:
    """Build Redis key for a token-cost counter."""
    return f"{REDIS_KEY_PREFIX}{user_id}:{tier}"


def _redis_get_int(pool: redis.Redis, key: str) -> int | None:
    """Read a Redis key and cast to int.

    The redis client lacks type stubs — `pool.get()` returns `str | bytes | None`.
    This helper narrows the type via explicit cast after None guard.

    Args:
        pool: Redis connection pool.
        key: Redis key to read.

    Returns:
        Integer value or None if key absent.
    """
    raw = pool.get(key)  # type: ignore[assignment]
    if raw is None:
        return None
    return cast(int, int(raw))  # type: ignore[arg-type]


async def _redis_get_int_async(pool: redis_async.Redis, key: str) -> int | None:
    """Async variant of `_redis_get_int` for use with async Redis client."""
    raw = await pool.get(key)  # type: ignore[assignment]
    if raw is None:
        return None
    return cast(int, int(raw))  # type: ignore[arg-type]


def _get_counter_with_fallback(
    user_id: str,
    tier: str,
    input_suffix: str,
    output_suffix: str,
) -> tuple[int, int]:
    """Get counters from Redis with single DB fallback.

    Returns (input, output) as ints — DB fallback guarantees non-zero ints.

    Key-absence (new user / expired keys) does NOT set `_redis_available = False`.
    Only connection errors trigger fallback state change. TTL-based recovery
    periodically re-checks Redis health.

    Args:
        user_id: User identifier.
        tier: Rate-limit tier.
        input_suffix: Counter suffix for input (typically 'input').
        output_suffix: Counter suffix for output (typically 'output').

    Returns:
        Tuple of (input_counter, output_counter) as ints.
    """
    global _redis_available, _redis_last_check

    # TTL-based recovery: schedule async ping if previously marked unavailable
    if not _redis_available:
        if (
            _redis_last_check > 0
            and time.time() - _redis_last_check > REDIS_HEALTH_CHECK_INTERVAL
        ):
            # Schedule non-blocking recovery ping — current call returns DB fallback
            _schedule_redis_recovery()
            return (
                _db_get_token_counter(user_id, tier, input_suffix),
                _db_get_token_counter(user_id, tier, output_suffix),
            )
        else:
            return (
                _db_get_token_counter(user_id, tier, input_suffix),
                _db_get_token_counter(user_id, tier, output_suffix),
            )

    try:
        pool = _get_pool(_get_settings())
        inp = _redis_get_int(pool, f"{_redis_key(user_id, tier)}:{input_suffix}")
        out = _redis_get_int(pool, f"{_redis_key(user_id, tier)}:{output_suffix}")
        if inp is None or out is None:
            # Key absent (new user / expired TTL) — DB fallback ONLY,
            # do NOT mark Redis as unavailable.
            return (
                _db_get_token_counter(user_id, tier, input_suffix),
                _db_get_token_counter(user_id, tier, output_suffix),
            )
        return inp, out
    except (redis.ConnectionError, redis.TimeoutError, OSError):
        logger.warning(
            "redis_counter_failed",
            user_id=user_id,
            tier=tier,
            fallback="db",
        )
        _redis_available = False
        _redis_last_check = time.time()
        return (
            _db_get_token_counter(user_id, tier, input_suffix),
            _db_get_token_counter(user_id, tier, output_suffix),
        )


def _get_counter_from_redis(
    user_id: str,
    tier: str,
    suffix: str,
) -> int | None:
    """Read a single Redis counter (input or output).

    Returns None if the key does not exist or Redis is unavailable.

    Args:
        user_id: User identifier.
        tier: Rate-limit tier.
        suffix: Counter suffix — 'input' or 'output'.

    Returns:
        Counter value or None.
    """
    if not _redis_available:
        return None
    pool = _get_pool(_get_settings())
    key = f"{_redis_key(user_id, tier)}:{suffix}"
    value = _redis_get_int(pool, key)
    return value


def _increment_counter_in_redis(
    user_id: str,
    tier: str,
    suffix: str,
    amount: int,
) -> int | None:
    """Increment a Redis counter by amount and apply TTL.

    Returns None if Redis is unavailable.

    Args:
        user_id: User identifier.
        tier: Rate-limit tier.
        suffix: Counter suffix — 'input' or 'output'.
        amount: Increment value.

    Returns:
        New counter value or None if Redis unavailable.
    """
    if not _redis_available:
        return None
    pool = _get_pool(_get_settings())
    key = f"{_redis_key(user_id, tier)}:{suffix}"
    pool.incrby(key, amount)  # type: ignore[no-untyped-call]
    ttl = _rl_get_tier_ttl(tier)
    pool.expire(key, ttl)  # type: ignore[no-untyped-call]
    return _redis_get_int(pool, key)


def _get_user_token_limits(user_id: str) -> dict[str, int | None]:
    """Retrieve configured token limits for a user from the DB.

    Returns:
        Dict with keys 'daily', 'weekly', 'monthly'.
        None means no limit configured (unlimited).
    """
    user = get_user_by_id(user_id)
    if user is None:
        return {"daily": None, "weekly": None, "monthly": None}
    tl = user.get("token_limits", {})
    return {
        "daily": tl.get("daily"),
        "weekly": tl.get("weekly"),
        "monthly": tl.get("monthly"),
    }


def _db_get_connection() -> "sqlite3.Connection":
    """Return a SQLite connection to the KG database (shared with user_store)."""
    from app.core.user_store import init_db

    return init_db()


def _db_get_token_counter(
    user_id: str,
    tier: str,
    suffix: str,
) -> int:
    """Read a token counter from token_cost_snapshots table.

    Uses a column whitelist to prevent SQL injection from dynamic suffix values.

    Args:
        user_id: User identifier.
        tier: Rate-limit tier.
        suffix: Counter suffix — 'input' or 'output'.

    Returns:
        Current counter value (0 if no row exists).
    """
    conn = _db_get_connection()
    conn.row_factory = sqlite3.Row
    try:
        col = _COLUMN_WHITELIST.get(suffix)
        if col is None:
            logger.warning(
                "invalid_counter_suffix",
                suffix=suffix,
                allowed=list(_COLUMN_WHITELIST.keys()),
            )
            return 0
        row = conn.execute(
            f"SELECT {col} FROM token_cost_snapshots WHERE user_id = ? AND tier = ?",  # nosec B608 — col validated against _COLUMN_WHITELIST
            (user_id, tier),
        ).fetchone()
        return (row[col] or 0) if row else 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Non-blocking Redis recovery helpers
# ---------------------------------------------------------------------------


async def _perform_redis_recovery() -> None:
    """Non-blocking Redis recovery ping.

    Updates ``_redis_available`` and ``_redis_last_check`` asynchronously.
    """
    global _redis_available, _redis_last_check
    try:
        settings = _get_settings()
        pool = _get_pool(settings)
        await asyncio.to_thread(pool.ping)  # type: ignore[no-untyped-call]
        _redis_available = True
        _redis_last_check = time.time()
    except (redis.ConnectionError, redis.TimeoutError, OSError):
        logger.warning("redis_recovery_failed", fallback="db")
        _redis_last_check = time.time()


def _schedule_redis_recovery() -> None:
    """Schedule a non-blocking async Redis recovery ping.

    Uses ``asyncio.create_task`` if an event loop is running.
    Silently skips if no event loop is available.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_perform_redis_recovery())
    except RuntimeError:
        pass  # no running event loop — skip async recovery


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_tokens(
    user_id: str,
    tier: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Record token usage for a user/tier.

    Increments Redis counters tc:{user_id}:{tier}:input and
    tc:{user_id}:{tier}:output with tier-appropriate TTL.
    Async non-blocking — failure does not affect tool execution.
    If Redis unavailable → uses DB fallback (token_cost_snapshots).

    Args:
        user_id: User identifier (UUID hex string).
        tier: Rate-limit tier — 'daily', 'weekly', or 'monthly'.
        input_tokens: Number of input tokens consumed.
        output_tokens: Number of output tokens consumed.

    Note:
        Token cost data is NOT logged to audit logs. Only event metadata
        is recorded via structlog.
    """
    global _redis_available

    if _redis_available:
        try:
            _increment_counter_in_redis(user_id, tier, "input", input_tokens)
            _increment_counter_in_redis(user_id, tier, "output", output_tokens)
            return
        except (redis.ConnectionError, redis.TimeoutError, OSError):
            logger.warning(
                "redis_record_failed",
                user_id=user_id,
                tier=tier,
                fallback="db",
            )
            _redis_available = False

    # DB fallback — non-blocking, silent failure
    try:
        conn = _db_get_connection()
        conn.row_factory = sqlite3.Row
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Read current values via unified fallback helper
        current_input, current_output = _get_counter_with_fallback(
            user_id, tier, "input", "output"
        )

        new_input = current_input + input_tokens
        new_output = current_output + output_tokens

        conn.execute(
            """
            INSERT INTO token_cost_snapshots (user_id, tier, input_tokens, output_tokens, total_tokens, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id, tier)
            DO UPDATE SET
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                total_tokens = excluded.total_tokens,
                last_updated = excluded.last_updated
            """,
            (user_id, tier, new_input, new_output, new_input + new_output, now),
        )
        conn.commit()
    finally:
        conn.close()


def check_token_limits(
    user_id: str,
    tier: str,
) -> dict[str, int | bool | str | None]:
    """Check current token usage against configured limits.

    Returns:
        Dict with keys:
            current_input: int — current input token count
            current_output: int — current output token count
            total: int — combined total
            limit_input: int | None — configured input limit (None = unlimited)
            limit_output: int | None — configured output limit (None = unlimited)
            exceeded: bool — True if any limit is exceeded
            warning: str | None — warning message if exceeded, None otherwise

    Note:
        Enforcement is informational only — never hard-blocks execution.
    """
    global _redis_available

    current_input, current_output = _get_counter_with_fallback(
        user_id, tier, "input", "output"
    )

    total = current_input + current_output

    # Get configured limits from user store — tier-specific
    limits = _get_user_token_limits(user_id)
    limit_input = limits.get(tier)  # read limit for the specific tier
    limit_output = (
        limit_input  # single limit applies to both input and output for the tier
    )

    # Determine exceeded status and warning
    exceeded = False
    warning: str | None = None

    if limit_input is not None and limit_input > 0 and total >= limit_input:
        exceeded = True
        remaining = max(0, limit_input - total)
        warning = (
            f"Token usage ({total} total) approaching/exceeding {tier} limit "
            f"({limit_input}). Remaining: {remaining} tokens."
        )

    return {
        "current_input": current_input,
        "current_output": current_output,
        "total": total,
        "limit_input": limit_input,
        "limit_output": limit_output,
        "exceeded": exceeded,
        "warning": warning,
    }


def sync_to_db(
    user_id: str,
    tier: str,
) -> None:
    """Sync Redis token cost counters to token_cost_snapshots table.

    Upserts (user_id, tier) → (input_tokens, output_tokens, total_tokens,
    last_updated).

    Args:
        user_id: User identifier.
        tier: Rate-limit tier.
    """
    global _redis_available

    current_input, current_output = _get_counter_with_fallback(
        user_id, tier, "input", "output"
    )

    total = current_input + current_output

    conn = _db_get_connection()
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO token_cost_snapshots (user_id, tier, input_tokens, output_tokens, total_tokens, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id, tier)
            DO UPDATE SET
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                total_tokens = excluded.total_tokens,
                last_updated = excluded.last_updated
            """,
            (user_id, tier, current_input, current_output, total, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_token_usage(
    user_id: str,
    tier: str,
) -> dict[str, int]:
    """Get current token usage from Redis or DB fallback.

    Args:
        user_id: User identifier.
        tier: Rate-limit tier.

    Returns:
        Dict with keys: input_tokens, output_tokens, total_tokens.
    """
    global _redis_available

    current_input, current_output = _get_counter_with_fallback(
        user_id, tier, "input", "output"
    )

    return {
        "input_tokens": current_input,
        "output_tokens": current_output,
        "total_tokens": current_input + current_output,
    }


async def flush_counters_to_db() -> dict[str, int]:
    """Flush all Redis token cost counters to the DB snapshots table.

    Migration note: this function was transitioned from sync to async via
    ``asyncio.to_thread`` for the SQLite upsert step to prevent blocking
    the event loop. Future maintainers should keep the DB write path in
    ``asyncio.to_thread`` when modifying this function.

    Uses Redis SCAN (cursor iteration) to enumerate keys matching ``tc:*``
    instead of the blocking KEYS command. Returns counts of successful and
    failed syncs.

    Returns:
        Dict with keys: synced, failed.
    """
    global _redis_available

    if not _redis_available:
        return {"synced": 0, "failed": 0}

    try:
        pool = _get_async_pool(_get_settings())
    except (redis_async.ConnectionError, redis_async.TimeoutError, OSError):
        _redis_available = False
        return {"synced": 0, "failed": 0}

    synced = 0
    failed = 0

    cursor: int = 0
    while True:
        cursor, keys = await pool.scan(cursor=cursor, match="tc:*", count=100)
        if not keys:
            if cursor == 0:
                break
            continue
        # keys is non-empty list here (guard above handles empty/None)
        for key in keys:
            try:
                raw = key.decode() if isinstance(key, bytes) else key
                # Strip REDIS_KEY_PREFIX ("tc:") — key format: tc:user_id:tier:suffix
                _, rest = raw.split(":", 1)
                if rest.count(":") < 2:
                    continue  # malformed key (expected user_id:tier:suffix)
                user_id, tier_rest = rest.split(":", 1)
                tier, suffix = tier_rest.split(":", 1)

                if suffix not in ("input", "output"):
                    continue

                count_val = await _redis_get_int_async(pool, key)
                count_val = count_val if count_val is not None else 0

                # Read the counterpart counter
                counterpart_key = f"{REDIS_KEY_PREFIX}{user_id}:{tier}:{'output' if suffix == 'input' else 'input'}"
                counterpart_count = await _redis_get_int_async(pool, counterpart_key)
                counterpart_count = (
                    counterpart_count if counterpart_count is not None else 0
                )

                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                await asyncio.to_thread(
                    _flush_single_token_snapshot,
                    user_id,
                    tier,
                    counterpart_count,
                    count_val,
                    now,
                )

                synced += 1
            except Exception:
                failed += 1

        if cursor == 0:
            break

    return {"synced": synced, "failed": failed}


def _flush_single_token_snapshot(
    user_id: str,
    tier: str,
    input_tokens: int,
    output_tokens: int,
    now: str,
) -> None:
    """Blocking SQLite upsert for token_cost_snapshots (run via asyncio.to_thread)."""
    conn = _db_get_connection()
    try:
        conn.execute(
            """
            INSERT INTO token_cost_snapshots (user_id, tier, input_tokens, output_tokens, total_tokens, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id, tier)
            DO UPDATE SET
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                total_tokens = excluded.total_tokens,
                last_updated = excluded.last_updated
            """,
            (
                user_id,
                tier,
                input_tokens,
                output_tokens,
                input_tokens + output_tokens,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_tier_ttl(tier: str) -> int:
    """Return TTL for tier (same as rate_limiter).

    Args:
        tier: Tier name ('daily', 'weekly', 'monthly', or custom).

    Returns:
        TTL in seconds. Defaults to 86400 for unknown tiers.
    """
    return _rl_get_tier_ttl(tier)


def validate_token_limit_bounds(
    daily: int | None,
    weekly: int | None,
    monthly: int | None,
) -> bool:
    """Validate token limit bounds.

    Bounds:
        daily: [0, 10_000_000]
        weekly: [0, 50_000_000]
        monthly: [0, 200_000_000]

    0 = unlimited allowed. None = no limit configured.

    Args:
        daily: Daily token limit value or None.
        weekly: Weekly token limit value or None.
        monthly: Monthly token limit value or None.

    Returns:
        True if all provided values are within bounds, False otherwise.
    """
    if daily is not None:
        if not (_TOKEN_LIMIT_DAILY_MIN <= daily <= _TOKEN_LIMIT_DAILY_MAX):
            return False
    if weekly is not None:
        if not (_TOKEN_LIMIT_WEEKLY_MIN <= weekly <= _TOKEN_LIMIT_WEEKLY_MAX):
            return False
    if monthly is not None:
        if not (_TOKEN_LIMIT_MONTHLY_MIN <= monthly <= _TOKEN_LIMIT_MONTHLY_MAX):
            return False
    return True
