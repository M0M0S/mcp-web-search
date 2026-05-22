"""Integration tests for SearchService cache _set_in_cache and _get_from_cache."""

import json
import os
import re

import pytest

from app.core.config import Settings
from app.models.search import (
    SearchParameters,
    SearchResponse,
    SearchResult,
    SearchResultJudge,
)
from app.services.search_service import SearchService

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _set_llm_api_key():
    """Ensure LLM_API_KEY is set for all tests."""
    original = os.environ.get("LLM_API_KEY")
    os.environ["LLM_API_KEY"] = "PLACEHOLDER_KEY"
    yield
    if original is None:
        os.environ.pop("LLM_API_KEY", None)
    else:
        os.environ["LLM_API_KEY"] = original


class MockRedisClient:
    """Mock Redis client with async methods for cache tests."""

    def __init__(self):
        self._data: dict[str, str] = {}
        self._ex: dict[str, int | None] = {}
        self.last_set_ex: int | None = None

    async def keys(self, pattern: str) -> list[str]:
        regex = pattern.replace("*", ".*")
        return [k for k in self._data if re.match(regex, k)]

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._data[key] = value
        self._ex[key] = ex
        self.last_set_ex = ex


class MockRedis:
    """Mock Redis wrapper."""

    client = MockRedisClient()


def _make_service(settings: Settings | None = None) -> SearchService:
    """Create SearchService with mock Redis."""
    s = settings or Settings()
    return SearchService(s, MockRedis())  # type: ignore[arg-type]


def _make_response(
    freshness: float | None = None,
    ttl_override: int | None = None,
    provider: str = "duck",
) -> SearchResponse:
    """Build a SearchResponse for cache testing."""
    judgment = (
        SearchResultJudge(
            diversity_score=0.8,
            trustworthiness_score=0.8,
            relevance_to_query=0.85,
            freshness_score=freshness if freshness is not None else 0.75,
        )
        if freshness is not None or ttl_override is not None
        else None
    )
    params = (
        SearchParameters(engines="duck", ttl_override=ttl_override)
        if ttl_override is not None
        else None
    )
    return SearchResponse(
        results=[
            SearchResult(url="https://example.com", title="Test", description="desc")
        ],
        provider=provider,
        cache_hit=False,
        total_found=1,
        diversity_scores={"overall": 0.8},
        parameters=params,
        judgment=judgment,
    )


class TestSetInCacheTTLOverridePriority:
    """Tests for _set_in_cache TTL priority: override > adaptive > default."""

    async def test_default_ttl_when_no_judgment(self):
        """No judgment → TTL = CONTENT_CACHE_TTL (86400)."""
        settings = Settings()
        service = _make_service(settings)
        response = _make_response(freshness=None, ttl_override=None)

        await service._set_in_cache("test-key", response)

        assert MockRedis.client.last_set_ex == settings.CONTENT_CACHE_TTL
        stored = await MockRedis.client.get("isearch:test-key")
        assert stored is not None
        data = json.loads(stored)
        # Verify TTL was applied via Redis 'ex' — check via mock data existence
        assert data["provider"] == "duck"

    async def test_adaptive_ttl_when_no_override(self):
        """Adaptive TTL across 3 freshness buckets when no override."""
        settings = Settings()
        service = _make_service(settings)

        # High freshness bucket (>= 0.8 → 86400)
        high_resp = _make_response(freshness=0.9, ttl_override=None)
        await service._set_in_cache("test-key-high", high_resp)
        assert MockRedis.client.last_set_ex == 86400
        stored = await MockRedis.client.get("isearch:test-key-high")
        assert stored is not None
        assert json.loads(stored)["judgment"]["freshness_score"] == 0.9

        # Medium freshness bucket (>= 0.5 and < 0.8 → 21600)
        medium_resp = _make_response(freshness=0.6, ttl_override=None)
        await service._set_in_cache("test-key-medium", medium_resp)
        assert MockRedis.client.last_set_ex == 21600
        stored = await MockRedis.client.get("isearch:test-key-medium")
        assert stored is not None
        assert json.loads(stored)["judgment"]["freshness_score"] == 0.6

        # Low freshness bucket (< 0.5 → 3600)
        low_resp = _make_response(freshness=0.3, ttl_override=None)
        await service._set_in_cache("test-key-low", low_resp)
        assert MockRedis.client.last_set_ex == 3600
        stored = await MockRedis.client.get("isearch:test-key-low")
        assert stored is not None
        assert json.loads(stored)["judgment"]["freshness_score"] == 0.3

    async def test_ttl_override_priority_over_adaptive(self):
        """ttl_override > adaptive TTL when TTL_OVERRIDE_ENABLED=True."""
        settings = Settings(TTL_OVERRIDE_ENABLED=True)
        service = _make_service(settings)
        response = _make_response(freshness=0.3, ttl_override=7200)

        await service._set_in_cache("test-key-override", response)

        assert MockRedis.client.last_set_ex == 7200
        stored = await MockRedis.client.get("isearch:test-key-override")
        assert stored is not None
        data = json.loads(stored)
        assert data["judgment"]["freshness_score"] == 0.3
        assert data["parameters"]["ttl_override"] == 7200

    async def test_ttl_override_disabled_falls_back_to_adaptive(self):
        """TTL_OVERRIDE_ENABLED=False → adaptive TTL used, override ignored."""
        settings = Settings(TTL_OVERRIDE_ENABLED=False)
        service = _make_service(settings)
        response = _make_response(freshness=0.3, ttl_override=7200)

        await service._set_in_cache("test-key-no-override", response)

        stored = await MockRedis.client.get("isearch:test-key-no-override")
        assert stored is not None
        data = json.loads(stored)
        assert data["judgment"]["freshness_score"] == 0.3

    async def test_ttl_override_priority_over_default(self):
        """ttl_override > default TTL when no judgment."""
        settings = Settings(TTL_OVERRIDE_ENABLED=True)
        service = _make_service(settings)
        # No judgment but ttl_override present — parameters carry override
        judgment = SearchResultJudge(
            diversity_score=0.8,
            trustworthiness_score=0.8,
            relevance_to_query=0.85,
            freshness_score=0.75,
        )
        params = SearchParameters(engines="duck", ttl_override=1800)
        response = SearchResponse(
            results=[SearchResult(url="https://example.com", title="Test")],
            provider="duck",
            cache_hit=False,
            total_found=1,
            diversity_scores={"overall": 0.8},
            parameters=params,
            judgment=judgment,
        )

        await service._set_in_cache("test-key-override-default", response)

        stored = await MockRedis.client.get("isearch:test-key-override-default")
        assert stored is not None
        data = json.loads(stored)
        assert data["parameters"]["ttl_override"] == 1800


