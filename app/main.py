"""Main entry point - FastMCP server."""

from fastmcp import FastMCP

from app.core.config import Settings
from app.core.logging import get_logger, setup_logging
from app.tools import content, search, webfetch, register_user_manage_tools

settings = Settings()
setup_logging(settings)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Conditional auth setup
# ---------------------------------------------------------------------------

auth_provider = None
if settings.auth_enabled:  # MCP_ENCRYPTION_KEY present and non-empty
    from fastmcp.server.auth import DebugTokenVerifier, MultiAuth

    from app.core.token_verifier import validate_token, get_admin_key_ids

    # Create DebugTokenVerifier with our validate callable
    debug_verifier = DebugTokenVerifier(
        validate=validate_token,  # returns bool
        required_scopes=["read"],
    )

    # Create MultiAuth combining debug verifier
    auth_provider = MultiAuth(verifiers=[debug_verifier])

# ---------------------------------------------------------------------------
# Shutdown lifecycle handler
# ---------------------------------------------------------------------------


from contextlib import asynccontextmanager


async def on_shutdown(server: FastMCP) -> None:
    """Flush Redis counters to DB on shutdown."""
    from app.core.rate_limiter import flush_counters_to_db
    from app.core.token_cost_tracker import flush_counters_to_db as flush_tokens

    # Flush rate limit counters to DB (sync remaining data)
    try:
        rl_result = await flush_counters_to_db()
        logger.info("shutdown_rate_limits_flushed", extra=rl_result)
    except Exception as e:
        logger.error("shutdown_rate_limits_flush_failed", extra={"error": str(e)})

    # Flush token cost counters to DB (sync remaining data)
    try:
        tc_result = await flush_tokens()
        logger.info("shutdown_token_costs_flushed", extra=tc_result)
    except Exception as e:
        logger.error("shutdown_token_costs_flush_failed", extra={"error": str(e)})


# ---------------------------------------------------------------------------
# FastMCP server with optional auth
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name=settings.MCP_NAME,
    version=settings.MCP_VERSION,
    auth=auth_provider,  # None if no auth enabled
    lifespan=on_shutdown,  # shutdown lifecycle handler
)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


async def warm_cache_on_startup(settings: Settings) -> None:
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


def create_app() -> FastMCP:
    """Create and return FastMCP server instance with optional cache warming."""
    import asyncio

    # Warm cache on startup if URLs configured
    if settings.WARM_CACHE_URLS:
        asyncio.run(warm_cache_on_startup(settings))

    return mcp


if __name__ == "__main__":
    # Run FastMCP server with HTTP transport (production)
    mcp.run(transport="http", host=settings.MCP_HOST)
