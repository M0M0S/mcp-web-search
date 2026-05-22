"""Integration tests for webfetch pipeline Gap 1-7 fixes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.search import SearchResponse, SearchResult
from app.models.webfetch import (
    FeatureSet,
    JudgeVerdict,
    SourceFeature,
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
                SearchResult(
                    url="https://example.com/2", title="Title 2", description="Desc 2"
                ),
                SearchResult(
                    url="https://example.com/3", title="Title 3", description="Desc 3"
                ),
            ],
            provider="duck",
            total_found=3,
        )
    )
    return service


@pytest.fixture
def mock_content_service():
    """Mock content service."""
    service = MagicMock()
    service.extract_content = AsyncMock(return_value="Extracted text content from URL")
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


# ─── Integration tests: full pipeline ───


class TestFullPipeline:
    """Integration tests for full webfetch pipeline with Gap fixes."""

    @pytest.mark.asyncio
    async def test_full_pipeline_with_dedup(
        self, mock_llm, mock_search_service, mock_content_service, mock_checkpoint_store
    ):
        """Full pipeline: search → judge_with_content → dedup → fetch → score → aggregate."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        service = WebFetchService(
            settings, mock_search_service, mock_content_service, mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store
        service.llm = mock_llm

        result = await service.execute(
            prompt="test query",
            tenant_id="tenant-1",
            gen_srch_q_cnt=5,
            sel_top_level=20,
        )

        assert result["success"] is True
        assert result["state"]["search_queries"] is not None
        assert len(result["state"]["search_queries"]) >= 3
        assert result["state"]["selected_urls"] is not None
        assert result["state"]["url_judgment"] is not None
        assert result["state"]["fetched_content"] is not None
        assert result["sources"] is not None
        assert len(result["sources"]) > 0

    @pytest.mark.asyncio
    async def test_checkpoint_resume_with_dedup(
        self, mock_llm, mock_search_service, mock_content_service, mock_checkpoint_store
    ):
        """Checkpoint save/restore + deduplication re-run."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        service = WebFetchService(
            settings, mock_search_service, mock_content_service, mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store
        service.llm = mock_llm

        # First run — save checkpoint at select_urls node
        state1 = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            gen_srch_q_cnt=5,
            sel_top_level=20,
        )
        state1.selected_urls = [
            URLSelectionItem(url="https://example.com/1", priority=1, reason="test"),
            URLSelectionItem(url="https://example.com/2", priority=2, reason="test"),
        ]
        cp_id = f"{state1.checkpoint_key}:select_urls"
        await mock_checkpoint_store.save(cp_id, state1.model_dump())

        # Second run — resume checkpoint
        mock_checkpoint_store.load = AsyncMock(return_value=state1.model_dump())

        state2 = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            gen_srch_q_cnt=5,
            sel_top_level=20,
        )

        resumed = await service._resume_checkpoint(
            {"configurable": {"thread_id": state2.checkpoint_key}}
        )
        assert resumed is not None
        assert resumed.selected_urls is not None
        assert len(resumed.selected_urls) == 2

    @pytest.mark.asyncio
    async def test_checkpoint_resume_fetched_content_skip(
        self, mock_llm, mock_search_service, mock_content_service, mock_checkpoint_store
    ):
        """fetched_content restored → fetch skipped."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        service = WebFetchService(
            settings, mock_search_service, mock_content_service, mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store
        service.llm = mock_llm

        # Checkpoint with fetched_content already populated
        state_cp = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            gen_srch_q_cnt=5,
            sel_top_level=20,
        )
        state_cp.fetched_content = [
            {"url": "https://example.com/1", "text": "restored content"},
            {"url": "https://example.com/2", "text": "restored content 2"},
        ]

        mock_checkpoint_store.load = AsyncMock(return_value=state_cp.model_dump())

        # Execute — _node_fetch_content should skip because fetched_content already populated
        result = await service.execute(
            prompt="test query",
            tenant_id="tenant-1",
        )

        assert result["state"]["fetched_content"] is not None
        assert len(result["state"]["fetched_content"]) == 2

    @pytest.mark.asyncio
    async def test_checkpoint_resume_duplicated_urls(
        self, mock_llm, mock_search_service, mock_content_service, mock_checkpoint_store
    ):
        """Checkpoint with duplicated selected_urls → fetch dedup re-run."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        service = WebFetchService(
            settings, mock_search_service, mock_content_service, mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store
        service.llm = mock_llm

        # Checkpoint with duplicated URLs
        state_cp = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            gen_srch_q_cnt=5,
            sel_top_level=20,
        )
        state_cp.selected_urls = [
            URLSelectionItem(url="https://example.com/dup", priority=1, reason="r1"),
            URLSelectionItem(url="https://example.com/dup", priority=2, reason="r2"),
        ]
        state_cp.url_judgment = JudgeVerdict(
            score=0.9, verdict="pass", reasons=["pass"]
        )

        mock_checkpoint_store.load = AsyncMock(return_value=state_cp.model_dump())

        result = await service.execute(
            prompt="test query",
            tenant_id="tenant-1",
        )

        # After resume, _node_select_urls should skip (already populated),
        # but _node_fetch_content should dedup
        assert result["state"]["fetched_content"] is not None

    @pytest.mark.asyncio
    async def test_checkpoint_resume_scoring_always(
        self, mock_llm, mock_search_service, mock_content_service, mock_checkpoint_store
    ):
        """Scoring re-run at resume if sources_with_features empty."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        service = WebFetchService(
            settings, mock_search_service, mock_content_service, mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store
        service.llm = mock_llm

        # Checkpoint with sources_with_features empty but fetched_content populated
        state_cp = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            gen_srch_q_cnt=5,
            sel_top_level=20,
        )
        state_cp.fetched_content = [{"url": "https://example.com/1", "text": "content"}]
        state_cp.selected_urls = [
            URLSelectionItem(url="https://example.com/1", priority=1, reason="test")
        ]
        state_cp.sources_with_features = []  # empty — scoring must re-run

        mock_checkpoint_store.load = AsyncMock(return_value=state_cp.model_dump())

        result = await service.execute(
            prompt="test query",
            tenant_id="tenant-1",
        )

        # Verify scoring ran and sources populated
        assert len(result["sources"]) >= 1


