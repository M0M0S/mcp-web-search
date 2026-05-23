"""Main entry point - FastMCP server."""

from contextlib import asynccontextmanager

from fastmcp import FastMCP

from app.core.config import Settings
from app.core.logging import get_logger, setup_logging

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


setup_logging(_get_settings())

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Conditional auth setup
# ---------------------------------------------------------------------------

auth_provider = None
if _get_settings().auth_enabled:  # MCP_ENCRYPTION_KEY present and non-empty
    from fastmcp.server.auth import DebugTokenVerifier, MultiAuth

    from app.core.token_verifier import validate_token

    # Create DebugTokenVerifier with our validate callable
    debug_verifier = DebugTokenVerifier(
        validate=validate_token,  # returns bool
        required_scopes=["read"],
    )

    # Create MultiAuth combining debug verifier
    auth_provider = MultiAuth(verifiers=[debug_verifier])

# ---------------------------------------------------------------------------
# FastMCP lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(server: FastMCP):
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

    yield


# ---------------------------------------------------------------------------
# FastMCP server with optional auth
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name=_get_settings().MCP_NAME,
    version=_get_settings().MCP_VERSION,
    auth=auth_provider,  # None if no auth enabled
    lifespan=lifespan,
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
    # Warm cache on startup if URLs configured
    if _get_settings().WARM_CACHE_URLS:
        _warm_cache_sync(_get_settings())

    return mcp


def _warm_cache_sync(settings: Settings) -> None:
    """Sync cache warming via new event loop (avoids asyncio.run RuntimeError in nested loops)."""
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(warm_cache_on_startup(settings))
    finally:
        loop.close()


if __name__ == "__main__":
    # Run FastMCP server with HTTP transport (production)
    mcp.run(transport="http", host=_get_settings().MCP_HOST)
