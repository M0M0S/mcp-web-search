"""Tests for MCP authorization user_store module.

Covers DB schema auto-create, CRUD operations, revoke, key rotation,
rate/token limit bounds validation, and pagination.
"""

from __future__ import annotations

import sqlite3
from typing import Generator

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_connection(shared_db_path: str, mcp_encryption_key: str) -> Generator[sqlite3.Connection, None, None]:
    """Return a SQLite connection with schema created.

    Closes the connection on teardown to prevent ResourceWarning.
    """
    from app.core.user_store import init_db

    conn: sqlite3.Connection = init_db()
    yield conn
    conn.close()


@pytest.fixture
def created_user(db_connection: sqlite3.Connection, mcp_encryption_key: str) -> dict:
    """Create a test user and return the record."""
    from app.core.user_store import create_user

    return create_user(
        name="test_user", rate_limits={"daily": 100, "weekly": 500, "monthly": 2000}
    )


# ---------------------------------------------------------------------------
# 1. DB schema auto-create on init_db()
# ---------------------------------------------------------------------------


class TestSchemaAutoCreate:
    """init_db() creates all tables and indexes."""

    def test_users_table_exists(self, db_connection: sqlite3.Connection) -> None:
        """users table is created by init_db."""
        rows = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchall()
        assert len(rows) == 1

    def test_rate_limit_snapshots_table_exists(
        self, db_connection: sqlite3.Connection
    ) -> None:
        """rate_limit_snapshots table is created by init_db."""
        rows = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='rate_limit_snapshots'"
        ).fetchall()
        assert len(rows) == 1

    def test_token_cost_snapshots_table_exists(
        self, db_connection: sqlite3.Connection
    ) -> None:
        """token_cost_snapshots table is created by init_db."""
        rows = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='token_cost_snapshots'"
        ).fetchall()
        assert len(rows) == 1

    def test_indexes_created(self, db_connection: sqlite3.Connection) -> None:
        """Indexes on users and snapshots are created."""
        rows = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        index_names: list[str] = [r["name"] for r in rows]

        assert "idx_users_key_id" in index_names
        assert "idx_users_status" in index_names
        assert "idx_snapshots_user" in index_names
        assert "idx_token_snapshots_user" in index_names


# ---------------------------------------------------------------------------
# 2. CRUD operations
# ---------------------------------------------------------------------------


