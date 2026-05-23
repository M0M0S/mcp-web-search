"""Redis rate limiter with SQLite DB fallback for MCP authorization system.

Manages per-user rate limit counters across tiers (daily/weekly/monthly).
Primary storage: Redis atomic counters. Fallback: SQLite rate_limit_snapshots
table with threading.Lock for single-process atomicity.
"""

from __future__ import annotations

import asyncio
import datetime
import sqlite3
import threading
import time
from typing import Final

import redis
import redis.asyncio as redis_async
import structlog

from app.core.config import Settings

logger = structlog.get_logger("rate_limiter")

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

REDIS_KEY_PREFIX: Final[str] = "rl:"

DEFAULT_LIMITS: Final[dict[str, int]] = {
    "daily": 100,
    "weekly": 500,
    "monthly": 2000,
}

DEFAULT_TTLS: Final[dict[str, int]] = {
    "daily": 86400,
    "weekly": 604800,
    "monthly": 2592000,
}

REDIS_TIMEOUT: Final[float] = 2.0
REDIS_HEALTH_CHECK_INTERVAL: Final[float] = 30.0

# ---------------------------------------------------------------------------
# DB fallback lock (single-process only)
# ---------------------------------------------------------------------------

_db_lock: Final[threading.Lock] = threading.Lock()

# ---------------------------------------------------------------------------
# Redis connection pool (lazy init)
# ---------------------------------------------------------------------------

_redis_pool: redis.Redis | None = None
_redis_async_pool: redis_async.Redis | None = None
_redis_available: bool = True
_redis_last_check: float = 0.0


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
    """Build Redis key for a rate-limit counter."""
    return f"{REDIS_KEY_PREFIX}{user_id}:{tier}"


def _get_tier_ttl(tier: str) -> int:
    """Return TTL (seconds) for a rate-limit tier.

    Falls back to DEFAULT_TTLS when an unknown tier is requested.
    """
    return DEFAULT_TTLS.get(tier, 86400)


def _get_default_limit(tier: str) -> int:
    """Return the default limit for a tier when none is provided."""
    return DEFAULT_LIMITS.get(tier, 100)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def auto_fallback() -> bool:
    """Detect Redis availability and activate fallback if needed.

    Performs a synchronous ping against the Redis pool. If the connection
    times out (> 2 s) or raises an exception, Redis is marked unavailable
    and DB fallback is activated.

    Returns:
        True if DB fallback is currently active, False otherwise.
    """
    global _redis_available, _redis_last_check

    now = time.monotonic()
    if now - _redis_last_check < REDIS_HEALTH_CHECK_INTERVAL:
        return not _redis_available

    _redis_last_check = now

    try:
        pool = _get_pool(_get_settings())
        pool.ping()
        if not _redis_available:
            logger.info("redis_recovered")
        _redis_available = True
        return False
    except (redis.ConnectionError, redis.TimeoutError, OSError) as exc:
        if _redis_available:
            logger.warning(
                "redis_unavailable",
                error=str(exc),
                fallback="db",
            )
        _redis_available = False
        return True


def restore_redis() -> None:
    """Mark Redis as available again (manual reconnection trigger).

    Should be called after a successful Redis reconnect or when the
    infrastructure issue has been resolved externally.
    """
    global _redis_available, _redis_last_check
    _redis_available = True
    _redis_last_check = time.monotonic()
    logger.info("redis_restored_manual")


