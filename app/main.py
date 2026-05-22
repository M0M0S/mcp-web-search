"""Main entry point - FastMCP server."""

from fastmcp import FastMCP

from app.core.config import Settings
from app.core.logging import get_logger, setup_logging
from app.tools import content, search, webfetch

settings = Settings()
setup_logging(settings)

logger = get_logger(__name__)

# Create FastMCP server (primary server)
mcp = FastMCP(
    name=settings.MCP_NAME,
    version=settings.MCP_VERSION,
)

# Register tools
mcp.add_tool(search)
mcp.add_tool(content)
mcp.add_tool(webfetch)


async def warm_cache_on_startup(settings):
    """Pre-populate cache on startup (cache warming)."""
    from app.core.dependencies import init_redis
    from app.services.content_service import ContentService

    redis = await init_redis(settings)
    content_service = ContentService(settings, redis)

    # Warm cache with configured URLs
    for url in settings.WARM_CACHE_URLS:
        try:
            # Silent failure - don't block startup if warming fails
            await content_service.extract_content(url)
        except Exception as e:
            logger.info(f"Cache warm failed for {url}: {e}")


def create_app():
    """Create and return FastMCP server instance with optional cache warming."""
    import asyncio

    # Warm cache on startup if URLs configured
    if settings.WARM_CACHE_URLS:
        asyncio.run(warm_cache_on_startup(settings))

    return mcp


if __name__ == "__main__":
    # Run FastMCP server with HTTP transport (production)
    mcp.run(transport="http", host=settings.MCP_HOST)
