"""Unit tests for LLMHealthProbe — background periodic health probe lifecycle."""

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

from app.core.llm_client import LLMHealthProbe, LLMHealthTracker


class MockSettings:
    """Minimal Settings mock for LLMHealthProbe tests."""

    LLM_HEALTH_PROBE_INTERVAL = 60
    available_llm_models = ["gpt-4o", "claude-3", "llama-3"]

    def _resolve_llm_key_name(self, model: str) -> str | None:
        """Map model to env key name. Returns None for models without key requirement."""
        mapping = {
            "gpt-4o": "LLM_API_KEY",
            "claude-3": "CLAUDE_API_KEY",
            "llama-3": None,
        }
        return mapping.get(model)


class TestLLMHealthProbeLifecycle:
    """Tests for LLMHealthProbe start/stop lifecycle."""

    def test_probe_start_stop_lifecycle(self):
        """Verify probe can start and stop cleanly without errors."""
        tracker = LLMHealthTracker()
        settings = MockSettings()
        probe = LLMHealthProbe(tracker, settings, interval=1)

        assert not probe._running
        assert probe._task is None

        async def lifecycle():
            await probe.start()
            assert probe._running
            assert probe._task is not None
            # Give the probe loop one iteration to begin
            await asyncio.sleep(0.01)
            await probe.stop()
            assert not probe._running
            assert probe._task is None

        asyncio.run(lifecycle())

    def test_probe_start_when_already_running_noop(self):
        """Verify calling start() while already running is a no-op."""
        tracker = LLMHealthTracker()
        settings = MockSettings()
        probe = LLMHealthProbe(tracker, settings, interval=1)

        asyncio.run(probe.start())
        task = probe._task

        asyncio.run(probe.start())
        assert probe._task is task  # same task, not recreated
        assert probe._running

    def test_probe_stop_when_not_running_noop(self):
        """Verify calling stop() while not running is a no-op."""
        tracker = LLMHealthTracker()
        settings = MockSettings()
        probe = LLMHealthProbe(tracker, settings, interval=1)

        asyncio.run(probe.stop())
        assert not probe._running
        assert probe._task is None


class TestLLMHealthProbeModelFiltering:
    """Tests for LLMHealthProbe model selection and filtering."""

    def test_probe_skips_no_api_key_models(self):
        """Verify probe skips models whose API key env var is not set."""
        tracker = LLMHealthTracker()
        settings = MockSettings()

        with patch.dict(os.environ, {"LLM_API_KEY": "PLACEHOLDER_KEY"}, clear=False):
            if "CLAUDE_API_KEY" in os.environ:
                del os.environ["CLAUDE_API_KEY"]

            probe = LLMHealthProbe(tracker, settings, interval=1)

            probed_models: list[str] = []

            async def real_probe_all():
                """Call real _probe_single for each model — the real code does the key check."""
                for model in settings.available_llm_models:
                    await probe._probe_single(model)
                    # Track via health tracker — only probed models get record_success
                    if model in tracker._success_counts:
                        probed_models.append(model)

            with patch.object(
                probe, "_probe_all_models", new_callable=AsyncMock
            ) as mock_probe_all:
                mock_probe_all.side_effect = real_probe_all

                # Mock os.getenv to return None for CLAUDE_API_KEY
                original_getenv = os.getenv

                def mock_getenv(name, default=None):
                    if name == "CLAUDE_API_KEY":
                        return None
                    return original_getenv(name, default)

                with patch("os.getenv", side_effect=mock_getenv):
                    # Mock instructor.from_openai to return a mock client that succeeds
                    mock_probe_client = AsyncMock()
                    mock_probe_client.chat.completions.create = AsyncMock(
                        return_value="pong"
                    )

                    with patch(
                        "app.core.llm_client.instructor.from_openai",
                        return_value=mock_probe_client,
                    ):

                        async def run_probe():
                            await probe.start()
                            await asyncio.sleep(0.05)
                            await probe.stop()

                        asyncio.run(run_probe())

            # claude-3 should be skipped (no CLAUDE_API_KEY)
            assert "claude-3" not in probed_models
            # gpt-4o and llama-3 should be probed
            assert "gpt-4o" in probed_models
            assert "llama-3" in probed_models

    def test_probe_all_models_when_all_keys_available(self):
        """Verify probe attempts all models when all API keys are set."""
        tracker = LLMHealthTracker()
        settings = MockSettings()

        with patch.dict(
            os.environ,
            {
                "LLM_API_KEY": "PLACEHOLDER_KEY",
                "CLAUDE_API_KEY": "sk-claude-placeholder",
            },
            clear=False,
        ):
            probe = LLMHealthProbe(tracker, settings, interval=1)

            probed_models: list[str] = []

            async def track_probe(model: str):
                probed_models.append(model)
                tracker.record_success(model)

            with patch.object(
                probe, "_probe_single", new_callable=AsyncMock
            ) as mock_probe_single:
                mock_probe_side_effect = AsyncMock(side_effect=track_probe)
                mock_probe_single.side_effect = mock_probe_side_effect

                async def run_probe():
                    await probe.start()
                    await asyncio.sleep(0.05)
                    await probe.stop()

                asyncio.run(run_probe())

            assert "gpt-4o" in probed_models
            assert "claude-3" in probed_models
            assert "llama-3" in probed_models


