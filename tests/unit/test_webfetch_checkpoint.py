"""Tests for WebFetchService checkpointing and resume logic."""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.webfetch import WebFetchState


class TestCheckpointKeyGeneration:
    """Tests for WebFetchState.checkpoint_key property."""

    def test_checkpoint_key_from_cache_key(self):
        """AC3: checkpoint_key uses cache_key when present."""
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            cache_key="abc123",
        )
        assert state.checkpoint_key == "webfetch_checkpoint:tenant-1:1.0:abc123"

    def test_checkpoint_key_generated_from_prompt_tenant(self):
        """AC3: checkpoint_key generated from prompt + tenant_id."""
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
        )
        expected_raw = "test query:tenant-1:1.0"
        expected_key = hashlib.sha256(expected_raw.encode()).hexdigest()[:16]
        assert (
            state.checkpoint_key == f"webfetch_checkpoint:tenant-1:1.0:{expected_key}"
        )
        # computed_field is read-only — cache_key must NOT be mutated
        assert state.cache_key is None

    def test_checkpoint_key_idempotent(self):
        """checkpoint_key returns same value on repeated calls."""
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
        )
        first = state.checkpoint_key
        second = state.checkpoint_key
        assert first == second

    def test_checkpoint_key_no_cache_key_mutation(self):
        """computed_field checkpoint_key does NOT mutate self.cache_key.

        Pydantic computed fields must be read-only — no side effects.
        """
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
        )
        # cache_key starts as None
        assert state.cache_key is None

        # Access checkpoint_key multiple times
        _ = state.checkpoint_key
        _ = state.checkpoint_key
        _ = state.checkpoint_key

        # cache_key must remain None — no mutation
        assert state.cache_key is None

    def test_checkpoint_key_idempotent_with_cache_key_set(self):
        """checkpoint_key idempotent when cache_key is already set."""
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            cache_key="pre-set-key",
        )
        first = state.checkpoint_key
        second = state.checkpoint_key
        assert first == second
        assert first == "webfetch_checkpoint:tenant-1:1.0:pre-set-key"


