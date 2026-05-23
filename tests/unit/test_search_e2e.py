"""E2E tests for full search fallback chain — mock providers, no external deps."""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.core.config import Provider, Settings
from app.core.llm_client import LLMClient
from app.models.search import SearchRequest, SearchResponse, SearchResult, SearchResultJudge
from app.models.judge import JudgeVerdict
from app.services.search_service import SearchService


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def mock_llm() -> LLMClient:
    """Return a fully-mocked LLMClient."""
    llm = MagicMock(spec=LLMClient)
    llm.judge_urls_with_content = AsyncMock(
        return_value=JudgeVerdict(
            score=0.85,
            diversity_score=0.8,
            trustworthiness_score=0.9,
            relevance_to_query=0.85,
            freshness_score=0.75,
            verdict="pass",
            reasons=["good results"],
        )
    )
    return llm


@pytest.fixture
def mock_redis() -> MagicMock:
    """Return a mock RedisClient."""
    redis = MagicMock()
    redis.client = MagicMock()
    redis.client.get = AsyncMock(return_value=None)
    redis.client.set = AsyncMock(return_value=True)
    return redis


@pytest.fixture
def search_service(mock_llm: LLMClient, mock_redis: MagicMock) -> SearchService:
    """Build a SearchService with mocked LLM and Redis."""
    settings = Settings()
    return SearchService(
        settings=settings,
        redis=mock_redis,
        llm_client=mock_llm,
    )


# ── T3: test_search_duckduckgo_success ───────────────────────────────────


@pytest.mark.asyncio
async def test_search_duckduckgo_success(search_service: SearchService):
    """Verify DuckDuckGo provider returns results successfully."""
    mock_results = [
        SearchResult(url="https://public-domain.org/1", title="T1", description="D1", provider="duck"),
        SearchResult(url="https://public-domain.org/2", title="T2", description="D2", provider="duck"),
    ]

    # Patch _search_duckduckgo directly to avoid asyncio.to_thread patching issues
    with patch.object(search_service, "_search_duckduckgo") as mock_ddg:
        mock_ddg.return_value = mock_results

        request = SearchRequest(query="test query")
        result = await search_service.search(request)

    assert len(result.results) == 2
    assert result.provider == Provider.duck
    assert result.cache_hit is False


# ── T3: test_search_duckduckgo_fallback_to_searxng ───────────────────────


@pytest.mark.asyncio
async def test_search_duckduckgo_fallback_to_searxng(search_service: SearchService):
    """Verify fallback from DuckDuckGo to SearxNG when DDGS fails."""
    # Make DuckDuckGo return None (failure)
    with patch.object(search_service, "_search_duckduckgo") as mock_ddg:
        mock_ddg.return_value = None

        # Make SearxNG return results
        with patch.object(search_service, "_search_searxng") as mock_searxng:
            mock_searxng.return_value = [
                SearchResult(url="https://searxng.example.com/1", title="S1", description="S1 desc", provider="searxng"),
            ]

            request = SearchRequest(query="test query")
            result = await search_service.search(request)

    assert len(result.results) == 1
    assert result.provider == Provider.searxng
    assert result.cache_hit is False


# ── T3: test_search_searxng_fallback_to_tavily ───────────────────────────


@pytest.mark.asyncio
async def test_search_searxng_fallback_to_tavily(search_service: SearchService):
    """Verify fallback from SearxNG to Tavily when SearxNG fails."""
    # Make DuckDuckGo and SearxNG fail
    with patch.object(search_service, "_search_duckduckgo") as mock_ddg:
        mock_ddg.return_value = None

        with patch.object(search_service, "_search_searxng") as mock_searxng:
            mock_searxng.return_value = None

            # Make Tavily return results AND include tavily in available providers
            with patch.object(search_service, "_search_tavily") as mock_tavily:
                mock_tavily.return_value = [
                    SearchResult(url="https://tavily.example.com/1", title="T1", description="T1 desc", provider="tavily"),
                ]

                # Patch available_providers property to include tavily
                with patch.object(
                    type(search_service.settings),
                    "available_providers",
                    new_callable=PropertyMock,
                    return_value=["duck", "searxng", "tavily"],
                ):
                    request = SearchRequest(query="test query", skip_judge=True)
                    result = await search_service.search(request)

    assert len(result.results) == 1
    assert result.provider == Provider.tavily
    assert result.cache_hit is False


# ── T3: test_search_all_excluded_judge_reject ────────────────────────────


@pytest.mark.asyncio
async def test_search_all_excluded_judge_reject(search_service: SearchService):
    """Verify that all providers returning results but LLM judge rejects raises SearchError."""
    mock_results = [
        SearchResult(url="https://example.com/1", title="T1", description="D1", provider="duck"),
    ]

    # Make LLM judge reject all results
    search_service.llm.judge_urls_with_content = AsyncMock(
        return_value=SearchResultJudge(
            diversity_score=0.3,
            trustworthiness_score=0.4,
            relevance_to_query=0.3,
            freshness_score=0.75,
            score=0.0,
            verdict="reject",
            reasons=["not relevant"],
        )
    )

    with patch.object(search_service, "_search_duckduckgo") as mock_ddg:
        mock_ddg.return_value = mock_results

    with patch.object(search_service, "_search_searxng") as mock_searxng:
        mock_searxng.return_value = mock_results

    with patch.object(search_service, "_search_tavily") as mock_tavily:
        mock_tavily.return_value = mock_results

    request = SearchRequest(query="test query")

    with pytest.raises(Exception) as exc_info:
        await search_service.search(request)

    assert "No relevant results found" in str(exc_info.value)


