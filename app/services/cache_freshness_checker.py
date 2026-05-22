"""CacheFreshnessChecker — background task for periodic cache freshness evaluation.

Periodically scans cached entries, identifies stale content (freshness_score <
threshold), invalidates them from cache, and records Prometheus metrics.
Runs as a background asyncio task with configurable interval.
"""

import asyncio
import json
from typing import TYPE_CHECKING

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.metrics import record_cache_stale_invalidation

if TYPE_CHECKING:
    from app.core.dependencies import RedisClient


logger = get_logger("cache-freshness-checker")


class CacheFreshnessChecker:
    """Background task that periodically checks and invalidates stale cache entries.

    AC 11: Background task periodic check freshness
    AC 12: Content with freshness_score < threshold marked for invalidation
    AC 13: Invalidation — remove from cache + mark cache_stale=true in metadata
    AC 15: Invalidation interval configurable (default: 3600s)
    """

    def __init__(
        self,
        settings: Settings,
        redis: "RedisClient",
    ):
        self.settings = settings
        self.redis = redis
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background freshness checker task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "cache_freshness_checker_started",
            interval=self.settings.CACHE_INVALIDATION_INTERVAL,
            threshold=self.settings.FRESHNESS_INVALIDATION_THRESHOLD,
        )

    async def stop(self) -> None:
        """Stop the background freshness checker task."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("cache_freshness_checker_stopped")

    async def _run_loop(self) -> None:
        """Main loop: periodically check freshness and invalidate stale entries."""
        interval = self.settings.CACHE_INVALIDATION_INTERVAL
        threshold = self.settings.FRESHNESS_INVALIDATION_THRESHOLD

        while self._running:
            try:
                await self._check_and_invalidate(threshold)
            except Exception as e:
                logger.error(
                    "cache_freshness_checker_error",
                    error=type(e).__name__,
                    error_message=str(e),
                )

            await asyncio.sleep(interval)

    async def _check_and_invalidate(self, threshold: float) -> int:
        """Scan cache keys, identify stale entries, invalidate them.

        Returns the number of invalidated entries.
        """
        invalidated_count = 0

        # Check search cache keys (isearch:*)
        search_keys = await self.redis.client.keys("isearch:*")
        for key in search_keys:
            raw = await self.redis.client.get(key)
            if raw is None:
                continue

            try:
                data = json.loads(raw)
                judgment = data.get("judgment")
                if judgment is None:
                    continue

                freshness = judgment.get("freshness_score", 0.75)
                if freshness < threshold:
                    # Invalidate: remove from cache (AC 13)
                    await self.redis.client.delete(key)
                    invalidated_count += 1
                    record_cache_stale_invalidation(cache_type="search")
                    logger.info(
                        "cache_stale_invalidated",
                        cache_type="search",
                        key=key.decode(),
                        freshness_score=freshness,
                        threshold=threshold,
                    )
            except (json.JSONDecodeError, TypeError):
                continue

        # Check content cache keys (content:*)
        content_keys = await self.redis.client.keys("content:*")
        for key in content_keys:
            raw = await self.redis.client.get(key)
            if raw is None:
                continue

            try:
                data = json.loads(raw)
                metadata = data.get("metadata", {})
                freshness = metadata.get("freshness_score", 0.75)
                if freshness < threshold:
                    # Invalidate: remove from cache (AC 13)
                    await self.redis.client.delete(key)
                    invalidated_count += 1
                    record_cache_stale_invalidation(cache_type="content")
                    logger.info(
                        "cache_stale_invalidated",
                        cache_type="content",
                        key=key.decode(),
                        freshness_score=freshness,
                        threshold=threshold,
                    )
            except (json.JSONDecodeError, TypeError):
                continue

        if invalidated_count > 0:
            logger.info(
                "cache_freshness_check_complete",
                invalidated_count=invalidated_count,
                threshold=threshold,
            )

        return invalidated_count
