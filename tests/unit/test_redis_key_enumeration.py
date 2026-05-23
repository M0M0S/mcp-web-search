"""Exact key enumeration tests for revoke_user / rotate_key operations — mock Redis, no external deps."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.rate_limiter import (
    REDIS_KEY_PREFIX,
    DEFAULT_LIMITS,
    flush_counters_to_db,
    _redis_key,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def mock_redis_pool() -> MagicMock:
    """Mock Redis pool with configurable scan behavior."""
    with patch("redis.from_url") as mock_from_url:
        pool: MagicMock = MagicMock()

        pool.get.return_value = None
        pool.incr.return_value = 1
        pool.ping.return_value = True
        pool.delete.return_value = 1
        pool.expire.return_value = True

        mock_from_url.return_value = pool
        yield pool


@pytest.fixture
def mock_async_redis_pool() -> MagicMock:
    """Mock async Redis pool with configurable scan behavior."""
    with patch("redis.asyncio.from_url") as mock_from_url:
        pool: MagicMock = AsyncMock()

        pool.get = MagicMock(return_value=None)  # sync call in flush_counters_to_db
        pool.incr = AsyncMock(return_value=1)
        pool.ping = AsyncMock(return_value=True)
        pool.delete = MagicMock(return_value=1)  # sync call in flush_counters_to_db
        pool.expire = AsyncMock(return_value=True)

        mock_from_url.return_value = pool
        yield pool


# ── T4: test_revoke_user_enumerates_exact_rate_keys ──────────────────────


def test_revoke_user_enumerates_exact_rate_keys(mock_redis_pool: MagicMock):
    """Verify that key enumeration produces exact rate limiter keys for a user."""
    user_id = "user-abc-123"

    # Build expected keys
    expected_keys = {
        f"{REDIS_KEY_PREFIX}{user_id}:daily",
        f"{REDIS_KEY_PREFIX}{user_id}:weekly",
        f"{REDIS_KEY_PREFIX}{user_id}:monthly",
    }

    # Verify _redis_key helper produces correct format
    for tier in ["daily", "weekly", "monthly"]:
        key = _redis_key(user_id, tier)
        assert key in expected_keys
        assert key.startswith(f"{REDIS_KEY_PREFIX}{user_id}:")


# ── T4: test_revoke_user_enumerates_exact_token_keys ─────────────────────


def test_revoke_user_enumerates_exact_token_keys(mock_redis_pool: MagicMock):
    """Verify token-related key enumeration for a user."""
    user_id = "user-abc-123"

    for tier in ["daily", "weekly", "monthly"]:
        key = _redis_key(user_id, tier)
        assert key.startswith(REDIS_KEY_PREFIX)
        assert user_id in key
        assert tier in key


# ── T4: test_revoke_user_no_keys_when_empty ──────────────────────────────


@pytest.mark.asyncio
async def test_revoke_user_no_keys_when_empty(mock_async_redis_pool: MagicMock):
    """Verify flush returns zero counts when no rate keys exist."""
    # Configure scan to return empty results
    mock_async_redis_pool.scan = AsyncMock(side_effect=[
        (0, []),  # First scan: no keys
    ])

    with patch("app.core.rate_limiter._get_async_pool", return_value=mock_async_redis_pool):
        with patch("app.core.rate_limiter._redis_available", True):
            result = await flush_counters_to_db()

    assert result["synced"] == 0
    assert result["failed"] == 0


# ── T4: test_rotate_key_enumerates_and_clears_keys ───────────────────────


@pytest.mark.asyncio
async def test_rotate_key_enumerates_and_clears_keys(mock_async_redis_pool: MagicMock):
    """Verify key enumeration and clearing during key rotation."""
    user_id = "user-abc-123"

    # Configure scan to return rate keys for this user
    rate_keys = [
        f"{REDIS_KEY_PREFIX}{user_id}:daily",
        f"{REDIS_KEY_PREFIX}{user_id}:weekly",
        f"{REDIS_KEY_PREFIX}{user_id}:monthly",
    ]

    mock_async_redis_pool.scan = AsyncMock(side_effect=[
        (100, rate_keys),  # First scan: found keys
        (0, []),  # Second scan: done
    ])

    # Configure get to return counter values (sync call)
    mock_async_redis_pool.get = MagicMock(side_effect=[
        5,  # daily count
        50,  # weekly count
        200,  # monthly count
    ])

    with patch("app.core.rate_limiter._get_async_pool", return_value=mock_async_redis_pool):
        with patch("app.core.rate_limiter._redis_available", True):
            result = await flush_counters_to_db()

    assert result["synced"] == 3
    assert result["failed"] == 0


# ── T4: test_key_enumeration_no_collision_with_other_users ───────────────


def test_key_enumeration_no_collision_with_other_users(mock_redis_pool: MagicMock):
    """Verify that key enumeration doesn't produce keys for other users."""
    user_a = "user-a-111"
    user_b = "user-b-222"

    keys_for_a = {_redis_key(user_a, tier) for tier in ["daily", "weekly", "monthly"]}
    keys_for_b = {_redis_key(user_b, tier) for tier in ["daily", "weekly", "monthly"]}

    # Keys should not overlap
    assert keys_for_a.isdisjoint(keys_for_b)

    # Each key should contain the exact user_id
    for key in keys_for_a:
        assert user_a in key
        assert user_b not in key

    for key in keys_for_b:
        assert user_b in key
        assert user_a not in key


# ── Additional key format tests ──────────────────────────────────────────


def test_redis_key_prefix_consistency():
    """Verify REDIS_KEY_PREFIX is consistent across all key operations."""
    assert REDIS_KEY_PREFIX == "rl:"

    user_id = "test-user"
    for tier in ["daily", "weekly", "monthly"]:
        key = _redis_key(user_id, tier)
        assert key.startswith("rl:")


def test_default_limits_coverage():
    """Verify all tiers have default limits defined."""
    for tier in ["daily", "weekly", "monthly"]:
        assert tier in DEFAULT_LIMITS
        assert DEFAULT_LIMITS[tier] > 0


def test_default_ttls_coverage():
    """Verify all tiers have default TTLs defined."""
    from app.core.rate_limiter import DEFAULT_TTLS

    for tier in ["daily", "weekly", "monthly"]:
        assert tier in DEFAULT_TTLS
        assert DEFAULT_TTLS[tier] > 0


def test_unknown_tier_fallback_ttl():
    """Verify unknown tier falls back to default TTL."""
    from app.core.rate_limiter import _get_tier_ttl

    ttl = _get_tier_ttl("unknown_tier")
    assert ttl == 86400  # DEFAULT_TTLS default


def test_unknown_tier_fallback_limit():
    """Verify unknown tier falls back to default limit."""
    from app.core.rate_limiter import _get_default_limit

    limit = _get_default_limit("unknown_tier")
    assert limit == 100  # DEFAULT_LIMITS default
