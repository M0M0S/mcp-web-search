"""Content extraction service with SSRF protection."""

from typing import TYPE_CHECKING

import redis

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.metrics import record_cache_hit_with_stale, record_cache_ttl
from app.core.ssrf import SSRFConnectionError, ssrf_protection
from app.models.content import CleanContent, ContentMetadata

if TYPE_CHECKING:
    from app.core.dependencies import RedisClient


logger = get_logger(__name__)


class ContentService:
    """Content extraction service with SSRF protection and fallback chain."""

    def __init__(self, settings: Settings, redis: "RedisClient | None" = None):
        self.settings = settings
        self.redis = redis
        # In-memory cache with LRU eviction as fallback when Redis is unavailable
        from collections import OrderedDict

        self._in_memory_cache: OrderedDict[str, tuple[CleanContent, float]] = (
            OrderedDict()
        )
        self._max_cache_size = 1000

    async def extract_content(
        self, url: str, ttl_override: int | None = None
    ) -> CleanContent:
        """Extract clean content from URL with SSRF protection and adaptive TTL."""
        try:
            # Validate and fetch with SSRF protection BEFORE cache check
            content_bytes = await ssrf_protection.fetch_async(url)

            # Check cache first (after validation)
            cached = await self._get_from_cache(url)
            if cached:
                return cached

            # Extract text with fallback chain
            text = self._extract_text(content_bytes)

            # Sanitize with bleach
            from bleach import clean

            sanitized = clean(text, tags=[], strip=True)

            # Truncate to token limit
            truncated = self._truncate(sanitized, self.settings.TOKEN_LIMIT)

            # Create metadata
            metadata = ContentMetadata(
                source_url=url,
                extract_method="trafilatura",
                is_cached=False,
                token_count=len(truncated.split()),
            )

            result = CleanContent(
                text=truncated,
                metadata=metadata,
                is_truncated=len(sanitized) > self.settings.TOKEN_LIMIT,
                html_cleaned=True,
            )

            # Always cache result (Redis if available, else in-memory)
            await self._set_in_cache(url, result)

            return result
        except SSRFConnectionError as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            raise  # Re-raise for proper error handling in tool
        except ValueError as e:
            logger.warning(f"Invalid URL {url}: {e}")
            raise  # Re-raise for proper error handling in tool
        except Exception as e:
            logger.error(f"Unexpected error fetching {url}: {e}")
            raise

    def _extract_text(self, content_bytes: bytes) -> str:
        """Extract text from HTML with fallback chain."""
        # Try trafilatura first
        try:
            import trafilatura

            html = content_bytes.decode("utf-8")
            text = trafilatura.extract(html, include_images=False)
            if text:
                return text
        except Exception:
            pass

        # Fallback to readability-lxml
        try:
            from readability import Document

            html = content_bytes.decode("utf-8")
            doc = Document(html)
            text = doc.title + "\n\n" + doc.content
            if text:
                return text
        except Exception:
            pass

        # Final fallback to BeautifulSoup
        try:
            from bs4 import BeautifulSoup

            html = content_bytes.decode("utf-8")
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text()
            if text:
                return text
        except Exception:
            pass

        return ""

    def _truncate(self, text: str, token_limit: int) -> str:
        """Truncate text to token limit."""
        tokens = text.split()
        if len(tokens) <= token_limit:
            return text

        truncated = " ".join(tokens[:token_limit])
        return truncated + "..."

    def compute_adaptive_ttl(self, freshness_score: float) -> int:
        """Compute adaptive TTL based on freshness score.

        Uses Settings.ADAPTIVE_TTL_RANGES for configurability (AC 9).
        """
        ranges = self.settings.ADAPTIVE_TTL_RANGES

        # Check high freshness bucket
        high_range = ranges.get("high", (0.8, 86400))
        if freshness_score >= high_range[0]:
            return high_range[1]

        # Check medium freshness bucket
        medium_range = ranges.get("medium", (0.5, 21600))
        if freshness_score >= medium_range[0]:
            return medium_range[1]

        # Low freshness bucket
        low_range = ranges.get("low", (0.0, 3600))
        return low_range[1]

    async def _get_from_cache(self, url: str) -> CleanContent | None:
        """Get content from cache (Redis or in-memory LRU) with stale detection (AC 14)."""
        import time

        # Check Redis first if available
        if self.redis:
            try:
                cached = await self.redis.client.get(f"content:{url}")
                if cached:
                    import json

                    data = json.loads(cached)
                    metadata = data.get("metadata", {})
                    freshness = metadata.get("freshness_score", 0.75)

                    # Check freshness against invalidation threshold (AC 12)
                    is_stale = (
                        freshness < self.settings.FRESHNESS_INVALIDATION_THRESHOLD
                    )

                    if is_stale:
                        record_cache_hit_with_stale(cache_type="content")
                        logger.warning(
                            "served_stale_content_cache",
                            url=url,
                            freshness_score=freshness,
                            threshold=self.settings.FRESHNESS_INVALIDATION_THRESHOLD,
                        )

                    # Mark metadata as stale if applicable
                    metadata["cache_stale"] = is_stale

                    return CleanContent(
                        text=data["text"],
                        metadata=ContentMetadata(**metadata),
                        is_truncated=data.get("is_truncated", False),
                        html_cleaned=data.get("html_cleaned", True),
                    )
            except (redis.RedisError, TypeError, ValueError):
                pass

        # Check LRU cache first (recent entries)
        if url in self._in_memory_cache:
            content, expiry = self._in_memory_cache[url]
            if time.time() < expiry:
                # Move to end for LRU
                self._in_memory_cache.move_to_end(url)
                return content
            else:
                # Expired - remove
                del self._in_memory_cache[url]

        return None

    async def _set_in_cache(
        self, url: str, content: CleanContent, ttl_override: int | None = None
    ) -> None:
        """Set content in cache (Redis if available, else LRU in-memory) with adaptive TTL (AC 8)."""
        import time

        # Determine TTL: override > adaptive > default
        ttl = self.settings.CONTENT_CACHE_TTL

        if content.metadata.freshness_score is not None:
            ttl = self.compute_adaptive_ttl(content.metadata.freshness_score)

        # Apply TTL override if provided
        if ttl_override is not None:
            ttl = ttl_override

        # Record metrics (AC 16)
        bucket = "high" if ttl >= 86400 else "medium" if ttl >= 21600 else "low"
        record_cache_ttl(bucket=bucket, ttl_seconds=float(ttl))

        # Try Redis first if available
        if self.redis:
            try:
                await self.redis.client.set(
                    f"content:{url}",
                    content.model_dump_json(),
                    ex=ttl,
                )
                return  # Success - cached to Redis
            except (redis.RedisError, TypeError):
                pass

        # Fall back to LRU in-memory cache
        # Evict oldest entries if at capacity
        if len(self._in_memory_cache) >= self._max_cache_size:
            self._in_memory_cache.popitem(last=False)

        expiry = time.time() + ttl
        self._in_memory_cache[url] = (content, expiry)
