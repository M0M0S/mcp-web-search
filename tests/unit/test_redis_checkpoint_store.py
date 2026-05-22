"""Tests for RedisCheckpointStore (AC 11-15)."""

import json
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
import redis.asyncio as aioredis

from app.core.checkpoint_store import RedisCheckpointStore


class TestRedisCheckpointStoreSave:
    """AC 11: RedisCheckpointStore persist checkpoints in Redis."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client that behaves like aioredis.Redis."""
        client = MagicMock(spec=aioredis.Redis)
        client.set = AsyncMock()
        client.get = AsyncMock()
        client.keys = AsyncMock()
        client.ttl = AsyncMock()
        client.delete = AsyncMock()
        return client

    @pytest.fixture
    def store(self, mock_redis):
        """Create RedisCheckpointStore with mocked Redis."""
        return RedisCheckpointStore(redis_client=mock_redis)

    @pytest.mark.asyncio
    async def test_save_persists_to_redis(self, store, mock_redis):
        """AC11: checkpoint saved to Redis with TTL."""
        checkpoint_key = "webfetch_checkpoint:tenant-1:1.0:abc123"
        state_data = {"prompt": "test", "tenant_id": "tenant-1"}

        await store.save(checkpoint_key, state_data)

        mock_redis.set.assert_called_once_with(
            checkpoint_key,
            json.dumps(state_data, ensure_ascii=False, default=str),
            ex=store._ttl,
        )

    @pytest.mark.asyncio
    async def test_save_with_default_ttl(self, store):
        """AC13: default TTL is 3600s."""
        assert store._ttl == 3600

    @pytest.mark.asyncio
    async def test_save_with_custom_ttl(self):
        """AC13: TTL configurable via Settings."""
        from app.core.config import Settings

        settings = Settings(REDIS_CHECKPOINT_TTL=7200)
        mock_redis = MagicMock(spec=aioredis.Redis)
        mock_redis.set = AsyncMock()

        store = RedisCheckpointStore(redis_client=mock_redis, settings=settings)
        assert store._ttl == 7200

        checkpoint_key = "webfetch_checkpoint:test"
        await store.save(checkpoint_key, {"data": 1})

        mock_redis.set.assert_called_once_with(
            checkpoint_key,
            ANY,
            ex=7200,
        )


class TestRedisCheckpointStoreKeyFormat:
    """AC 12: Key format webfetch_checkpoint:{tenant_id}:{version}:{cache_key}."""

    @pytest.fixture
    def store_key_format(self):
        """Create RedisCheckpointStore with mocked Redis for key format tests."""
        mock_redis = MagicMock(spec=aioredis.Redis)
        mock_redis.set = AsyncMock()
        return RedisCheckpointStore(redis_client=mock_redis)

    @pytest.mark.asyncio
    async def test_key_format_with_cache_key(self, store_key_format):
        """AC12: key uses tenant_id, version, cache_key."""
        checkpoint_key = "webfetch_checkpoint:tenant-42:1.0:def456"
        state_data = {"prompt": "test"}

        await store_key_format.save(checkpoint_key, state_data)

        called_key = store_key_format._redis.set.call_args[0][0]
        assert called_key == checkpoint_key
        assert called_key.startswith("webfetch_checkpoint:")
        parts = called_key.split(":")
        assert len(parts) == 4
        assert parts[1] == "tenant-42"
        assert parts[2] == "1.0"

    @pytest.mark.asyncio
    async def test_key_format_without_cache_key(self):
        """AC12: key generated from prompt + tenant_id + version."""
        from app.models.webfetch import WebFetchState

        state = WebFetchState(prompt="test query", tenant_id="tenant-1")
        checkpoint_key = state.checkpoint_key

        assert checkpoint_key.startswith("webfetch_checkpoint:")
        parts = checkpoint_key.split(":")
        assert len(parts) == 4
        assert parts[1] == "tenant-1"
        assert parts[2] == "1.0"


