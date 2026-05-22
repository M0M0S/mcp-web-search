"""Checkpoint store — Redis-backed with MemorySaver fallback."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any, cast

import redis.asyncio as aioredis
from langgraph.checkpoint.base import Checkpoint, RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger(__name__)


class RedisCheckpointStore:
    """Persist LangGraph checkpoints in Redis with MemorySaver fallback.

    Key format: ``webfetch_checkpoint:{tenant_id}:{version}:{cache_key}``.
    TTL configurable via Settings (default: 3600s).
    Gracefully falls back to MemorySaver when Redis is unavailable.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis | None,
        settings: Settings | None = None,
    ) -> None:
        self._redis: aioredis.Redis | None = redis_client
        self._settings = settings
        self._ttl = self._resolve_ttl()
        self._cleanup_interval = self._resolve_cleanup_interval()
        # In-memory fallback store
        self._memory_saver: MemorySaver = MemorySaver()
        self._redis_available: bool = self._is_real_redis(redis_client)

    # ── TTL / cleanup resolution ──────────────────────────────────────

    @staticmethod
    def _is_real_redis(client: aioredis.Redis | None) -> bool:
        """Check whether the client is a real aioredis.Redis instance."""
        if client is None:
            return False
        return isinstance(client, aioredis.Redis)

    # ── TTL / cleanup resolution ──────────────────────────────────────

    def _resolve_ttl(self) -> int:
        if self._settings and hasattr(self._settings, "REDIS_CHECKPOINT_TTL"):
            ttl = self._settings.REDIS_CHECKPOINT_TTL
            if isinstance(ttl, int) and ttl > 0:
                return ttl
        return 3600

    def _resolve_cleanup_interval(self) -> int:
        if self._settings and hasattr(self._settings, "CHECKPOINT_CLEANUP_INTERVAL"):
            interval = self._settings.CHECKPOINT_CLEANUP_INTERVAL
            if isinstance(interval, int) and interval > 0:
                return interval
        return 3600

    # ── Helpers ───────────────────────────────────────────────────────

    def _make_config(self, checkpoint_key: str) -> RunnableConfig:
        """Build RunnableConfig for MemorySaver from checkpoint key."""
        return {"configurable": {"thread_id": checkpoint_key, "checkpoint_ns": ""}}

    # ── Public API ────────────────────────────────────────────────────

    async def save(self, checkpoint_key: str, state_data: dict[str, Any]) -> None:
        """Persist a checkpoint. Falls back to MemorySaver on Redis failure."""
        if self._redis_available and self._redis is not None:
            try:
                serialized = json.dumps(state_data, ensure_ascii=False, default=str)
                await self._redis.set(
                    checkpoint_key,
                    serialized,
                    ex=self._ttl,
                )
                return
            except (aioredis.ConnectionError, aioredis.TimeoutError, OSError) as exc:
                logger.warning(
                    "checkpoint_redis_unavailable_falling_back_to_memory",
                    extra={"error": str(exc), "key": checkpoint_key},
                )
                self._redis_available = False

        # Fallback to MemorySaver — wrap plain dict into LangGraph checkpoint format
        config = self._make_config(checkpoint_key)
        channel_versions: dict[str, int] = {
            key: int(uuid.uuid4().hex[:8], 16) for key in state_data
        }
        checkpoint_dict: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "channel_values": state_data,
            "channel_versions": channel_versions,
            "parents": [],
        }
        await self._memory_saver.aput(
            config,
            cast(Checkpoint, checkpoint_dict),
            {},
            cast(dict[str, str | int | float], channel_versions),
        )

    async def load(self, checkpoint_key: str) -> dict[str, Any] | None:
        """Load a checkpoint. Falls back to MemorySaver on Redis failure."""
        if self._redis_available and self._redis is not None:
            try:
                raw = await self._redis.get(checkpoint_key)
                if raw is None:
                    return None
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                return json.loads(raw)
            except (aioredis.ConnectionError, aioredis.TimeoutError, OSError) as exc:
                logger.warning(
                    "checkpoint_redis_unavailable_falling_back_to_memory",
                    extra={"error": str(exc), "key": checkpoint_key},
                )
                self._redis_available = False

        # Fallback to MemorySaver — extract channel_values from checkpoint
        config = self._make_config(checkpoint_key)
        tuple_result = await self._memory_saver.aget_tuple(config)
        if tuple_result and tuple_result.checkpoint:
            cv = tuple_result.checkpoint.get("channel_values")
            if cv is not None:
                return cv
        return None

    async def cleanup_expired(self) -> int:
        """Periodic cleanup of expired checkpoints.

        Returns the number of keys scanned (Redis) or skipped (fallback).
        """
        if self._redis_available and self._redis is not None:
            try:
                keys = await self._redis.keys("webfetch_checkpoint:*")
                scanned = len(keys)
                # Redis TTL handles expiration automatically; manual scan
                # is useful for monitoring / proactive eviction.
                for key in keys:
                    ttl_remaining = await self._redis.ttl(key)
                    if ttl_remaining <= 0:
                        await self._redis.delete(key)
                return scanned
            except (aioredis.ConnectionError, aioredis.TimeoutError, OSError):
                self._redis_available = False
                logger.warning(
                    "checkpoint_redis_unavailable_falling_back_to_memory",
                    extra={"operation": "cleanup"},
                )

        logger.debug("checkpoint_cleanup_skipped_redis_unavailable")
        return 0

    async def reset(self) -> None:
        """Reset the in-memory fallback store."""
        # Delete all threads from MemorySaver storage
        thread_ids = list(self._memory_saver.storage.keys())
        for thread_id in thread_ids:
            await self._memory_saver.adelete_thread(thread_id)

    @property
    def is_redis_available(self) -> bool:
        """Whether Redis is currently considered available."""
        return self._redis_available

    def restore_redis(self) -> None:
        """Mark Redis as available again (for reconnection scenarios)."""
        self._redis_available = True


def create_checkpoint_store(
    redis_client: aioredis.Redis | None,
    settings: Settings | None = None,
) -> RedisCheckpointStore:
    """Factory for RedisCheckpointStore."""
    return RedisCheckpointStore(redis_client, settings)
