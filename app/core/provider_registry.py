"""Search provider registry with health tracking and dynamic fallback chain."""

import asyncio
import collections
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ddgs import DDGS

# Lazy import tavily to avoid mypy import-untyped error at module level
# TavilyClient is imported inside _probe_tavily() method only

from app.core.logging import get_logger
from app.core.metrics import (
    get_metrics_bytes,
    increment_failure,
    increment_success,
    update_chain_position,
    update_health_score,
)
from app.core.ssrf import ssrf_protection

if TYPE_CHECKING:
    from .config import Settings
    from .dependencies import RedisClient


logger = get_logger("provider-registry")


@dataclass
class ProviderHealthMetrics:
    """Per-provider health metrics with circular buffer bounded by health_window.

    Tracks success/failure counts, last success time, and consecutive failures
    for each provider. Health score is computed as success_rate within the window.

    Supports optional Redis persistence via ProviderHealthTracker.
    """

    health_window: int = 10
    failure_threshold: float = 0.5

    # Per-provider circular buffers of recent events (True = success, False = failure)
    _event_buffers: dict[str, collections.deque] = field(default_factory=dict)
    # Per-provider last success timestamp (kept for Redis persistence only)
    _last_success_times: dict[str, float | None] = field(default_factory=dict)
    # Per-provider cooldown expiry timestamp (when excluded provider can re-enter)
    _cooldown_expiry: dict[str, float] = field(default_factory=dict)

    def success_count(self, provider: str) -> int:
        """Success count for provider computed from the event buffer."""
        buffer = self._event_buffers.get(provider)
        if not buffer:
            return 0
        return sum(1 for event in buffer if event)

    def failure_count(self, provider: str) -> int:
        """Failure count for provider computed from the event buffer."""
        buffer = self._event_buffers.get(provider)
        if not buffer:
            return 0
        return sum(1 for event in buffer if not event)

    def get_health_score(self, provider: str) -> float:
        """Return health score for provider in range [0.0, 1.0].

        Score is the ratio of successes to total events in the circular buffer.
        If no events recorded, returns 1.0 (healthy by default).
        """
        buffer = self._event_buffers.get(provider)
        if not buffer or len(buffer) == 0:
            return 1.0

        successes = sum(1 for event in buffer if event)
        total = len(buffer)
        return successes / total

    def should_exclude(self, provider: str) -> bool:
        """Return True if provider should be excluded from the fallback chain.

        Exclusion criteria:
        - consecutive_failures > failure_threshold * health_window
        - cooldown period not expired
        """
        # Check cooldown expiry first
        cooldown_expiry = self._cooldown_expiry.get(provider)
        if cooldown_expiry is not None and time.time() < cooldown_expiry:
            return True

        consecutive = self._consecutive_failures(provider)
        threshold_count = self.failure_threshold * self.health_window
        return consecutive > threshold_count

    def record_success(self, provider: str) -> None:
        """Record a successful search for the given provider."""
        self._record_event(provider, True)
        self._last_success_times[provider] = time.time()
        # Remove cooldown if provider was in cooldown — recovery event clears it
        self._cooldown_expiry.pop(provider, None)

    def record_failure(self, provider: str) -> None:
        """Record a failed search for the given provider."""
        self._record_event(provider, False)

    def _record_event(self, provider: str, success: bool) -> None:
        """Add event to the circular buffer for the provider."""
        if provider not in self._event_buffers:
            self._event_buffers[provider] = collections.deque(maxlen=self.health_window)
        self._event_buffers[provider].append(success)

    def _consecutive_failures(self, provider: str) -> int:
        """Count consecutive failures from the most recent event backwards."""
        buffer = self._event_buffers.get(provider)
        if not buffer or len(buffer) == 0:
            return 0

        count = 0
        for event in reversed(buffer):
            if event:
                break
            count += 1
        return count

    def reset_provider(self, provider: str) -> None:
        """Reset all metrics for a provider (e.g. after manual recovery)."""
        self._event_buffers.pop(provider, None)
        self._last_success_times.pop(provider, None)
        self._cooldown_expiry.pop(provider, None)

    def apply_cooldown(self, provider: str, cooldown_period: int) -> None:
        """Apply cooldown to a provider — temporarily exclude from chain."""
        self._cooldown_expiry[provider] = time.time() + cooldown_period


