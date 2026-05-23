"""MCP tool for search functionality."""

from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    pass

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.search import SearchRequest
from app.services.output_formatter import OutputFormatter
from app.services.search_service import SearchService

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


async def search(
    query: str,
    max_results: int = 10,
    region: str = "wt-wt",
    language: Optional[str] = None,
    filter_blacklist: bool = True,
    calculate_quality: bool = True,
    apply_smart_filter: bool = True,
    engines: Optional[str] = None,
    time_range: Optional[Literal["day", "week", "month", "year"]] = None,
    site: Optional[str] = None,
    skip_judge: bool = False,
    output_format: Literal["markdown", "json"] = "markdown",
) -> dict | str:
    """
    Search the internet for information with fallback chain and smart filtering.

    This tool performs a comprehensive search across multiple providers (DuckDuckGo,
    SearxNG, Tavily, Google) using a fallback strategy. It applies intelligent filtering to
    remove low-quality results, SEO spam, and blacklisted domains.

    Args:
        query: The search query string (1-1000 characters)
        max_results: Maximum number of results to return (1-50, default: 10)
        region: Region code for search (default: "wt-wt" - world)
        language: Language code for search (optional, auto-detected if None)
        filter_blacklist: Filter out blacklisted domains (default: True)
        calculate_quality: Calculate quality scores for results (default: True)
        apply_smart_filter: Apply SEO spam and clickbait filtering (default: True)
        engines: Comma-separated list of provider names to search (optional)
        time_range: Time range filter for search (day, week, month, year - optional)
        site: Domain restriction for search (optional)
        skip_judge: Skip LLM-as-Judge relevance check (default: False)
        output_format: Output format for results (markdown or json, default: markdown)

    Returns:
        Dictionary with search results (json format) or markdown string (markdown format)
    """
    from app.core.dependencies import get_redis

    settings = _get_settings()
    redis_client = get_redis(settings.REDIS_URL)
    # Initialize Redis connection before use (only if not already connected)
    if not hasattr(redis_client, "_client") or redis_client._client is None:
        await redis_client.connect()
    search_service = SearchService(settings, redis_client)

    request = SearchRequest(
        query=query,
        max_results=max_results,
        region=region,
        language=language,
        filter_blacklist=filter_blacklist,
        calculate_quality=calculate_quality,
        apply_smart_filter=apply_smart_filter,
        engines=engines,
        time_range=time_range,
        site=site,
        skip_judge=skip_judge,
        output_format=output_format,
    )

    try:
        response = await search_service.search(request)

        logger.info("output_format_set", format=output_format)

        if output_format == "markdown":
            return OutputFormatter.format_markdown_search(request, response)

        return OutputFormatter.format_json_search(response, query).model_dump()
    except Exception as e:
        logger.error("search_failed", error=type(e).__name__, message=str(e))
        return {
            "status": "error",
            "message": str(e),
        }
