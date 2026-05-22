"""pytest configuration for integration tests (uv + FastMCP 3.x)."""

import asyncio

import pytest
import pytest_asyncio
from fastmcp import Client


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for async tests."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def settings():
    """Test settings (Pydantic Settings from core/config)."""
    from app.core.config import Settings

    return Settings()


@pytest_asyncio.fixture(scope="module")
async def mcp_client():
    """FastMCP client for integration tests (HTTP transport)."""
    async with Client("http://127.0.0.1:8000/mcp") as client:
        yield client


@pytest_asyncio.fixture
async def search_request():
    """Example payload for search tests."""
    return {
        "query": "test query",
        "max_results": 3,
        "region": "wt-wt",
        "language": None,
        "filter_blacklist": True,
        "calculate_quality": True,
        "apply_smart_filter": True,
    }