class TestLLMHealthProbeHealthTrackerInteraction:
    """Tests for LLMHealthProbe interaction with LLMHealthTracker."""

    def test_probe_success_updates_tracker(self):
        """Verify probe success calls record_success on the tracker."""
        tracker = LLMHealthTracker()
        settings = MockSettings()

        with patch.dict(os.environ, {"LLM_API_KEY": "PLACEHOLDER_KEY"}, clear=False):
            probe = LLMHealthProbe(tracker, settings, interval=1)

            mock_probe_client = AsyncMock()
            mock_probe_client.chat.completions.create = AsyncMock(return_value="pong")

            with patch(
                "app.core.llm_client.instructor.from_openai",
                return_value=mock_probe_client,
            ):

                async def run_probe():
                    await probe.start()
                    await asyncio.sleep(0.05)
                    await probe.stop()

                asyncio.run(run_probe())

        assert tracker._success_counts.get("gpt-4o", 0) >= 1
        assert tracker.get_health_score("gpt-4o") == pytest.approx(
            1 / tracker._success_counts.get("gpt-4o", 1)
        )

    def test_probe_failure_not_counted(self):
        """Verify probe error does NOT call record_failure on the tracker."""
        tracker = LLMHealthTracker()
        settings = MockSettings()

        with patch.dict(os.environ, {"LLM_API_KEY": "PLACEHOLDER_KEY"}, clear=False):
            probe = LLMHealthProbe(tracker, settings, interval=1)

            mock_probe_client = AsyncMock()
            mock_probe_client.chat.completions.create = AsyncMock(
                side_effect=ConnectionError("probe connection failed")
            )

            with patch(
                "app.core.llm_client.instructor.from_openai",
                return_value=mock_probe_client,
            ):

                async def run_probe():
                    await probe.start()
                    await asyncio.sleep(0.05)
                    await probe.stop()

                asyncio.run(run_probe())

        # Probe failure should NOT increment failure count
        assert tracker._failure_counts.get("gpt-4o", 0) == 0
        # Health score should remain 1.0 (no events recorded)
        assert tracker.get_health_score("gpt-4o") == 1.0

    def test_probe_loop_error_does_not_crash(self):
        """Verify probe loop error is logged but does not stop the probe."""
        tracker = LLMHealthTracker()
        settings = MockSettings()

        with patch.dict(os.environ, {"LLM_API_KEY": "PLACEHOLDER_KEY"}, clear=False):
            probe = LLMHealthProbe(tracker, settings, interval=1)

            # Make persist_health raise an error
            tracker.persist_health = AsyncMock(
                side_effect=RuntimeError("redis unavailable")
            )

            async def run_probe():
                await probe.start()
                await asyncio.sleep(0.05)
                # Probe should still be running despite persist_health error
                assert probe._running
                assert probe._task is not None
                await probe.stop()

            asyncio.run(run_probe())
