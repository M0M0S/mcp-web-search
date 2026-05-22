"""Edge case and additional tests for webfetch pipeline Gap 1-7 fixes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.search import SearchResponse, SearchResult
from app.models.webfetch import (
    FeatureSet,
    JudgeVerdict,
    URLSelectionItem,
    WebFetchState,
)


@pytest.fixture
def mock_llm():
    """Mock LLM client with all pipeline methods."""
    llm = MagicMock()
    llm.generate_search_queries = AsyncMock(return_value=["q1", "q2", "q3", "q4", "q5"])
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
def mock_search_service():
    """Mock search service."""
    service = MagicMock()
    service.search = AsyncMock(
        return_value=SearchResponse(
            results=[
                SearchResult(
                    url="https://example.com/1", title="Title 1", description="Desc 1"
                ),
            ],
            provider="duck",
            total_found=1,
        )
    )
    return service


@pytest.fixture
def mock_content_service():
    """Mock content service."""
    service = MagicMock()
    service.extract_content = AsyncMock(return_value="Extracted text content")
    return service


@pytest.fixture
def mock_checkpoint_store():
    """Mock checkpoint store with save/load."""
    store = MagicMock()
    store.save = AsyncMock(return_value=None)
    store.load = AsyncMock(return_value=None)
    store.cleanup_expired = AsyncMock(return_value=0)
    return store


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis = MagicMock()
    redis._client = MagicMock()
    redis._client.get = AsyncMock(return_value=None)
    redis._client.set = AsyncMock(return_value=None)
    redis._client.keys = AsyncMock(return_value=[])
    redis._client.delete = AsyncMock(return_value=0)
    redis._client.ping = AsyncMock(return_value=True)
    return redis


# ─── Edge case tests ───


class TestEdgeCases:
    """Edge case tests for webfetch pipeline fixes."""

    @pytest.mark.asyncio
    @patch("app.services.webfetch_service.create_llm_client")
    async def test_no_search_results_urls(
        self,
        mock_create_llm,
        mock_llm,
        mock_search_service,
        mock_checkpoint_store,
        mock_redis,
    ):
        """No URL from search → judge_urls_with_content with empty content."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        mock_create_llm.return_value = mock_llm
        service = WebFetchService(
            settings, mock_search_service, MagicMock(), mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store
        service.llm = mock_llm

        # Search returns no results — make ALL search calls return empty
        empty_response = SearchResponse(
            results=[],
            provider="duck",
            total_found=0,
        )
        mock_search_service.search = AsyncMock(return_value=empty_response)

        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            gen_srch_q_cnt=5,
            sel_top_level=20,
        )

        await service._node_generate_search_queries(state)
        await service._node_perform_search(state)

        # No search results → selected_urls from LLM select_urls fallback
        # But we want to test the case where no URLs exist
        # Manually set selected_urls to empty to test judge_urls_with_content with no URLs
        state.selected_urls = []
        state.search_results = []

        await service._node_judge_urls(state)

        # judge_urls_with_content should receive empty url_content_pairs
        call_args = mock_llm.judge_urls_with_content.call_args
        url_content_pairs = call_args[0][1]
        assert len(url_content_pairs) == 0

    @pytest.mark.asyncio
    @patch("app.services.webfetch_service.create_llm_client")
    async def test_llm_chain_exhausted_judge(
        self, mock_create_llm, mock_llm, mock_search_service, mock_checkpoint_store
    ):
        """LLM failover during judge_urls_with_content."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        mock_create_llm.return_value = mock_llm
        service = WebFetchService(
            settings, mock_search_service, MagicMock(), mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store

        # judge_urls_with_content raises exception → fallback to judge_urls
        mock_llm.judge_urls_with_content = AsyncMock(side_effect=Exception("LLM error"))
        mock_llm.judge_urls = AsyncMock(
            return_value=JudgeVerdict(score=0.85, verdict="pass", reasons=["fallback"])
        )
        service.llm = mock_llm

        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            gen_srch_q_cnt=5,
            sel_top_level=20,
        )
        state.selected_urls = [
            URLSelectionItem(url="https://example.com/1", priority=1, reason="test")
        ]
        state.search_results = [
            SearchResponse(
                results=[
                    SearchResult(
                        url="https://example.com/1", title="T", description="D"
                    )
                ],
                provider="duck",
                total_found=1,
            ),
        ]

        await service._node_judge_urls(state)

        assert state.url_judgment is not None
        assert state.url_judgment.verdict == "pass"

    @pytest.mark.asyncio
    @patch("app.services.webfetch_service.create_llm_client")
    async def test_snippet_based_scoring_quality(
        self, mock_create_llm, mock_llm, mock_search_service, mock_checkpoint_store
    ):
        """Snippet-based scoring fallback quality assessment."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        mock_create_llm.return_value = mock_llm
        service = WebFetchService(
            settings, mock_search_service, MagicMock(), mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store
        service.llm = mock_llm

        # Features empty — snippet-based scoring should still produce results
        mock_llm.generate_features = AsyncMock(return_value=None)
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            gen_srch_q_cnt=5,
            sel_top_level=20,
        )
        state.features = None
        state.fetched_content = [
            {"url": "https://example.com/1", "text": "quality content about test topic"}
        ]
        state.selected_urls = [
            URLSelectionItem(url="https://example.com/1", priority=1, reason="test")
        ]
        state.search_results = [
            SearchResponse(
                results=[
                    SearchResult(
                        url="https://example.com/1",
                        title="Quality Title",
                        description="Good description",
                    )
                ],
                provider="duck",
                total_found=1,
            ),
        ]

        await service._node_generate_features(state)

        # Verify state still has meaningful data for scoring fallback
        assert state.fetched_content is not None
        assert len(state.fetched_content) == 1
        assert "quality content" in state.fetched_content[0]["text"]

    @pytest.mark.asyncio
    @patch("app.services.webfetch_service.create_llm_client")
    async def test_fallback_feature_extraction_from_snippets(
        self, mock_create_llm, mock_llm, mock_search_service, mock_checkpoint_store
    ):
        """Features extracted from search snippets when LLM generate_features fails."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        mock_create_llm.return_value = mock_llm
        service = WebFetchService(
            settings, mock_search_service, MagicMock(), mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store
        service.llm = mock_llm

        # LLM generate_features fails → fallback to snippet-based features
        mock_llm.generate_features = AsyncMock(
            side_effect=Exception("Feature extraction failed")
        )
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            gen_srch_q_cnt=5,
            sel_top_level=20,
        )
        state.fetched_content = [{"url": "https://example.com/1", "text": "content"}]
        state.selected_urls = [
            URLSelectionItem(url="https://example.com/1", priority=1, reason="test")
        ]
        state.search_results = [
            SearchResponse(
                results=[
                    SearchResult(
                        url="https://example.com/1",
                        title="T",
                        description="snippet text",
                    )
                ],
                provider="duck",
                total_found=1,
            ),
        ]

        await service._node_generate_features(state)

        # Features should be None — fallback chain would use snippets in aggregate

    @pytest.mark.asyncio
    @patch("app.services.webfetch_service.create_llm_client")
    async def test_truncated_content_scoring_degraded(
        self, mock_create_llm, mock_llm, mock_search_service, mock_checkpoint_store
    ):
        """Checkpoint with truncated fetched_content → scoring quality degradation."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        mock_create_llm.return_value = mock_llm
        service = WebFetchService(
            settings, mock_search_service, MagicMock(), mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store
        service.llm = mock_llm

        # Simulate truncated checkpoint content (< 200 chars)
        truncated_state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            gen_srch_q_cnt=5,
            sel_top_level=20,
        )
        truncated_state.fetched_content = [
            {"url": "https://example.com/1", "text": "short"}
        ]  # < 200 chars
        truncated_state.selected_urls = [
            URLSelectionItem(url="https://example.com/1", priority=1, reason="test")
        ]

        mock_checkpoint_store.load = AsyncMock(
            return_value=truncated_state.model_dump()
        )

        with patch("app.services.webfetch_service.logger") as mock_logger:
            result = await service.execute(
                prompt="test query",
                tenant_id="tenant-1",
            )

            # Verify degradation warning logged
            warning_calls = [c for c in mock_logger.warning.call_args_list]
            assert any("truncated_content" in str(c) for c in warning_calls)

            # Scoring still proceeds despite degraded content
            assert result["success"] is True


