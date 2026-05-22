"""LLM client for instructor-based structured outputs and LLM-as-Judge."""

import asyncio
import collections
import ipaddress
import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urlparse

import instructor
from openai import AsyncOpenAI, RateLimitError
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionDeveloperMessageParam,
    ChatCompletionFunctionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)

from app.core.config import Settings
from app.core.knowledge_graph import KnowledgeGraph
from app.core.logging import get_logger
from app.core.metrics import (
    record_kg_expansion_applied,
    record_llm_failover,
    record_llm_failover_duration,
    update_llm_active_model_index,
    update_llm_health_score,
)
from app.models.webfetch import (
    FeatureSet,
    JudgeVerdict,
    SearchQueryList,
    URLSelectionList,
)

logger = get_logger("llm-client")


class LLMChainExhaustedError(Exception):
    """Raised when all models in the failover chain have been exhausted.

    Attributes:
        failed_models: List of models that were attempted and failed.
        last_error: The error from the final model attempt.
    """

    def __init__(self, failed_models: list[str], last_error: Exception):
        self.failed_models = failed_models
        self.last_error = last_error
        models_str = ", ".join(failed_models)
        super().__init__(
            f"LLM chain exhausted: all {len(failed_models)} models failed "
            f"({models_str}). Last error: {type(last_error).__name__}"
        )