class TestRedisCheckpointStoreFallback:
    """AC 14: Redis fallback to MemorySaver."""

    @pytest.fixture
    def non_redis_client(self):
        """Create a client that is NOT a real aioredis.Redis."""
        return MagicMock()  # No from_url/_pool, not isinstance(aioredis.Redis)

    @pytest.fixture
    def store_fallback(self, non_redis_client):
        """RedisCheckpointStore with non-Redis client → always fallback."""
        return RedisCheckpointStore(redis_client=non_redis_client)

    @pytest.mark.asyncio
    async def test_fallback_to_memory_saver_on_init(self, store_fallback):
        """AC14: non-Redis client → _redis_available = False."""
        assert store_fallback.is_redis_available is False

    @pytest.mark.asyncio
    async def test_fallback_save_stores_in_memory(self, store_fallback):
        """AC14: save on unavailable Redis → stored in MemorySaver."""
        checkpoint_key = "webfetch_checkpoint:tenant-1:1.0:test"
        state_data = {"prompt": "test", "search_queries": ["q1"]}

        await store_fallback.save(checkpoint_key, state_data)

        # Verify stored in MemorySaver
        config = store_fallback._make_config(checkpoint_key)
        tuple_result = await store_fallback._memory_saver.aget_tuple(config)
        assert tuple_result is not None
        assert tuple_result.checkpoint is not None
        assert "prompt" in tuple_result.checkpoint["channel_values"]

    @pytest.mark.asyncio
    async def test_fallback_load_from_memory(self, store_fallback):
        """AC14: load on unavailable Redis → from MemorySaver."""
        checkpoint_key = "webfetch_checkpoint:tenant-1:1.0:test"
        state_data = {"prompt": "test", "search_queries": ["q1", "q2"]}

        await store_fallback.save(checkpoint_key, state_data)

        loaded = await store_fallback.load(checkpoint_key)
        assert loaded is not None
        assert loaded["prompt"] == "test"
        assert loaded["search_queries"] == ["q1", "q2"]

    @pytest.mark.asyncio
    async def test_redis_failure_triggers_fallback(self):
        """AC14: Redis connection error → fallback activated."""
        mock_redis = MagicMock(spec=aioredis.Redis)
        mock_redis.set = AsyncMock(side_effect=ConnectionError("Redis down"))

        store = RedisCheckpointStore(redis_client=mock_redis)
        assert store.is_redis_available is True

        checkpoint_key = "webfetch_checkpoint:tenant-1:1.0:test"
        await store.save(checkpoint_key, {"data": 1})

        assert store.is_redis_available is False

        # Next save should use MemorySaver
        mock_redis.set.reset_mock()
        await store.save(checkpoint_key, {"data": 2})
        mock_redis.set.assert_not_called()  # Redis not used anymore


class TestRedisCheckpointStoreCleanup:
    """AC 15: Checkpoint cleanup expired."""

    @pytest.fixture
    def mock_redis(self):
        """Mock Redis with keys/ttl/delete — real aioredis.Redis instance."""
        client = MagicMock(spec=aioredis.Redis)
        client.set = AsyncMock()
        client.get = AsyncMock()
        client.keys = AsyncMock(return_value=["key1", "key2", "key3"])
        client.ttl = AsyncMock(return_value=-1)  # expired
        client.delete = AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_cleanup_scans_keys(self, mock_redis):
        """AC15: cleanup scans webfetch_checkpoint:* keys."""
        store = RedisCheckpointStore(redis_client=mock_redis)
        scanned = await store.cleanup_expired()
        assert scanned == 3

    @pytest.mark.asyncio
    async def test_cleanup_deletes_expired(self, mock_redis):
        """AC15: expired keys (ttl <= 0) are deleted."""
        store = RedisCheckpointStore(redis_client=mock_redis)
        await store.cleanup_expired()

        assert mock_redis.delete.call_count == 3

    @pytest.mark.asyncio
    async def test_cleanup_skipped_when_redis_unavailable(self):
        """AC15: cleanup skipped when Redis unavailable."""
        mock_redis = MagicMock()  # not real Redis

        store = RedisCheckpointStore(redis_client=mock_redis)
        scanned = await store.cleanup_expired()
        assert scanned == 0

    @pytest.mark.asyncio
    async def test_cleanup_interval_configurable(self):
        """AC15: cleanup interval configurable via Settings."""
        from app.core.config import Settings

        settings = Settings(CHECKPOINT_CLEANUP_INTERVAL=1800)
        mock_redis = MagicMock()
        mock_redis.from_url = True

        store = RedisCheckpointStore(redis_client=mock_redis, settings=settings)
        assert store._cleanup_interval == 1800


class TestRedisCheckpointStoreReset:
    """Reset in-memory fallback store."""

    @pytest.mark.asyncio
    async def test_reset_clears_memory(self):
        """Reset clears the MemorySaver fallback."""
        mock_redis = MagicMock()  # not real Redis → always fallback

        store = RedisCheckpointStore(redis_client=mock_redis)
        await store.save("key1", {"data": 1})

        await store.reset()

        config = store._make_config("key1")
        tuple_result = await store._memory_saver.aget_tuple(config)
        assert tuple_result is None