class TestCheckpointSave:
    """Tests for checkpoint save logic."""

    @pytest.fixture
    def mock_service(self):
        """Create WebFetchService with mocked dependencies."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        search_service = MagicMock()
        content_service = MagicMock()
        redis = MagicMock()

        with patch("app.services.webfetch_service.create_llm_client") as mock_llm:
            mock_llm.return_value = MagicMock()
            service = WebFetchService(settings, search_service, content_service, redis)
        # Mock the Redis checkpoint store so tests don't hit real Redis
        service._redis_checkpoint_store = MagicMock()
        service._redis_checkpoint_store.save = AsyncMock()
        service._redis_checkpoint_store.load = AsyncMock()
        return service

    @pytest.fixture
    def sample_state(self):
        """Create a sample WebFetchState with some data."""
        return WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            search_queries=["query1", "query2"],
            search_results=[
                {
                    "results": [
                        {
                            "url": "https://example.com",
                            "title": "Test",
                            "snippet": "Test snippet",
                        }
                    ],
                    "provider": "test_provider",
                    "cache_hit": False,
                    "total_found": 1,
                    "diversity_scores": {},
                }
            ],
        )

    @pytest.fixture
    def sample_config(self, sample_state):
        """Create checkpoint config."""
        thread_id = f"webfetch:{sample_state.tenant_id}:{sample_state.prompt[:32]}"
        return {
            "configurable": {
                "thread_id": thread_id,
                "tenant_id": sample_state.tenant_id,
            }
        }

    @pytest.mark.asyncio
    async def test_checkpoint_save_creates_entry(
        self, mock_service, sample_state, sample_config
    ):
        """AC2: checkpoint saved after node execution."""
        await mock_service._save_checkpoint(
            sample_state, sample_config, node="generate_search_queries"
        )

        # Verify Redis save was called with correct key format
        mock_service._redis_checkpoint_store.save.assert_called_once()
        call_args = mock_service._redis_checkpoint_store.save.call_args
        cp_id = call_args[0][0]
        assert cp_id.startswith(sample_state.checkpoint_key)
        assert cp_id.endswith(":generate_search_queries")
        stored = call_args[0][1]
        assert "search_queries" in stored
        assert stored["search_queries"] == sample_state.search_queries

    @pytest.mark.asyncio
    async def test_checkpoint_serialization_pydantic(
        self, mock_service, sample_state, sample_config
    ):
        """AC5: checkpoint serialization uses Pydantic model_dump."""
        await mock_service._save_checkpoint(
            sample_state, sample_config, node="perform_search"
        )

        call_args = mock_service._redis_checkpoint_store.save.call_args
        stored = call_args[0][1]

        assert stored["prompt"] == sample_state.prompt
        assert stored["tenant_id"] == sample_state.tenant_id
        assert stored["version"] == sample_state.version

    @pytest.mark.asyncio
    async def test_checkpoint_multiple_saves_different_nodes(
        self, mock_service, sample_config
    ):
        """AC4: multiple checkpoints saved with distinct node suffixes."""
        state1 = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            search_queries=["q1"],
        )
        state2 = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            search_queries=["q1", "q2"],
            search_results=[
                {
                    "results": [
                        {
                            "url": "https://example.com",
                            "title": "Test",
                            "snippet": "Test snippet",
                        }
                    ],
                    "provider": "test_provider",
                    "cache_hit": False,
                    "total_found": 1,
                    "diversity_scores": {},
                }
            ],
        )

        await mock_service._save_checkpoint(
            state1, sample_config, node="generate_search_queries"
        )
        await mock_service._save_checkpoint(
            state2, sample_config, node="perform_search"
        )

        assert mock_service._redis_checkpoint_store.save.call_count == 2
        calls = mock_service._redis_checkpoint_store.save.call_args_list
        assert calls[0][0][0].endswith(":generate_search_queries")
        assert calls[1][0][0].endswith(":perform_search")


class TestCheckpointResume:
    """Tests for checkpoint resume logic."""

    @pytest.fixture
    def mock_service(self):
        """Create WebFetchService with mocked dependencies."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        search_service = MagicMock()
        content_service = MagicMock()
        redis = MagicMock()

        with patch("app.services.webfetch_service.create_llm_client") as mock_llm:
            mock_llm.return_value = MagicMock()
            service = WebFetchService(settings, search_service, content_service, redis)
        # Mock the Redis checkpoint store so tests don't hit real Redis
        service._redis_checkpoint_store = MagicMock()
        service._redis_checkpoint_store.save = AsyncMock()
        service._redis_checkpoint_store.load = AsyncMock()
        return service

    @pytest.mark.asyncio
    async def test_resume_from_checkpoint(self, mock_service):
        """AC6-AC7: checkpoint located and state restored."""
        config = {"configurable": {"thread_id": "resume-test", "tenant_id": "tenant-1"}}

        mock_service._redis_checkpoint_store.load = AsyncMock(
            return_value={
                "prompt": "test query",
                "tenant_id": "tenant-1",
                "version": "1.0",
                "search_queries": ["q1", "q2"],
                "search_results": [],
                "selected_urls": [],
            }
        )

        # Resume logic
        restored_state = await mock_service._resume_checkpoint(config)

        # Verify restored state matches checkpoint
        assert restored_state is not None
        assert restored_state.search_queries == ["q1", "q2"]
        assert restored_state.tenant_id == "tenant-1"
        assert restored_state.prompt == "test query"

    @pytest.mark.asyncio
    async def test_resume_same_tenant_id(self, mock_service):
        """AC9: resume with same tenant_id and original parameters."""
        config = {
            "configurable": {
                "thread_id": "tenant-resume-test",
                "tenant_id": "tenant-42",
            }
        }

        mock_service._redis_checkpoint_store.load = AsyncMock(
            return_value={
                "prompt": "original query",
                "tenant_id": "tenant-42",
                "version": "1.0",
                "gen_srch_q_cnt": 7,
                "sel_top_level": 30,
                "search_queries": ["q1"],
            }
        )

        restored = await mock_service._resume_checkpoint(config)

        assert restored.tenant_id == "tenant-42"
        assert restored.gen_srch_q_cnt == 7
        assert restored.sel_top_level == 30

    @pytest.mark.asyncio
    async def test_checkpoint_not_found_full_restart(self, mock_service):
        """AC10: checkpoint not found → None (full restart)."""
        config = {
            "configurable": {"thread_id": "nonexistent-thread", "tenant_id": "tenant-1"}
        }

        # Configure mock to return None (no checkpoint)
        mock_service._redis_checkpoint_store.load = AsyncMock(return_value=None)

        result = await mock_service._resume_checkpoint(config)

        # No checkpoint found
        assert result is None


