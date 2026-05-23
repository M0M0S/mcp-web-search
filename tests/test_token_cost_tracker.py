"""Tests for MCP authorization token_cost_tracker module.

Covers token cost recording, Redis counters (mocked via DB fallback),
tier-specific limits, bounds validation, and DB sync.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Generator

import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _ensure_schema() -> Generator[None, None, None]:
    """Ensure token_cost_snapshots table exists before each test."""
    from app.core.user_store import init_db

    init_db()  # creates all tables including token_cost_snapshots
    yield


@pytest.fixture
def shared_db(shared_db_path: str) -> sqlite3.Connection:
    """Return a single SQLite connection with schema created.

    Sets ``row_factory = None`` so queries return plain tuples (index-accessible).
    """
    from app.core.user_store import init_db

    conn: sqlite3.Connection = init_db()
    conn.row_factory = None  # ensure tuple results for index access
    return conn


@pytest.fixture
def user_with_token_limits(shared_db: sqlite3.Connection) -> dict:
    """Create a user with configured token limits."""
    from app.core.user_store import create_user

    return create_user(
        name="token_user",
        token_limits={"daily": 1000000, "weekly": 5000000, "monthly": 20000000},
    )


@pytest.fixture
def mock_redis_pool() -> Generator[None, None, None]:
    """Mock Redis pool — patches ``redis.from_url`` for token_cost_tracker.

    Default state: Redis available, all operations succeed.
    Configure the mock via ``pool.get.return_value``, ``pool.incrby.return_value``
    etc. before running test logic.
    """
    from unittest.mock import MagicMock

    with patch("redis.from_url") as mock_from_url:
        from app.core import token_cost_tracker

        # Reset module-level pool so mock is actually used
        token_cost_tracker._redis_pool = None

        pool: MagicMock = MagicMock()

        # Default: Redis available and working
        pool.get.return_value = None
        pool.incrby.return_value = 1
        pool.ping.return_value = True
        pool.delete.return_value = 1
        pool.scan.return_value = iter([(0, [])])
        pool.expire.return_value = True

        mock_from_url.return_value = pool
        yield pool


@pytest.fixture
def force_db_fallback() -> Generator[None, None, None]:
    """Force token_cost_tracker to use DB fallback (Redis unavailable).

    Patches ``redis.from_url`` and sets ``_redis_available = False``.
    """
    with patch("redis.from_url"):
        from app.core import token_cost_tracker

        token_cost_tracker._redis_available = False
        token_cost_tracker._redis_pool = None
        token_cost_tracker._redis_last_check = 0.0
        yield
    # Restore original state
    from app.core import token_cost_tracker

    token_cost_tracker._redis_available = True
    token_cost_tracker._redis_pool = None
    token_cost_tracker._redis_last_check = 0.0


# ---------------------------------------------------------------------------
# 1. Token cost recording — input/output per tier
# ---------------------------------------------------------------------------

class TestTokenCostRecording:
    """record_tokens — input and output counters per tier — DB fallback path."""

    def test_record_input_tokens(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """record_tokens increments input counter via DB fallback."""
        from app.core.token_cost_tracker import record_tokens

        record_tokens("rec_user", "daily", input_tokens=100, output_tokens=50)

        row = shared_db.execute(
            "SELECT input_tokens FROM token_cost_snapshots WHERE user_id = ? AND tier = ?",
            ("rec_user", "daily"),
        ).fetchone()

        assert row is not None
        assert row[0] == 100

    def test_record_output_tokens(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """record_tokens increments output counter via DB fallback."""
        from app.core.token_cost_tracker import record_tokens

        record_tokens("rec_user_out", "daily", input_tokens=100, output_tokens=50)

        row = shared_db.execute(
            "SELECT output_tokens FROM token_cost_snapshots WHERE user_id = ? AND tier = ?",
            ("rec_user_out", "daily"),
        ).fetchone()

        assert row is not None
        assert row[0] == 50

    def test_record_multiple_tiers(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """record_tokens works for weekly and monthly tiers via DB fallback."""
        from app.core.token_cost_tracker import record_tokens

        record_tokens("rec_user", "weekly", input_tokens=200, output_tokens=100)
        record_tokens("rec_user", "monthly", input_tokens=300, output_tokens=150)

        weekly_row = shared_db.execute(
            "SELECT input_tokens FROM token_cost_snapshots WHERE user_id = ? AND tier = ?",
            ("rec_user", "weekly"),
        ).fetchone()
        assert weekly_row[0] == 200

        monthly_row = shared_db.execute(
            "SELECT input_tokens FROM token_cost_snapshots WHERE user_id = ? AND tier = ?",
            ("rec_user", "monthly"),
        ).fetchone()
        assert monthly_row[0] == 300

    def test_record_accumulates(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """Multiple record_tokens calls accumulate via DB fallback."""
        from app.core.token_cost_tracker import record_tokens

        record_tokens("acc_user", "daily", input_tokens=100, output_tokens=50)
        record_tokens("acc_user", "daily", input_tokens=200, output_tokens=100)

        row = shared_db.execute(
            "SELECT input_tokens FROM token_cost_snapshots WHERE user_id = ? AND tier = ?",
            ("acc_user", "daily"),
        ).fetchone()

        assert row[0] == 300  # 100 + 200


# ---------------------------------------------------------------------------
# 2. Redis counters (mocked — DB fallback path)
# ---------------------------------------------------------------------------

class TestRedisCountersMocked:
    """Token counters via mocked Redis — Redis path."""

    def test_get_token_usage_from_redis(self, mock_redis_pool: Generator[None, None, None]) -> None:
        """get_token_usage returns values from mocked Redis."""
        def _get_side_effect(key: str) -> int | None:
            if "input" in key:
                return 500
            if "output" in key:
                return 250
            return None

        mock_redis_pool.get.side_effect = _get_side_effect

        from app.core.token_cost_tracker import get_token_usage

        usage = get_token_usage("usage_user", "daily")

        assert usage["input_tokens"] == 500
        assert usage["output_tokens"] == 250
        assert usage["total_tokens"] == 750

    def test_check_token_limits_from_redis(self, mock_redis_pool: Generator[None, None, None]) -> None:
        """check_token_limits reads from mocked Redis."""
        def _get_side_effect(key: str) -> int | None:
            if "input" in key:
                return 100
            if "output" in key:
                return 50
            return None

        mock_redis_pool.get.side_effect = _get_side_effect

        from app.core.token_cost_tracker import check_token_limits

        result = check_token_limits("check_user", "daily")

        assert result["current_input"] == 100
        assert result["current_output"] == 50
        assert result["total"] == 150


# ---------------------------------------------------------------------------
# 3. Tier-specific token limits — limits.get(tier)
# ---------------------------------------------------------------------------

class TestTierSpecificLimits:
    """check_token_limits uses tier-specific limits from user store — DB fallback path."""

    def test_daily_limit_applied(self, force_db_fallback: None, user_with_token_limits: dict, shared_db: sqlite3.Connection) -> None:
        """daily limit from user config is used for daily tier via DB fallback."""
        from app.core.token_cost_tracker import check_token_limits, record_tokens

        record_tokens(user_with_token_limits["user_id"], "daily", input_tokens=500000, output_tokens=500000)

        result = check_token_limits(user_with_token_limits["user_id"], "daily")

        assert result["limit_input"] == 1000000
        assert result["exceeded"] is True

    def test_weekly_limit_applied(self, force_db_fallback: None, user_with_token_limits: dict, shared_db: sqlite3.Connection) -> None:
        """weekly limit from user config is used for weekly tier via DB fallback."""
        from app.core.token_cost_tracker import check_token_limits, record_tokens

        record_tokens(user_with_token_limits["user_id"], "weekly", input_tokens=1000000, output_tokens=1000000)

        result = check_token_limits(user_with_token_limits["user_id"], "weekly")

        assert result["limit_input"] == 5000000
        assert result["exceeded"] is False

    def test_monthly_limit_applied(self, force_db_fallback: None, user_with_token_limits: dict, shared_db: sqlite3.Connection) -> None:
        """monthly limit from user config is used for monthly tier via DB fallback."""
        from app.core.token_cost_tracker import check_token_limits, record_tokens

        record_tokens(user_with_token_limits["user_id"], "monthly", input_tokens=5000000, output_tokens=5000000)

        result = check_token_limits(user_with_token_limits["user_id"], "monthly")

        assert result["limit_input"] == 20000000
        assert result["exceeded"] is False

    def test_no_limit_returns_none(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """User with no token limits → limit_input = None via DB fallback."""
        from app.core.token_cost_tracker import check_token_limits
        from app.core.user_store import create_user

        user = create_user(name="no_limits_user")

        result = check_token_limits(user["user_id"], "daily")

        assert result["limit_input"] is None
        assert result["exceeded"] is False


# ---------------------------------------------------------------------------
# 4. Token limit bounds validation [0-10M/50M/200M]
# ---------------------------------------------------------------------------

class TestTokenLimitBounds:
    """validate_token_limit_bounds correctness."""

    def test_all_valid(self) -> None:
        """All values within bounds → True."""
        from app.core.token_cost_tracker import validate_token_limit_bounds

        assert validate_token_limit_bounds(
            daily=1000000,
            weekly=5000000,
            monthly=20000000,
        ) is True

    def test_zero_allowed(self) -> None:
        """Zero (unlimited) is within bounds."""
        from app.core.token_cost_tracker import validate_token_limit_bounds

        assert validate_token_limit_bounds(daily=0, weekly=None, monthly=None) is True

    def test_none_ignored(self) -> None:
        """None values are ignored (not validated)."""
        from app.core.token_cost_tracker import validate_token_limit_bounds

        assert validate_token_limit_bounds(daily=None, weekly=None, monthly=None) is True

    def test_daily_above_max(self) -> None:
        """Daily above 10_000_000 → False."""
        from app.core.token_cost_tracker import validate_token_limit_bounds

        assert validate_token_limit_bounds(daily=10000001, weekly=None, monthly=None) is False

    def test_weekly_above_max(self) -> None:
        """Weekly above 50_000_000 → False."""
        from app.core.token_cost_tracker import validate_token_limit_bounds

        assert validate_token_limit_bounds(daily=None, weekly=50000001, monthly=None) is False

    def test_monthly_above_max(self) -> None:
        """Monthly above 200_000_000 → False."""
        from app.core.token_cost_tracker import validate_token_limit_bounds

        assert validate_token_limit_bounds(daily=None, weekly=None, monthly=200000001) is False

    def test_negative_raises_false(self) -> None:
        """Negative values → False."""
        from app.core.token_cost_tracker import validate_token_limit_bounds

        assert validate_token_limit_bounds(daily=-1, weekly=None, monthly=None) is False


# ---------------------------------------------------------------------------
# 5. DB sync — token_cost_snapshots upsert
# ---------------------------------------------------------------------------

class TestTokenSyncToDB:
    """sync_to_db upserts token_cost_snapshots — DB fallback path."""

    def test_sync_creates_snapshot(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """sync_to_db creates a token_cost_snapshot row via DB fallback."""
        from app.core.token_cost_tracker import sync_to_db, record_tokens

        record_tokens("sync_user", "daily", input_tokens=100, output_tokens=50)
        sync_to_db("sync_user", "daily")

        row = shared_db.execute(
            "SELECT input_tokens, output_tokens, total_tokens FROM token_cost_snapshots "
            "WHERE user_id = ? AND tier = ?",
            ("sync_user", "daily"),
        ).fetchone()

        assert row is not None
        assert row[0] == 100
        assert row[1] == 50
        assert row[2] == 150

    def test_sync_upserts_existing(self, force_db_fallback: None, shared_db: sqlite3.Connection) -> None:
        """sync_to_db updates existing snapshot via DB fallback."""
        from app.core.token_cost_tracker import sync_to_db, record_tokens

        record_tokens("sync_user_2", "weekly", input_tokens=200, output_tokens=100)
        sync_to_db("sync_user_2", "weekly")

        row1 = shared_db.execute(
            "SELECT input_tokens FROM token_cost_snapshots WHERE user_id = ? AND tier = ?",
            ("sync_user_2", "weekly"),
        ).fetchone()
        assert row1[0] == 200

        record_tokens("sync_user_2", "weekly", input_tokens=300, output_tokens=150)
        sync_to_db("sync_user_2", "weekly")

        row2 = shared_db.execute(
            "SELECT input_tokens FROM token_cost_snapshots WHERE user_id = ? AND tier = ?",
            ("sync_user_2", "weekly"),
        ).fetchone()
        assert row2[0] == 500  # 200 + 300


# ---------------------------------------------------------------------------
# 6. get_tier_ttl (delegated to rate_limiter)
# ---------------------------------------------------------------------------

class TestTokenTierTTL:
    """get_tier_ttl delegates to rate_limiter."""

    def test_daily_ttl(self) -> None:
        """daily TTL = 86400."""
        from app.core.token_cost_tracker import get_tier_ttl

        assert get_tier_ttl("daily") == 86400

    def test_weekly_ttl(self) -> None:
        """weekly TTL = 604800."""
        from app.core.token_cost_tracker import get_tier_ttl

        assert get_tier_ttl("weekly") == 604800

    def test_monthly_ttl(self) -> None:
        """monthly TTL = 2592000."""
        from app.core.token_cost_tracker import get_tier_ttl

        assert get_tier_ttl("monthly") == 2592000

    def test_unknown_tier_fallback(self) -> None:
        """Unknown tier falls back to 86400."""
        from app.core.token_cost_tracker import get_tier_ttl

        assert get_tier_ttl("custom") == 86400