# ── T3: test_search_cache_hit ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_cache_hit(search_service: SearchService, mock_redis: MagicMock):
    """Verify cache hit returns cached results without calling providers."""
    cached_data = {
        "results": [
            {"url": "https://cached.example.com/1", "title": "Cached T1", "description": "Cached D1", "provider": "duck"},
        ],
        "provider": "duck",
        "cache_hit": True,
        "total_found": 1,
        "diversity_scores": {"overall": 0.8},
        "judgment": {"diversity_score": 0.8, "trustworthiness_score": 0.9, "relevance_to_query": 0.85, "freshness_score": 0.9, "score": 0.85, "verdict": "pass", "reasons": ["cached"]},
        "cache_stale": False,
    }

    mock_redis.client.get = AsyncMock(return_value=SearchResponse(**cached_data).model_dump_json())

    request = SearchRequest(query="test query")
    result = await search_service.search(request)

    assert result.cache_hit is True
    assert len(result.results) == 1
    # Providers should NOT have been called
    with patch.object(search_service, "_search_duckduckgo") as mock_ddg:
        mock_ddg.return_value = [SearchResult(url="https://example.com/1", title="T", description="D", provider="duck")]
        await search_service.search(SearchRequest(query="test query"))
        mock_ddg.assert_not_called()


# ── T3: test_search_cache_stale_invalidation ─────────────────────────────


@pytest.mark.asyncio
async def test_search_cache_stale_invalidation(search_service: SearchService, mock_redis: MagicMock):
    """Verify stale cache is served but marked as stale."""
    cached_data = {
        "results": [
            {"url": "https://stale.example.com/1", "title": "Stale T1", "description": "Stale D1", "provider": "duck"},
        ],
        "provider": "duck",
        "cache_hit": True,
        "total_found": 1,
        "diversity_scores": {"overall": 0.8},
        "judgment": {"diversity_score": 0.8, "trustworthiness_score": 0.9, "relevance_to_query": 0.85, "freshness_score": 0.29, "score": 0.85, "verdict": "pass", "reasons": ["stale"]},
        "cache_stale": True,
    }

    mock_redis.client.get = AsyncMock(return_value=SearchResponse(**cached_data).model_dump_json())

    request = SearchRequest(query="test query")
    result = await search_service.search(request)

    assert result.cache_hit is True
    assert result.cache_stale is True


# ── T3: test_search_blacklist_filtering ──────────────────────────────────


@pytest.mark.asyncio
async def test_search_blacklist_filtering(search_service: SearchService):
    """Verify blacklist filtering removes blacklisted URLs."""
    mock_results = [
        SearchResult(url="https://good.example.com/1", title="Good", description="G", provider="duck"),
        SearchResult(url="https://blacklisted.example.com/1", title="Bad", description="B", provider="duck"),
    ]

    # Patch both DuckDuckGo and broader query path to prevent real API calls
    with patch.object(search_service, "_search_duckduckgo") as mock_ddg:
        mock_ddg.return_value = mock_results

        # Also patch broader query generation to prevent fallback
        with patch.object(search_service.llm, "generate_search_queries") as mock_gen_queries:
            mock_gen_queries.return_value = []  # No broader queries

            # Set blacklist in settings (add to existing default)
            original_blacklist = search_service.settings.BLACKLIST_DOMAINS
            search_service.settings.BLACKLIST_DOMAINS = ["example.com", "blacklisted.example.com"]

            try:
                request = SearchRequest(query="test query", filter_blacklist=True)
                result = await search_service.search(request)

                assert len(result.results) == 1
                assert result.results[0].url == "https://good.example.com/1"
            finally:
                search_service.settings.BLACKLIST_DOMAINS = original_blacklist


# ── T3: test_search_quality_score_threshold ──────────────────────────────


@pytest.mark.asyncio
async def test_search_quality_score_threshold(search_service: SearchService):
    """Verify quality score threshold filters low-quality results."""
    mock_results = [
        SearchResult(url="https://high-quality.example.com/1", title="High Quality", description="Good content", provider="duck"),
        SearchResult(url="https://low-quality.xyz/blog/post", title="You won't believe this!", description="", provider="duck"),
    ]

    # Patch both DuckDuckGo and broader query path to prevent real API calls
    with patch.object(search_service, "_search_duckduckgo") as mock_ddg:
        mock_ddg.return_value = mock_results

        # Also patch broader query generation to prevent fallback
        with patch.object(search_service.llm, "generate_search_queries") as mock_gen_queries:
            mock_gen_queries.return_value = []  # No broader queries

            request = SearchRequest(query="test query", calculate_quality=True)
            result = await search_service.search(request)

    # Low-quality result should be filtered out (SEO spam + clickbait)
    assert len(result.results) == 1
    assert "xyz" not in result.results[0].url.lower()