@dataclass
class LLMHealthTracker:
    """Per-model health metrics with circular buffer bounded by health_window.

    Tracks success/failure counts, last success time, and consecutive failures
    for each model. Health score is computed as success_rate within the window.

    Supports Redis persistence via persist_health() / restore_health().
    """

    health_window: int = 10
    failure_threshold: float = 0.5

    # Optional Redis client and Settings for health persistence TTL
    _redis_client: Any | None = None
    _settings: Any | None = None

    # Per-model circular buffers of recent events (True = success, False = failure)
    _event_buffers: dict[str, collections.deque] = field(default_factory=dict)
    # Per-model aggregate counters
    _success_counts: dict[str, int] = field(default_factory=dict)
    _failure_counts: dict[str, int] = field(default_factory=dict)
    # Per-model last success timestamp
    _last_success_times: dict[str, float | None] = field(default_factory=dict)

    def get_health_score(self, model: str) -> float:
        """Return health score for model in range [0.0, 1.0].

        Score is the ratio of successes to total events in the circular buffer.
        If no events recorded, returns 1.0 (healthy by default).
        """
        buffer = self._event_buffers.get(model)
        if not buffer or len(buffer) == 0:
            return 1.0

        successes = sum(1 for event in buffer if event)
        total = len(buffer)
        return successes / total

    def should_exclude(self, model: str) -> bool:
        """Return True if model should be excluded from the failover chain.

        Exclusion criteria:
        - consecutive_failures > failure_threshold * health_window
        """
        consecutive = self._consecutive_failures(model)
        threshold_count = self.failure_threshold * self.health_window
        return consecutive > threshold_count

    def record_success(self, model: str) -> None:
        """Record a successful LLM call for the given model."""
        self._record_event(model, True)
        self._success_counts[model] = self._success_counts.get(model, 0) + 1
        # Use time.time() for sync/async compatibility
        self._last_success_times[model] = time.time()

    def record_failure(self, model: str) -> None:
        """Record a failed LLM call for the given model."""
        self._record_event(model, False)
        self._failure_counts[model] = self._failure_counts.get(model, 0) + 1

    def _record_event(self, model: str, success: bool) -> None:
        """Add event to the circular buffer for the model."""
        if model not in self._event_buffers:
            self._event_buffers[model] = collections.deque(maxlen=self.health_window)
        self._event_buffers[model].append(success)

    def _consecutive_failures(self, model: str) -> int:
        """Count consecutive failures from the most recent event backwards."""
        buffer = self._event_buffers.get(model)
        if not buffer or len(buffer) == 0:
            return 0

        count = 0
        for event in reversed(buffer):
            if event:
                break
            count += 1
        return count

    def reset_model(self, model: str) -> None:
        """Reset all metrics for a model (e.g. after manual recovery)."""
        self._event_buffers.pop(model, None)
        self._success_counts.pop(model, None)
        self._failure_counts.pop(model, None)
        self._last_success_times.pop(model, None)

    @property
    def _redis_ttl(self) -> int:
        """Return Redis TTL for health keys — from settings or default 3600."""
        if self._settings:
            return getattr(self._settings, "REDIS_HEALTH_TTL", 3600)
        return 3600

    async def persist_health(self) -> None:
        """Persist health metrics to Redis for cross-process recovery.

        Writes per-model health data as JSON under key `llm_health:{model_name}`.
        Gracefully skips if Redis is unavailable.

        TTL is derived from Settings.REDIS_HEALTH_TTL or defaults to 3600s.
        """
        if self._redis_client is None:
            return

        ttl = self._redis_ttl

        try:
            for model in self._event_buffers:
                health_data = {
                    "health_score": self.get_health_score(model),
                    "last_success_time": self._last_success_times.get(model),
                    "success_count": self._success_counts.get(model, 0),
                    "failure_count": self._failure_counts.get(model, 0),
                    "consecutive_failures": self._consecutive_failures(model),
                    "event_buffer": list(self._event_buffers[model]),
                }
                key = f"llm_health:{model}"
                await self._redis_client.set(key, json.dumps(health_data), ex=ttl)
        except Exception as e:
            logger.debug(
                "llm_health_persist_error",
                error=type(e).__name__,
            )

    async def restore_health(self) -> None:
        """Restore health metrics from Redis.

        Reads per-model health data from `llm_health:{model_name}` keys and
        replays the event buffer to reconstruct state. Gracefully skips if
        Redis is unavailable or data is missing.
        """
        if self._redis_client is None:
            return

        try:
            # Get all llm_health keys
            keys = await self._redis_client.keys("llm_health:*")
            for key in keys:
                model = key.decode().removeprefix("llm_health:")
                raw = await self._redis_client.get(key)
                if raw is None:
                    continue

                health_data = json.loads(raw)
                self._success_counts[model] = health_data.get("success_count", 0)
                self._failure_counts[model] = health_data.get("failure_count", 0)
                self._last_success_times[model] = health_data.get("last_success_time")

                event_buffer = health_data.get("event_buffer", [])
                if event_buffer:
                    self._event_buffers[model] = collections.deque(
                        event_buffer, maxlen=self.health_window
                    )
        except Exception as e:
            logger.debug(
                "llm_health_restore_error",
                error=type(e).__name__,
            )

    def get_health_summary(self) -> list[dict]:
        """Return health summary for all tracked models.

        Returns a list of dicts with health_score, last_success_time,
        consecutive_failures, and excluded status for each model.
        """
        summary = []
        all_models = (
            set(self._event_buffers.keys())
            | set(self._success_counts.keys())
            | set(self._failure_counts.keys())
        )
        for model in sorted(all_models):
            summary.append(
                {
                    "model": model,
                    "health_score": self.get_health_score(model),
                    "last_success_time": self._last_success_times.get(model),
                    "consecutive_failures": self._consecutive_failures(model),
                    "excluded": self.should_exclude(model),
                    "success_count": self._success_counts.get(model, 0),
                    "failure_count": self._failure_counts.get(model, 0),
                }
            )
        return summary


