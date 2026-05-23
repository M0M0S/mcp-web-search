"""Tests for MCP authorization token_cost_tracker module.

Covers token cost recording, Redis counters (mocked via DB fallback),
tier-specific limits, bounds validation, and DB sync.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
import redis

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
def mock_redis_pool() -> Generator[MagicMock, None, None]:
    """Mock Redis pool — patches ``redis.from_url`` for token_cost_tracker.

    Default state: Redis available, all operations succeed.
    Configure the mock via ``pool.get.return_value``, ``pool.incrby.return_value``
    etc. before running test logic.
    """
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

    def test_record_input_tokens(
        self, force_db_fallback: None, shared_db: sqlite3.Connection
    ) -> None:
        """record_tokens increments input counter via DB fallback."""
        from app.core.token_cost_tracker import record_tokens

        record_tokens("rec_user", "daily", input_tokens=100, output_tokens=50)

        row = shared_db.execute(
            "SELECT input_tokens FROM token_cost_snapshots WHERE user_id = ? AND tier = ?",
            ("rec_user", "daily"),
        ).fetchone()

        assert row is not None
        assert row[0] == 100

    def test_record_output_tokens(
        self, force_db_fallback: None, shared_db: sqlite3.Connection
    ) -> None:
        """record_tokens increments output counter via DB fallback."""
        from app.core.token_cost_tracker import record_tokens

        record_tokens("rec_user_out", "daily", input_tokens=100, output_tokens=50)

        row = shared_db.execute(
            "SELECT output_tokens FROM token_cost_snapshots WHERE user_id = ? AND tier = ?",
            ("rec_user_out", "daily"),
        ).fetchone()

        assert row is not None
        assert row[0] == 50

    def test_record_multiple_tiers(
        self, force_db_fallback: None, shared_db: sqlite3.Connection
    ) -> None:
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

    def test_record_accumulates(
        self, force_db_fallback: None, shared_db: sqlite3.Connection
    ) -> None:
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

    def test_get_token_usage_from_redis(
        self, mock_redis_pool: MagicMock
    ) -> None:
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

    def test_check_token_limits_from_redis(
        self, mock_redis_pool: MagicMock
    ) -> None:
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

    def test_daily_limit_applied(
        self,
        force_db_fallback: None,
        user_with_token_limits: dict,
        shared_db: sqlite3.Connection,
    ) -> None:
        """daily limit from user config is used for daily tier via DB fallback."""
        from app.core.token_cost_tracker import check_token_limits, record_tokens

        record_tokens(
            user_with_token_limits["user_id"],
            "daily",
            input_tokens=500000,
            output_tokens=500000,
        )

        result = check_token_limits(user_with_token_limits["user_id"], "daily")

        assert result["limit_input"] == 1000000
        assert result["exceeded"] is True

    def test_weekly_limit_applied(
        self,
        force_db_fallback: None,
        user_with_token_limits: dict,
        shared_db: sqlite3.Connection,
    ) -> None:
        """weekly limit from user config is used for weekly tier via DB fallback."""
        from app.core.token_cost_tracker import check_token_limits, record_tokens

        record_tokens(
            user_with_token_limits["user_id"],
            "weekly",
            input_tokens=1000000,
            output_tokens=1000000,
        )

        result = check_token_limits(user_with_token_limits["user_id"], "weekly")

        assert result["limit_input"] == 5000000
        assert result["exceeded"] is False

    def test_monthly_limit_applied(
        self,
        force_db_fallback: None,
        user_with_token_limits: dict,
        shared_db: sqlite3.Connection,
    ) -> None:
        """monthly limit from user config is used for monthly tier via DB fallback."""
        from app.core.token_cost_tracker import check_token_limits, record_tokens

        record_tokens(
            user_with_token_limits["user_id"],
            "monthly",
            input_tokens=5000000,
            output_tokens=5000000,
        )

        result = check_token_limits(user_with_token_limits["user_id"], "monthly")

        assert result["limit_input"] == 20000000
        assert result["exceeded"] is False

    def test_no_limit_returns_none(
        self, force_db_fallback: None, shared_db: sqlite3.Connection
    ) -> None:
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

        assert (
            validate_token_limit_bounds(
                daily=1000000,
                weekly=5000000,
                monthly=20000000,
            )
            is True
        )

    def test_zero_allowed(self) -> None:
        """Zero (unlimited) is within bounds."""
        from app.core.token_cost_tracker import validate_token_limit_bounds

        assert validate_token_limit_bounds(daily=0, weekly=None, monthly=None) is True

    def test_none_ignored(self) -> None:
        """None values are ignored (not validated)."""
        from app.core.token_cost_tracker import validate_token_limit_bounds

        assert (
            validate_token_limit_bounds(daily=None, weekly=None, monthly=None) is True
        )

    def test_daily_above_max(self) -> None:
        """Daily above 10_000_000 → False."""
        from app.core.token_cost_tracker import validate_token_limit_bounds

        assert (
            validate_token_limit_bounds(daily=10000001, weekly=None, monthly=None)
            is False
        )

    def test_weekly_above_max(self) -> None:
        """Weekly above 50_000_000 → False."""
        from app.core.token_cost_tracker import validate_token_limit_bounds

        assert (
            validate_token_limit_bounds(daily=None, weekly=50000001, monthly=None)
            is False
        )

    def test_monthly_above_max(self) -> None:
        """Monthly above 200_000_000 → False."""
        from app.core.token_cost_tracker import validate_token_limit_bounds

        assert (
            validate_token_limit_bounds(daily=None, weekly=None, monthly=200000001)
            is False
        )

    def test_negative_raises_false(self) -> None:
        """Negative values → False."""
        from app.core.token_cost_tracker import validate_token_limit_bounds

        assert validate_token_limit_bounds(daily=-1, weekly=None, monthly=None) is False


# ---------------------------------------------------------------------------
# 5. DB sync — token_cost_snapshots upsert
# ---------------------------------------------------------------------------


class TestTokenSyncToDB:
    """sync_to_db upserts token_cost_snapshots — DB fallback path."""

    def test_sync_creates_snapshot(
        self, force_db_fallback: None, shared_db: sqlite3.Connection
    ) -> None:
        """sync_to_db creates a token_cost_snapshot row via DB fallback."""
        from app.core.token_cost_tracker import record_tokens, sync_to_db

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

    def test_sync_upserts_existing(
        self, force_db_fallback: None, shared_db: sqlite3.Connection
    ) -> None:
        """sync_to_db updates existing snapshot via DB fallback."""
        from app.core.token_cost_tracker import record_tokens, sync_to_db

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


# ---------------------------------------------------------------------------
# 7. Key-absence bug fix — new users should NOT permanently disable Redis
# ---------------------------------------------------------------------------


class TestKeyAbsenceBugFix:
    """Verify that key-absence does NOT set `_redis_available = False`."""

    def test_new_user_does_not_disable_redis(
        self, mock_redis_pool: MagicMock
    ) -> None:
        """New user (no Redis keys) → counters from DB fallback, _redis_available stays True."""

        def _get_side_effect(key: str) -> int | None:
            return None  # all keys absent (new user)

        mock_redis_pool.get.side_effect = _get_side_effect

        from app.core import token_cost_tracker
        from app.core.token_cost_tracker import get_token_usage

        # Before: _redis_available should be True
        assert token_cost_tracker._redis_available is True

        usage = get_token_usage("new_user", "daily")

        # After: _redis_available MUST stay True (key absent ≠ Redis down)
        assert token_cost_tracker._redis_available is True
        # DB fallback returns 0 for new user
        assert usage["input_tokens"] == 0
        assert usage["output_tokens"] == 0
        assert usage["total_tokens"] == 0

    def test_key_absence_does_not_affect_subsequent_redis_reads(
        self, mock_redis_pool: MagicMock
    ) -> None:
        """After key-absence fallback, subsequent reads still use Redis."""

        def _get_side_effect(key: str) -> int | None:
            if "new_user" in key:
                return None  # first call: key absent
            if "input" in key:
                return 500
            if "output" in key:
                return 250
            return None

        mock_redis_pool.get.side_effect = _get_side_effect

        from app.core import token_cost_tracker
        from app.core.token_cost_tracker import get_token_usage

        # First call — new user, keys absent
        usage1 = get_token_usage("new_user", "daily")
        assert token_cost_tracker._redis_available is True

        # Second call — existing user, keys present
        usage2 = get_token_usage("existing_user", "daily")
        assert token_cost_tracker._redis_available is True
        assert usage2["input_tokens"] == 500
        assert usage2["output_tokens"] == 250


# ---------------------------------------------------------------------------
# 8. Redis health-check recovery mechanism
# ---------------------------------------------------------------------------


class TestRedisRecovery:
    """TTL-based Redis health-check recovery — non-blocking async ping."""

    def test_recovery_ping_scheduled_non_blocking(
        self, mock_redis_pool: MagicMock
    ) -> None:
        """Recovery ping scheduled as non-blocking async task.

        ``_redis_available`` stays False immediately after call — ping runs in background.
        """

        from app.core import token_cost_tracker
        from app.core.token_cost_tracker import get_token_usage

        # Simulate Redis failure + interval elapsed
        token_cost_tracker._redis_available = False
        token_cost_tracker._redis_last_check = time.time() - 60

        usage = get_token_usage("recover_user", "daily")

        # Ping scheduled but not yet completed — _redis_available still False
        assert token_cost_tracker._redis_available is False
        # DB fallback returns 0 for non-existent user
        assert usage["input_tokens"] == 0
        assert usage["output_tokens"] == 0

    async def test_async_recovery_completes(
        self, mock_redis_pool: MagicMock
    ) -> None:
        """Direct async recovery ping → ``_redis_available`` restored."""

        from app.core import token_cost_tracker
        from app.core.token_cost_tracker import _perform_redis_recovery

        token_cost_tracker._redis_available = False
        token_cost_tracker._redis_last_check = time.time() - 60

        await _perform_redis_recovery()

        assert token_cost_tracker._redis_available is True
        assert token_cost_tracker._redis_last_check > 0

    async def test_async_recovery_failure(
        self, mock_redis_pool: MagicMock
    ) -> None:
        """Async recovery ping fails → ``_redis_available`` stays False, ``_redis_last_check`` updated."""

        from app.core import token_cost_tracker
        from app.core.token_cost_tracker import _perform_redis_recovery

        token_cost_tracker._redis_available = False
        token_cost_tracker._redis_last_check = time.time() - 60

        mock_redis_pool.ping.side_effect = redis.ConnectionError("connection lost")  # type: ignore[assignment]

        await _perform_redis_recovery()

        assert token_cost_tracker._redis_available is False
        assert token_cost_tracker._redis_last_check > 0


# ---------------------------------------------------------------------------
# 10. record_tokens — Redis increment path
# ---------------------------------------------------------------------------


class TestRecordTokensRedisPath:
    """record_tokens — Redis incrby + expire path."""

    def test_record_tokens_increases_redis_counters(
        self, mock_redis_pool: MagicMock
    ) -> None:
        """record_tokens calls incrby + expire on Redis for both counters."""
        from app.core import token_cost_tracker
        from app.core.token_cost_tracker import record_tokens

        # Ensure Redis is available (may be False from previous tests)
        token_cost_tracker._redis_available = True

        record_tokens("redis_rec_user", "daily", input_tokens=100, output_tokens=50)

        incrby_calls = mock_redis_pool.incrby.call_args_list
        assert len(incrby_calls) == 2
        assert incrby_calls[0][0] == ("tc:redis_rec_user:daily:input", 100)
        assert incrby_calls[1][0] == ("tc:redis_rec_user:daily:output", 50)

        expire_calls = mock_redis_pool.expire.call_args_list
        assert len(expire_calls) == 2

    def test_record_tokens_redis_success_path(
        self, mock_redis_pool: MagicMock
    ) -> None:
        """record_tokens returns without exception on Redis success — counters updated."""

        def _get_side_effect(key: str) -> int | None:
            if "success_user" in key and "input" in key:
                return 200
            if "success_user" in key and "output" in key:
                return 100
            return None

        mock_redis_pool.get.side_effect = _get_side_effect

        from app.core.token_cost_tracker import record_tokens

        result = record_tokens("success_user", "daily", input_tokens=200, output_tokens=100)  # type: ignore[assignment,func-returns-value]

        assert result is None  # function returns None on success

        incrby_calls = mock_redis_pool.incrby.call_args_list
        assert len(incrby_calls) == 2
        assert incrby_calls[0][0] == ("tc:success_user:daily:input", 200)
        assert incrby_calls[1][0] == ("tc:success_user:daily:output", 100)

        expire_calls = mock_redis_pool.expire.call_args_list
        assert len(expire_calls) == 2
        assert expire_calls[0][0][1] == 86400  # daily TTL
        assert expire_calls[1][0][1] == 86400  # daily TTL

        # Verify _redis_available stays True
        from app.core import token_cost_tracker

        assert token_cost_tracker._redis_available is True

    def test_record_tokens_redis_failure_sets_unavailable(
        self, mock_redis_pool: MagicMock
    ) -> None:
        """record_tokens Redis failure → ``_redis_available`` = False."""
        from app.core import token_cost_tracker
        from app.core.token_cost_tracker import record_tokens

        mock_redis_pool.incrby.side_effect = redis.ConnectionError("connection lost")  # type: ignore[assignment]

        record_tokens("fail_user", "daily", input_tokens=100, output_tokens=50)

        assert token_cost_tracker._redis_available is False


# ---------------------------------------------------------------------------
# 11. Backup key independence — token_cost_tracker does not use encryption keys
# ---------------------------------------------------------------------------


class TestBackupKeyIndependence:
    """Verify backup key does not affect token_cost_tracker path."""

    def test_backup_key_does_not_affect_redis_available(
        self,
        mcp_encryption_key_backup: str,
        mock_redis_pool: MagicMock,
    ) -> None:
        """Setting a backup encryption key does not change ``_redis_available``."""
        from app.core import token_cost_tracker

        # Reset module state — backup key fixture does not touch token_cost_tracker
        token_cost_tracker._redis_available = True
        token_cost_tracker._redis_pool = None

        # Backup key is set via fixture — token_cost_tracker should be unaware
        assert token_cost_tracker._redis_available is True

        # Verify counters work normally
        from app.core.token_cost_tracker import get_token_usage

        def _get_side_effect(key: str) -> int | None:
            if "bk_user" in key and "input" in key:
                return 300
            if "bk_user" in key and "output" in key:
                return 150
            return None

        mock_redis_pool.get.side_effect = _get_side_effect

        usage = get_token_usage("bk_user", "daily")

        assert usage["input_tokens"] == 300
        assert usage["output_tokens"] == 150
        assert token_cost_tracker._redis_available is True

    def test_counters_independent_of_encryption_key_state(
        self,
        mcp_encryption_key: str,
        mcp_encryption_key_backup: str,
        force_db_fallback: None,
        shared_db: sqlite3.Connection,
    ) -> None:
        """Token counters work correctly via DB fallback regardless of encryption key presence."""
        from app.core.token_cost_tracker import record_tokens, get_token_usage

        # Both encryption keys are set via fixtures — token_cost_tracker should ignore them
        record_tokens("bk_indep_user", "daily", input_tokens=400, output_tokens=200)

        usage = get_token_usage("bk_indep_user", "daily")

        # DB fallback path — counters reflect recorded values (Redis forced unavailable)
        assert usage["input_tokens"] == 400
        assert usage["output_tokens"] == 200
        assert usage["total_tokens"] == 600

        # Verify DB has the recorded values
        row = shared_db.execute(
            "SELECT input_tokens, output_tokens FROM token_cost_snapshots "
            "WHERE user_id = ? AND tier = ?",
            ("bk_indep_user", "daily"),
        ).fetchone()

        assert row is not None
        assert row[0] == 400
        assert row[1] == 200

    @pytest.mark.asyncio
    async def test_flush_counters_noop_with_backup_key_present(
        self,
        mcp_encryption_key_backup: str,
        force_db_fallback: None,
    ) -> None:
        """flush_counters_to_db returns zero when Redis unavailable — backup key present."""
        from app.core import token_cost_tracker
        from app.core.token_cost_tracker import flush_counters_to_db

        # Backup key set via fixture — should not interfere
        assert token_cost_tracker._redis_available is False

        result = await flush_counters_to_db()

        assert result == {"synced": 0, "failed": 0}