class ProviderHealthTracker:
    """Central health tracker for all search providers.

    Wraps per-provider metrics and provides a configurable cooldown period.
    Supports optional Redis persistence for cross-process state recovery.
    Falls back to in-memory only when Redis is unavailable.
    """

    def __init__(
        self,
        health_window: int = 10,
        failure_threshold: float = 0.5,
        cooldown_period: int = 300,
        redis_client: "RedisClient | None" = None,
        settings: "Settings | None" = None,
    ):
        self.health_window = health_window
        self.failure_threshold = failure_threshold
        self.cooldown_period = cooldown_period
        self._redis_client = redis_client
        self._settings = settings
        self._metrics: dict[str, ProviderHealthMetrics] = {}

    @property
    def _redis_ttl(self) -> int:
        """Return Redis TTL for health keys — from settings or default 3600."""
        if self._settings:
            return getattr(self._settings, "REDIS_HEALTH_TTL", 3600)
        return 3600

    @property
    def has_redis(self) -> bool:
        """Return True if a connected Redis client is available."""
        if self._redis_client is None:
            return False
        try:
            return self._redis_client.client is not None
        except (RuntimeError, AttributeError):
            return False

    def _serialize_provider_state(self, provider: str) -> dict:
        """Serialize in-memory state for a single provider to a JSON-serializable dict."""
        metrics = self._get_metrics(provider)
        buffer = metrics._event_buffers.get(provider, collections.deque())
        last_n = min(len(buffer), metrics.health_window)
        events = list(buffer)[-last_n:] if last_n > 0 else []

        successes = sum(1 for e in events if e)
        failures = sum(1 for e in events if not e)

        return {
            "success_count": successes,
            "failure_count": failures,
            "last_success_time": metrics._last_success_times.get(provider),
            "consecutive_failures": metrics._consecutive_failures(provider),
            "health_score": metrics.get_health_score(provider),
            "cooldown_expiry": metrics._cooldown_expiry.get(provider),
            "events": events,
        }

    def _deserialize_provider_state(self, provider: str, data: dict) -> None:
        """Restore in-memory state for a provider from serialized data."""
        if provider not in self._metrics:
            self._metrics[provider] = ProviderHealthMetrics(
                health_window=self.health_window,
                failure_threshold=self.failure_threshold,
            )

        metrics = self._metrics[provider]
        metrics._last_success_times[provider] = data.get("last_success_time")
        cooldown_val = data.get("cooldown_expiry")
        if cooldown_val is not None:
            metrics._cooldown_expiry[provider] = cooldown_val

        events = data.get("events", [])
        if events:
            metrics._event_buffers[provider] = collections.deque(
                events, maxlen=metrics.health_window
            )

    async def persist_health(self) -> None:
        """Serialize all per-provider metrics to Redis.

        Each provider's state is stored under key `provider_health:{provider_name}`
        with TTL from settings.REDIS_HEALTH_TTL (default 3600s).

        Graceful fallback: if Redis is unavailable, silently skip without exceptions.
        """
        if not self.has_redis:
            return

        ttl = self._redis_ttl
        redis_client = self._redis_client
        assert redis_client is not None  # safe: has_redis checked above
        client = redis_client.client

        for provider in self._metrics:
            state = self._serialize_provider_state(provider)
            key = f"provider_health:{provider}"
            try:
                await client.set(key, json.dumps(state), ex=ttl)
            except Exception as e:
                logger.debug(
                    "provider_health_persist_error",
                    provider=provider,
                    error=type(e).__name__,
                )

    async def restore_health(self) -> None:
        """Load per-provider metrics from Redis and restore in-memory state.

        Reads keys `provider_health:{provider_name}` for all known providers.
        Only restores providers that have existing Redis data.

        Graceful fallback: if Redis is unavailable, silently skip without exceptions.
        """
        if not self.has_redis:
            return

        settings = self._settings
        assert settings is not None  # safe: has_redis implies settings is set
        providers = list(settings.available_providers)
        redis_client = self._redis_client
        assert redis_client is not None  # safe: has_redis checked above
        client = redis_client.client

        for provider in providers:
            key = f"provider_health:{provider}"
            try:
                raw = await client.get(key)
                if raw is None:
                    continue
                data = json.loads(raw)
                self._deserialize_provider_state(provider, data)
            except json.JSONDecodeError as e:
                logger.error(
                    "provider_health_restore_json_error",
                    provider=provider,
                    error=str(e),
                    key=key,
                )
            except Exception as e:
                logger.debug(
                    "provider_health_restore_error",
                    provider=provider,
                    error=type(e).__name__,
                )

    def _get_metrics(self, provider: str) -> ProviderHealthMetrics:
        """Get or create metrics for a provider."""
        if provider not in self._metrics:
            self._metrics[provider] = ProviderHealthMetrics(
                health_window=self.health_window,
                failure_threshold=self.failure_threshold,
            )
        return self._metrics[provider]

    def get_health_score(self, provider: str) -> float:
        """Return health score for provider in range [0.0, 1.0]."""
        metrics = self._get_metrics(provider)
        return metrics.get_health_score(provider)

    def should_exclude(self, provider: str) -> bool:
        """Return True if provider should be excluded from the fallback chain."""
        metrics = self._get_metrics(provider)
        return metrics.should_exclude(provider)

    def record_success(self, provider: str) -> None:
        """Record a successful search for the given provider."""
        metrics = self._get_metrics(provider)
        metrics.record_success(provider)

    def record_failure(self, provider: str) -> None:
        """Record a failed search for the given provider."""
        metrics = self._get_metrics(provider)
        metrics.record_failure(provider)

    def reset_provider(self, provider: str) -> None:
        """Reset all metrics for a provider."""
        metrics = self._get_metrics(provider)
        metrics.reset_provider(provider)

    def apply_cooldown(self, provider: str) -> None:
        """Apply cooldown to a provider."""
        metrics = self._get_metrics(provider)
        metrics.apply_cooldown(provider, self.cooldown_period)


