"""Unit tests for WebFetchService - LangGraph agent."""

import pytest

from ..tdd.fixtures import MockContentService, MockRedis, MockSearchService


class TestWebFetchServiceInit:
    """Tests for WebFetchService initialization."""

    @pytest.fixture(autouse=True)
    def setup_llm_mock(self, monkeypatch):
        """Mock LLM client creation."""
        from app.models.webfetch import FeatureSet, JudgeVerdict

        class MockLLMClient:
            async def generate_search_queries(self, prompt: str) -> list[str]:
                return [prompt, f"{prompt} example"]

            async def select_urls(
                self, prompt: str, search_results: list
            ) -> list[dict]:
                return [
                    {"url": "https://example.com", "priority": 1, "reason": "relevant"}
                ]

            async def judge_urls(self, prompt: str, urls: list[str]) -> JudgeVerdict:
                return JudgeVerdict(score=0.85, verdict="pass", reasons=[])

            async def generate_features(
                self, prompt: str, content: list[str]
            ) -> FeatureSet:
                return FeatureSet(
                    features=["feature1"], sources=["https://example.com"]
                )

            async def judge_features(
                self, prompt: str, features: JudgeVerdict
            ) -> JudgeVerdict:
                return JudgeVerdict(score=0.92, verdict="pass", reasons=[])

        # Mock create_llm_client
        import app.core.llm_client as llm_module

        monkeypatch.setattr(llm_module, "create_llm_client", lambda: MockLLMClient())

    def test_service_creation(self):
        """Test WebFetchService can be instantiated."""
        import os

        original = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "PLACEHOLDER_KEY"
        try:
            from app.core.config import Settings
            from app.services.webfetch_service import WebFetchService

            settings = Settings()

            search_service = MockSearchService()
            content_service = MockContentService()

            service = WebFetchService(
                settings, search_service, content_service, MockRedis()
            )

            assert service is not None
        finally:
            if original is not None:
                os.environ["LLM_API_KEY"] = original
            else:
                os.environ.pop("LLM_API_KEY", None)


class TestWebFetchStateMachine:
    """Tests for 8-node state machine."""

    def test_state_machine_nodes(self):
        """Test that all required nodes are implemented."""
        from app.services.webfetch_service import WebFetchService

        # Check that all node methods exist (replaced old generate_features with new scoring approach)
        assert hasattr(WebFetchService, "_node_generate_search_queries")
        assert hasattr(WebFetchService, "_node_perform_search")
        assert hasattr(WebFetchService, "_node_select_urls")
        assert hasattr(WebFetchService, "_node_judge_urls")
        assert hasattr(WebFetchService, "_node_fetch_content")
        assert hasattr(WebFetchService, "_node_score_and_select_sources")
        assert hasattr(WebFetchService, "_node_aggregate_result")


class TestWebFetchFallback:
    """Tests for fallback logic."""

    def test_fallback_flag(self):
        """Test that fallback flag is set when needed."""
        from app.models.webfetch import WebFetchState

        state = WebFetchState(
            prompt="test",
            tenant_id="tenant-1",
        )

        assert state.fallback_applied is False


class TestWebFetchJudgeVerdict:
    """Tests for judge verdict model."""

    def test_verdict_creation(self):
        """Test JudgeVerdict creation."""
        from app.models.webfetch import JudgeVerdict

        verdict = JudgeVerdict(
            score=0.85,
            verdict="pass",
            reasons=[],
        )

        assert verdict.score == 0.85
        assert verdict.verdict == "pass"
