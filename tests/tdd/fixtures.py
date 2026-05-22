"""TDD fixtures - Mock services for testing without real dependencies."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class MockRedis:
    """Mock Redis client for TDD tests. Returns None (cache miss) by default."""

    def __init__(self):
        self.client = self
        self._cache: dict[str, str] = {}

    async def get(self, key: str):
        # Return None for cache miss (now handled by in-memory cache)
        return self._cache.get(key)

    async def set(self, key: str, value: str, **kwargs):
        """Mock Redis set operation."""
        self._cache[key] = value


class MockSearchService:
    """Mock SearchService for TDD tests. Raises NotImplementedError on call."""

    async def search(self, request):
        raise NotImplementedError(
            "Search service not implemented yet — this is a Red-Green TDD test."
            "The test should FAIL before implementation (NotImplementedError) and PASS after."
        )


class MockContentService:
    """Mock ContentService for TDD tests. Raises NotImplementedError on call."""

    async def extract_content(self, url: str) -> None:
        raise NotImplementedError(
            "Content extraction service not implemented yet — this is a Red-Green TDD test."
            "The test should FAIL before implementation (NotImplementedError) and PASS after."
        )


@pytest.fixture
def mock_llm_client():
    """Mock LLM client with all pipeline methods for TDD tests.

    Patches create_llm_client at module level so WebFetchService can be
    instantiated without LLM_API_KEY.
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

    with pytest.MonkeyPatch.context() as mp:
        import app.core.llm_client as llm_module

        mp.setattr(llm_module, "create_llm_client", lambda **kwargs: llm)
        yield llm
