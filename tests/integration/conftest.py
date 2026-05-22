"""pytest configuration for integration tests (uv + FastMCP 3.x)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.fixture
def mock_llm_client():
    """Mock LLM client with all pipeline methods.

    Returns a MagicMock LLM client. Tests should use this fixture with
    `patch` to override create_llm_client before WebFetchService instantiation.
    """
    from app.models.webfetch import FeatureSet, JudgeVerdict

    llm = MagicMock()
    llm.generate_search_queries = AsyncMock(
        return_value=["test query", "test query details", "test query examples"]
    )
    llm.judge_urls_with_content = AsyncMock(
        return_value=JudgeVerdict(score=0.9, verdict="pass", reasons=["relevant"])
    )
    llm.judge_urls = AsyncMock(
        return_value=JudgeVerdict(score=0.85, verdict="pass", reasons=["default"])
    )
    llm.rate_relevance = AsyncMock(
        return_value=JudgeVerdict(score=0.85, verdict="pass", reasons=[])
    )
    llm.generate_features = AsyncMock(
        return_value=FeatureSet(features=["f1", "f2"], sources=["u1"])
    )
    llm.judge_features = AsyncMock(
        return_value=JudgeVerdict(score=0.95, verdict="pass", reasons=["good"])
    )
    llm.generate_final_answer = AsyncMock(return_value="final answer")
    llm.select_urls = AsyncMock(
        return_value=[{"url": "https://example.com", "priority": 1, "reason": "test"}]
    )
    return llm


@pytest.fixture
def patch_create_llm_client(mock_llm_client):
    """Patch create_llm_client to return mock_llm_client.

    Must be used as a context manager before any WebFetchService/SearchService
    instantiation in the test.
    """
    with patch("app.core.llm_client.create_llm_client", return_value=mock_llm_client):
        yield mock_llm_client


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