class LLMClient:
    """Async LLM client with Instructor integration and failover chain support."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        fallback_chain: list[str] | None = None,
        fallback_base_urls: dict[str, str] | None = None,
        health_window: int = 10,
        failure_threshold: float = 0.5,
        max_concurrent_calls: int = 10,
        redis_client: Any | None = None,
        settings: Settings | None = None,
    ):
        self._primary_model = model
        self._fallback_chain = fallback_chain or [model]
        self._fallback_base_urls = fallback_base_urls or {}
        self._health_tracker = LLMHealthTracker(
            health_window=health_window,
            failure_threshold=failure_threshold,
            _redis_client=redis_client,
            _settings=settings,
        )

        # Current active model (may change during failover)
        self._active_model: str = model

        # Store api_key for failover client creation (never log/print)
        self._api_key: str = api_key

        # Store base_url for failover resolution
        self._default_base_url: str = base_url

        # Client cache per (api_key, base_url) pair to avoid wasteful recreation
        self._client_cache: dict[tuple[str, str], instructor.Instructor] = {}

        # Rate limit: semaphore controls concurrent LLM calls (default 10 req/s)
        self._semaphore = asyncio.Semaphore(max_concurrent_calls)

        # Per-call failover counter — tracks how many times the model was
        # switched during the most recent _call_with_failover invocation.
        self._last_call_failover_count: int = 0

    @property
    def active_model(self) -> str:
        """Return the currently active LLM model."""
        return self._active_model

    @property
    def health_tracker(self) -> LLMHealthTracker:
        """Return the health tracker instance for monitoring."""
        return self._health_tracker

    @property
    def last_call_failover_count(self) -> int:
        """Return the number of model switches during the last LLM call.

        This is a per-call metric, not a global counter. Resets to 0 after
        each successful call or chain exhaustion.
        """
        return self._last_call_failover_count

    def _get_client(self, base_url: str) -> instructor.Instructor:
        """Get or create an instructor-wrapped AsyncOpenAI client for base_url."""
        key = (self._api_key, base_url)
        if key not in self._client_cache:
            self._client_cache[key] = instructor.from_openai(
                AsyncOpenAI(api_key=self._api_key, base_url=base_url),
                mode=instructor.Mode.MD_JSON,
            )
        return self._client_cache[key]

    def _resolve_base_url(self, model: str, default_base_url: str) -> str:
        """Resolve the base_url for a given model.

        Checks fallback_base_urls first, then falls back to default.
        Validates URL scheme (https/http only) and denies private IP ranges
        for SSRF protection.
        """
        url = self._fallback_base_urls.get(model, default_base_url)
        parsed = urlparse(url)
        if parsed.scheme not in ("https", "http"):
            logger.warning(
                "llm_invalid_base_url_scheme",
                model=model,
                url=url,
                fallback=self._default_base_url,
            )
            return self._default_base_url

        # Deny private/internal IP ranges for SSRF protection
        hostname = parsed.hostname
        if hostname:
            private_ranges = [
                "localhost",
                "127.0.0.1",
                "::1",
            ]
            if hostname in private_ranges:
                logger.warning(
                    "llm_private_ip_denied",
                    model=model,
                    hostname=hostname,
                    fallback=self._default_base_url,
                )
                return self._default_base_url

            # Check for private/unspecified IP ranges (RFC 1918 + RFC 5735)
            try:
                ip = ipaddress.ip_address(hostname)
                if ip.is_private or ip.is_loopback or ip.is_unspecified:
                    logger.warning(
                        "llm_private_ip_denied",
                        model=model,
                        hostname=hostname,
                        fallback=self._default_base_url,
                    )
                    return self._default_base_url
            except ValueError:
                pass  # hostname is not a valid IP — assume DNS resolution OK

        return url

    def _get_fallback_models(self) -> list[str]:
        """Get the list of models eligible for failover.

        Excludes models that have been marked unhealthy by the health tracker.
        """
        return [
            m
            for m in self._fallback_chain
            if not self._health_tracker.should_exclude(m)
        ]

    async def _call_with_failover(
        self,
        messages: list[dict],
        response_model,
        max_retries_per_model: int = 3,
        initial_delay: float = 0.5,
    ) -> Any:
        """Execute LLM call with failover across the model chain.

        On RateLimitError, ConnectionError, TimeoutError, or any non-retryable
        Exception, attempts the next model in the fallback chain. Logs failover
        transitions and records success/failure in the health tracker.

        Tracks per-call failover count: how many times the model was switched
        during this single invocation. Exposed via `last_call_failover_count`.

        Raises LLMChainExhaustedError if all models are exhausted.
        """
        # Reset per-call failover counter
        self._last_call_failover_count = 0

        # AC16: Track failover duration
        call_start_time = time.monotonic()

        # Build ordered list of (model, base_url) pairs
        candidates: list[tuple[str, str]] = []
        for model in self._get_fallback_models():
            url = self._resolve_base_url(model, self._default_base_url)
            candidates.append((model, url))

        if not candidates:
            raise LLMChainExhaustedError(
                list(self._fallback_chain),
                Exception("No eligible models available in failover chain"),
            )

        last_error: Exception | None = None
        failed_models: list[str] = []

        for candidate_model, candidate_url in candidates:
            client = self._get_client(candidate_url)

            for attempt in range(max_retries_per_model):
                try:
                    async with self._semaphore:
                        response = await client.chat.completions.create(
                            model=candidate_model,
                            messages=cast(
                                list[
                                    ChatCompletionDeveloperMessageParam
                                    | ChatCompletionSystemMessageParam
                                    | ChatCompletionUserMessageParam
                                    | ChatCompletionAssistantMessageParam
                                    | ChatCompletionToolMessageParam
                                    | ChatCompletionFunctionMessageParam
                                ],
                                messages,
                            ),
                            response_model=response_model,
                        )

                    # Success — update active model and health tracker
                    if candidate_model != self._active_model:
                        self._last_call_failover_count += 1
                        logger.info(
                            "llm_failover",
                            old_model=self._active_model,
                            new_model=candidate_model,
                            failover_count=self._last_call_failover_count,
                        )
                        # AC15: Record Prometheus failover counter
                        record_llm_failover(
                            from_model=self._active_model,
                            to_model=candidate_model,
                        )
                    self._active_model = candidate_model
                    self._health_tracker.record_success(candidate_model)
                    # AC17: Update Prometheus health score gauge
                    update_llm_health_score(
                        model=candidate_model,
                        score=self._health_tracker.get_health_score(candidate_model),
                    )
                    # AC18: Update Prometheus active model index gauge
                    try:
                        index = self._fallback_chain.index(candidate_model) + 1
                    except ValueError:
                        index = 1
                    update_llm_active_model_index(model=candidate_model, index=index)
                    # AC16: Record Prometheus failover duration
                    duration = time.monotonic() - call_start_time
                    record_llm_failover_duration(duration)
                    logger.info(
                        "llm_call_completed",
                        model=candidate_model,
                        failover_count=self._last_call_failover_count,
                        duration_seconds=round(duration, 3),
                    )
                    return response

                except RateLimitError:
                    wait_time = initial_delay * (2**attempt) + random.uniform(0, 0.1)
                    logger.debug(
                        "llm_rate_limit",
                        model=candidate_model,
                        attempt=attempt + 1,
                        delay=wait_time,
                    )
                    # Record rate limit as failure for health tracking
                    self._health_tracker.record_failure(candidate_model)
                    await asyncio.sleep(wait_time)
                    continue

                except (ConnectionError, TimeoutError) as exc:
                    logger.warning(
                        "llm_connection_error",
                        model=candidate_model,
                        attempt=attempt + 1,
                        error=type(exc).__name__,
                    )
                    await asyncio.sleep(initial_delay)
                    break  # break retry loop for this model, try next

                except Exception as exc:
                    logger.warning(
                        "llm_call_error",
                        model=candidate_model,
                        attempt=attempt + 1,
                        error=type(exc).__name__,
                    )
                    last_error = exc
                    self._health_tracker.record_failure(candidate_model)
                    failed_models.append(candidate_model)
                    break  # non-retryable error, try next model

        # All candidates exhausted
        error = last_error or Exception("Unknown error during final attempt")
        raise LLMChainExhaustedError(failed_models, error)

    async def generate_search_queries(
        self,
        prompt: str,
        query_count: int = 5,
        kg_expansion: bool = False,
        kg: KnowledgeGraph | None = None,
    ) -> list[str]:
        """Generate N search queries from user prompt via LLM with optional KG expansion.

        Args:
            prompt: User search prompt.
            query_count: Number of queries to generate (3-10).
            kg_expansion: If True, perform semantic expansion via KnowledgeGraph.
            kg: Optional persistent KnowledgeGraph instance. If provided and
                kg_expansion is True, this instance is used for lookup instead
                of creating a fresh one.

        Returns:
            List of generated search queries.

        Raises:
            ValueError: If query_count out of range [3, 10].
        """
        if query_count < 3 or query_count > 10:
            raise ValueError("query_count must be in range [3, 10]")

        # AC6-10: KG semantic expansion
        expanded_terms: list[tuple[str, float]] = []
        if kg_expansion:
            expanded_terms = self._kg_lookup(prompt, kg=kg)

        result = await self._call_with_failover(
            messages=[
                {
                    "role": "user",
                    "content": f'Generate {query_count} search queries for: {prompt}. Return as JSON object with key \'queries\'. Example: {{"queries": ["query1", "query2", ...]}}',
                },
            ],
            response_model=SearchQueryList,
        )

        queries = result.queries
        if len(queries) < 3:
            fallback_queries = self._generate_fallback_queries(prompt, query_count)
            return fallback_queries

        # AC8: Add KG expanded terms to queries (weighted by confidence)
        if kg_expansion and expanded_terms:
            queries = self._inject_kg_terms(queries, expanded_terms, query_count)
            # AC9: Log KG expansion event
            logger.info(
                "kg_expansion_applied",
                prompt=prompt,
                expanded_terms=[t[0] for t in expanded_terms],
                query_count=len(queries),
            )
            # Record Prometheus metric
            record_kg_expansion_applied()

        return queries

    def _kg_lookup(
        self, prompt: str, kg: KnowledgeGraph | None = None
    ) -> list[tuple[str, float]]:
        """Lookup related terms from KnowledgeGraph for the given prompt.

        AC7: Extract keywords from prompt and retrieve related terms.
        AC10: Returns empty list if KG is empty or no match found.

        Args:
            prompt: User prompt to lookup.
            kg: Optional KnowledgeGraph instance. If None, creates a fresh
                instance with seed data for lookup.

        Returns:
            List of (term, confidence) tuples sorted by confidence descending.
        """
        # Extract keywords from prompt (simple word-based extraction)
        keywords = [
            word.strip().lower() for word in prompt.split() if len(word.strip()) > 2
        ]

        if not keywords:
            return []

        # Use provided KG or create one with seed data
        graph = kg or KnowledgeGraph(
            storage_backend="sqlite",
            db_path=":memory:",
            seed_data=KnowledgeGraph.default_seed_data(),
        )

        related_terms = graph.lookup_related_terms(keywords)

        if not related_terms:
            logger.debug(
                "kg_no_match",
                prompt=prompt,
                keywords=keywords,
            )

        return related_terms

    def _inject_kg_terms(
        self,
        queries: list[str],
        expanded_terms: list[tuple[str, float]],
        query_count: int,
    ) -> list[str]:
        """Inject KG expanded terms into generated queries.

        AC8: Related terms added to generated queries weighted by KG confidence.
        High-confidence terms (>0.7) are injected as additional queries.
        Lower-confidence terms are appended to existing queries.

        Args:
            queries: Original LLM-generated queries.
            expanded_terms: (term, confidence) tuples from KG lookup.
            query_count: Target query count.

        Returns:
            Combined list of queries with KG terms injected.
        """
        result_queries = list(queries)

        # Separate high and low confidence terms
        high_confidence = [t for t in expanded_terms if t[1] >= 0.7]
        low_confidence = [t for t in expanded_terms if t[1] < 0.7]

        # Inject high-confidence terms as new queries
        for term, confidence in high_confidence:
            if len(result_queries) < query_count + len(high_confidence):
                result_queries.append(term)

        # Append low-confidence terms to existing queries
        for term, confidence in low_confidence:
            if result_queries:
                # Append to the first query (most relevant)
                result_queries[0] = f"{result_queries[0]} {term}"

        return result_queries[: query_count + len(high_confidence)]

    def _generate_fallback_queries(self, prompt: str, query_count: int) -> list[str]:
        """Generate fallback queries when LLM returns fewer than 3."""
        fallback_queries = [prompt]
        variations = [
            f"{prompt} example",
            f"related to {prompt}",
        ]
        while len(fallback_queries) < 3:
            fallback_queries.extend(variations)
            fallback_queries = list(set(fallback_queries))
        return fallback_queries[:query_count]

    async def select_urls(self, prompt: str, search_results: list) -> list[dict]:
        """Select top 5 URLs with priority and reason via LLM."""
        return await self._call_with_failover(
            messages=[
                {
                    "role": "user",
                    "content": f"""Generate URL selection for query: {prompt}
                        Return as JSON object with key 'urls'. Each URL must have url, priority, reason. Example: {{"urls": [{{"url": "https://example.com", "priority": 1, "reason": "relevant"}}]}}.""",
                },
            ],
            response_model=URLSelectionList,
        )

    async def judge_urls(self, prompt: str, urls: list[str]) -> JudgeVerdict:
        """Judge URLs via LLM-as-Judge (Faithfulness, Trustworthiness, Diversity).

        AC 2: LLM prompt includes temporal context for date-awareness freshness scoring.
        """
        import datetime

        current_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

        urls_str = "\n".join(f"- {url}" for url in urls)

        return await self._call_with_failover(
            messages=[
                {
                    "role": "user",
                    "content": f"""Evaluate the quality of these URLs for: {prompt}.
                        Score relevance, trustworthiness, diversity. Return verdict (pass/retry/reject).
                        Provide scores for diversity_score, trustworthiness_score, relevance_to_query, freshness_score.

                        Temporal context: current date is {current_date}.
                        Consider how likely each URL's content is to be still accurate as of this date.

                        URLs:
                        {urls_str}
                        """,
                },
            ],
            response_model=JudgeVerdict,
        )

    async def judge_urls_with_content(
        self, prompt: str, url_content_pairs: list[dict]
    ) -> JudgeVerdict:
        """Judge URLs via LLM-as-Judge with content (Faithfulness, Trustworthiness, Diversity).

        AC 2: LLM prompt includes temporal context for date-awareness freshness scoring.
        """
        import datetime

        content_str = ""
        for i, pair in enumerate(url_content_pairs):
            url = str(pair.get("url", ""))
            title = str(pair.get("title", ""))
            description = str(pair.get("description", ""))
            content_str += f"[{i + 1}] URL: {url}\nTitle: {title}\nContent: {description[:500] if description else 'N/A'}\n\n"

        current_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

        return await self._call_with_failover(
            messages=[
                {
                    "role": "user",
                    "content": f"""Evaluate the quality of these URLs with their content for: {prompt}.
                        Score relevance, trustworthiness, diversity. Return verdict (pass/retry/reject).
                        Provide scores for diversity_score, trustworthiness_score, relevance_to_query, freshness_score.

                        Temporal context: current date is {current_date}.
                        Consider how likely the content is to be still accurate as of this date.
                        Give a freshness_score (0.0-1.0) reflecting content staleness risk.

                        URL Content:
                        {content_str}
                        """,
                },
            ],
            response_model=JudgeVerdict,
        )

    async def generate_features(self, prompt: str, content: list[str]) -> FeatureSet:
        """Generate atomic features with quotes from content via LLM."""
        content_str = "\n".join(content[:3])

        return await self._call_with_failover(
            messages=[
                {
                    "role": "system",
                    "content": """Extract atomic features from the provided content. Return as JSON object with key 'features' (list of strings) and key 'sources' (list of URLs). Do not return schema definition, return actual instance.""",
                },
                {
                    "role": "user",
                    "content": f"""Extract atomic features from the content for: {prompt}.
