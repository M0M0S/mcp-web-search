"""Unit tests for LLM failover system: LLMChainExhaustedError, LLMHealthTracker, LLMClient."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import RateLimitError

from app.core.llm_client import (
    LLMChainExhaustedError,
    LLMClient,
    LLMHealthTracker,
)
from app.models.judge import JudgeVerdict
from app.models.webfetch import FeatureSet

# ── LLMChainExhaustedError ──────────────────────────────────────────────


class TestLLMChainExhaustedError:
    """Tests for LLMChainExhaustedError exception."""

    def test_creation_with_failed_models_and_error(self):
        """Verify exception stores failed_models and last_error attributes."""
        last_exc = ConnectionError("connection refused")
        failed_models = ["gpt-4", "claude-3"]
        exc = LLMChainExhaustedError(failed_models, last_exc)

        assert exc.failed_models == ["gpt-4", "claude-3"]
        assert exc.last_error is last_exc

    def test_exception_message_contains_model_count(self):
        """Verify exception message includes the number of failed models."""
        last_exc = Exception("generic error")
        exc = LLMChainExhaustedError(["model-a"], last_exc)

        assert "1 models" in str(exc)

    def test_exception_message_includes_model_names(self):
        """Verify exception message lists all failed model names."""
        last_exc = Exception("generic error")
        exc = LLMChainExhaustedError(["gpt-4o", "llama-3"], last_exc)

        assert "gpt-4o" in str(exc)
        assert "llama-3" in str(exc)

    def test_exception_message_includes_last_error_type(self):
        """Verify exception message includes the type name of last_error."""
        from openai import RateLimitError

        # RateLimitError requires response/body args; use a mock to verify type name in message
        mock_response = MagicMock(status_code=429)
        last_exc = RateLimitError(
            message="rate limited", response=mock_response, body={}
        )
        exc = LLMChainExhaustedError(["model"], last_exc)

        assert "RateLimitError" in str(exc)

    def test_exception_is_standard_exception(self):
        """Verify LLMChainExhaustedError is a standard Exception subclass."""
        exc = LLMChainExhaustedError([], Exception("empty"))
        assert isinstance(exc, Exception)


# ── LLMHealthTracker ────────────────────────────────────────────────────


class TestLLMHealthTracker:
    """Tests for LLMHealthTracker health metrics and circular buffer."""

    def test_default_health_score_is_1_0_for_unseen_model(self):
        """Verify health score returns 1.0 when no events recorded."""
        tracker = LLMHealthTracker()
        assert tracker.get_health_score("unknown-model") == 1.0

    def test_health_score_from_success_events(self):
        """Verify health score reflects ratio of successes."""
        tracker = LLMHealthTracker()
        tracker.record_success("model-a")
        tracker.record_success("model-a")
        assert tracker.get_health_score("model-a") == 1.0

    def test_health_score_from_failure_events(self):
        """Verify health score reflects ratio of failures."""
        tracker = LLMHealthTracker()
        tracker.record_failure("model-a")
        tracker.record_failure("model-a")
        assert tracker.get_health_score("model-a") == 0.0

    def test_health_score_mixed_events(self):
        """Verify health score for mixed success/failure events."""
        tracker = LLMHealthTracker()
        tracker.record_success("model-a")
        tracker.record_failure("model-a")
        tracker.record_success("model-a")
        assert tracker.get_health_score("model-a") == pytest.approx(2 / 3)

    def test_record_success_updates_success_count(self):
        """Verify record_success increments _success_counts."""
        tracker = LLMHealthTracker()
        tracker.record_success("model-a")
        tracker.record_success("model-a")
        assert tracker._success_counts["model-a"] == 2

    def test_record_failure_updates_failure_count(self):
        """Verify record_failure increments _failure_counts."""
        tracker = LLMHealthTracker()
        tracker.record_failure("model-a")
        tracker.record_failure("model-a")
        assert tracker._failure_counts["model-a"] == 2

    def test_record_success_updates_last_success_time(self):
        """Verify record_success sets _last_success_times."""
        tracker = LLMHealthTracker()
        before = time.time()
        tracker.record_success("model-a")
        after = time.time()
        assert before <= tracker._last_success_times["model-a"] <= after

    def test_should_exclude_returns_false_for_healthy_model(self):
        """Verify should_exclude returns False when model is healthy."""
        tracker = LLMHealthTracker()
        tracker.record_success("model-a")
        assert not tracker.should_exclude("model-a")

    def test_should_exclude_returns_false_for_no_events(self):
        """Verify should_exclude returns False when no events recorded."""
        tracker = LLMHealthTracker()
        assert not tracker.should_exclude("model-a")

    def test_should_exclude_returns_true_when_consecutive_failures_exceed_threshold(
        self,
    ):
        """Verify should_exclude returns True when consecutive failures exceed threshold."""
        tracker = LLMHealthTracker(health_window=10, failure_threshold=0.5)
        # 6 consecutive failures > 0.5 * 10 = 5
        for _ in range(6):
            tracker.record_failure("model-a")
        assert tracker.should_exclude("model-a")

    def test_should_exclude_returns_false_at_threshold_boundary(self):
        """Verify should_exclude returns False when consecutive failures equal threshold."""
        tracker = LLMHealthTracker(health_window=10, failure_threshold=0.5)
        # 5 consecutive failures = 0.5 * 10 = 5 (not exceeded)
        for _ in range(5):
            tracker.record_failure("model-a")
        assert not tracker.should_exclude("model-a")

    def test_consecutive_failures_with_interleaved_success(self):
        """Verify consecutive failure count resets on success event."""
        tracker = LLMHealthTracker()
        for _ in range(3):
            tracker.record_failure("model-a")
        tracker.record_success("model-a")
        for _ in range(2):
            tracker.record_failure("model-a")
        # Only the last 2 failures are consecutive
        assert tracker._consecutive_failures("model-a") == 2

    def test_reset_model_clears_all_metrics(self):
        """Verify reset_model removes all per-model data."""
        tracker = LLMHealthTracker()
        tracker.record_success("model-a")
        tracker.record_failure("model-a")
        tracker.reset_model("model-a")

        assert "model-a" not in tracker._event_buffers
        assert "model-a" not in tracker._success_counts
        assert "model-a" not in tracker._failure_counts
        assert "model-a" not in tracker._last_success_times

    def test_reset_model_does_not_affect_other_models(self):
        """Verify reset_model only affects the specified model."""
        tracker = LLMHealthTracker()
        tracker.record_success("model-a")
        tracker.record_success("model-b")
        tracker.reset_model("model-a")

        assert tracker.get_health_score("model-a") == 1.0
        assert tracker.get_health_score("model-b") == 1.0

    def test_circular_buffer_bounded_by_health_window(self):
        """Verify event buffer respects maxlen=health_window."""
        tracker = LLMHealthTracker(health_window=5)
        # Record 7 events — buffer should contain only last 5
        for i in range(7):
            tracker.record_success("model-a") if i % 2 == 0 else tracker.record_failure(
                "model-a"
            )

        assert len(tracker._event_buffers["model-a"]) == 5

    def test_circular_buffer_keeps_most_recent_events(self):
        """Verify circular buffer retains the most recent events."""
        tracker = LLMHealthTracker(health_window=3)
        tracker.record_success("model-a")
        tracker.record_failure("model-a")
        tracker.record_failure("model-a")
        tracker.record_failure("model-a")  # pushes out the first success

        buffer = tracker._event_buffers["model-a"]
        assert list(buffer) == [False, False, False]


# ── LLMClient.__init__ ──────────────────────────────────────────────────


class TestLLMClientInit:
    """Tests for LLMClient initialization."""

    def test_init_sets_primary_model(self):
        """Verify __init__ stores the primary model."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        assert client._primary_model == "gpt-4o"

    def test_init_default_fallback_chain_is_primary_model(self):
        """Verify fallback_chain defaults to [primary_model] when None."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        assert client._fallback_chain == ["gpt-4o"]

    def test_init_custom_fallback_chain(self):
        """Verify __init__ accepts custom fallback_chain."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3", "llama-3"],
        )
        assert client._fallback_chain == ["gpt-4o", "claude-3", "llama-3"]

    def test_init_health_tracker_creation(self):
        """Verify __init__ creates an LLMHealthTracker instance."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        assert isinstance(client.health_tracker, LLMHealthTracker)

    def test_init_health_tracker_custom_params(self):
        """Verify health_tracker uses custom health_window and failure_threshold."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
            health_window=20,
            failure_threshold=0.7,
        )
        assert client.health_tracker.health_window == 20
        assert client.health_tracker.failure_threshold == 0.7

    def test_init_default_fallback_base_urls_is_empty(self):
        """Verify fallback_base_urls defaults to empty dict when None."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        assert client._fallback_base_urls == {}

    def test_init_custom_fallback_base_urls(self):
        """Verify __init__ accepts custom fallback_base_urls."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
            fallback_base_urls={"claude-3": "https://api.anthropic.com/v1"},
        )
        assert client._fallback_base_urls["claude-3"] == "https://api.anthropic.com/v1"

    def test_init_active_model_is_primary(self):
        """Verify active_model property returns the primary model after init."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        assert client.active_model == "gpt-4o"

    def test_init_semaphore_max_concurrent_calls(self):
        """Verify semaphore is created with the specified max_concurrent_calls."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
            max_concurrent_calls=5,
        )
        assert client._semaphore._value == 5

    def test_api_key_not_logged_or_printed(self):
        """Verify api_key is stored internally but not exposed via public API."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        # No public property exposes the raw api_key
        assert not hasattr(client, "api_key")


# ── LLMClient._get_fallback_models ──────────────────────────────────────


class TestLLMClientGetFallbackModels:
    """Tests for LLMClient._get_fallback_models failover filtering."""

    def test_returns_all_models_when_all_healthy(self):
        """Verify all fallback models returned when none are unhealthy."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3", "llama-3"],
        )
        result = client._get_fallback_models()
        assert result == ["gpt-4o", "claude-3", "llama-3"]

    def test_excludes_unhealthy_model(self):
        """Verify unhealthy models are excluded from fallback list."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3", "llama-3"],
        )
        # Mark claude-3 as unhealthy
        tracker = client.health_tracker
        for _ in range(6):
            tracker.record_failure("claude-3")

        result = client._get_fallback_models()
        assert "claude-3" not in result
        assert "gpt-4o" in result
        assert "llama-3" in result

    def test_returns_empty_when_all_models_unhealthy(self):
        """Verify empty list returned when all models are unhealthy."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3"],
        )
        tracker = client.health_tracker
        for _ in range(6):
            tracker.record_failure("gpt-4o")
            tracker.record_failure("claude-3")

        result = client._get_fallback_models()
        assert result == []

    def test_respects_health_tracker_threshold(self):
        """Verify exclusion respects custom failure_threshold."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3"],
            failure_threshold=0.8,
        )
        tracker = client.health_tracker
        # With threshold 0.8 and window 10: need > 8 consecutive failures
        for _ in range(8):
            tracker.record_failure("claude-3")
        assert not tracker.should_exclude("claude-3")

        for _ in range(1):
            tracker.record_failure("claude-3")
        assert tracker.should_exclude("claude-3")

        result = client._get_fallback_models()
        assert "claude-3" not in result


# ── LLMClient._resolve_base_url ─────────────────────────────────────────


class TestLLMClientResolveBaseUrl:
    """Tests for LLMClient._resolve_base_url URL resolution logic."""

    def test_returns_default_url_for_unknown_model(self):
        """Verify default base_url returned when model not in fallback_base_urls."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._resolve_base_url("unknown-model", "http://localhost:11434/v1")
        assert result == "http://localhost:11434/v1"

    def test_returns_fallback_url_when_model_has_override(self):
        """Verify fallback base_url returned when model has override."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
            fallback_base_urls={"claude-3": "https://api.anthropic.com/v1"},
        )
        result = client._resolve_base_url("claude-3", "http://localhost:11434/v1")
        assert result == "https://api.anthropic.com/v1"

    def test_primary_model_defaults_to_default_url(self):
        """Verify primary model uses default base_url when no override."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._resolve_base_url("gpt-4o", "http://localhost:11434/v1")
        assert result == "http://localhost:11434/v1"

    def test_empty_fallback_base_urls_defaults_to_default(self):
        """Verify empty fallback_base_urls dict defaults to default URL."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
            fallback_base_urls={},
        )
        result = client._resolve_base_url("gpt-4o", "http://localhost:11434/v1")
        assert result == "http://localhost:11434/v1"


# ── LLMClient._generate_fallback_queries ────────────────────────────────


class TestLLMClientGenerateFallbackQueries:
    """Tests for LLMClient._generate_fallback_queries fallback generation."""

    def test_fallback_queries_at_least_three(self):
        """Verify _generate_fallback_queries produces at least 3 queries."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._generate_fallback_queries("test query", 5)
        assert len(result) >= 3

    def test_fallback_queries_includes_original_prompt(self):
        """Verify fallback queries include the original prompt."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._generate_fallback_queries("my prompt", 5)
        assert "my prompt" in result

    def test_fallback_queries_respects_query_count_cap(self):
        """Verify fallback queries are capped at query_count."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._generate_fallback_queries("test query", 3)
        assert len(result) <= 3

    def test_fallback_queries_no_duplicates(self):
        """Verify fallback queries are deduplicated."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._generate_fallback_queries("test query", 5)
        assert len(result) == len(set(result))

    def test_fallback_queries_includes_variations(self):
        """Verify fallback queries include prompt variations."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._generate_fallback_queries("test query", 5)
        assert "test query example" in result
        assert "related to test query" in result

    def test_fallback_queries_with_empty_prompt(self):
        """Verify fallback queries work with an empty prompt."""
        client = LLMClient(
            api_key="sk-test-key-placeholder",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._generate_fallback_queries("", 5)
        assert len(result) >= 3


# ── create_llm_client Factory ───────────────────────────────────────────


class TestCreateLLMClientFactory:
    """Tests for create_llm_client factory function."""

    def test_valid_env_returns_llm_client_instance(self, monkeypatch):
        """Verify factory returns LLMClient when LLM_API_KEY is present."""
        monkeypatch.setenv("LLM_API_KEY", "PLACEHOLDER_KEY")
        from app.core.llm_client import create_llm_client

        client = create_llm_client()

        assert client is not None
        assert hasattr(client, "_call_with_failover")
        assert hasattr(client, "health_tracker")

    def test_missing_llm_api_key_raises_value_error(self, monkeypatch):
        """Verify factory raises ValueError when LLM_API_KEY is absent."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        from app.core.llm_client import create_llm_client

        with pytest.raises(ValueError, match="LLM_API_KEY is required"):
            create_llm_client()

    def test_fallback_chain_from_settings_contains_three_models(self, monkeypatch):
        """Verify fallback chain from Settings default contains 3 models."""
        monkeypatch.setenv("LLM_API_KEY", "PLACEHOLDER_KEY")
        from app.core.config import Settings
        from app.core.llm_client import create_llm_client

        settings = Settings()
        client = create_llm_client()

        assert len(settings.LLM_MODEL_FALLBACK_CHAIN) == 3
        assert client._fallback_chain == settings.LLM_MODEL_FALLBACK_CHAIN

    def test_health_window_and_threshold_from_settings(self, monkeypatch):
        """Verify health_window and failure_threshold come from Settings."""
        monkeypatch.setenv("LLM_API_KEY", "PLACEHOLDER_KEY")
        from app.core.config import Settings
        from app.core.llm_client import create_llm_client

        settings = Settings()
        client = create_llm_client()

        assert client.health_tracker.health_window == settings.LLM_HEALTH_WINDOW
        assert (
            client.health_tracker.failure_threshold
            == settings.LLM_HEALTH_FAILURE_THRESHOLD
        )

    def test_base_url_from_settings_default(self, monkeypatch):
        """Verify base_url uses Settings default when LLM_BASE_URL not set."""
        monkeypatch.setenv("LLM_API_KEY", "PLACEHOLDER_KEY")
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        from app.core.llm_client import create_llm_client

        client = create_llm_client()

        assert client._default_base_url == "http://localhost:11434/v1"


# ── _call_with_failover Integration ─────────────────────────────────────


class TestCallWithFailoverIntegration:
    """Integration tests for LLMClient._call_with_failover with mocked client."""

    def _make_client(
        self,
        model: str = "gpt-4o",
        fallback_chain: list[str] | None = None,
        api_key: str = "PLACEHOLDER_KEY",
        base_url: str = "http://localhost:11434/v1",
        fallback_base_urls: dict[str, str] | None = None,
    ) -> LLMClient:
        """Helper to create an LLMClient with test params."""
        return LLMClient(
            api_key=api_key,
            base_url=base_url,
            model=model,
            fallback_chain=fallback_chain or [model],
            fallback_base_urls=fallback_base_urls,
        )

    def test_success_on_primary_returns_response_and_active_model(self):
        """Verify primary model success returns response and sets active_model."""
        client = self._make_client()
        mock_response = MagicMock()

        with patch.object(client, "_get_client") as mock_get_client:
            mock_instructor = AsyncMock()
            mock_instructor.chat.completions.create = AsyncMock(
                return_value=mock_response
            )
            mock_get_client.return_value = mock_instructor

            result = asyncio.run(
                client._call_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                    response_model=str,
                )
            )

        assert result is mock_response
        assert client.active_model == "gpt-4o"

    def test_rate_limit_error_on_primary_retries_and_failover(self):
        """Verify RateLimitError on primary triggers retry then failover to next model."""
        client = self._make_client(
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3"],
        )
        mock_response = MagicMock()

        with patch.object(client, "_get_client") as mock_get_client:
            primary_mock = AsyncMock()
            primary_mock.chat.completions.create = AsyncMock(
                side_effect=RateLimitError(
                    message="rate limited",
                    response=MagicMock(status_code=429),
                    body={},
                )
            )

            fallback_mock = AsyncMock()
            fallback_mock.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            mock_get_client.side_effect = [primary_mock, fallback_mock]

            result = asyncio.run(
                client._call_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                    response_model=str,
                    max_retries_per_model=3,
                )
            )

        assert result is mock_response
        assert client.active_model == "claude-3"
        # Primary should have been called 3 times (retries)
        assert primary_mock.chat.completions.create.call_count == 3
        # Fallback should have been called once
        assert fallback_mock.chat.completions.create.call_count == 1

    def test_max_retries_per_model_respects_custom_limit(self):
        """Verify _call_with_failover respects custom max_retries_per_model parameter."""
        client = self._make_client(
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3"],
        )
        mock_response = MagicMock()

        with patch.object(client, "_get_client") as mock_get_client:
            primary_mock = AsyncMock()
            primary_mock.chat.completions.create = AsyncMock(
                side_effect=RateLimitError(
                    message="rate limited",
                    response=MagicMock(status_code=429),
                    body={},
                )
            )

            fallback_mock = AsyncMock()
            fallback_mock.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            mock_get_client.side_effect = [primary_mock, fallback_mock]

            result = asyncio.run(
                client._call_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                    response_model=str,
                    max_retries_per_model=2,
                )
            )

        assert result is mock_response
        assert client.active_model == "claude-3"
        # Primary should have been called exactly 2 times (custom limit)
        assert primary_mock.chat.completions.create.call_count == 2
        # Fallback should have been called once
        assert fallback_mock.chat.completions.create.call_count == 1

    def test_connection_error_on_primary_immediate_failover(self):
        """Verify ConnectionError on primary breaks retries and failovers immediately."""
        client = self._make_client(
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3"],
        )
        mock_response = MagicMock()

        with patch.object(client, "_get_client") as mock_get_client:
            primary_mock = AsyncMock()
            primary_mock.chat.completions.create = AsyncMock(
                side_effect=ConnectionError("connection refused")
            )

            fallback_mock = AsyncMock()
            fallback_mock.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            mock_get_client.side_effect = [primary_mock, fallback_mock]

            result = asyncio.run(
                client._call_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                    response_model=str,
                    max_retries_per_model=3,
                )
            )

        assert result is mock_response
        assert client.active_model == "claude-3"
        # Primary should have been called only once (no retries for ConnectionError)
        assert primary_mock.chat.completions.create.call_count == 1

    def test_all_models_exhausted_raises_llm_chain_exhausted_error(self):
        """Verify LLMChainExhaustedError raised when all models fail."""
        client = self._make_client(
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3"],
        )

        with patch.object(client, "_get_client") as mock_get_client:
            mock_primary = AsyncMock()
            mock_primary.chat.completions.create = AsyncMock(
                side_effect=Exception("generic failure")
            )

            mock_fallback = AsyncMock()
            mock_fallback.chat.completions.create = AsyncMock(
                side_effect=Exception("generic failure")
            )

            mock_get_client.side_effect = [mock_primary, mock_fallback]

            with pytest.raises(LLMChainExhaustedError) as exc_info:
                asyncio.run(
                    client._call_with_failover(
                        messages=[{"role": "user", "content": "test"}],
                        response_model=str,
                    )
                )

        assert "gpt-4o" in exc_info.value.failed_models
        assert "claude-3" in exc_info.value.failed_models

    def test_health_tracker_records_success_after_failover(self):
        """Verify health tracker records success on the failover model."""
        client = self._make_client(
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3"],
        )
        mock_response = MagicMock()

        with patch.object(client, "_get_client") as mock_get_client:
            primary_mock = AsyncMock()
            primary_mock.chat.completions.create = AsyncMock(
                side_effect=RateLimitError(
                    message="rate limited",
                    response=MagicMock(status_code=429),
                    body={},
                )
            )

            fallback_mock = AsyncMock()
            fallback_mock.chat.completions.create = AsyncMock(
                return_value=mock_response
            )

            mock_get_client.side_effect = [primary_mock, fallback_mock]

            asyncio.run(
                client._call_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                    response_model=str,
                )
            )

        assert client.health_tracker.get_health_score("claude-3") == 1.0

    def test_health_tracker_records_failure_after_failover(self):
        """Verify health tracker records failure on a non-retryable error model."""
        client = self._make_client(
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3", "llama-3"],
        )

        with patch.object(client, "_get_client") as mock_get_client:
            primary_mock = AsyncMock()
            primary_mock.chat.completions.create = AsyncMock(
                side_effect=RateLimitError(
                    message="rate limited",
                    response=MagicMock(status_code=429),
                    body={},
                )
            )

            fallback_mock = AsyncMock()
            fallback_mock.chat.completions.create = AsyncMock(
                side_effect=Exception("non-retryable error")
            )

            final_mock = AsyncMock()
            final_mock.chat.completions.create = AsyncMock(
                side_effect=Exception("non-retryable error")
            )

            mock_get_client.side_effect = [primary_mock, fallback_mock, final_mock]

            with pytest.raises(LLMChainExhaustedError):
                asyncio.run(
                    client._call_with_failover(
                        messages=[{"role": "user", "content": "test"}],
                        response_model=str,
                    )
                )

        assert client.health_tracker.get_health_score("claude-3") == 0.0
        assert client.health_tracker.get_health_score("llama-3") == 0.0

    def test_client_cache_reused_per_api_key_base_url_pair(self):
        """Verify _client_cache reuses the same instructor client per (api_key, base_url)."""
        client = self._make_client(
            api_key="sk-test-key-1",
            base_url="http://localhost:11434/v1",
        )

        mock_instructor = AsyncMock()
        mock_instructor.chat.completions.create = AsyncMock(return_value=MagicMock())

        with patch(
            "app.core.llm_client.instructor.from_openai",
            return_value=mock_instructor,
        ):
            asyncio.run(
                client._call_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                    response_model=str,
                )
            )

        # First call should have created a cache entry
        cache_key = ("sk-test-key-1", "http://localhost:11434/v1")
        assert cache_key in client._client_cache
        assert len(client._client_cache) == 1

        # Second call with same key should reuse the cached client (no new from_openai call)
        with patch(
            "app.core.llm_client.instructor.from_openai",
            return_value=mock_instructor,
        ) as mock_from_openai:
            asyncio.run(
                client._call_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                    response_model=str,
                )
            )

        # from_openai should not be called again — cache was reused
        assert mock_from_openai.call_count == 0

    def test_get_fallback_models_excludes_unhealthy_models(self):
        """Verify _get_fallback_models excludes models marked unhealthy."""
        client = self._make_client(
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3", "llama-3"],
        )

        # Mark claude-3 as unhealthy
        tracker = client.health_tracker
        for _ in range(6):
            tracker.record_failure("claude-3")

        fallback_models = client._get_fallback_models()

        assert "gpt-4o" in fallback_models
        assert "claude-3" not in fallback_models
        assert "llama-3" in fallback_models

    def test_3_model_chain_primary_success_health_tracker_records(self):
        """Verify 3-model chain with primary success records health tracker events."""
        client = self._make_client(
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3", "llama-3"],
        )
        mock_response = MagicMock()

        with patch.object(client, "_get_client") as mock_get_client:
            mock_instructor = AsyncMock()
            mock_instructor.chat.completions.create = AsyncMock(
                return_value=mock_response
            )
            mock_get_client.return_value = mock_instructor

            result = asyncio.run(
                client._call_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                    response_model=str,
                )
            )

        assert result is mock_response
        assert client.active_model == "gpt-4o"
        assert client.health_tracker.get_health_score("gpt-4o") == 1.0
        assert client.health_tracker._success_counts["gpt-4o"] == 1
        # Fallback models should not have any events
        assert "claude-3" not in client.health_tracker._success_counts
        assert "llama-3" not in client.health_tracker._success_counts

    def test_3_model_chain_primary_rate_limit_failover_model2_success(self):
        """Verify 3-model chain: primary RateLimitError → retry → failover model 2 → success."""
        client = self._make_client(
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3", "llama-3"],
            fallback_base_urls={
                "gpt-4o": "https://openai.example.com/v1",
                "claude-3": "https://anthropic.example.com/v1",
                "llama-3": "https://ollama.example.com/v1",
            },
        )
        mock_response = MagicMock()

        with patch.object(client, "_get_client") as mock_get_client:
            primary_mock = AsyncMock()
            primary_mock.chat.completions.create = AsyncMock(
                side_effect=RateLimitError(
                    message="rate limited",
                    response=MagicMock(status_code=429),
                    body={},
                )
            )

            model2_mock = AsyncMock()
            model2_mock.chat.completions.create = AsyncMock(return_value=mock_response)

            mock_get_client.side_effect = [primary_mock, model2_mock]

            result = asyncio.run(
                client._call_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                    response_model=str,
                    max_retries_per_model=3,
                )
            )

        assert result is mock_response
        assert client.active_model == "claude-3"
        assert primary_mock.chat.completions.create.call_count == 3
        assert model2_mock.chat.completions.create.call_count == 1
        assert client.health_tracker.get_health_score("claude-3") == 1.0
        assert (
            client.health_tracker._failure_counts.get("gpt-4o", 0) == 0
        )  # RateLimitError does NOT record as health failure (H1 fix)
        assert client.health_tracker._success_counts.get("claude-3", 0) == 1

    def test_3_model_chain_primary_connection_error_model2_rate_limit_model3_success(
        self,
    ):
        """Verify 3-model chain: primary ConnectionError → failover model 2 RateLimitError → failover model 3 → success."""
        client = self._make_client(
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3", "llama-3"],
            fallback_base_urls={
                "gpt-4o": "https://openai.example.com/v1",
                "claude-3": "https://anthropic.example.com/v1",
                "llama-3": "https://ollama.example.com/v1",
            },
        )
        mock_response = MagicMock()

        with patch.object(client, "_get_client") as mock_get_client:
            primary_mock = AsyncMock()
            primary_mock.chat.completions.create = AsyncMock(
                side_effect=ConnectionError("connection refused")
            )

            model2_mock = AsyncMock()
            model2_mock.chat.completions.create = AsyncMock(
                side_effect=RateLimitError(
                    message="rate limited",
                    response=MagicMock(status_code=429),
                    body={},
                )
            )

            model3_mock = AsyncMock()
            model3_mock.chat.completions.create = AsyncMock(return_value=mock_response)

            mock_get_client.side_effect = [primary_mock, model2_mock, model3_mock]

            result = asyncio.run(
                client._call_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                    response_model=str,
                    max_retries_per_model=3,
                )
            )

        assert result is mock_response
        assert client.active_model == "llama-3"
        assert primary_mock.chat.completions.create.call_count == 1
        assert model2_mock.chat.completions.create.call_count == 3
        assert model3_mock.chat.completions.create.call_count == 1
        assert client.health_tracker.get_health_score("llama-3") == 1.0

    def test_3_model_chain_all_models_fail_llm_chain_exhausted_error(self):
        """Verify 3-model chain: all models fail → LLMChainExhaustedError raised."""
        client = self._make_client(
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3", "llama-3"],
            fallback_base_urls={
                "gpt-4o": "https://openai.example.com/v1",
                "claude-3": "https://anthropic.example.com/v1",
                "llama-3": "https://ollama.example.com/v1",
            },
        )

        with patch.object(client, "_get_client") as mock_get_client:
            primary_mock = AsyncMock()
            primary_mock.chat.completions.create = AsyncMock(
                side_effect=ConnectionError("connection refused")
            )

            model2_mock = AsyncMock()
            model2_mock.chat.completions.create = AsyncMock(
                side_effect=RateLimitError(
                    message="rate limited",
                    response=MagicMock(status_code=429),
                    body={},
                )
            )

            model3_mock = AsyncMock()
            model3_mock.chat.completions.create = AsyncMock(
                side_effect=Exception("model 3 crashed")
            )

            mock_get_client.side_effect = [primary_mock, model2_mock, model3_mock]

            with pytest.raises(LLMChainExhaustedError) as exc_info:
                asyncio.run(
                    client._call_with_failover(
                        messages=[{"role": "user", "content": "test"}],
                        response_model=str,
                        max_retries_per_model=3,
                    )
                )

            # Only non-retryable Exception adds to failed_models.
            # last_error carries the final error across all attempts.
            assert exc_info.value.failed_models == ["llama-3"]
            assert isinstance(exc_info.value.last_error, Exception)
            assert "llama-3" in str(exc_info.value)

    def test_health_tracker_consecutive_failures_threshold_model_excluded(self):
        """Verify health tracker excludes model after N consecutive failures exceeds threshold."""
        client = self._make_client(
            model="gpt-4o",
            fallback_chain=["gpt-4o", "claude-3", "llama-3"],
        )
        # Default: health_window=10, failure_threshold=0.5 → threshold_count = 5
        # Need > 5 consecutive failures to exclude
        tracker = client.health_tracker
        for _ in range(5):
            tracker.record_failure("claude-3")
        assert not tracker.should_exclude("claude-3")

        tracker.record_failure("claude-3")  # 6th consecutive failure
        assert tracker.should_exclude("claude-3")

        # Verify excluded model removed from fallback chain
        fallback = client._get_fallback_models()
        assert "claude-3" not in fallback
        assert "gpt-4o" in fallback
        assert "llama-3" in fallback

        # Verify interleaved success resets consecutive count
        tracker.record_success("claude-3")
        for _ in range(3):
            tracker.record_failure("claude-3")
        assert not tracker.should_exclude("claude-3")


# ── TestResolveBaseUrlSSRF ──────────────────────────────────────────────


class TestResolveBaseUrlSSRF:
    """Tests for _resolve_base_url SSRF protection edge cases."""

    def test_https_url_accepted(self):
        """Verify https URL is accepted and returned unchanged."""
        client = LLMClient(
            api_key="PLACEHOLDER_KEY",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._resolve_base_url("test-model", "https://api.example.com/v1")
        assert result == "https://api.example.com/v1"

    def test_http_url_accepted(self):
        """Verify http URL is accepted and returned unchanged."""
        client = LLMClient(
            api_key="PLACEHOLDER_KEY",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._resolve_base_url("test-model", "http://api.example.com/v1")
        assert result == "http://api.example.com/v1"

    def test_ftp_url_denied_fallback_returned(self):
        """Verify ftp URL scheme denied, default fallback returned."""
        client = LLMClient(
            api_key="PLACEHOLDER_KEY",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._resolve_base_url("test-model", "ftp://files.example.com/data")
        assert result == "http://localhost:11434/v1"

    def test_localhost_hostname_denied_fallback_returned(self):
        """Verify localhost hostname denied, default fallback returned."""
        client = LLMClient(
            api_key="PLACEHOLDER_KEY",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._resolve_base_url("test-model", "http://localhost:8080/v1")
        assert result == "http://localhost:11434/v1"

    def test_127_0_0_1_denied_fallback_returned(self):
        """Verify 127.0.0.1 IP denied, default fallback returned."""
        client = LLMClient(
            api_key="PLACEHOLDER_KEY",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._resolve_base_url("test-model", "http://127.0.0.1:9090/v1")
        assert result == "http://localhost:11434/v1"

    def test_192_168_1_1_private_ip_denied_fallback_returned(self):
        """Verify 192.168.1.1 (RFC 1918 private) denied, default fallback returned."""
        client = LLMClient(
            api_key="PLACEHOLDER_KEY",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._resolve_base_url("test-model", "http://192.168.1.1:3000/v1")
        assert result == "http://localhost:11434/v1"

    def test_google_com_dns_hostname_accepted(self):
        """Verify google.com DNS hostname accepted and returned."""
        client = LLMClient(
            api_key="PLACEHOLDER_KEY",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )
        result = client._resolve_base_url("test-model", "https://google.com/v1")
        assert result == "https://google.com/v1"

    def test_custom_fallback_base_urls_override_applied(self):
        """Verify custom fallback_base_urls override applied for known model."""
        client = LLMClient(
            api_key="PLACEHOLDER_KEY",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
            fallback_base_urls={
                "claude-3": "https://api.anthropic.com/v1",
                "llama-3": "https://ollama.example.com:11434/v1",
            },
        )
        # claude-3 has custom override → accepted
        result_claude = client._resolve_base_url(
            "claude-3", "http://localhost:11434/v1"
        )
        assert result_claude == "https://api.anthropic.com/v1"

        # llama-3 has custom override with DNS hostname → accepted
        result_llama = client._resolve_base_url("llama-3", "http://localhost:11434/v1")
        assert result_llama == "https://ollama.example.com:11434/v1"

        # unknown model → uses default
        result_unknown = client._resolve_base_url(
            "unknown-model", "http://localhost:11434/v1"
        )
        assert result_unknown == "http://localhost:11434/v1"


# ── LLMClient Public Methods ────────────────────────────────────────────


class TestLLMClientPublicMethods:
    """Tests for 4 public LLMClient methods with mocked _call_with_failover."""

    def _make_client(self) -> LLMClient:
        """Helper to create an LLMClient with test-safe params."""
        return LLMClient(
            api_key="PLACEHOLDER_KEY",
            base_url="http://localhost:11434/v1",
            model="gpt-4o",
        )

    def test_generate_features_returns_populated_feature_set(self):
        """Verify generate_features returns FeatureSet with populated features."""
        client = self._make_client()
        mock_features = FeatureSet(
            features=["f1", "f2"], sources=["https://s.example.com"]
        )

        with patch.object(
            client, "_call_with_failover", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = mock_features

            result = asyncio.run(client.generate_features("query", ["content"]))

        assert result is mock_features
        assert result.features == ["f1", "f2"]
        assert result.sources == ["https://s.example.com"]

    def test_generate_features_handles_exception_from_failover(self):
        """Verify generate_features propagates Exception from _call_with_failover."""
        client = self._make_client()

        with patch.object(
            client, "_call_with_failover", new_callable=AsyncMock
        ) as mock_call:
            mock_call.side_effect = Exception("llm error")

            with pytest.raises(Exception, match="llm error"):
                asyncio.run(client.generate_features("query", ["content"]))

    def test_judge_features_returns_pass_verdict(self):
        """Verify judge_features returns JudgeVerdict with pass verdict."""
        client = self._make_client()
        mock_features = FeatureSet(features=["f1"])
        mock_verdict = JudgeVerdict(verdict="pass", score=0.9)

        with patch.object(
            client, "_call_with_failover", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = mock_verdict

            result = asyncio.run(client.judge_features("query", mock_features))

        assert result is mock_verdict
        assert result.verdict == "pass"
        assert result.score == 0.9

    def test_judge_features_returns_reject_verdict(self):
        """Verify judge_features returns JudgeVerdict with reject verdict."""
        client = self._make_client()
        mock_features = FeatureSet(features=["f1"])
        mock_verdict = JudgeVerdict(
            verdict="reject", score=0.3, reasons=["hallucination"]
        )

        with patch.object(
            client, "_call_with_failover", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = mock_verdict

            result = asyncio.run(client.judge_features("query", mock_features))

        assert result is mock_verdict
        assert result.verdict == "reject"
        assert result.reasons == ["hallucination"]

    def test_rate_relevance_clamps_score_0_95(self):
        """Verify rate_relevance returns 0.95 when mock returns 0.95 (within range)."""
        client = self._make_client()

        with patch.object(
            client, "_call_with_failover", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = 0.95

            result = asyncio.run(client.rate_relevance("text", "query"))

        assert result == 0.95

    def test_rate_relevance_clamps_score_1_5_to_1_0(self):
        """Verify rate_relevance clamps 1.5 to 1.0 (upper bound)."""
        client = self._make_client()

        with patch.object(
            client, "_call_with_failover", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = 1.5

            result = asyncio.run(client.rate_relevance("text", "query"))

        assert result == 1.0

    def test_rate_relevance_clamps_score_negative_to_0_0(self):
        """Verify rate_relevance clamps -0.5 to 0.0 (lower bound)."""
        client = self._make_client()

        with patch.object(
            client, "_call_with_failover", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = -0.5

            result = asyncio.run(client.rate_relevance("text", "query"))

        assert result == 0.0

    def test_generate_final_answer_returns_string(self):
        """Verify generate_final_answer returns the string from _call_with_failover."""
        client = self._make_client()
        mock_answer = "Final synthesized answer."

        with patch.object(
            client, "_call_with_failover", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = mock_answer

            result = asyncio.run(client.generate_final_answer("query", ["f1", "f2"]))

        assert result == "Final synthesized answer."