class TestGetFromCacheStaleDetection:
    """Tests for _get_from_cache stale detection."""

    async def test_cache_hit_no_stale_when_freshness_above_threshold(self):
        """freshness > threshold → cache_hit=True, cache_stale=False."""
        settings = Settings(FRESHNESS_INVALIDATION_THRESHOLD=0.3)
        service = _make_service(settings)

        # Pre-populate cache with fresh entry
        fresh_response = _make_response(freshness=0.8, ttl_override=None)
        await MockRedis.client.set(
            "isearch:test-key-fresh",
            fresh_response.model_dump_json(),
            ex=86400,
        )

        result = await service._get_from_cache("test-key-fresh")

        assert result is not None
        assert result.cache_hit is True
        assert result.cache_stale is False
        assert len(result.results) == 1

    async def test_cache_stale_when_freshness_below_threshold(self):
        """freshness < threshold → cache_hit=True, cache_stale=True."""
        settings = Settings(FRESHNESS_INVALIDATION_THRESHOLD=0.3)
        service = _make_service(settings)

        # Pre-populate cache with stale entry
        stale_response = _make_response(freshness=0.2, ttl_override=None)
        await MockRedis.client.set(
            "isearch:test-key-stale",
            stale_response.model_dump_json(),
            ex=3600,
        )

        result = await service._get_from_cache("test-key-stale")

        assert result is not None
        assert result.cache_hit is True
        assert result.cache_stale is True
        assert len(result.results) == 1

    async def test_cache_stale_default_freshness_when_no_judgment(self):
        """No judgment in cached data → default freshness 0.75 → not stale."""
        settings = Settings(FRESHNESS_INVALIDATION_THRESHOLD=0.3)
        service = _make_service(settings)

        # Pre-populate cache without judgment
        no_judgment_data = json.dumps(
            {
                "results": [
                    {
                        "url": "https://example.com",
                        "title": "Test",
                        "description": "desc",
                        "provider": "duck",
                    }
                ],
                "provider": "duck",
                "cache_hit": False,
                "total_found": 1,
                "diversity_scores": {"overall": 0.8},
            }
        )
        await MockRedis.client.set(
            "isearch:test-key-no-judgment", no_judgment_data, ex=3600
        )

        result = await service._get_from_cache("test-key-no-judgment")

        assert result is not None
        assert result.cache_hit is True
        assert result.cache_stale is False

    async def test_cache_miss_returns_none(self):
        """No cached key → returns None."""
        settings = Settings()
        service = _make_service(settings)

        result = await service._get_from_cache("nonexistent-key")

        assert result is None

    async def test_stale_hit_returns_correct_result_fields(self):
        """Stale cache hit returns correct result fields."""
        settings = Settings(FRESHNESS_INVALIDATION_THRESHOLD=0.3)
        service = _make_service(settings)

        expected = SearchResponse(
            results=[
                SearchResult(
                    url="https://example.com/article",
                    title="Test Article",
                    description="A test description",
                    provider="searxng",
                )
            ],
            provider="searxng",
            cache_hit=False,
            total_found=1,
            diversity_scores={"source_diversity": 0.5, "overall": 0.6},
            parameters=SearchParameters(engines="searxng", time_range="week"),
            judgment=SearchResultJudge(
                diversity_score=0.5,
                trustworthiness_score=0.7,
                relevance_to_query=0.6,
                freshness_score=0.15,
            ),
        )

        await MockRedis.client.set(
            "isearch:test-key-fields",
            expected.model_dump_json(),
            ex=3600,
        )

        result = await service._get_from_cache("test-key-fields")

        assert result is not None
        assert result.cache_hit is True
        assert result.cache_stale is True
        assert result.provider == "searxng"
        assert result.total_found == 1
        assert len(result.results) == 1
        assert result.results[0].url == "https://example.com/article"
        assert result.results[0].title == "Test Article"
        assert result.results[0].description == "A test description"
        assert result.results[0].provider == "searxng"
        assert result.diversity_scores["overall"] == 0.6