Content: {content_str}
Return as JSON object with key 'features' (list of strings) and key 'sources' (list of URLs).""",
                },
            ],
            response_model=FeatureSet,
        )

    async def judge_features(self, prompt: str, features: FeatureSet) -> JudgeVerdict:
        """Judge features via LLM-as-Judge (Groundedness, hallucinations)."""
        return await self._call_with_failover(
            messages=[
                {
                    "role": "user",
                    "content": f"""Evaluate the quality of these features for: {prompt}.
                        Check groundedness and detect hallucinations. Return verdict.""",
                },
            ],
            response_model=JudgeVerdict,
        )

    async def rate_relevance(self, text: str, query: str) -> float:
        """Rate relevance of text to query via LLM."""
        text_snippet = text[:500]

        result = await self._call_with_failover(
            messages=[
                {
                    "role": "system",
                    "content": "Return a score from 0.0 to 1.0 based on relevance.",
                },
                {
                    "role": "user",
                    "content": f"Rate how relevant this text is to query '{query}'. Text: {text_snippet}",
                },
            ],
            response_model=float,
        )

        score = float(result) if isinstance(result, (int, float)) else 0.0
        return max(0.0, min(1.0, score))

    async def generate_final_answer(self, prompt: str, features: list[str]) -> str:
        """Generate final aggregated answer from features via LLM."""
        features_str = "\n".join(features[:10])

        result = await self._call_with_failover(
            messages=[
                {
                    "role": "system",
                    "content": f"""Synthesize a final answer to the question: {prompt}.