def check_rate_limit(
    user_id: str,
    tier: str,
    limit: int | None = None,
) -> dict[str, int | bool]:
    """Check whether a user has remaining capacity for the given tier.

    Args:
        user_id: User identifier (UUID string).
        tier: Rate-limit tier — 'daily', 'weekly', or 'monthly'.
        limit: Explicit limit override. Uses DEFAULT_LIMITS[tier] when None.

    Returns:
        Dict with keys: allowed, current, limit, remaining.
    """
    global _redis_available

    if limit is None:
        limit = _get_default_limit(tier)

    if _redis_available:
        try:
            pool = _get_pool(_get_settings())
            key = _redis_key(user_id, tier)
            current = pool.get(key)  # type: ignore[assignment]
            if current is None:
                current = 0
            else:
                current = int(current)  # type: ignore[arg-type]
            remaining = max(0, limit - current)
            return {
                "allowed": current < limit,
                "current": current,
                "limit": limit,
                "remaining": remaining,
            }
        except (redis.ConnectionError, redis.TimeoutError, OSError):
            logger.warning(
                "redis_check_failed",
                user_id=user_id,
                tier=tier,
                fallback="db",
            )
            _redis_available = False

    # DB fallback
    return _db_check_rate_limit(user_id, tier, limit)


def increment_counter(
    user_id: str,
    tier: str,
) -> int:
    """Increment the rate-limit counter for a user/tier and return the new value.

    Uses Redis INCR for atomicity when Redis is available.
    Falls back to DB with threading.Lock for single-process safety.

    Args:
        user_id: User identifier.
        tier: Rate-limit tier.

    Returns:
        The new counter value after increment.
    """
    global _redis_available

    if _redis_available:
        try:
            pool = _get_pool(_get_settings())
            key = _redis_key(user_id, tier)
            ttl = _get_tier_ttl(tier)
            new_count = pool.incr(key)  # type: ignore[assignment]
            pool.expire(key, ttl)  # type: ignore[assignment]
            return new_count  # type: ignore[return-value]
        except (redis.ConnectionError, redis.TimeoutError, OSError):
            logger.warning(
                "redis_incr_failed",
                user_id=user_id,
                tier=tier,
                fallback="db",
            )
            _redis_available = False

    # DB fallback
    return _db_increment_counter(user_id, tier)


def sync_to_db(
    user_id: str,
    tier: str,
) -> None:
    """Sync a Redis counter to the rate_limit_snapshots SQLite table.

    Upserts (user_id, tier) → (count, last_updated).

    Args:
        user_id: User identifier.
        tier: Rate-limit tier.
    """
    if _redis_available:
        try:
            pool = _get_pool(_get_settings())
            key = _redis_key(user_id, tier)
            count = pool.get(key)  # type: ignore[assignment]
            if count is None:
                count = 0
            else:
                count = int(count)  # type: ignore[arg-type]
        except (redis.ConnectionError, redis.TimeoutError, OSError):
            count = _db_get_counter(user_id, tier)
    else:
        count = _db_get_counter(user_id, tier)

    _db_upsert_snapshot(user_id, tier, count)
    logger.debug(
        "counter_synced_to_db",
        user_id=user_id,
        tier=tier,
        count=count,
    )


def get_counter(
    user_id: str,
    tier: str,
) -> int:
    """Return the current counter value for a user/tier.

    Reads from Redis first; falls back to DB when Redis is unavailable.

    Args:
        user_id: User identifier.
        tier: Rate-limit tier.

    Returns:
        Current counter value (int).
    """
    global _redis_available

    if _redis_available:
        try:
            pool = _get_pool(_get_settings())
            key = _redis_key(user_id, tier)
            value = pool.get(key)  # type: ignore[assignment]
            if value is None:
                return 0
            return int(value)  # type: ignore[return-value,arg-type]
        except (redis.ConnectionError, redis.TimeoutError, OSError):
            _redis_available = False

    return _db_get_counter(user_id, tier)


