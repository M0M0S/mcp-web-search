"""Tests for MCP authorization rate_limiter module.

Covers Redis counter operations, DB fallback, boundary cases, counter sync,
and tier TTL constants.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Generator

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def shared_db(shared_db_path: str) -> sqlite3.Connection:
    """Return a single SQLite connection with schema created.

    Sets ``row_factory = None`` so queries return plain tuples (index-accessible).
    """
    from app.core.user_store import init_db

    conn: sqlite3.Connection = init_db()
    conn.row_factory = None  # ensure tuple results for index access
    return conn


@pytest.fixture(scope="module")
def mock_redis_pool() -> Generator[MagicMock, None, None]:
    """Module-scoped mock Redis pool — patches ``redis.from_url`` globally.

    Default state: Redis available, all operations succeed.
    Configure the mock via ``pool.get.return_value``, ``pool.incr.return_value``
    etc. before running test logic.
    """
    with patch("redis.from_url") as mock_from_url:
        from app.core import rate_limiter

        # Reset module-level pool so mock is actually used
        rate_limiter._redis_pool = None

        pool: MagicMock = MagicMock()

        # Default: Redis available and working
        pool.get.return_value = None
        pool.incr.return_value = 1
        pool.incrby.return_value = 1
        pool.ping.return_value = True
        pool.delete.return_value = 1
        pool.scan.return_value = iter([(0, [])])
        pool.expire.return_value = True

        mock_from_url.return_value = pool
        yield pool


@pytest.fixture
def force_db_fallback() -> Generator[None, None, None]:
    """Force rate_limiter to use DB fallback (Redis unavailable).

    Sets ``_redis_available = False`` in ``app.core.rate_limiter`` and
    patches ``redis.from_url`` so the module never attempts a real connection.
    """
    from app.core import rate_limiter

    with patch("redis.from_url"):
        rate_limiter._redis_available = False
        rate_limiter._redis_pool = None
        rate_limiter._redis_last_check = 0.0
        yield
    # Restore original state
    rate_limiter._redis_available = True
    rate_limiter._redis_pool = None
    rate_limiter._redis_last_check = 0.0


# ---------------------------------------------------------------------------
# 1. Redis counter operations (mocked — DB fallback path)
# ---------------------------------------------------------------------------

class TestCounterOperations:
    """incr, get, check against limit via mocked Redis."""

    def test_increment_counter(self, mock_redis_pool: MagicMock) -> None:
        """increment_counter returns incremented value via mocked Redis."""
        from app.core.rate_limiter import increment_counter

        # Configure mock to return incrementing values
        mock_redis_pool.incr.side_effect = [1, 2, 3]

        user_id: str = "test_user_1"
        tier: str = "daily"

        count1 = increment_counter(user_id, tier)
        count2 = increment_counter(user_id, tier)
        count3 = increment_counter(user_id, tier)

        assert count1 == 1
        assert count2 == 2
        assert count3 == 3

    def test_get_counter_zero_initial(self, mock_redis_pool: MagicMock) -> None:
        """get_counter returns 0 for a new user/tier via mocked Redis."""
        from app.core.rate_limiter import get_counter

        mock_redis_pool.get.return_value = None

        assert get_counter("new_user", "daily") == 0

    def test_get_counter_after_increment(self, mock_redis_pool: MagicMock) -> None:
        """get_counter returns correct value after increment via mocked Redis."""
        from app.core.rate_limiter import get_counter, increment_counter

        mock_redis_pool.incr.side_effect = [1, 2]
        mock_redis_pool.get.return_value = 2

        user_id: str = "test_user_2"
        for _ in range(2):
            increment_counter(user_id, "daily")

        assert get_counter(user_id, "daily") == 2

    def test_check_rate_limit_allowed(self, mock_redis_pool: MagicMock) -> None:
        """check_rate_limit returns allowed=True when under limit via mocked Redis."""
        from app.core.rate_limiter import check_rate_limit

        mock_redis_pool.get.return_value = None

        result = check_rate_limit("test_user_3", "daily", limit=100)

        assert result["allowed"] is True
        assert result["current"] == 0
        assert result["limit"] == 100
        assert result["remaining"] == 100

    def test_check_rate_limit_counter_reflected(self, mock_redis_pool: MagicMock) -> None:
        """check_rate_limit reflects current counter value via mocked Redis."""
        from app.core.rate_limiter import check_rate_limit, increment_counter

        mock_redis_pool.incr.side_effect = [1, 2, 3, 4, 5]
        mock_redis_pool.get.return_value = 5

        user_id: str = "test_user_4"
        for _ in range(5):
            increment_counter(user_id, "daily")

        result = check_rate_limit(user_id, "daily", limit=100)

        assert result["current"] == 5
        assert result["remaining"] == 95


# ---------------------------------------------------------------------------
# 2. Redis fallback — DB counters when Redis unavailable
# ---------------------------------------------------------------------------

class TestRedisFallback:
    """DB fallback path when Redis is unavailable."""

    def test_fallback_increment(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """increment_counter works via DB when Redis unavailable."""
        from app.core.rate_limiter import increment_counter

        assert increment_counter("fallback_user", "weekly") == 1

    def test_fallback_get_counter(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """get_counter reads from DB when Redis unavailable."""
        from app.core.rate_limiter import get_counter, increment_counter

        increment_counter("fallback_user", "monthly")
        assert get_counter("fallback_user", "monthly") == 1

    def test_fallback_check_rate_limit(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """check_rate_limit uses DB when Redis unavailable."""
        from app.core.rate_limiter import check_rate_limit, increment_counter

        user_id: str = "fallback_user_2"
        for _ in range(10):
            increment_counter(user_id, "daily")

        result = check_rate_limit(user_id, "daily", limit=10)

        assert result["allowed"] is False
        assert result["current"] == 10


# ---------------------------------------------------------------------------
# 3. Rate limit boundary cases
# ---------------------------------------------------------------------------

class TestRateLimitBoundary:
    """Exactly at limit, over limit — DB fallback path."""

    def test_exactly_at_limit_not_allowed(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """Counter exactly at limit → allowed=False."""
        from app.core.rate_limiter import check_rate_limit, increment_counter

        user_id: str = "boundary_user"
        limit: int = 10

        for _ in range(limit):
            increment_counter(user_id, "daily")

        result = check_rate_limit(user_id, "daily", limit=limit)

        assert result["allowed"] is False
        assert result["current"] == limit
        assert result["remaining"] == 0

    def test_one_below_limit_allowed(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """Counter one below limit → allowed=True."""
        from app.core.rate_limiter import check_rate_limit, increment_counter

        user_id: str = "boundary_user_2"
        limit: int = 10

        for _ in range(limit - 1):
            increment_counter(user_id, "daily")

        result = check_rate_limit(user_id, "daily", limit=limit)

        assert result["allowed"] is True
        assert result["remaining"] == 1

    def test_over_limit_remaining_zero(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """Counter over limit → remaining=0 (max(0, limit - current))."""
        from app.core.rate_limiter import check_rate_limit, increment_counter

        user_id: str = "boundary_user_3"
        limit: int = 5

        for _ in range(10):
            increment_counter(user_id, "daily")

        result = check_rate_limit(user_id, "daily", limit=limit)

        assert result["remaining"] == 0


# ---------------------------------------------------------------------------
# 4. Counter sync to DB — rate_limit_snapshots upsert
# ---------------------------------------------------------------------------

class TestCounterSyncToDB:
    """sync_to_db upserts rate_limit_snapshots — DB fallback path."""

    def test_sync_creates_snapshot(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """sync_to_db creates a rate_limit_snapshot row."""
        from app.core.rate_limiter import sync_to_db, increment_counter

        user_id: str = "sync_user"
        tier: str = "daily"

        increment_counter(user_id, tier)
        sync_to_db(user_id, tier)

        row = shared_db.execute(
            "SELECT count FROM rate_limit_snapshots WHERE user_id = ? AND tier = ?",
            (user_id, tier),
        ).fetchone()

        assert row is not None
        assert row[0] == 1

    def test_sync_upserts_existing(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """sync_to_db updates existing snapshot."""
        from app.core.rate_limiter import sync_to_db, increment_counter

        user_id: str = "sync_user_2"
        tier: str = "weekly"

        increment_counter(user_id, tier)
        increment_counter(user_id, tier)
        sync_to_db(user_id, tier)

        row1 = shared_db.execute(
            "SELECT count FROM rate_limit_snapshots WHERE user_id = ? AND tier = ?",
            (user_id, tier),
        ).fetchone()
        assert row1[0] == 2

        increment_counter(user_id, tier)
        sync_to_db(user_id, tier)

        row2 = shared_db.execute(
            "SELECT count FROM rate_limit_snapshots WHERE user_id = ? AND tier = ?",
            (user_id, tier),
        ).fetchone()
        assert row2[0] == 3


# ---------------------------------------------------------------------------
# 5. Tier TTL — daily=86400, weekly=604800, monthly=2592000
# ---------------------------------------------------------------------------

class TestTierTTL:
    """DEFAULT_TTLS values."""

    def test_daily_ttl(self) -> None:
        """daily tier TTL = 86400 seconds."""
        from app.core.rate_limiter import get_tier_ttl

        assert get_tier_ttl("daily") == 86400

    def test_weekly_ttl(self) -> None:
        """weekly tier TTL = 604800 seconds."""
        from app.core.rate_limiter import get_tier_ttl

        assert get_tier_ttl("weekly") == 604800

    def test_monthly_ttl(self) -> None:
        """monthly tier TTL = 2592000 seconds."""
        from app.core.rate_limiter import get_tier_ttl

        assert get_tier_ttl("monthly") == 2592000

    def test_unknown_tier_fallback(self) -> None:
        """Unknown tier falls back to 86400."""
        from app.core.rate_limiter import get_tier_ttl

        assert get_tier_ttl("custom") == 86400


# ---------------------------------------------------------------------------
# 6. restore_redis
# ---------------------------------------------------------------------------

class TestRestoreRedis:
    """restore_redis marks Redis as available."""

    def test_restore_sets_available(self) -> None:
        """restore_redis sets _redis_available to True."""
        from app.core import rate_limiter

        rate_limiter._redis_available = False
        rate_limiter.restore_redis()

        assert rate_limiter._redis_available is True
