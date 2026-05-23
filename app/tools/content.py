"""MCP tool for content extraction."""

from app.core.config import Settings
from app.core.logging import get_logger
from app.services.content_service import ContentService

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Settings singleton (lazy init, shared across modules)
# ---------------------------------------------------------------------------

_settings: Settings | None = None


def _get_settings() -> Settings:
    """Return module-level Settings singleton (lazy-init, cached)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


async def content(url: str) -> dict:
    """
    Extract clean text content from a URL with SSRF protection and fallback chain.

    This tool safely fetches web pages, extracts clean text content using multiple
    extraction methods (Trafilatura, readability-lxml, BeautifulSoup), applies
    HTML sanitization, and caches the results for performance.

    Args:
        url: The URL to extract content from (1-2048 characters)

    Returns:
        Dictionary with extracted text, metadata, and extraction status
    """
    from app.core.dependencies import get_redis

    settings = _get_settings()

    # Try Redis, but continue without it if unavailable
    redis = None
    try:
        redis = get_redis(settings.REDIS_URL)
        await redis.connect()
    except Exception:
        logger.warning("Redis not available, proceeding without caching")

    content_service = ContentService(settings, redis)

    try:
        result = await content_service.extract_content(url)

        return {
            "status": "success",
            "text": result.text,
            "metadata": result.metadata.model_dump(),
            "is_truncated": result.is_truncated,
        }
    except Exception as e:
        logger.error("content_extraction_failed", error=type(e).__name__)
        return {
            "status": "error",
            "message": str(e),
        }