class TestCRUD:
    """create_user, get_user_by_key_id, get_user_by_id, list_users."""

    def test_create_user_returns_required_fields(
        self, db_connection: sqlite3.Connection
    ) -> None:
        """create_user returns dict with user_id, key_id, status, etc."""
        from app.core.user_store import create_user

        user = create_user(name="alice")

        assert "user_id" in user
        assert "key_id" in user
        assert user["status"] == "active"
        assert "created_at" in user
        assert "updated_at" in user

    def test_create_user_encrypted_key_is_none(
        self, db_connection: sqlite3.Connection
    ) -> None:
        """encrypted_key is None — key is encrypted externally via encryption.py."""
        from app.core.user_store import create_user

        user = create_user(name="frank")

        assert user["encrypted_key"] is None

    def test_create_user_default_rate_limits(
        self, db_connection: sqlite3.Connection
    ) -> None:
        """Default rate limits are 100/500/2000."""
        from app.core.user_store import create_user

        user = create_user(name="bob")

        assert user["rate_limits"]["daily"] == 100
        assert user["rate_limits"]["weekly"] == 500
        assert user["rate_limits"]["monthly"] == 2000

    def test_create_user_default_token_limits_none(
        self, db_connection: sqlite3.Connection
    ) -> None:
        """Default token limits are None (unlimited)."""
        from app.core.user_store import create_user

        user = create_user(name="charlie")

        assert user["token_limits"]["daily"] is None
        assert user["token_limits"]["weekly"] is None
        assert user["token_limits"]["monthly"] is None

    def test_create_user_custom_rate_limits(
        self, db_connection: sqlite3.Connection
    ) -> None:
        """Custom rate limits are stored correctly."""
        from app.core.user_store import create_user

        user = create_user(
            name="dave",
            rate_limits={"daily": 50, "weekly": 250, "monthly": 1000},
        )

        assert user["rate_limits"]["daily"] == 50
        assert user["rate_limits"]["weekly"] == 250
        assert user["rate_limits"]["monthly"] == 1000

    def test_create_user_custom_token_limits(
        self, db_connection: sqlite3.Connection
    ) -> None:
        """Custom token limits are stored correctly."""
        from app.core.user_store import create_user

        user = create_user(
            name="eve",
            token_limits={"daily": 1000000, "weekly": 5000000, "monthly": 20000000},
        )

        assert user["token_limits"]["daily"] == 1000000
        assert user["token_limits"]["weekly"] == 5000000
        assert user["token_limits"]["monthly"] == 20000000

    def test_get_user_by_key_id(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """get_user_by_key_id returns the user."""
        from app.core.user_store import get_user_by_key_id

        user = get_user_by_key_id(created_user["key_id"])

        assert user is not None
        assert user["user_id"] == created_user["user_id"]
        assert user["key_id"] == created_user["key_id"]
        assert user["encrypted_key"] is None  # key encrypted externally

    def test_get_user_by_key_id_not_found(
        self, db_connection: sqlite3.Connection
    ) -> None:
        """get_user_by_key_id returns None for unknown key_id."""
        from app.core.user_store import get_user_by_key_id

        assert get_user_by_key_id("key_nonexistent") is None

    def test_get_user_by_id(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """get_user_by_id returns the user."""
        from app.core.user_store import get_user_by_id

        user = get_user_by_id(created_user["user_id"])

        assert user is not None
        assert user["user_id"] == created_user["user_id"]

    def test_get_user_by_id_not_found(self, db_connection: sqlite3.Connection) -> None:
        """get_user_by_id returns None for unknown user_id."""
        from app.core.user_store import get_user_by_id

        assert get_user_by_id("nonexistent_uuid") is None

    def test_list_users_returns_all(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """list_users with status_filter='all' returns all users."""
        from app.core.user_store import list_users

        result = list_users(status_filter="all")

        assert result["total"] >= 1
        assert len(result["users"]) >= 1
        assert result["page"] == 1
        assert result["page_size"] == 20

    def test_list_users_status_filter_active(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """list_users with status_filter='active' returns only active users."""
        from app.core.user_store import list_users

        result = list_users(status_filter="active")

        assert all(u["status"] == "active" for u in result["users"])

    def test_list_users_status_filter_revoked(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """list_users with status_filter='revoked' returns only revoked users."""
        from app.core.user_store import list_users, revoke_user

        revoke_user(created_user["user_id"])

        result = list_users(status_filter="revoked")

        assert all(u["status"] == "revoked" for u in result["users"])
        assert result["total"] >= 1


# ---------------------------------------------------------------------------
# 3. revoke_user — status = 'revoked'
# ---------------------------------------------------------------------------


class TestRevokeUser:
    """revoke_user sets status to 'revoked'."""

    def test_revoke_sets_status(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """revoke_user returns user with status='revoked'."""
        from app.core.user_store import revoke_user

        revoked = revoke_user(created_user["user_id"])

        assert revoked["status"] == "revoked"

    def test_revoke_not_found_raises(self, db_connection: sqlite3.Connection) -> None:
        """revoke_user raises ValueError for unknown user_id."""
        from app.core.user_store import revoke_user

        with pytest.raises(ValueError, match="not found"):
            revoke_user("nonexistent_uuid")

    def test_revoke_user_by_key_id_returns_none(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """After revoke, get_user_by_key_id still returns the user (key_id unchanged)."""
        from app.core.user_store import get_user_by_key_id, revoke_user

        revoke_user(created_user["user_id"])

        user = get_user_by_key_id(created_user["key_id"])
        assert user is not None
        assert user["status"] == "revoked"


# ---------------------------------------------------------------------------
# 4. rotate_key — key_version + 1
# ---------------------------------------------------------------------------


class TestRotateKey:
    """rotate_key increments key_version."""

    def test_rotate_increments_version(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """rotate_key returns new_key_version = old + 1."""
        from app.core.user_store import rotate_key

        result = rotate_key(created_user["user_id"])

        assert result["new_key_version"] == 2
        assert result["user_id"] == created_user["user_id"]

    def test_rotate_multiple_times(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Multiple rotations increment sequentially."""
        from app.core.user_store import rotate_key

        r1 = rotate_key(created_user["user_id"])
        r2 = rotate_key(created_user["user_id"])
        r3 = rotate_key(created_user["user_id"])

        assert r1["new_key_version"] == 2
        assert r2["new_key_version"] == 3
        assert r3["new_key_version"] == 4

    def test_rotate_not_found_raises(self, db_connection: sqlite3.Connection) -> None:
        """rotate_key raises ValueError for unknown user_id."""
        from app.core.user_store import rotate_key

        with pytest.raises(ValueError, match="not found"):
            rotate_key("nonexistent_uuid")


# ---------------------------------------------------------------------------
# 5. update_rate_limits — bounds validation [1-1000/10000/100000]
# ---------------------------------------------------------------------------


class TestUpdateRateLimits:
    """update_rate_limits bounds validation."""

    def test_update_daily_within_bounds(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Valid daily limit accepted."""
        from app.core.user_store import update_rate_limits

        updated = update_rate_limits(created_user["user_id"], daily=500)

        assert updated["rate_limits"]["daily"] == 500

    def test_update_daily_at_min(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Daily limit at minimum (1) accepted."""
        from app.core.user_store import update_rate_limits

        updated = update_rate_limits(created_user["user_id"], daily=1)

        assert updated["rate_limits"]["daily"] == 1

    def test_update_daily_at_max(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Daily limit at maximum (1000) accepted."""
        from app.core.user_store import update_rate_limits

        updated = update_rate_limits(created_user["user_id"], daily=1000)

        assert updated["rate_limits"]["daily"] == 1000

    def test_update_daily_below_min_raises(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Daily limit below 1 raises ValueError."""
        from app.core.user_store import update_rate_limits

        with pytest.raises(ValueError, match="out of bounds"):
            update_rate_limits(created_user["user_id"], daily=0)

    def test_update_daily_above_max_raises(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Daily limit above 1000 raises ValueError."""
        from app.core.user_store import update_rate_limits

        with pytest.raises(ValueError, match="out of bounds"):
            update_rate_limits(created_user["user_id"], daily=1001)

    def test_update_weekly_within_bounds(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Valid weekly limit accepted."""
        from app.core.user_store import update_rate_limits

        updated = update_rate_limits(created_user["user_id"], weekly=5000)

        assert updated["rate_limits"]["weekly"] == 5000

    def test_update_weekly_at_max(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Weekly limit at maximum (10000) accepted."""
        from app.core.user_store import update_rate_limits

        updated = update_rate_limits(created_user["user_id"], weekly=10000)

        assert updated["rate_limits"]["weekly"] == 10000

    def test_update_weekly_below_min_raises(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Weekly limit below 1 raises ValueError."""
        from app.core.user_store import update_rate_limits

        with pytest.raises(ValueError, match="out of bounds"):
            update_rate_limits(created_user["user_id"], weekly=0)

    def test_update_monthly_within_bounds(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Valid monthly limit accepted."""
        from app.core.user_store import update_rate_limits

        updated = update_rate_limits(created_user["user_id"], monthly=50000)

        assert updated["rate_limits"]["monthly"] == 50000

    def test_update_monthly_at_max(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Monthly limit at maximum (100000) accepted."""
        from app.core.user_store import update_rate_limits

        updated = update_rate_limits(created_user["user_id"], monthly=100000)

        assert updated["rate_limits"]["monthly"] == 100000

    def test_update_monthly_below_min_raises(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Monthly limit below 1 raises ValueError."""
        from app.core.user_store import update_rate_limits

        with pytest.raises(ValueError, match="out of bounds"):
            update_rate_limits(created_user["user_id"], monthly=0)

    def test_update_all_tiers(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Updating all tiers simultaneously works."""
        from app.core.user_store import update_rate_limits

        updated = update_rate_limits(
            created_user["user_id"],
            daily=200,
            weekly=1000,
            monthly=5000,
        )

        assert updated["rate_limits"]["daily"] == 200
        assert updated["rate_limits"]["weekly"] == 1000
        assert updated["rate_limits"]["monthly"] == 5000

    def test_update_none_keeps_current(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Passing None for all tiers returns unchanged user."""
        from app.core.user_store import update_rate_limits

        original_daily = created_user["rate_limits"]["daily"]
        updated = update_rate_limits(created_user["user_id"])

        assert updated["rate_limits"]["daily"] == original_daily


# ---------------------------------------------------------------------------
# 6. update_token_limits — bounds validation [0-10M/50M/200M], 0 = unlimited
# ---------------------------------------------------------------------------


class TestUpdateTokenLimits:
    """update_token_limits bounds validation."""

    def test_update_daily_within_bounds(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Valid daily token limit accepted."""
        from app.core.user_store import update_token_limits

        updated = update_token_limits(created_user["user_id"], daily=5000000)

        assert updated["token_limits"]["daily"] == 5000000

    def test_update_daily_at_min_zero(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Daily token limit at 0 (unlimited) accepted."""
        from app.core.user_store import update_token_limits

        updated = update_token_limits(created_user["user_id"], daily=0)

        assert updated["token_limits"]["daily"] == 0

    def test_update_daily_at_max(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Daily token limit at maximum (10_000_000) accepted."""
        from app.core.user_store import update_token_limits

        updated = update_token_limits(created_user["user_id"], daily=10000000)

        assert updated["token_limits"]["daily"] == 10000000

    def test_update_daily_above_max_raises(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Daily token limit above 10_000_000 raises ValueError."""
        from app.core.user_store import update_token_limits

        with pytest.raises(ValueError, match="out of bounds"):
            update_token_limits(created_user["user_id"], daily=10000001)

    def test_update_weekly_at_max(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Weekly token limit at maximum (50_000_000) accepted."""
        from app.core.user_store import update_token_limits

        updated = update_token_limits(created_user["user_id"], weekly=50000000)

        assert updated["token_limits"]["weekly"] == 50000000

    def test_update_weekly_above_max_raises(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Weekly token limit above 50_000_000 raises ValueError."""
        from app.core.user_store import update_token_limits

        with pytest.raises(ValueError, match="out of bounds"):
            update_token_limits(created_user["user_id"], weekly=50000001)

    def test_update_monthly_at_max(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Monthly token limit at maximum (200_000_000) accepted."""
        from app.core.user_store import update_token_limits

        updated = update_token_limits(created_user["user_id"], monthly=200000000)

        assert updated["token_limits"]["monthly"] == 200000000

    def test_update_monthly_above_max_raises(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Monthly token limit above 200_000_000 raises ValueError."""
        from app.core.user_store import update_token_limits

        with pytest.raises(ValueError, match="out of bounds"):
            update_token_limits(created_user["user_id"], monthly=200000001)

    def test_update_none_keeps_current(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """Passing None for all tiers returns unchanged user."""
        from app.core.user_store import update_token_limits

        original = created_user["token_limits"]["daily"]
        updated = update_token_limits(created_user["user_id"])

        assert updated["token_limits"]["daily"] == original


# ---------------------------------------------------------------------------
# 7. list_users pagination — page_size max 100, status filter
# ---------------------------------------------------------------------------


class TestListUsersPagination:
    """list_users pagination behavior."""

    def test_page_size_max_100(self, db_connection: sqlite3.Connection) -> None:
        """page_size=100 accepted."""
        from app.core.user_store import list_users

        result = list_users(page_size=100)

        assert result["page_size"] == 100

    def test_page_size_above_max_raises(
        self, db_connection: sqlite3.Connection
    ) -> None:
        """page_size > 100 raises ValueError."""
        from app.core.user_store import list_users

        with pytest.raises(ValueError, match="page_size"):
            list_users(page_size=101)

    def test_page_size_below_min_raises(
        self, db_connection: sqlite3.Connection
    ) -> None:
        """page_size < 1 raises ValueError."""
        from app.core.user_store import list_users

        with pytest.raises(ValueError, match="page_size"):
            list_users(page_size=0)

    def test_page_1_returns_first_batch(
        self, created_user: dict, db_connection: sqlite3.Connection
    ) -> None:
        """page=1 returns first items."""
        from app.core.user_store import list_users

        result = list_users(page=1, page_size=1)

        assert result["page"] == 1
        assert len(result["users"]) >= 1

    def test_page_2_returns_second_batch(
        self, db_connection: sqlite3.Connection
    ) -> None:
        """page=2 returns items after first batch."""
        from app.core.user_store import create_user, list_users

        # Create exactly 3 users in this test
        create_user(name="page_test_a")
        create_user(name="page_test_b")
        create_user(name="page_test_c")

        page1 = list_users(page=1, page_size=2)
        page2 = list_users(page=2, page_size=2)

        assert len(page1["users"]) == 2
        assert len(page2["users"]) >= 1  # at least one user on page 2

    def test_page_below_1_raises(self, db_connection: sqlite3.Connection) -> None:
        """page < 1 raises ValueError."""
        from app.core.user_store import list_users

        with pytest.raises(ValueError, match="page"):
            list_users(page=0)

    def test_invalid_status_filter_raises(
        self, db_connection: sqlite3.Connection
    ) -> None:
        """Invalid status_filter raises ValueError."""
        from app.core.user_store import list_users

        with pytest.raises(ValueError, match="status_filter"):
            list_users(status_filter="invalid")
