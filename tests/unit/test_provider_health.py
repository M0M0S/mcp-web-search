"""Tests for ProviderHealthMetrics, ProviderHealthTracker, ProviderRegistry, and Prometheus metrics."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestProviderHealthMetrics:
    """Tests for ProviderHealthMetrics pure logic."""

    def test_init_default_values(self):
        """Test default initialization values."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics()
        assert metrics.health_window == 10
        assert metrics.failure_threshold == 0.5
        assert metrics._event_buffers == {}
        assert metrics._last_success_times == {}
        assert metrics._cooldown_expiry == {}

    def test_get_health_score_no_events(self):
        """Test health score returns 1.0 when no events recorded."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics()
        assert metrics.get_health_score("duck") == 1.0

    def test_get_health_score_with_events(self):
        """Test health score computed correctly from buffer events."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics(health_window=10)
        for _ in range(7):
            metrics.record_success("duck")
        for _ in range(3):
            metrics.record_failure("duck")

        score = metrics.get_health_score("duck")
        assert score == 0.7

    def test_get_health_score_degraded_provider(self):
        """Test health score for degraded provider (below threshold)."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics(health_window=10, failure_threshold=0.5)
        for _ in range(2):
            metrics.record_success("tavily")
        for _ in range(8):
            metrics.record_failure("tavily")

        score = metrics.get_health_score("tavily")
        assert score == 0.2

    def test_should_exclude_consecutive_failures(self):
        """Test exclusion when consecutive failures exceed threshold."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics(health_window=10, failure_threshold=0.5)
        # 6 consecutive failures > 0.5 * 10 = 5
        for _ in range(6):
            metrics.record_failure("duck")

        assert metrics.should_exclude("duck") is True

    def test_should_exclude_not_excluded_when_healthy(self):
        """Test provider not excluded when healthy."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics(health_window=10, failure_threshold=0.5)
        for _ in range(5):
            metrics.record_success("duck")
        for _ in range(3):
            metrics.record_failure("duck")

        assert metrics.should_exclude("duck") is False

    def test_should_exclude_with_cooldown(self):
        """Test exclusion due to active cooldown period."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics()
        metrics.apply_cooldown("tavily", cooldown_period=3600)

        assert metrics.should_exclude("tavily") is True

    def test_cooldown_expiry_clears_exclusion(self):
        """Test that expired cooldown no longer excludes provider."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics()
        # Set cooldown to past
        metrics._cooldown_expiry["tavily"] = time.time() - 10

        assert metrics.should_exclude("tavily") is False

    def test_record_success_clears_cooldown(self):
        """Test that recording success clears active cooldown."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics()
        metrics.apply_cooldown("duck", cooldown_period=3600)
        assert metrics.should_exclude("duck") is True

        metrics.record_success("duck")
        assert "duck" not in metrics._cooldown_expiry
        assert metrics.should_exclude("duck") is False

    def test_circular_buffer_overflow(self):
        """Test that circular buffer respects maxlen and drops oldest events."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics(health_window=5)
        for _ in range(7):
            metrics.record_success("duck")

        buffer = metrics._event_buffers["duck"]
        assert len(buffer) == 5

        # All events are successes — score should be 1.0
        assert metrics.get_health_score("duck") == 1.0

    def test_success_count_computed_from_buffer(self):
        """Test success_count computed from event buffer."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics()
        metrics.record_success("duck")
        metrics.record_success("duck")
        metrics.record_failure("duck")

        assert metrics.success_count("duck") == 2

    def test_failure_count_computed_from_buffer(self):
        """Test failure_count computed from event buffer."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics()
        metrics.record_success("duck")
        metrics.record_failure("duck")
        metrics.record_failure("duck")

        assert metrics.failure_count("duck") == 2

    def test_reset_provider_clears_all_state(self):
        """Test reset_provider clears all per-provider state."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics()
        metrics.record_success("duck")
        metrics.record_failure("duck")
        metrics.apply_cooldown("duck", 3600)

        metrics.reset_provider("duck")

        assert "duck" not in metrics._event_buffers
        assert "duck" not in metrics._last_success_times
        assert "duck" not in metrics._cooldown_expiry
        assert metrics.get_health_score("duck") == 1.0

    def test_consecutive_failures_with_interleaved_events(self):
        """Test consecutive failures count stops at first success."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics(health_window=10)
        metrics.record_success("duck")
        for _ in range(4):
            metrics.record_failure("duck")
        metrics.record_success("duck")
        for _ in range(3):
            metrics.record_failure("duck")

        # Consecutive failures from most recent = 3
        assert metrics._consecutive_failures("duck") == 3

    def test_should_exclude_boundary_threshold(self):
        """Test exclusion at exact threshold boundary."""
        from app.core.provider_registry import ProviderHealthMetrics

        metrics = ProviderHealthMetrics(health_window=10, failure_threshold=0.5)
        # Exactly 5 consecutive failures = 0.5 * 10 — should NOT exclude (> not >=)
        for _ in range(5):
            metrics.record_failure("duck")

        assert metrics.should_exclude("duck") is False

        # 6 consecutive failures > 5 — should exclude
        metrics.record_failure("duck")
        assert metrics.should_exclude("duck") is True


class TestProviderHealthTracker:
    """Tests for ProviderHealthTracker."""

    def test_get_health_score_via_tracker(self):
        """Test get_health_score through ProviderHealthTracker."""
        from app.core.provider_registry import ProviderHealthTracker

        tracker = ProviderHealthTracker(health_window=10)
        assert tracker.get_health_score("duck") == 1.0

        tracker.record_success("duck")
        tracker.record_success("duck")
        tracker.record_failure("duck")

        assert tracker.get_health_score("duck") == 2 / 3

    def test_should_exclude_via_tracker(self):
        """Test should_exclude through ProviderHealthTracker."""
        from app.core.provider_registry import ProviderHealthTracker

        tracker = ProviderHealthTracker(health_window=10, failure_threshold=0.5)
        for _ in range(6):
            tracker.record_failure("tavily")

        assert tracker.should_exclude("tavily") is True

    def test_record_success_via_tracker(self):
        """Test record_success updates tracker metrics."""
        from app.core.provider_registry import ProviderHealthTracker

        tracker = ProviderHealthTracker(health_window=10)
        tracker.record_success("duck")

        score = tracker.get_health_score("duck")
        assert score == 1.0

    def test_record_failure_via_tracker(self):
        """Test record_failure updates tracker metrics."""
        from app.core.provider_registry import ProviderHealthTracker

        tracker = ProviderHealthTracker(health_window=10, failure_threshold=0.5)
        for _ in range(6):
            tracker.record_failure("searxng")

        assert tracker.should_exclude("searxng") is True

    def test_reset_provider_via_tracker(self):
        """Test reset_provider through ProviderHealthTracker."""
        from app.core.provider_registry import ProviderHealthTracker

        tracker = ProviderHealthTracker(health_window=10, failure_threshold=0.5)
        for _ in range(6):
            tracker.record_failure("duck")

        assert tracker.should_exclude("duck") is True

        tracker.reset_provider("duck")
        assert tracker.get_health_score("duck") == 1.0
        assert tracker.should_exclude("duck") is False

    def test_apply_cooldown_via_tracker(self):
        """Test apply_cooldown through ProviderHealthTracker."""
        from app.core.provider_registry import ProviderHealthTracker

        tracker = ProviderHealthTracker(cooldown_period=3600)
        tracker.record_failure("tavily")

        # Not excluded yet (only 1 failure)
        assert tracker.should_exclude("tavily") is False

        tracker.apply_cooldown("tavily")
        assert tracker.should_exclude("tavily") is True

    def test_has_redis_no_client(self):
        """Test has_redis returns False when no Redis client."""
        from app.core.provider_registry import ProviderHealthTracker

        tracker = ProviderHealthTracker(redis_client=None)
        assert tracker.has_redis is False

    def test_has_redis_with_mock_client(self):
        """Test has_redis returns True with mock Redis client."""
        from app.core.provider_registry import ProviderHealthTracker

        mock_client = MagicMock()
        mock_client.client = MagicMock()
        tracker = ProviderHealthTracker(redis_client=mock_client)
        assert tracker.has_redis is True


class TestProviderRegistryGetProviders:
    """Tests for ProviderRegistry.get_providers() dynamic chain reordering."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings with available providers."""
        settings = MagicMock()
        settings.SEARCH_FALLBACK_CHAIN = ["duck", "searxng", "tavily", "google"]
        settings.PROVIDER_HEALTH_WINDOW = 10
        settings.PROVIDER_HEALTH_FAILURE_THRESHOLD = 0.5
        settings.PROVIDER_COOLDOWN_PERIOD = 3600
        settings.PROVIDER_HEALTH_PROBE_INTERVAL = 30
        settings.available_providers = ["duck", "searxng", "tavily", "google"]
        return settings

    @pytest.fixture
    def registry(self, mock_settings):
        """Create ProviderRegistry with mock settings."""
        from app.core.provider_registry import ProviderRegistry

        return ProviderRegistry(settings=mock_settings)

    def test_chain_all_healthy_preserves_order(self, registry):
        """Test that all healthy providers preserve original chain order."""
        # All providers start healthy (no events)
        chain = registry.get_providers()

        assert chain == ["duck", "searxng", "tavily", "google"]

    def test_chain_degraded_provider_moved_last(self, registry):
        """Test degraded provider moved to end of chain."""
        # Make tavily degraded
        for _ in range(8):
            registry.record_failure("tavily")
        for _ in range(2):
            registry.record_success("tavily")

        chain = registry.get_providers()

        # tavily should be in degraded (last) position
        assert "tavily" in chain
        assert chain.index("tavily") > chain.index("duck")
        assert chain.index("tavily") > chain.index("searxng")

    def test_chain_excluded_provider_removed(self, registry):
        """Test excluded provider removed from chain."""
        # Make searxng excluded via consecutive failures
        for _ in range(6):
            registry.record_failure("searxng")

        chain = registry.get_providers()

        assert "searxng" not in chain

    def test_chain_excluded_with_cooldown(self, registry):
        """Test provider excluded via cooldown."""
        registry.apply_cooldown("duck")

        chain = registry.get_providers()

        assert "duck" not in chain

    def test_chain_recovery_returns_to_position(self, registry):
        """Test recovered provider returns to healthy position."""
        # Make duck degraded
        for _ in range(7):
            registry.record_failure("duck")
        for _ in range(3):
            registry.record_success("duck")

        chain_before = registry.get_providers()
        assert "duck" in chain_before
        assert chain_before.index("duck") > 0  # not first

        # Recover duck
        for _ in range(7):
            registry.record_success("duck")

        chain_after = registry.get_providers()

        # duck should be back in healthy group (first position)
        assert chain_after.index("duck") == 0

    def test_chain_partial_failure_reordering(self, registry):
        """Test partial failure triggers reordering."""
        registry.record_failure("searxng")
        registry.record_failure("searxng")
        registry.record_failure("searxng")
        registry.record_failure("searxng")
        registry.record_failure("searxng")
        registry.record_failure("searxng")

        chain = registry.get_providers()

        # searxng excluded, order of remaining preserved
        assert "searxng" not in chain
        assert chain == ["duck", "tavily", "google"]