class TestCheckpointMetrics:
    """Tests for AC16-AC20: checkpoint metrics instrumentation."""

    @pytest.fixture(autouse=True)
    def reset_metrics(self):
        """Reset Prometheus counters before each test."""
        from app.core.metrics import (
            webfetch_active_checkpoints,
            webfetch_checkpoint_resume_total,
            webfetch_checkpoint_save_total,
        )

        # Reset all counters and gauges
        webfetch_checkpoint_save_total.labels(tenant_id="tenant-1")._value._value = 0
        webfetch_checkpoint_save_total.labels(tenant_id="tenant-42")._value._value = 0
        webfetch_checkpoint_resume_total.labels(tenant_id="tenant-1")._value._value = 0
        webfetch_checkpoint_resume_total.labels(tenant_id="tenant-42")._value._value = 0
        webfetch_active_checkpoints.labels(tenant_id="tenant-1")._value._value = 0
        webfetch_active_checkpoints.labels(tenant_id="tenant-42")._value._value = 0
        yield

    @pytest.fixture
    def mock_service(self):
        """Create WebFetchService with mocked dependencies."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        search_service = MagicMock()
        content_service = MagicMock()
        redis = MagicMock()

        with patch("app.services.webfetch_service.create_llm_client") as mock_llm:
            mock_llm.return_value = MagicMock()
            service = WebFetchService(settings, search_service, content_service, redis)
        # Mock the Redis checkpoint store so tests don't hit real Redis
        service._redis_checkpoint_store = MagicMock()
        service._redis_checkpoint_store.save = AsyncMock()
        service._redis_checkpoint_store.load = AsyncMock(
            return_value={"prompt": "test", "tenant_id": "tenant-1", "version": "1.0"}
        )
        return service

    @pytest.fixture
    def sample_state(self):
        """Create a sample WebFetchState with some data."""
        return WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            search_queries=["query1", "query2"],
            search_results=[
                {
                    "results": [
                        {
                            "url": "https://example.com",
                            "title": "Test",
                            "snippet": "Test snippet",
                        }
                    ],
                    "provider": "test_provider",
                    "cache_hit": False,
                    "total_found": 1,
                    "diversity_scores": {},
                }
            ],
        )

    @pytest.mark.asyncio
    async def test_ac16_checkpoint_save_counter_increments(
        self, mock_service, sample_state
    ):
        """AC16: webfetch_checkpoint_save_total counter increments with tenant_id label."""
        from app.core.metrics import webfetch_checkpoint_save_total

        config = {"configurable": {"thread_id": "ac16-test", "tenant_id": "tenant-1"}}

        initial_value = webfetch_checkpoint_save_total.labels(
            tenant_id="tenant-1"
        )._value._value

        await mock_service._save_checkpoint(
            sample_state, config, node="generate_search_queries"
        )

        final_value = webfetch_checkpoint_save_total.labels(
            tenant_id="tenant-1"
        )._value._value
        assert final_value == initial_value + 1

    @pytest.mark.asyncio
    async def test_ac16_checkpoint_save_tenant_label(self, mock_service):
        """AC16: checkpoint save counter uses correct tenant_id label."""
        from app.core.metrics import webfetch_checkpoint_save_total

        config = {
            "configurable": {"thread_id": "ac16-label-test", "tenant_id": "tenant-42"}
        }
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-42",
            version="1.0",
        )

        await mock_service._save_checkpoint(state, config, node="judge_urls")

        assert (
            webfetch_checkpoint_save_total.labels(tenant_id="tenant-42")._value._value
            > 0
        )
        assert (
            webfetch_checkpoint_save_total.labels(tenant_id="tenant-1")._value._value
            == 0
        )

    @pytest.mark.asyncio
    async def test_ac17_checkpoint_resume_counter_increments(self, mock_service):
        """AC17: webfetch_checkpoint_resume_total counter increments with tenant_id label."""
        from app.core.metrics import webfetch_checkpoint_resume_total

        config = {
            "configurable": {
                "thread_id": "ac17-test",
                "tenant_id": "tenant-1",
            }
        }
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            search_queries=["q1"],
        )
        await mock_service._save_checkpoint(
            state, config, node="generate_search_queries"
        )

        initial_value = webfetch_checkpoint_resume_total.labels(
            tenant_id="tenant-1"
        )._value._value

        mock_service._redis_checkpoint_store.load = AsyncMock(
            return_value={
                "prompt": "test query",
                "tenant_id": "tenant-1",
                "version": "1.0",
                "search_queries": ["q1"],
            }
        )

        result = await mock_service._resume_checkpoint(config)
        assert result is not None

        final_value = webfetch_checkpoint_resume_total.labels(
            tenant_id="tenant-1"
        )._value._value
        assert final_value == initial_value + 1

    @pytest.mark.asyncio
    async def test_ac18_checkpoint_size_histogram(self, mock_service, sample_state):
        """AC18: webfetch_checkpoint_size_bytes histogram records payload size."""
        from app.core.metrics import webfetch_checkpoint_size_bytes

        config = {"configurable": {"thread_id": "ac18-test", "tenant_id": "tenant-1"}}

        await mock_service._save_checkpoint(sample_state, config, node="select_urls")

        # Histogram should have recorded the size
        assert webfetch_checkpoint_size_bytes._sum._value > 0
        samples = webfetch_checkpoint_size_bytes._samples()
        count_val = next((s.value for s in samples if s.name == "_count"), 0)
        assert count_val >= 1

    @pytest.mark.asyncio
    async def test_ac20_metrics_exported_via_get_metrics_bytes(self, mock_service):
        """AC20: checkpoint metrics are included in Prometheus export."""
        from app.core.metrics import get_metrics_bytes

        config = {"configurable": {"thread_id": "ac20-test", "tenant_id": "tenant-1"}}
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            search_queries=["q1"],
        )
        await mock_service._save_checkpoint(state, config, node="fetch_content")

        metrics_bytes = get_metrics_bytes()
        metrics_text = metrics_bytes.decode("utf-8")

        assert "webfetch_checkpoint_save_total" in metrics_text
        assert "webfetch_checkpoint_resume_total" in metrics_text
        assert "webfetch_checkpoint_size_bytes" in metrics_text


class TestCheckpointSizeCap:
    """Tests for MAX_CHECKPOINT_SIZE enforcement."""

    @pytest.fixture
    def mock_service(self):
        """Create WebFetchService with mocked dependencies."""
        from app.core.config import Settings
        from app.services.webfetch_service import WebFetchService

        settings = Settings()
        search_service = MagicMock()
        content_service = MagicMock()
        redis = MagicMock()

        with patch("app.services.webfetch_service.create_llm_client") as mock_llm:
            mock_llm.return_value = MagicMock()
            service = WebFetchService(settings, search_service, content_service, redis)
        # Mock the Redis checkpoint store so tests don't hit real Redis
        service._redis_checkpoint_store = MagicMock()
        service._redis_checkpoint_store.save = AsyncMock()
        service._redis_checkpoint_store.load = AsyncMock()
        return service

    @pytest.mark.asyncio
    async def test_checkpoint_normal_size_saved(self, mock_service):
        """Normal-size checkpoint is saved without truncation."""
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            search_queries=["q1", "q2"],
        )
        config = {"configurable": {"thread_id": "size-test", "tenant_id": "tenant-1"}}

        await mock_service._save_checkpoint(state, config, node="test")

        mock_service._redis_checkpoint_store.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_checkpoint_oversized_truncated(self, mock_service):
        """Oversized checkpoint triggers smarter truncation of fetched_content."""
        from app.core.config import MAX_CHECKPOINT_SIZE

        # Create state with large fetched_content to exceed MAX_CHECKPOINT_SIZE
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            search_queries=["q1"],
        )
        # Each item ~110KB: 10 items → ~1.1MB (exceeds limit)
        item_text = "x" * (MAX_CHECKPOINT_SIZE // 9)
        state.fetched_content = [
            {"text": item_text, "metadata": {"source_url": "https://example.com"}}
            for _ in range(10)
        ]

        config = {"configurable": {"thread_id": "size-test", "tenant_id": "tenant-1"}}

        await mock_service._save_checkpoint(state, config, node="fetch_content")

        # After smarter truncation, save should be called (truncated version)
        mock_service._redis_checkpoint_store.save.assert_called_once()
        call_args = mock_service._redis_checkpoint_store.save.call_args
        stored = call_args[0][1]
        # Smarter truncation preserves all items but truncates text to fit limit
        assert len(stored["fetched_content"]) == 10
        # First item text truncated to ~500 chars, last item text truncated to ~200 chars
        assert len(stored["fetched_content"][0]["text"]) < len(item_text)
        assert len(stored["fetched_content"][-1]["text"]) < len(item_text)

    @pytest.mark.asyncio
    async def test_checkpoint_still_oversized_after_truncate_skipped(
        self, mock_service
    ):
        """If truncation doesn't help, checkpoint is skipped."""
        from app.core.config import MAX_CHECKPOINT_SIZE

        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            version="1.0",
            search_queries=["q1"],
        )
        # Inject huge text in prompt itself to exceed limit even after truncation
        huge_prompt = "x" * (MAX_CHECKPOINT_SIZE + 1024)
        state.prompt = huge_prompt
        state.fetched_content = [
            {
                "text": "x" * (MAX_CHECKPOINT_SIZE // 2),
                "metadata": {"source_url": "https://example.com"},
            }
            for _ in range(10)
        ]

        config = {"configurable": {"thread_id": "size-test", "tenant_id": "tenant-1"}}

        await mock_service._save_checkpoint(state, config, node="perform_search")

        # save should NOT be called because prompt is oversized
        # and truncation of fetched_content doesn't help
        mock_service._redis_checkpoint_store.save.assert_not_called()


class TestCheckpointIdAndStore:
    """Tests for Epic 3 AC1: checkpoint_id and checkpoint_store fields."""

    def test_checkpoint_id_default_empty(self):
        """checkpoint_id defaults to empty string."""
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
        )
        assert state.checkpoint_id == ""

    def test_checkpoint_id_settable(self):
        """checkpoint_id can be set to a non-empty value."""
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            checkpoint_id="cp-abc123",
        )
        assert state.checkpoint_id == "cp-abc123"

    def test_checkpoint_store_default_none(self):
        """checkpoint_store defaults to None."""
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
        )
        assert state.checkpoint_store is None

    def test_checkpoint_store_settable(self):
        """checkpoint_store can be set to a store instance."""

        store = MagicMock()
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            checkpoint_store=store,
        )
        assert state.checkpoint_store is store

    def test_checkpoint_id_serialization(self):
        """checkpoint_id is included in model_dump."""
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
            checkpoint_id="cp-xyz789",
        )
        dumped = state.model_dump()
        assert dumped["checkpoint_id"] == "cp-xyz789"

    def test_checkpoint_store_serialization(self):
        """checkpoint_store is included in model_dump (None when unset)."""
        state = WebFetchState(
            prompt="test query",
            tenant_id="tenant-1",
        )
        dumped = state.model_dump()
        assert dumped["checkpoint_store"] is None

    def test_checkpoint_id_model_validate(self):
        """checkpoint_id survives model_validate from dict."""
        raw = {
            "prompt": "test query",
            "tenant_id": "tenant-1",
            "checkpoint_id": "cp-recovered",
        }
        restored = WebFetchState.model_validate(raw)
        assert restored.checkpoint_id == "cp-recovered"
