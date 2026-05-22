"""MCP tool for webfetch agent (LangGraph)."""

from typing import Literal

from app.core.logging import get_logger
from app.models.webfetch import WebFetchState
from app.services.content_service import ContentService
from app.services.output_formatter import OutputFormatter
from app.services.search_service import SearchService
from app.services.webfetch_service import WebFetchService

logger = get_logger(__name__)


async def webfetch(
    prompt: str,
    tenant_id: str = "default",
    gen_srch_q_cnt: int = 5,  # Stage 1: number of generated queries (3-10)
    sel_top_level: int = 20,  # Stage 3-4: number of URLs to select (5-50)
    output_format: Literal["markdown", "json"] = "markdown",
) -> dict | str:
    """
    Execute webfetch agent with LangGraph StateGraph and LLM-as-Judge.

    This tool implements a multi-stage agentic search process:
    1. Generate search queries via LLM (with variable count)
    2. Search by main query FIRST, then by additional queries
    3. Select URLs for content extraction (variable amount)
    4. Judge URL quality via LLM-as-Judge
    5. Fetch content from ALL selected URLs after deduplication
    6. Score and select sources with features
    7. Aggregate final result

    Args:
        prompt: The search query/prompt (1-1000 characters)
        tenant_id: Tenant identifier for cache isolation (default: "default")
        gen_srch_q_cnt: Number of search queries to generate (3-10, default: 5)
        sel_top_level: Number of URLs to select (5-50, default: 20)
        output_format: Output format for results (markdown or json, default: markdown)

    Returns:
        Dictionary with agent results (json format) or markdown string (markdown format)
    """
    from app.core.config import Settings
    from app.core.dependencies import get_redis, init_redis

    settings = Settings()
    redis_client = get_redis(settings.REDIS_URL)
    await init_redis(settings)

    # Initialize services
    search_service = SearchService(settings, redis_client)
    content_service = ContentService(settings, redis_client)
    webfetch_service = WebFetchService(
        settings, search_service, content_service, redis_client
    )

    try:
        result = await webfetch_service.execute(
            prompt, tenant_id, gen_srch_q_cnt, sel_top_level
        )

        logger.debug("webfetch_result", success=result.get("success", False))

        state = result.get("state", {})
        if isinstance(state, dict):
            webfetch_state = WebFetchState.model_validate(state)
        else:
            webfetch_state = state

        sources = result.get("sources", [])

        logger.info("output_format_set", format=output_format)

        if output_format == "markdown":
            return OutputFormatter.format_markdown_webfetch(
                prompt, webfetch_state, sources
            )

        return OutputFormatter.format_json_webfetch(
            prompt, webfetch_state, sources
        ).model_dump()
    except Exception as e:
        logger.exception("webfetch_failed", error=type(e).__name__)

        return {
            "success": False,
            "state": {},
            "result": f"Error: {str(e)[:200]}",
            "sources": [],
        }