class TestPrometheusMetricsIntegration:
    """Tests for Prometheus metrics integration."""

    def test_increment_success_counter(self):
        """Test increment_success increments the correct counter."""
        from app.core.metrics import get_metrics_bytes, increment_success

        increment_success("duck")
        text = get_metrics_bytes().decode("utf-8")

        # Find duck's counter line
        for line in text.splitlines():
            if line.startswith("provider_search_total{provider=") and "duck" in line:
                # Format: provider_search_total{provider="duck"} 5.0
                value = line.split("{")[1].split("}")[1].strip()
                assert float(value) > 0
                break
        else:
            pytest.fail("No duck counter line found in metrics")

    def test_increment_failure_counter(self):
        """Test increment_failure increments the failure counter."""
        from app.core.metrics import get_metrics_bytes, increment_failure

        increment_failure("tavily")
        text = get_metrics_bytes().decode("utf-8")

        for line in text.splitlines():
            if (
                line.startswith("provider_search_failure_total{provider=")
                and "tavily" in line
            ):
                value = line.split("{")[1].split("}")[1].strip()
                assert float(value) > 0
                break
        else:
            pytest.fail("No tavily failure counter line found in metrics")

    def test_update_health_score_gauge(self):
        """Test update_health_score sets gauge value correctly."""
        from app.core.metrics import get_metrics_bytes, update_health_score

        update_health_score("duck", 0.75)
        text = get_metrics_bytes().decode("utf-8")

        for line in text.splitlines():
            if line.startswith("provider_health_score{provider=") and "duck" in line:
                value = line.split("{")[1].split("}")[1].strip()
                assert float(value) == 0.75
                break
        else:
            pytest.fail("No duck health score line found in metrics")

    def test_update_health_score_zero(self):
        """Test update_health_score with zero score (dead provider)."""
        from app.core.metrics import get_metrics_bytes, update_health_score

        update_health_score("searxng", 0.0)
        text = get_metrics_bytes().decode("utf-8")

        for line in text.splitlines():
            if line.startswith("provider_health_score{provider=") and "searxng" in line:
                value = line.split("{")[1].split("}")[1].strip()
                assert float(value) == 0.0
                break
        else:
            pytest.fail("No searxng health score line found in metrics")

    def test_update_chain_position_gauge(self):
        """Test update_chain_position sets gauge value correctly."""
        from app.core.metrics import get_metrics_bytes, update_chain_position

        update_chain_position("duck", 1)
        text = get_metrics_bytes().decode("utf-8")

        for line in text.splitlines():
            if line.startswith("provider_chain_position{provider=") and "duck" in line:
                value = line.split("{")[1].split("}")[1].strip()
                assert float(value) == 1
                break
        else:
            pytest.fail("No duck chain position line found in metrics")

    def test_update_chain_position_multi_provider(self):
        """Test chain positions set correctly for multiple providers."""
        from app.core.metrics import get_metrics_bytes, update_chain_position

        update_chain_position("duck", 1)
        update_chain_position("searxng", 2)
        update_chain_position("tavily", 3)

        text = get_metrics_bytes().decode("utf-8")

        positions = {}
        for line in text.splitlines():
            if line.startswith("provider_chain_position{provider="):
                provider = line.split('"')[1]
                value = float(line.split("{")[1].split("}")[1].strip())
                positions[provider] = value

        assert positions.get("duck") == 1
        assert positions.get("searxng") == 2
        assert positions.get("tavily") == 3

    def test_get_metrics_bytes_format(self):
        """Test get_metrics_bytes returns valid Prometheus exposition format."""
        from app.core.metrics import get_metrics_bytes

        metrics = get_metrics_bytes()
        assert isinstance(metrics, bytes)

        text = metrics.decode("utf-8")
        assert "provider_search_total" in text
        assert "provider_search_failure_total" in text
        assert "provider_health_score" in text
        assert "provider_chain_position" in text

    def test_provider_registry_get_metrics(self):
        """Test ProviderRegistry.get_metrics() delegates correctly."""
        from unittest.mock import MagicMock

        from app.core.provider_registry import ProviderRegistry

        mock_settings = MagicMock()
        mock_settings.SEARCH_FALLBACK_CHAIN = ["duck"]
        mock_settings.PROVIDER_HEALTH_WINDOW = 10
        mock_settings.PROVIDER_HEALTH_FAILURE_THRESHOLD = 0.5
        mock_settings.PROVIDER_COOLDOWN_PERIOD = 3600
        mock_settings.PROVIDER_HEALTH_PROBE_INTERVAL = 30
        mock_settings.available_providers = ["duck"]

        registry = ProviderRegistry(settings=mock_settings)
        metrics = registry.get_metrics()

        assert isinstance(metrics, bytes)
        assert b"provider_search_total" in metrics