Use the following features extracted from sources. Be concise and factual.
Return only the final answer as plain text, no JSON.""",
                },
                {
                    "role": "user",
                    "content": f"""Features:
{features_str}

Final answer:""",
                },
            ],
            response_model=str,
        )

        return result if isinstance(result, str) else str(result)


class LLMHealthProbe:
    """Background periodic health probe for each LLM model.

    Performs a lightweight chat call with a minimal "ping" message at configurable
    intervals. Probe results update health metrics in the LLMHealthTracker.
    Probe errors do NOT count as failures (probe-specific handling).

    Start/stop lifecycle matches ProviderHealthProbe pattern.
    """

    def __init__(
        self,
        tracker: LLMHealthTracker,
        settings: "Settings",
        interval: int | None = None,
    ):
        self._tracker = tracker
        self._settings = settings
        # Use Settings.LLM_HEALTH_PROBE_INTERVAL if available, else default 60s
        self._interval = interval or settings.LLM_HEALTH_PROBE_INTERVAL
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background health probe loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._probe_loop())
        logger.info("llm_health_probe_started", interval=self._interval)

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
        logger.info("llm_health_probe_stopped")

    async def _probe_loop(self) -> None:
        """Main probe loop — runs at configurable interval.

        Periodically probes each available LLM model and persists health to Redis.
        """
        while self._running:
            try:
                await self._probe_all_models()
                await self._tracker.persist_health()
            except Exception as exc:
                logger.warning(
                    "llm_health_probe_loop_error",
                    error=type(exc).__name__,
                )

            await asyncio.sleep(self._interval)

    async def _probe_all_models(self) -> None:
        """Probe each available LLM model with a lightweight chat call."""
        available = self._settings.available_llm_models
        for model in available:
            try:
                await self._probe_single(model)
            except Exception as exc:
                logger.debug(
                    "llm_health_probe_skipped",
                    model=model,
                    reason=type(exc).__name__,
                )

    async def _probe_single(self, model: str) -> None:
        """Probe a single LLM model with a minimal ping message."""
        # Skip models that require unavailable API keys
        key_name = self._settings._resolve_llm_key_name(model)
        if key_name and not os.getenv(key_name):
            logger.debug("llm_health_probe_skipped_no_key", model=model)
            return

        # Create a temporary client for the probe (never stored in cache)
        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            logger.debug("llm_health_probe_no_api_key", model=model)
            return

        base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        probe_client = instructor.from_openai(
            AsyncOpenAI(api_key=api_key, base_url=base_url),
            mode=instructor.Mode.MD_JSON,
        )

        # Lightweight ping — minimal message to test connectivity
        probe_messages = [
            {
                "role": "user",
                "content": "ping",
            },
        ]

        try:
            # Use a simple response model to avoid heavy parsing
            await probe_client.chat.completions.create(
                model=model,
                messages=cast(
                    list[
                        ChatCompletionDeveloperMessageParam
                        | ChatCompletionSystemMessageParam
                        | ChatCompletionUserMessageParam
                        | ChatCompletionAssistantMessageParam
                        | ChatCompletionToolMessageParam
                        | ChatCompletionFunctionMessageParam
                    ],
                    probe_messages,
                ),
                response_model=str,
                max_tokens=5,
            )
            # Probe succeeded — update health tracker (but NOT as a normal success)
            # We record a probe-specific success to keep health metrics warm
            self._tracker.record_success(model)
            logger.debug(
                "llm_health_probe_success",
                model=model,
                score=self._tracker.get_health_score(model),
            )
        except Exception:
            # Probe errors do NOT count as failures — only probe-specific handling
            logger.debug(
                "llm_health_probe_failed_not_counted",
                model=model,
            )


def create_llm_client(
    redis_client: Any | None = None,
    settings: Settings | None = None,
) -> LLMClient:
    """Factory function to create LLMClient from Settings with failover params.

    Optional redis_client and settings parameters enable health persistence.
    When provided, the LLMClient passes them to its LLMHealthTracker for
    Redis-based state recovery.
    """
    from app.core.config import Settings

    local_settings = settings or Settings()
    api_key = os.getenv("LLM_API_KEY")

    if not api_key:
        raise ValueError("LLM_API_KEY is required")

    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    model = local_settings.LLM_MODEL
    fallback_chain = local_settings.LLM_MODEL_FALLBACK_CHAIN
    fallback_base_urls = local_settings.LLM_MODEL_FALLBACK_BASE_URLS
    health_window = local_settings.LLM_HEALTH_WINDOW
    failure_threshold = local_settings.LLM_HEALTH_FAILURE_THRESHOLD

    return LLMClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        fallback_chain=fallback_chain,
        fallback_base_urls=fallback_base_urls,
        health_window=health_window,
        failure_threshold=failure_threshold,
        redis_client=redis_client,
        settings=local_settings,
    )