class ProviderHealthProbe:
    """Background periodic health probe for each provider.

    Performs lightweight search with minimal query ("test probe") at configurable
    intervals. Probe results update health metrics. Probes are skipped for
    providers with API keys unavailable. Probe errors do not count as failures
    (probe-specific handling).
    """

    def __init__(
        self,
        tracker: ProviderHealthTracker,
        settings: "Settings",
        interval: int = 30,
    ):
        self._tracker = tracker
        self._settings = settings
        self._interval = interval
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background health probe loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._probe_loop())
        logger.info("provider_health_probe_started", interval=self._interval)

    async def stop(self) -> None:
        """Stop the background health probe loop."""
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("provider_health_probe_stopped")

    async def _probe_loop(self) -> None:
        """Main probe loop — runs at configurable interval.

        Periodically persists health metrics to Redis for cross-process recovery.
        """
        while self._running:
            try:
                await self._probe_all_providers()
                await self._tracker.persist_health()
            except Exception as exc:
                logger.warning(
                    "provider_health_probe_loop_error",
                    error=type(exc).__name__,
                )

            await asyncio.sleep(self._interval)

    async def _probe_all_providers(self) -> None:
        """Probe each available provider with a lightweight search query."""
        available = self._settings.available_providers
        for provider in available:
            try:
                await self._probe_single(provider)
            except Exception as exc:
                logger.debug(
                    "provider_health_probe_skipped",
                    provider=provider,
                    reason=type(exc).__name__,
                )

    async def _probe_single(self, provider: str) -> None:
        """Probe a single provider with minimal query."""
        # Skip providers with unavailable API keys
        if provider == "tavily" and not self._settings._has_api_key("TAVILY_API_KEY"):
            logger.debug("provider_health_probe_skipped_no_key", provider=provider)
            return

        if provider == "google" and (
            not self._settings._has_google_api_key()
            or not self._settings.USE_GOOGLE_FALLBACK
        ):
            logger.debug("provider_health_probe_skipped_no_key", provider=provider)
            return

        probe_query = "test probe"

        if provider == "duck":
            result = await self._probe_duckduckgo(probe_query)
        elif provider == "searxng":
            result = await self._probe_searxng(probe_query)
        elif provider == "tavily":
            result = await self._probe_tavily(probe_query)
        elif provider == "google":
            result = await self._probe_google(probe_query)
        else:
            logger.debug("provider_health_probe_unknown", provider=provider)
            return

        if result:
            self._tracker.record_success(provider)
            logger.debug(
                "provider_health_probe_success",
                provider=provider,
                score=self._tracker.get_health_score(provider),
            )
        else:
            # Probe errors do NOT count as failures — only probe-specific handling
            logger.debug(
                "provider_health_probe_failed_not_counted",
                provider=provider,
            )

    async def _probe_duckduckgo(self, query: str) -> bool:
        """Probe DuckDuckGo with minimal query."""

        def fetch_sync():
            with DDGS() as ddgs:
                results = list(ddgs.text(query=query, max_results=1))
                return len(results) > 0

        return await asyncio.to_thread(fetch_sync)

    async def _probe_searxng(self, query: str) -> bool:
        """Probe SearxNG with minimal query."""
        base_url = self._settings.SEARXNG_BASE
        if not base_url:
            logger.debug("searxng_base_not_configured", provider="searxng")
            return False
        params = {"q": query, "format": "json", "limit": "1"}

        try:
            content = await ssrf_protection.fetch_async(
                f"{base_url}/search", params=params
            )

            parsed = json.loads(content.decode("utf-8"))
            results = parsed.get("results", [])
            return len(results) > 0
        except Exception:
            return False

    async def _probe_tavily(self, query: str) -> bool:
        """Probe Tavily with minimal query."""
        api_key = self._settings._get_api_key("TAVILY_API_KEY")
        if not api_key:
            return False

        from tavily import TavilyClient  # type: ignore[import-untyped]  # no official stubs

        client = TavilyClient(api_key=api_key)
        try:
            response = client.search(query=query, max_results=1)
            return len(response.results) > 0
        except Exception:
            return False

    async def _probe_google(self, query: str) -> bool:
        """Probe Google Custom Search API with minimal query."""
        api_key = self._settings._get_google_api_key()
        if not api_key:
            return False

        cse_id = getattr(self._settings, "GOOGLE_CSE_ID", None)
        if not cse_id:
            logger.debug("provider_health_probe_google_no_cse_id", provider="google")
            return False

        url = "https://www.googleapis.com/customsearch/v1"
        params = {"key": api_key, "q": query, "cx": cse_id, "num": 1}

        try:
            content = await ssrf_protection.fetch_async(url, params=params)
            data = json.loads(content.decode("utf-8"))
            results = data.get("items", [])
            return len(results) > 0
        except Exception:
            return False


