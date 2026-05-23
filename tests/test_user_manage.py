"""Tests for MCP authorization user_manage admin tool.

Covers create_user, list_users, revoke_user, rotate_key, check_limits,
update_limits, update_token_limits with bounds validation.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Generator

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_redis_available() -> Generator[None, None, None]:
    """Force rate_limiter and token_cost_tracker to use DB fallback."""
    from app.core import rate_limiter, token_cost_tracker

    rate_limiter._redis_available = False
    rate_limiter._redis_pool = None
    rate_limiter._redis_last_check = 0.0

    token_cost_tracker._redis_available = False
    token_cost_tracker._redis_pool = None
    token_cost_tracker._redis_last_check = 0.0
    yield


@pytest.fixture
def db_connection(shared_db_path: str, mcp_encryption_key: str) -> sqlite3.Connection:
    """Return a SQLite connection with schema created."""
    from app.core.user_store import init_db

    conn: sqlite3.Connection = init_db()
    return conn


# ---------------------------------------------------------------------------
# 1. create_user — generates key_id, raw_key, encrypts, stores
# ---------------------------------------------------------------------------

class TestCreateUser:
    """create_user admin tool."""

    @pytest.mark.asyncio
    async def test_create_user_returns_raw_key(self, db_connection: sqlite3.Connection) -> None:
        """create_user returns raw_key (one-time delivery)."""
        from app.tools.user_manage import create_user

        result = await create_user(name="test_alice")

        assert "user_id" in result
        assert "key_id" in result
        assert "raw_key" in result
        assert "encrypted_key" in result
        assert result["status"] == "active"
        assert len(result["raw_key"]) >= 43

    @pytest.mark.asyncio
    async def test_create_user_stored_in_db(self, db_connection: sqlite3.Connection) -> None:
        """create_user stores user in DB."""
        from app.core.user_store import get_user_by_key_id
        from app.tools.user_manage import create_user

        result = await create_user(name="test_bob")

        user = get_user_by_key_id(result["key_id"])

        assert user is not None
        assert user["name"] == "test_bob"
        assert user["status"] == "active"

    @pytest.mark.asyncio
    async def test_create_user_default_rate_limits(self, db_connection: sqlite3.Connection) -> None:
        """create_user uses default rate limits."""
        from app.tools.user_manage import create_user

        result = await create_user(name="test_charlie")

        assert result["rate_limits"]["daily"] == 100
        assert result["rate_limits"]["weekly"] == 500
        assert result["rate_limits"]["monthly"] == 2000

    @pytest.mark.asyncio
    async def test_create_user_custom_rate_limits(self, db_connection: sqlite3.Connection) -> None:
        """create_user accepts custom rate limits."""
        from app.tools.user_manage import create_user

        result = await create_user(
            name="test_dave",
            rate_limits={"daily": 200, "weekly": 1000, "monthly": 5000},
        )

        assert result["rate_limits"]["daily"] == 200
        assert result["rate_limits"]["weekly"] == 1000
        assert result["rate_limits"]["monthly"] == 5000

    @pytest.mark.asyncio
    async def test_create_user_custom_token_limits(self, db_connection: sqlite3.Connection) -> None:
        """create_user accepts custom token limits."""
        from app.tools.user_manage import create_user

        result = await create_user(
            name="test_eve",
            token_limits={"daily": 5000000, "weekly": 10000000, "monthly": 50000000},
        )

        assert result["token_limits"]["daily"] == 5000000
        assert result["token_limits"]["weekly"] == 10000000
        assert result["token_limits"]["monthly"] == 50000000

    @pytest.mark.asyncio
    async def test_create_user_invalid_rate_limits_raises(self, db_connection: sqlite3.Connection) -> None:
        """create_user raises ValueError for invalid rate limits."""
        from app.tools.user_manage import create_user

        with pytest.raises(ValueError, match="out of bounds"):
            await create_user(
                name="test_bad",
                rate_limits={"daily": 0},  # below min 1
            )

    @pytest.mark.asyncio
    async def test_create_user_invalid_token_limits_raises(self, db_connection: sqlite3.Connection) -> None:
        """create_user raises ValueError for invalid token limits."""
        from app.tools.user_manage import create_user

        with pytest.raises(ValueError, match="out of bounds"):
            await create_user(
                name="test_bad",
                token_limits={"daily": 10000001},  # above max 10M
            )

    @pytest.mark.asyncio
    async def test_create_user_unique_key_id(self, db_connection: sqlite3.Connection) -> None:
        """Two create_user calls produce different key_ids."""
        from app.tools.user_manage import create_user

        r1 = await create_user(name="user_a")
        r2 = await create_user(name="user_b")

        assert r1["key_id"] != r2["key_id"]


# ---------------------------------------------------------------------------
# 2. list_users — paginated, status filter
# ---------------------------------------------------------------------------

class TestListUsers:
    """list_users admin tool."""

    @pytest.mark.asyncio
    async def test_list_users_returns_users(self, db_connection: sqlite3.Connection) -> None:
        """list_users returns paginated user list."""
        from app.tools.user_manage import create_user, list_users

        await create_user(name="list_user_1")

        result = await list_users()

        assert "users" in result
        assert "total" in result
        assert "page" in result
        assert "page_size" in result

    @pytest.mark.asyncio
    async def test_list_users_no_encrypted_key(self, db_connection: sqlite3.Connection) -> None:
        """list_users output does NOT include encrypted_key."""
        from app.tools.user_manage import create_user, list_users

        await create_user(name="secure_user")

        result = await list_users()

        for user in result["users"]:
            assert "encrypted_key" not in user

    @pytest.mark.asyncio
    async def test_list_users_status_filter_active(self, db_connection: sqlite3.Connection) -> None:
        """list_users with status_filter='active' returns only active."""
        from app.tools.user_manage import create_user, list_users

        await create_user(name="active_user")

        result = await list_users(status_filter="active")

        assert all(u["status"] == "active" for u in result["users"])

    @pytest.mark.asyncio
    async def test_list_users_page_size_max(self, db_connection: sqlite3.Connection) -> None:
        """list_users page_size > 100 raises ValueError."""
        from app.tools.user_manage import list_users

        with pytest.raises(ValueError, match="page_size"):
            await list_users(page_size=101)

    @pytest.mark.asyncio
    async def test_list_users_page_below_1_raises(self, db_connection: sqlite3.Connection) -> None:
        """list_users page < 1 raises ValueError."""
        from app.tools.user_manage import list_users

        with pytest.raises(ValueError, match="page"):
            await list_users(page=0)


# ---------------------------------------------------------------------------
# 3. revoke_user — status = 'revoked', Redis cache cleared
# ---------------------------------------------------------------------------

class TestRevokeUser:
    """revoke_user admin tool."""

    @pytest.mark.asyncio
    async def test_revoke_user_status_revoked(self, db_connection: sqlite3.Connection) -> None:
        """revoke_user returns status='revoked'."""
        from app.tools.user_manage import create_user, revoke_user

        created = await create_user(name="revoke_target")
        result = await revoke_user(created["user_id"])

        assert result["status"] == "revoked"
        assert result["user_id"] == created["user_id"]
        assert "revoked_at" in result

    @pytest.mark.asyncio
    async def test_revoke_user_not_found_raises(self, db_connection: sqlite3.Connection) -> None:
        """revoke_user raises ValueError for unknown user_id."""
        from app.tools.user_manage import revoke_user

        with pytest.raises(ValueError, match="not found"):
            await revoke_user("nonexistent_uuid")


# ---------------------------------------------------------------------------
# 4. rotate_key — new key_id, old key revoked, counters carried over
# ---------------------------------------------------------------------------

class TestRotateKey:
    """rotate_key admin tool."""

    @pytest.mark.asyncio
    async def test_rotate_key_returns_new_key(self, db_connection: sqlite3.Connection) -> None:
        """rotate_key returns new_key_id and raw_key."""
        from app.tools.user_manage import create_user, rotate_key

        created = await create_user(name="rotate_target")
        result = await rotate_key(created["user_id"])

        assert "new_key_id" in result
        assert "raw_key" in result
        assert "old_key_id" in result
        assert "key_version" in result
        assert result["old_key_id"] == created["key_id"]

    @pytest.mark.asyncio
    async def test_rotate_key_increments_version(self, db_connection: sqlite3.Connection) -> None:
        """rotate_key increments key_version by 1."""
        from app.core.user_store import get_user_by_id
        from app.tools.user_manage import create_user, rotate_key

        created = await create_user(name="version_target")
        await rotate_key(created["user_id"])

        user = get_user_by_id(created["user_id"])
        assert user["key_version"] == 2

    @pytest.mark.asyncio
    async def test_rotate_key_not_found_raises(self, db_connection: sqlite3.Connection) -> None:
        """rotate_key raises ValueError for unknown user_id."""
        from app.tools.user_manage import rotate_key

        with pytest.raises(ValueError, match="not found"):
            await rotate_key("nonexistent_uuid")


# ---------------------------------------------------------------------------
# 5. check_limits — rate + token usage per tier
# ---------------------------------------------------------------------------

class TestCheckLimits:
    """check_limits admin tool."""

    @pytest.mark.asyncio
    async def test_check_limits_returns_rate_and_token(self, db_connection: sqlite3.Connection) -> None:
        """check_limits returns rate_limits and token_costs per tier."""
        from app.tools.user_manage import create_user, check_limits

        created = await create_user(name="check_target")

        result = await check_limits(created["user_id"])

        assert "rate_limits" in result
        assert "token_costs" in result

        for tier in ("daily", "weekly", "monthly"):
            assert tier in result["rate_limits"]
            assert tier in result["token_costs"]

    @pytest.mark.asyncio
    async def test_check_limits_rate_allowed_when_zero(self, db_connection: sqlite3.Connection) -> None:
        """check_limits rate allowed=True when counter is 0."""
        from app.tools.user_manage import create_user, check_limits

        created = await create_user(name="zero_target")

        result = await check_limits(created["user_id"])

        assert result["rate_limits"]["daily"]["allowed"] is True
        assert result["rate_limits"]["daily"]["current"] == 0


# ---------------------------------------------------------------------------
# 6. update_limits — bounds validation
# ---------------------------------------------------------------------------

class TestUpdateLimits:
    """update_limits admin tool — rate limits bounds."""

    @pytest.mark.asyncio
    async def test_update_limits_daily(self, db_connection: sqlite3.Connection) -> None:
        """update_limits accepts valid daily limit."""
        from app.tools.user_manage import create_user, update_limits

        created = await create_user(name="limit_target")
        result = await update_limits(created["user_id"], daily=500)

        assert result["updated_rate_limits"]["daily"] == 500

    @pytest.mark.asyncio
    async def test_update_limits_daily_at_min(self, db_connection: sqlite3.Connection) -> None:
        """update_limits daily=1 accepted."""
        from app.tools.user_manage import create_user, update_limits

        created = await create_user(name="min_target")
        result = await update_limits(created["user_id"], daily=1)

        assert result["updated_rate_limits"]["daily"] == 1

    @pytest.mark.asyncio
    async def test_update_limits_daily_at_max(self, db_connection: sqlite3.Connection) -> None:
        """update_limits daily=1000 accepted."""
        from app.tools.user_manage import create_user, update_limits

        created = await create_user(name="max_target")
        result = await update_limits(created["user_id"], daily=1000)

        assert result["updated_rate_limits"]["daily"] == 1000

    @pytest.mark.asyncio
    async def test_update_limits_daily_below_min_raises(self, db_connection: sqlite3.Connection) -> None:
        """update_limits daily=0 raises ValueError."""
        from app.tools.user_manage import create_user, update_limits

        created = await create_user(name="bad_target")

        with pytest.raises(ValueError, match="out of bounds"):
            await update_limits(created["user_id"], daily=0)

    @pytest.mark.asyncio
    async def test_update_limits_daily_above_max_raises(self, db_connection: sqlite3.Connection) -> None:
        """update_limits daily=1001 raises ValueError."""
        from app.tools.user_manage import create_user, update_limits

        created = await create_user(name="bad_target")

        with pytest.raises(ValueError, match="out of bounds"):
            await update_limits(created["user_id"], daily=1001)

    @pytest.mark.asyncio
    async def test_update_limits_weekly(self, db_connection: sqlite3.Connection) -> None:
        """update_limits accepts valid weekly limit."""
        from app.tools.user_manage import create_user, update_limits

        created = await create_user(name="weekly_target")
        result = await update_limits(created["user_id"], weekly=5000)

        assert result["updated_rate_limits"]["weekly"] == 5000

    @pytest.mark.asyncio
    async def test_update_limits_weekly_at_max(self, db_connection: sqlite3.Connection) -> None:
        """update_limits weekly=10000 accepted."""
        from app.tools.user_manage import create_user, update_limits

        created = await create_user(name="weekly_max")
        result = await update_limits(created["user_id"], weekly=10000)

        assert result["updated_rate_limits"]["weekly"] == 10000

    @pytest.mark.asyncio
    async def test_update_limits_weekly_below_min_raises(self, db_connection: sqlite3.Connection) -> None:
        """update_limits weekly=0 raises ValueError."""
        from app.tools.user_manage import create_user, update_limits

        created = await create_user(name="bad_weekly")

        with pytest.raises(ValueError, match="out of bounds"):
            await update_limits(created["user_id"], weekly=0)

    @pytest.mark.asyncio
    async def test_update_limits_monthly(self, db_connection: sqlite3.Connection) -> None:
        """update_limits accepts valid monthly limit."""
        from app.tools.user_manage import create_user, update_limits

        created = await create_user(name="monthly_target")
        result = await update_limits(created["user_id"], monthly=50000)

        assert result["updated_rate_limits"]["monthly"] == 50000

    @pytest.mark.asyncio
    async def test_update_limits_monthly_at_max(self, db_connection: sqlite3.Connection) -> None:
        """update_limits monthly=100000 accepted."""
        from app.tools.user_manage import create_user, update_limits

        created = await create_user(name="monthly_max")
        result = await update_limits(created["user_id"], monthly=100000)

        assert result["updated_rate_limits"]["monthly"] == 100000

    @pytest.mark.asyncio
    async def test_update_limits_monthly_below_min_raises(self, db_connection: sqlite3.Connection) -> None:
        """update_limits monthly=0 raises ValueError."""
        from app.tools.user_manage import create_user, update_limits

        created = await create_user(name="bad_monthly")

        with pytest.raises(ValueError, match="out of bounds"):
            await update_limits(created["user_id"], monthly=0)

    @pytest.mark.asyncio
    async def test_update_limits_all_tiers(self, db_connection: sqlite3.Connection) -> None:
        """update_limits updates all tiers simultaneously."""
        from app.tools.user_manage import create_user, update_limits

        created = await create_user(name="all_tiers")
        result = await update_limits(
            created["user_id"],
            daily=200,
            weekly=2000,
            monthly=10000,
        )

        assert result["updated_rate_limits"]["daily"] == 200
        assert result["updated_rate_limits"]["weekly"] == 2000
        assert result["updated_rate_limits"]["monthly"] == 10000


# ---------------------------------------------------------------------------
# 7. update_token_limits — bounds validation
# ---------------------------------------------------------------------------

class TestUpdateTokenLimits:
    """update_token_limits admin tool — token limits bounds."""

    @pytest.mark.asyncio
    async def test_update_token_limits_daily(self, db_connection: sqlite3.Connection) -> None:
        """update_token_limits accepts valid daily limit."""
        from app.tools.user_manage import create_user, update_token_limits

        created = await create_user(name="tl_target")
        result = await update_token_limits(created["user_id"], daily=5000000)

        assert result["updated_token_limits"]["daily"] == 5000000

    @pytest.mark.asyncio
    async def test_update_token_limits_daily_zero(self, db_connection: sqlite3.Connection) -> None:
        """update_token_limits daily=0 (unlimited) accepted."""
        from app.tools.user_manage import create_user, update_token_limits

        created = await create_user(name="unlimited_target")
        result = await update_token_limits(created["user_id"], daily=0)

        assert result["updated_token_limits"]["daily"] == 0

    @pytest.mark.asyncio
    async def test_update_token_limits_daily_at_max(self, db_connection: sqlite3.Connection) -> None:
        """update_token_limits daily=10_000_000 accepted."""
        from app.tools.user_manage import create_user, update_token_limits

        created = await create_user(name="tl_max")
        result = await update_token_limits(created["user_id"], daily=10000000)

        assert result["updated_token_limits"]["daily"] == 10000000

    @pytest.mark.asyncio
    async def test_update_token_limits_daily_above_max_raises(self, db_connection: sqlite3.Connection) -> None:
        """update_token_limits daily=10_000_001 raises ValueError."""
        from app.tools.user_manage import create_user, update_token_limits

        created = await create_user(name="tl_bad")

        with pytest.raises(ValueError, match="out of bounds"):
            await update_token_limits(created["user_id"], daily=10000001)

    @pytest.mark.asyncio
    async def test_update_token_limits_weekly_at_max(self, db_connection: sqlite3.Connection) -> None:
        """update_token_limits weekly=50_000_000 accepted."""
        from app.tools.user_manage import create_user, update_token_limits

        created = await create_user(name="tw_max")
        result = await update_token_limits(created["user_id"], weekly=50000000)

        assert result["updated_token_limits"]["weekly"] == 50000000

    @pytest.mark.asyncio
    async def test_update_token_limits_weekly_above_max_raises(self, db_connection: sqlite3.Connection) -> None:
        """update_token_limits weekly=50_000_001 raises ValueError."""
        from app.tools.user_manage import create_user, update_token_limits

        created = await create_user(name="tw_bad")

        with pytest.raises(ValueError, match="out of bounds"):
            await update_token_limits(created["user_id"], weekly=50000001)

    @pytest.mark.asyncio
    async def test_update_token_limits_monthly_at_max(self, db_connection: sqlite3.Connection) -> None:
        """update_token_limits monthly=200_000_000 accepted."""
        from app.tools.user_manage import create_user, update_token_limits

        created = await create_user(name="tm_max")
        result = await update_token_limits(created["user_id"], monthly=200000000)

        assert result["updated_token_limits"]["monthly"] == 200000000

    @pytest.mark.asyncio
    async def test_update_token_limits_monthly_above_max_raises(self, db_connection: sqlite3.Connection) -> None:
        """update_token_limits monthly=200_000_001 raises ValueError."""
        from app.tools.user_manage import create_user, update_token_limits

        created = await create_user(name="tm_bad")

        with pytest.raises(ValueError, match="out of bounds"):
            await update_token_limits(created["user_id"], monthly=200000001)

    @pytest.mark.asyncio
    async def test_update_token_limits_all_tiers(self, db_connection: sqlite3.Connection) -> None:
        """update_token_limits updates all tiers simultaneously."""
        from app.tools.user_manage import create_user, update_token_limits

        created = await create_user(name="tl_all")
        result = await update_token_limits(
            created["user_id"],
            daily=1000000,
            weekly=5000000,
            monthly=20000000,
        )

        assert result["updated_token_limits"]["daily"] == 1000000
        assert result["updated_token_limits"]["weekly"] == 5000000
        assert result["updated_token_limits"]["monthly"] == 20000000