async def flush_counters_to_db() -> dict[str, int]:
    """Flush all Redis rate-limit counters to the DB snapshots table.

    Migration note: this function was transitioned from sync to async via
    ``asyncio.to_thread`` for the SQLite upsert step to prevent blocking
    the event loop. Future maintainers should keep the DB write path in
    ``asyncio.to_thread`` when modifying this function.

    Uses Redis SCAN (cursor iteration) to enumerate keys matching ``rl:*``
    instead of the blocking KEYS command. Returns counts of successful and
    failed syncs.

    Returns:
        Dict with keys: synced, failed.
    """
    global _redis_available

    if not _redis_available:
        logger.info("flush_skipped_redis_unavailable")
        return {"synced": 0, "failed": 0}

    try:
        pool = _get_async_pool(_get_settings())
    except (redis_async.ConnectionError, redis_async.TimeoutError, OSError) as exc:
        logger.warning("flush_redis_error", error=str(exc))
        _redis_available = False
        return {"synced": 0, "failed": 0}

    synced = 0
    failed = 0

    cursor: int = 0
    while True:
        cursor, keys = await pool.scan(cursor=cursor, match="rl:*", count=100)
        if not keys:  # type: ignore[union-attr]
            if cursor == 0:
                break
            continue
        for key in keys:  # type: ignore[union-attr]
            try:
                raw = key.decode() if isinstance(key, bytes) else key
                parts = raw.split(":", 2)
                if len(parts) != 3:
                    logger.warning("flush_invalid_key_format", key=key)
                    failed += 1
                    continue
                user_id, tier = parts[1], parts[2]
                count = pool.get(key)  # type: ignore[assignment]
                count_val = int(count) if count is not None else 0  # type: ignore[arg-type]
                await asyncio.to_thread(_db_upsert_snapshot, user_id, tier, count_val)
                synced += 1
            except Exception as exc:
                logger.warning("flush_key_failed", key=key, error=str(exc))
                failed += 1

        if cursor == 0:
            break

    logger.info(
        "flush_complete",
        synced=synced,
        failed=failed,
    )
    return {"synced": synced, "failed": failed}


def get_tier_ttl(tier: str) -> int:
    """Return the TTL (seconds) for a given rate-limit tier.

    Args:
        tier: Tier name ('daily', 'weekly', 'monthly', or custom).

    Returns:
        TTL in seconds. Defaults to 86400 for unknown tiers.
    """
    return _get_tier_ttl(tier)


# ---------------------------------------------------------------------------
# DB fallback internals
# ---------------------------------------------------------------------------


def _db_get_connection() -> "sqlite3.Connection":
    """Return a SQLite connection to the KG database (shared with user_store)."""
    from app.core.user_store import init_db

    return init_db()


def _db_get_counter(user_id: str, tier: str) -> int:
    """Read counter from rate_limit_snapshots table."""
    conn = _db_get_connection()
    try:
        row = conn.execute(
            "SELECT count FROM rate_limit_snapshots WHERE user_id = ? AND tier = ?",
            (user_id, tier),
        ).fetchone()
        return row["count"] if row else 0
    finally:
        conn.close()


def _db_upsert_snapshot(
    user_id: str,
    tier: str,
    count: int,
) -> None:
    """Upsert a rate_limit_snapshot row (user_id, tier, count, last_updated)."""
    conn = _db_get_connection()
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO rate_limit_snapshots (user_id, tier, count, last_updated)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (user_id, tier)
            DO UPDATE SET count = excluded.count, last_updated = excluded.last_updated
            """,
            (user_id, tier, count, now),
        )
        conn.commit()
    finally:
        conn.close()


def _db_increment_counter(
    user_id: str,
    tier: str,
) -> int:
    """Increment counter via DB with threading.Lock for single-process safety."""
    with _db_lock:
        conn = _db_get_connection()
        try:
            row = conn.execute(
                "SELECT count FROM rate_limit_snapshots WHERE user_id = ? AND tier = ?",
                (user_id, tier),
            ).fetchone()
            current = row["count"] if row else 0
            new_count = current + 1

            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO rate_limit_snapshots (user_id, tier, count, last_updated)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (user_id, tier)
                DO UPDATE SET count = excluded.count, last_updated = excluded.last_updated
                """,
                (user_id, tier, new_count, now),
            )
            conn.commit()
            return new_count
        finally:
            conn.close()


def _db_check_rate_limit(
    user_id: str,
    tier: str,
    limit: int,
) -> dict[str, int | bool]:
    """Check rate limit using DB fallback."""
    current = _db_get_counter(user_id, tier)
    remaining = max(0, limit - current)
    return {
        "allowed": current < limit,
        "current": current,
        "limit": limit,
        "remaining": remaining,
    }