class ProviderRegistry:
    """Registry for search providers with dynamic health-aware fallback chain."""

    def __init__(self, settings: "Settings", redis_client: "RedisClient | None" = None):
        self.settings = settings
        self._registry = {
            "duck": "DuckDuckGoSearchProvider",
            "tavily": "TavilySearchProvider",
            "google": "GoogleSearchProvider",
        }

        # Initialize health tracker from settings (with optional Redis)
        self._health_tracker = ProviderHealthTracker(
            health_window=settings.PROVIDER_HEALTH_WINDOW,
            failure_threshold=settings.PROVIDER_HEALTH_FAILURE_THRESHOLD,
            cooldown_period=settings.PROVIDER_COOLDOWN_PERIOD,
            redis_client=redis_client,
            settings=settings,
        )

        # Initialize health probe
        self._health_probe = ProviderHealthProbe(
            tracker=self._health_tracker,
            settings=settings,
            interval=settings.PROVIDER_HEALTH_PROBE_INTERVAL,
        )

    async def initialize(self) -> None:
        """Async initialization — restore health state from Redis if available."""
        if self._health_tracker.has_redis:
            await self._health_tracker.restore_health()

    @property
    def health_tracker(self) -> ProviderHealthTracker:
        """Return the health tracker instance for monitoring."""
        return self._health_tracker

    @property
    def health_probe(self) -> ProviderHealthProbe:
        """Return the health probe instance."""
        return self._health_probe

    async def start_probes(self) -> None:
        """Start background health probes."""
        await self._health_probe.start()

    async def stop_probes(self) -> None:
        """Stop background health probes."""
        await self._health_probe.stop()

    def record_success(self, provider: str) -> None:
        """Record a successful search — update health tracker and Prometheus counter."""
        self._health_tracker.record_success(provider)
        increment_success(provider)

    def record_failure(self, provider: str) -> None:
        """Record a failed search — update health tracker and Prometheus counter."""
        self._health_tracker.record_failure(provider)
        increment_failure(provider)

    def apply_cooldown(self, provider: str) -> None:
        """Apply cooldown to a provider — temporarily exclude from chain."""
        self._health_tracker.apply_cooldown(provider)

    def get_providers(self) -> list[str]:
        """Get providers in dynamic health-aware fallback chain order.

        Healthy providers are placed first, degraded providers last.
        Providers with consecutive_failures > 3 are temporarily excluded
        during cooldown period. Providers that recover (success event)
        return to their original position in the chain.

        Chain reordering is logged for observability.
        """
        available = self.settings.available_providers
        original_order = [
            p for p in self.settings.SEARCH_FALLBACK_CHAIN if p in available
        ]

        healthy: list[str] = []
        degraded: list[str] = []
        excluded: list[str] = []

        for provider in original_order:
            if self._health_tracker.should_exclude(provider):
                excluded.append(provider)
                continue

            score = self._health_tracker.get_health_score(provider)
            if score >= 0.5:
                healthy.append(provider)
            else:
                degraded.append(provider)

        # Log chain reordering if it differs from original order
        dynamic_chain = healthy + degraded
        if dynamic_chain != original_order:
            logger.info(
                "provider_chain_reordered",
                healthy=healthy,
                degraded=degraded,
                excluded=excluded,
                original=original_order,
                dynamic=dynamic_chain,
            )

        # Update Prometheus chain position gauges for all providers in the chain
        for idx, provider in enumerate(dynamic_chain, start=1):
            update_chain_position(provider, idx)
            update_health_score(
                provider, self._health_tracker.get_health_score(provider)
            )

        return dynamic_chain

    def get_metrics(self) -> bytes:
        """Return Prometheus metrics in standard exposition format.

        Convenience wrapper around `app.core.metrics.get_metrics_bytes`.
        """
        return get_metrics_bytes()

    def get_provider_names(self) -> list[str]:
        """Return provider names for available providers."""
        return [self._registry.get(p, p) for p in self.settings.available_providers]