class TestProviderHealthProbeGoogle:
    """Tests for _probe_google implementation."""

    def test_probe_google_no_api_key(self):
        """Test _probe_google returns False when no API key."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )

        tracker = ProviderHealthTracker()
        settings = MagicMock()
        settings._get_google_api_key.return_value = None

        probe = ProviderHealthProbe(tracker=tracker, settings=settings)
        result = asyncio.run(probe._probe_google("test"))

        assert result is False

    def test_probe_google_no_cse_id(self):
        """Test _probe_google returns False when no CSE ID."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )

        tracker = ProviderHealthTracker()
        settings = MagicMock()
        settings._get_google_api_key.return_value = "fake_api_key"
        settings.GOOGLE_CSE_ID = None

        probe = ProviderHealthProbe(tracker=tracker, settings=settings)
        result = asyncio.run(probe._probe_google("test"))

        assert result is False

    @pytest.mark.asyncio
    async def test_probe_google_success(self):
        """Test _probe_google returns True when API returns results."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )
        from app.core.ssrf import ssrf_protection

        tracker = ProviderHealthTracker()
        settings = MagicMock()
        settings._get_google_api_key.return_value = "fake_api_key"
        settings.GOOGLE_CSE_ID = "fake_cse_id"

        valid_json = b'{"items": [{"title": "test"}]}'

        with patch.object(
            ssrf_protection,
            "fetch_async",
            new_callable=AsyncMock,
            return_value=valid_json,
        ):
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            result = await probe._probe_google("test probe")

        assert result is True

    @pytest.mark.asyncio
    async def test_probe_google_no_results(self):
        """Test _probe_google returns False when API returns no results."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )
        from app.core.ssrf import ssrf_protection

        tracker = ProviderHealthTracker()
        settings = MagicMock()
        settings._get_google_api_key.return_value = "fake_api_key"
        settings.GOOGLE_CSE_ID = "fake_cse_id"

        valid_json = b'{"items": []}'

        with patch.object(
            ssrf_protection,
            "fetch_async",
            new_callable=AsyncMock,
            return_value=valid_json,
        ):
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            result = await probe._probe_google("test probe")

        assert result is False

    @pytest.mark.asyncio
    async def test_probe_google_http_error(self):
        """Test _probe_google returns False on HTTP error."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )
        from app.core.ssrf import ssrf_protection

        tracker = ProviderHealthTracker()
        settings = MagicMock()
        settings._get_google_api_key.return_value = "fake_api_key"
        settings.GOOGLE_CSE_ID = "fake_cse_id"

        with patch.object(
            ssrf_protection,
            "fetch_async",
            new_callable=AsyncMock,
            side_effect=Exception("HTTP error"),
        ):
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            result = await probe._probe_google("test probe")

        assert result is False


class TestRestoreHealthJSONDecodeError:
    """Tests for JSONDecodeError handling in restore_health."""

    @pytest.mark.asyncio
    async def test_restore_health_corrupted_json(self):
        """Test restore_health handles corrupted JSON gracefully."""
        from app.core.provider_registry import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        mock_redis = AsyncMock()
        mock_redis.client.get = AsyncMock(return_value="not valid json {{{")
        tracker._redis_client = mock_redis

        mock_settings = MagicMock()
        mock_settings.available_providers = ["duck", "tavily"]
        tracker._settings = mock_settings

        # Should not raise — corrupted JSON handled by logger.error
        await tracker.restore_health()

        # Both providers should have been attempted
        calls = [c[0][0] for c in mock_redis.client.get.call_args_list]
        assert "provider_health:duck" in calls
        assert "provider_health:tavily" in calls

    @pytest.mark.asyncio
    async def test_restore_health_valid_json_restores_state(self):
        """Test restore_health correctly restores state from valid JSON."""
        import json

        from app.core.provider_registry import ProviderHealthTracker

        tracker = ProviderHealthTracker(health_window=10)
        mock_redis = AsyncMock()

        valid_state = {
            "success_count": 5,
            "failure_count": 3,
            "last_success_time": time.time(),
            "consecutive_failures": 0,
            "health_score": 0.625,
            "cooldown_expiry": None,
            "events": [True, True, True, True, True, False, False, False],
        }

        mock_redis.client.get = AsyncMock(
            side_effect=[
                json.dumps(valid_state),  # duck
                None,  # tavily — no data
            ]
        )
        tracker._redis_client = mock_redis

        mock_settings = MagicMock()
        mock_settings.available_providers = ["duck", "tavily"]
        tracker._settings = mock_settings

        await tracker.restore_health()

        score = tracker.get_health_score("duck")
        assert score == 5 / 8

        buffer = tracker._metrics["duck"]._event_buffers["duck"]
        assert len(buffer) == 8

    @pytest.mark.asyncio
    async def test_restore_health_missing_key_skips(self):
        """Test restore_health skips providers with no Redis data."""
        from app.core.provider_registry import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        mock_redis = AsyncMock()
        mock_redis.client.get = AsyncMock(return_value=None)
        tracker._redis_client = mock_redis

        mock_settings = MagicMock()
        mock_settings.available_providers = ["duck"]
        tracker._settings = mock_settings

        await tracker.restore_health()

        # No metrics should be created
        assert "duck" not in tracker._metrics


class TestProbeGoogleSSRF:
    """Tests for SSRF protection in _probe_google."""

    @pytest.mark.asyncio
    async def test_probe_google_ssrf_violation(self):
        """Test _probe_google returns False when SSRF protection blocks private IP."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )
        from app.core.ssrf import ssrf_protection

        tracker = ProviderHealthTracker()
        settings = MagicMock()
        settings._get_google_api_key.return_value = "fake_api_key"
        settings.GOOGLE_CSE_ID = "fake_cse_id"

        def validate_raises(url: str) -> None:
            if "10.0.0.1" in url or "192.168.1.1" in url:
                raise ValueError("Private IP address not allowed")

        with patch.object(
            ssrf_protection, "_validate_url", side_effect=validate_raises
        ):
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            result = await probe._probe_google("test probe")

        assert result is False

    @pytest.mark.asyncio
    async def test_probe_google_valid_url_passes_validation(self):
        """Test _probe_google passes SSRF validation for trusted public URL."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )
        from app.core.ssrf import ssrf_protection

        tracker = ProviderHealthTracker()
        settings = MagicMock()
        settings._get_google_api_key.return_value = "fake_api_key"
        settings.GOOGLE_CSE_ID = "fake_cse_id"

        valid_json = b'{"items": [{"title": "test"}]}'

        with patch.object(
            ssrf_protection,
            "fetch_async",
            new_callable=AsyncMock,
            return_value=valid_json,
        ):
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            result = await probe._probe_google("test probe")

        assert result is True


class TestProviderHealthProbeSearxNG:
    """Tests for _probe_searxng implementation."""

    @pytest.mark.asyncio
    async def test_probe_searxng_success(self):
        """Test _probe_searxng returns True when fetch returns valid JSON with results."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )
        from app.core.ssrf import ssrf_protection

        tracker = ProviderHealthTracker()
        settings = MagicMock()
        settings.SEARXNG_BASE = "https://searx.example.com"

        valid_json = (
            b'{"results": [{"title": "test result", "url": "https://example.com"}]}'
        )

        with patch.object(
            ssrf_protection,
            "fetch_async",
            new_callable=AsyncMock,
            return_value=valid_json,
        ) as mock_fetch:
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            result = await probe._probe_searxng("test probe")

        assert result is True
        mock_fetch.assert_called_once()
        call_args = mock_fetch.call_args
        assert call_args[0][0] == "https://searx.example.com/search"
        assert call_args[1]["params"]["format"] == "json"
        assert call_args[1]["params"]["limit"] == "1"

    @pytest.mark.asyncio
    async def test_probe_searxng_timeout(self):
        """Test _probe_searxng returns False when fetch_async raises TimeoutError."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )
        from app.core.ssrf import ssrf_protection

        tracker = ProviderHealthTracker()
        settings = MagicMock()
        settings.SEARXNG_BASE = "https://searx.example.com"

        with patch.object(
            ssrf_protection, "fetch_async", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.side_effect = TimeoutError("Connection timed out")
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            result = await probe._probe_searxng("test probe")

        assert result is False

    @pytest.mark.asyncio
    async def test_probe_searxng_corrupted_json(self):
        """Test _probe_searxng returns False when fetch returns invalid JSON."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )
        from app.core.ssrf import ssrf_protection

        tracker = ProviderHealthTracker()
        settings = MagicMock()
        settings.SEARXNG_BASE = "https://searx.example.com"

        corrupted = b"not valid json {{{ broken data"

        with patch.object(
            ssrf_protection,
            "fetch_async",
            new_callable=AsyncMock,
            return_value=corrupted,
        ):
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            result = await probe._probe_searxng("test probe")

        assert result is False

    @pytest.mark.asyncio
    async def test_probe_searxng_no_results(self):
        """Test _probe_searxng returns False when fetch returns JSON with empty results."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )
        from app.core.ssrf import ssrf_protection

        tracker = ProviderHealthTracker()
        settings = MagicMock()
        settings.SEARXNG_BASE = "https://searx.example.com"

        no_results_json = b'{"results": [], "query": "test probe"}'

        with patch.object(
            ssrf_protection,
            "fetch_async",
            new_callable=AsyncMock,
            return_value=no_results_json,
        ) as mock_fetch:
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            result = await probe._probe_searxng("test probe")

        assert result is False
        mock_fetch.assert_called_once()


class TestProviderHealthProbeDuckDuckGo:
    """Tests for _probe_duckduckgo implementation."""

    @pytest.mark.asyncio
    async def test_probe_duckduckgo_success(self):
        """Test _probe_duckduckgo returns True when DDGS returns results."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )

        tracker = ProviderHealthTracker()
        settings = MagicMock()

        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=None)
        mock_ddgs.text = MagicMock(return_value=[{"title": "test"}])

        with (
            patch("app.core.provider_registry.DDGS", return_value=mock_ddgs),
            patch("asyncio.to_thread", new_callable=AsyncMock, return_value=True),
        ):
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            result = await probe._probe_duckduckgo("test probe")

        assert result is True

    @pytest.mark.asyncio
    async def test_probe_duckduckgo_no_results(self):
        """Test _probe_duckduckgo returns False when DDGS returns empty list."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )

        tracker = ProviderHealthTracker()
        settings = MagicMock()

        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=None)
        mock_ddgs.text = MagicMock(return_value=[])

        with (
            patch("app.core.provider_registry.DDGS", return_value=mock_ddgs),
            patch("asyncio.to_thread", new_callable=AsyncMock, return_value=False),
        ):
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            result = await probe._probe_duckduckgo("test probe")

        assert result is False

    @pytest.mark.asyncio
    async def test_probe_duckduckgo_ddgs_error(self):
        """Test _probe_duckduckgo returns False when DDGS raises exception."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )

        tracker = ProviderHealthTracker()
        settings = MagicMock()

        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(side_effect=Exception("DDGS connection error"))
        mock_ddgs.__exit__ = MagicMock(return_value=None)

        with (
            patch("app.core.provider_registry.DDGS", return_value=mock_ddgs),
            patch("asyncio.to_thread", new_callable=AsyncMock, return_value=False),
        ):
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            result = await probe._probe_duckduckgo("test probe")

        assert result is False

    @pytest.mark.asyncio
    async def test_probe_duckduckgo_query_passed_to_ddgs(self):
        """Test _probe_duckduckgo passes correct query to DDGS.text."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )

        tracker = ProviderHealthTracker()
        settings = MagicMock()

        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs.__exit__ = MagicMock(return_value=None)
        mock_ddgs.text = MagicMock(return_value=[{"title": "test"}])

        with patch("app.core.provider_registry.DDGS", return_value=mock_ddgs):
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            await probe._probe_duckduckgo("my_probe_query")

        mock_ddgs.text.assert_called_once_with(query="my_probe_query", max_results=1)


class TestPersistHealth:
    """Tests for ProviderHealthTracker.persist_health()."""

    @pytest.mark.asyncio
    async def test_persist_health_with_mock_redis(self):
        """Test persist_health calls redis.set with correct key and TTL."""
        import json

        from app.core.provider_registry import ProviderHealthTracker

        tracker = ProviderHealthTracker(health_window=10)
        tracker.record_success("duck")
        tracker.record_failure("duck")
        tracker.record_success("tavily")

        mock_redis = AsyncMock()
        mock_redis.client.set = AsyncMock()
        tracker._redis_client = mock_redis

        mock_settings = MagicMock()
        mock_settings.REDIS_HEALTH_TTL = 3600
        tracker._settings = mock_settings

        await tracker.persist_health()

        # Verify set was called for each provider
        calls = [c[0] for c in mock_redis.client.set.call_args_list]
        assert len(calls) == 2

        for call_args, call_kwargs in mock_redis.client.set.call_args_list:
            key = call_args[0]
            value = call_args[1]
            ttl = call_kwargs.get("ex")

            assert key.startswith("provider_health:")
            assert ttl == 3600
            # value should be valid JSON
            parsed = json.loads(value)
            assert "success_count" in parsed
            assert "failure_count" in parsed

    @pytest.mark.asyncio
    async def test_persist_health_without_redis(self):
        """Test persist_health early returns when no Redis client."""
        from app.core.provider_registry import ProviderHealthTracker

        tracker = ProviderHealthTracker(redis_client=None)
        tracker.record_success("duck")

        mock_settings = MagicMock()
        mock_settings.REDIS_HEALTH_TTL = 3600
        tracker._settings = mock_settings

        # Should not raise — early return when has_redis is False
        await tracker.persist_health()

        assert tracker.has_redis is False


class TestProviderHealthProbeTavily:
    """Tests for _probe_tavily implementation."""

    @pytest.mark.asyncio
    async def test_probe_tavily_no_key(self):
        """Test _probe_tavily returns False when no API key."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )

        tracker = ProviderHealthTracker()
        settings = MagicMock()
        settings._get_api_key.return_value = None

        probe = ProviderHealthProbe(tracker=tracker, settings=settings)
        result = await probe._probe_tavily("test probe")

        assert result is False

    @pytest.mark.asyncio
    async def test_probe_tavily_success(self):
        """Test _probe_tavily returns True when TavilyClient returns results."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )

        tracker = ProviderHealthTracker()
        settings = MagicMock()
        settings._get_api_key.return_value = "fake_tavily_key"

        mock_response = MagicMock()
        mock_response.results = [{"title": "test result", "url": "https://example.com"}]

        mock_client = MagicMock()
        mock_client.search = MagicMock(return_value=mock_response)

        with patch("tavily.TavilyClient", return_value=mock_client):
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            result = await probe._probe_tavily("test probe")

        assert result is True
        mock_client.search.assert_called_once_with(query="test probe", max_results=1)

    @pytest.mark.asyncio
    async def test_probe_tavily_api_error(self):
        """Test _probe_tavily returns False when TavilyClient raises exception."""
        from app.core.provider_registry import (
            ProviderHealthProbe,
            ProviderHealthTracker,
        )

        tracker = ProviderHealthTracker()
        settings = MagicMock()
        settings._get_api_key.return_value = "fake_tavily_key"

        mock_client = MagicMock()
        mock_client.search = MagicMock(side_effect=Exception("Tavily API error"))

        with patch("tavily.TavilyClient", return_value=mock_client):
            probe = ProviderHealthProbe(tracker=tracker, settings=settings)
            result = await probe._probe_tavily("test probe")

        assert result is False
        mock_client.search.assert_called_once_with(query="test probe", max_results=1)


class TestSearchFallbackChainIntegration:
    """Integration tests for full search fallback chain with degraded providers."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings with available providers."""
        settings = MagicMock()
        settings.SEARCH_FALLBACK_CHAIN = ["duck", "searxng", "tavily", "google"]
        settings.PROVIDER_HEALTH_WINDOW = 10
        settings.PROVIDER_HEALTH_FAILURE_THRESHOLD = 0.5
        settings.PROVIDER_COOLDOWN_PERIOD = 3600
        settings.PROVIDER_HEALTH_PROBE_INTERVAL = 30
        settings.available_providers = ["duck", "searxng", "tavily", "google"]
        settings.BLACKLIST_DOMAINS = set()
        settings.QUALITY_SCORE_THRESHOLD = 0.5
        settings.SKIP_JUDGE = False
        settings.CACHE_VERSION = 1
        settings.SEARCH_RESULT_CACHE_TTL = 300
        settings._has_api_key.return_value = True
        settings._get_api_key.return_value = "fake_key"
        settings._has_google_api_key.return_value = True
        settings._get_google_api_key.return_value = "fake_google_key"
        settings.GOOGLE_CSE_ID = "fake_cse_id"
        settings.SEARXNG_BASE = "https://searx.example.com"
        settings.ADAPTIVE_TTL_RANGES = {
            "high": (0.8, 86400),
            "medium": (0.5, 21600),
            "low": (0.0, 3600),
        }
        return settings

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        redis = MagicMock()
        redis.client = AsyncMock()
        redis.client.get = AsyncMock(return_value=None)
        redis.client.set = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_fallback_chain_with_degraded_provider(
        self, mock_settings, mock_redis
    ):
        """Test fallback chain uses dynamic order when a provider is degraded."""
        from app.core.provider_registry import ProviderRegistry
        from app.models.search import SearchRequest
        from app.services.search_service import SearchService

        registry = ProviderRegistry(settings=mock_settings, redis_client=mock_redis)

        # Make searxng degraded (score < 0.5)
        for _ in range(7):
            registry.record_failure("searxng")
        for _ in range(3):
            registry.record_success("searxng")

        # Verify dynamic chain reorders searxng to degraded position
        chain = registry.get_providers()
        assert "searxng" in chain
        assert chain.index("searxng") > chain.index("duck")

        # Create SearchService with patched LLM client to avoid API key requirement
        mock_llm = MagicMock()
        mock_llm.judge_urls_with_content = AsyncMock(
            return_value=MagicMock(verdict="pass", reasons=[])
        )

        with patch(
            "app.services.search_service.create_llm_client", return_value=mock_llm
        ):
            service = SearchService(settings=mock_settings, redis=mock_redis)

            # Patch _search_provider to return results for duck, None for searxng
            original_search_provider = service._search_provider

            async def patched_search_provider(provider, request):
                if provider == "searxng":
                    return None  # simulate degraded provider failure
                return await original_search_provider(provider, request)

            with (
                patch.object(
                    service, "_search_provider", side_effect=patched_search_provider
                ),
                patch.object(
                    service,
                    "_calculate_diversity",
                    return_value={"diversity_scores": {"overall": 0.8}},
                ),
            ):
                request = SearchRequest(query="test query")
                response = await service.search(request)

                # Result should come from duck (first in dynamic chain), not searxng
                assert response.provider == "duck"
                assert response.cache_hit is False

    @pytest.mark.asyncio
    async def test_fallback_chain_all_excluded(self, mock_settings, mock_redis):
        """Test fallback behavior when all providers are excluded."""
        from app.core.provider_registry import ProviderRegistry
        from app.models.search import SearchRequest
        from app.services.search_service import SearchError, SearchService

        registry = ProviderRegistry(settings=mock_settings, redis_client=mock_redis)

        # Exclude all providers via consecutive failures
        for provider in ["duck", "searxng", "tavily", "google"]:
            for _ in range(6):
                registry.record_failure(provider)

        # Verify all providers excluded
        chain = registry.get_providers()
        assert chain == []

        mock_llm = MagicMock()
        mock_llm.judge_urls_with_content = AsyncMock(
            return_value=MagicMock(verdict="pass", reasons=[])
        )

        with patch(
            "app.services.search_service.create_llm_client", return_value=mock_llm
        ):
            service = SearchService(settings=mock_settings, redis=mock_redis)

            request = SearchRequest(query="test query")

            # Patch service._registry.get_providers to return empty chain (all excluded)
            with patch.object(service._registry, "get_providers", return_value=[]):
                with pytest.raises(SearchError) as exc_info:
                    await service.search(request)

                assert "No relevant results found" in str(exc_info.value)