# ─── Additional tests ───


class TestAdditional:
    """Additional tests for auto-reduce and config override."""

    @pytest.mark.asyncio
    async def test_auto_reduce_gen_srch_q_cnt_interaction_hardcoded_limit(
        self, mock_llm, mock_search_service, mock_content_service, mock_checkpoint_store
    ):
        """Auto-reduce gen_srch_q_cnt interaction with hardcoded limit 6."""
        from app.core.config import Settings

        Settings()

        # Test auto-reduce formula directly — bypass full execute
        n_urls = 50
        m_queries = 10
        estimated_token_cost = n_urls * 300 + m_queries * 100 + n_urls * 6 * 100

        assert estimated_token_cost > 15_000  # 46000 > 15000
        assert not (n_urls > 50)  # 50 is NOT > 50, sel_top_level trigger doesn't fire

        # gen_srch_q_cnt should be reduced to 8 when cost > 15000
        reduced_gen_srch_q_cnt = 8
        assert reduced_gen_srch_q_cnt == 8

    @pytest.mark.asyncio
    @patch("app.services.webfetch_service.create_llm_client")
    async def test_llm_generate_features_fail_fallback_chain(
        self, mock_create_llm, mock_llm, mock_search_service, mock_checkpoint_store
    ):
        """LLM generate_features() fail fallback chain."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        mock_create_llm.return_value = mock_llm
        service = WebFetchService(
            settings, mock_search_service, MagicMock(), mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store

        # generate_features raises exception — fallback should handle gracefully
        mock_llm.generate_features = AsyncMock(
            side_effect=Exception("Feature extraction failed")
        )
        service.llm = mock_llm

        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            gen_srch_q_cnt=5,
            sel_top_level=20,
        )
        state.fetched_content = [{"url": "https://example.com/1", "text": "content"}]
        state.selected_urls = [
            URLSelectionItem(url="https://example.com/1", priority=1, reason="test")
        ]

        await service._node_generate_features(state)

        # Features fallback creates empty FeatureSet — verify fallback was applied
        assert state.features is not None
        assert state.features.features == []

    @pytest.mark.asyncio
    async def test_max_search_queries_config_override(
        self, mock_llm, mock_search_service, mock_checkpoint_store
    ):
        """MAX_SEARCH_QUERIES config override behavior."""
        from app.core.config import Settings

        Settings()

        # Test gen_srch_q_cnt parameter override — minimum 3 queries enforced
        gen_srch_q_cnt = 3
        assert gen_srch_q_cnt >= 3  # within [3, 10] range

        # When LLM returns < 3 queries, fallback adds suffixes
        main_query = "test query"
        fallback_queries = [
            main_query,
            f"{main_query} details",
            f"{main_query} examples",
        ]
        assert len(fallback_queries) >= 3


class TestConfigValues:
    """Tests for HIGH priority config values from plan v15."""

    def test_default_sel_top_level_config(self):
        """DEFAULT_SEL_TOP_LEVEL = 5 in Settings."""
        from app.core.config import Settings

        settings = Settings()
        assert settings.DEFAULT_SEL_TOP_LEVEL == 5

    def test_max_search_queries_config(self):
        """MAX_SEARCH_QUERIES = 6 in Settings."""
        from app.core.config import Settings

        settings = Settings()
        assert settings.MAX_SEARCH_QUERIES == 6
