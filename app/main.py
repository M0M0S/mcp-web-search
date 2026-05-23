"""Main entry point - FastMCP server."""

import asyncio
from contextlib import asynccontextmanager

from fastmcp import FastMCP

from app.core.config import Settings
from app.core.logging import get_logger, setup_logging
from app.core.provider_registry import ProviderRegistry
from app.core.llm_client import create_llm_client
from app.core.ssrf import ssrf_protection

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
    """Flush Redis counters to DB on shutdown and warm cache on startup."""
    from app.core.rate_limiter import flush_counters_to_db
    from app.core.token_cost_tracker import flush_counters_to_db as flush_tokens

    # Warm cache on startup (non-blocking)
    try:
        asyncio.create_task(_warm_cache_async(_get_settings()))
    except Exception:
        logger.warning("cache_warm_task_failed", exc_info=True)

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

    # Persist provider health state
    try:
        settings = Settings()
        registry = ProviderRegistry(settings)
        await registry.health_tracker.persist_health()
    except Exception:
        logger.warning("provider_health_persist_failed", exc_info=True)

    # Persist LLM health state
    try:
        settings = Settings()
        llm = create_llm_client(redis_client=None, settings=settings)
        await llm.health_tracker.persist_health()
    except Exception:
        logger.warning("llm_health_persist_failed", exc_info=True)

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
# Prometheus metrics tool
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_prometheus_metrics() -> str:
    """Return Prometheus metrics in text exposition format."""
    from app.core.metrics import get_metrics_bytes

    return get_metrics_bytes().decode("utf-8")


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
    return mcp


async def _warm_cache_async(settings: Settings) -> None:
    """Async cache warming — non-blocking startup."""
    if not settings.WARM_CACHE_URLS:
        return
    for url in settings.WARM_CACHE_URLS:
        try:
            await ssrf_protection.fetch_async(url)
        except Exception:
            logger.warning("cache_warm_failed", url=url)


if __name__ == "__main__":
    # Run FastMCP server with HTTP transport (production)
    mcp.run(transport="http", host=_get_settings().MCP_HOST)