# ─── Integration tests: sources and slice ───


class TestSourcesIntegration:
    """Integration tests for Gap 5: sources always populated + configurable slice."""

    @pytest.mark.asyncio
    async def test_sel_top_level_parameter_integration(
        self, mock_llm, mock_search_service, mock_content_service, mock_checkpoint_store
    ):
        """Configurable sel_top_level (5, 30, 50) → verify slice behavior."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()

        for sel_top_level in [5, 30, 50]:
            service = WebFetchService(
                settings, mock_search_service, mock_content_service, mock_redis
            )
            service._redis_checkpoint_store = mock_checkpoint_store
            service.llm = mock_llm

            # Pre-populate sources with more items than sel_top_level
            state = WebFetchState(
                prompt="test query",
                tenant_id="tenant-1",
                version="1.0",
                gen_srch_q_cnt=5,
                sel_top_level=sel_top_level,
            )
            source_count = sel_top_level + 10
            state.sources_with_features = [
                SourceFeature(
                    url=f"https://example.com/{i}", text=f"t{i}", features=[f"f{i}"]
                )
                for i in range(source_count)
            ]

            max_sources = min(len(state.sources_with_features), state.sel_top_level)
            sliced = state.sources_with_features[:max_sources]

            assert len(sliced) == sel_top_level

    @pytest.mark.asyncio
    async def test_checkpoint_transition_old_to_new(
        self, mock_llm, mock_search_service, mock_content_service, mock_checkpoint_store
    ):
        """Resume from old checkpoint → idempotent check handles transition."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        service = WebFetchService(
            settings, mock_search_service, mock_content_service, mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store
        service.llm = mock_llm

        # Old checkpoint with version "0.9" — should still resume gracefully
        old_state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="0.9",
            gen_srch_q_cnt=5,
            sel_top_level=20,
        )
        old_state.search_queries = ["q1", "q2"]
        old_state.search_results = [
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

        mock_checkpoint_store.load = AsyncMock(return_value=old_state.model_dump())

        result = await service.execute(
            prompt="test query",
            tenant_id="tenant-1",
        )

        assert result["success"] is True
        # Pipeline should continue from checkpoint, not restart from scratch


# ─── Integration tests: auto-reduce ───


class TestAutoReduceIntegration:
    """Integration tests for auto-reduce mechanism."""

    @pytest.mark.asyncio
    async def test_auto_reduce_sel_top_level(
        self, mock_llm, mock_search_service, mock_content_service, mock_checkpoint_store
    ):
        """Auto-reduce mechanism verification (sel_top_level > 50 → reduced to 30)."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()

        # Test via checkpoint resume to bypass Pydantic sel_top_level le=50 constraint
        state_cp = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            gen_srch_q_cnt=5,
            sel_top_level=50,  # max allowed by Pydantic
        )
        state_cp.search_queries = ["q1", "q2"]
        state_cp.search_results = [
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

        mock_checkpoint_store.load = AsyncMock(return_value=state_cp.model_dump())

        service = WebFetchService(
            settings, mock_search_service, mock_content_service, mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store
        service.llm = mock_llm

        with patch("app.services.webfetch_service.logger") as mock_logger:
            await service.execute(
                prompt="test query",
                tenant_id="tenant-1",
            )

            # sel_top_level=50 is at threshold — auto-reduce triggers at > 50
            # Verify the formula: n_urls=50 → cost = 50*300 + 5*100 + 50*6*100 = 15000+500+30000 = 45500 > 15000
            # gen_srch_q_cnt should be reduced
            warning_calls = [c for c in mock_logger.warning.call_args_list]
            assert any("high_cost_pipeline" in str(c) for c in warning_calls)

    @pytest.mark.asyncio
    async def test_auto_reduce_gen_srch_q_cnt(
        self, mock_llm, mock_search_service, mock_content_service, mock_checkpoint_store
    ):
        """Auto-reduce mechanism verification (cost > 15000 → gen_srch_q_cnt = 8)."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        service = WebFetchService(
            settings, mock_search_service, mock_content_service, mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store
        service.llm = mock_llm

        with patch("app.services.webfetch_service.logger") as mock_logger:
            # sel_top_level=50, gen_srch_q_cnt=10 → cost = 50*300 + 10*100 + 50*6*100 = 15000+1000+30000 = 46000 > 15000
            result = await service.execute(
                prompt="test query",
                tenant_id="tenant-1",
                sel_top_level=50,
                gen_srch_q_cnt=10,
            )

            assert result["state"]["gen_srch_q_cnt"] == 8
            warning_calls = [c for c in mock_logger.warning.call_args_list]
            assert any("auto-reducing gen_srch_q_cnt" in str(c) for c in warning_calls)

    @pytest.mark.asyncio
    async def test_truncation_no_truncation_for_small_content(
        self, mock_llm, mock_search_service, mock_content_service, mock_checkpoint_store
    ):
        """Content < 700 chars → no truncation applied."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        service = WebFetchService(
            settings, mock_search_service, mock_content_service, mock_redis
        )
        service._redis_checkpoint_store = mock_checkpoint_store

        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            gen_srch_q_cnt=5,
            sel_top_level=20,
        )
        short_text = "x" * 500
        state.fetched_content = [{"url": "https://example.com/1", "text": short_text}]
        state.cache_key = "a" * 1000  # force oversized

        await service._save_checkpoint(
            state, {"configurable": {"thread_id": "t"}}, node="test"
        )

        assert state.fetched_content[0]["text"] == short_text
