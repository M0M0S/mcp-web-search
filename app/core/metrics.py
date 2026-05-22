"""Prometheus metrics for search providers and LLM failover chain.

Exports per-provider counters and gauges compatible with Prometheus scrape format.
Metrics are registered once at module load and updated via ProviderRegistry and
LLMClient.
"""

from prometheus_client import Counter, Gauge, Histogram, generate_latest

# Per-provider total search attempts
provider_search_total = Counter(
    "provider_search_total",
    "Total number of search attempts per provider",
    ["provider"],
)

# Per-provider total search failures
provider_search_failure_total = Counter(
    "provider_search_failure_total",
    "Total number of failed search attempts per provider",
    ["provider"],
)

# Per-provider health score (0.0 — dead, 1.0 — healthy)
provider_health_score = Gauge(
    "provider_health_score",
    "Current health score for a search provider",
    ["provider"],
)

# Per-provider position in the fallback chain (1-based index)
provider_chain_position = Gauge(
    "provider_chain_position",
    "Current position of a provider in the dynamic fallback chain",
    ["provider"],
)

# AC15: Counter total failover events
llm_failover_total = Counter(
    "llm_failover_total",
    "Total number of LLM failover events",
    ["from_model", "to_model"],
)

# AC16: Histogram failover duration
llm_failover_duration_seconds = Histogram(
    "llm_failover_duration_seconds",
    "Time spent on LLM failover in seconds",
)

# AC17: Gauge model health score
llm_model_health_score = Gauge(
    "llm_model_health_score",
    "Health score for LLM model (0.0 — dead, 1.0 — healthy)",
    ["model"],
)

# AC18: Gauge active model index
llm_active_model_index = Gauge(
    "llm_active_model_index",
    "Current active LLM model position in failover chain (1-based)",
    ["model"],
)

# KG metrics (Epic 5)
# AC16: Gauge total concepts count
knowledge_graph_concepts_count = Gauge(
    "knowledge_graph_concepts_count",
    "Total number of concepts in the Knowledge Graph",
)

# AC17: Gauge total related terms count
knowledge_graph_terms_count = Gauge(
    "knowledge_graph_terms_count",
    "Total number of related terms in the Knowledge Graph",
)

# AC18: Counter total KG expansion events
kg_expansion_applied_total = Counter(
    "kg_expansion_applied_total",
    "Total number of Knowledge Graph semantic expansion events",
)

# AC19: Counter total enriched concepts
kg_enriched_concepts_total = Counter(
    "kg_enriched_concepts_total",
    "Total number of concepts enriched from search results",
)

# AC16: Gauge cache TTL distribution by bucket
cache_ttl_distribution = Histogram(
    "cache_ttl_distribution_seconds",
    "Distribution of cache TTL values across cache entries",
    ["bucket"],
)

# AC17: Counter cache stale invalidations total
cache_stale_invalidations_total = Counter(
    "cache_stale_invalidations_total",
    "Total number of cache stale invalidations",
    ["cache_type"],
)

# AC18: Gauge average freshness across cache
cache_freshness_avg = Gauge(
    "cache_freshness_avg",
    "Average freshness score across cached entries",
    ["cache_type"],
)

# AC19: Counter cache hit with stale total
cache_hit_with_stale_total = Counter(
    "cache_hit_with_stale_total",
    "Total number of cache hits with stale entries",
    ["cache_type"],
)


def register_provider(provider: str, chain_position: int, health_score: float) -> None:
    """Register a provider with initial chain position and health score.

    Sets gauge values for a newly known provider. Counters start at 0 by default.
    """
    provider_chain_position.labels(provider=provider).set(chain_position)
    provider_health_score.labels(provider=provider).set(health_score)


def increment_success(provider: str) -> None:
    """Increment the success counter for a provider."""
    provider_search_total.labels(provider=provider).inc()


def increment_failure(provider: str) -> None:
    """Increment the failure counter for a provider."""
    provider_search_failure_total.labels(provider=provider).inc()


def update_health_score(provider: str, score: float) -> None:
    """Update the health score gauge for a provider."""
    provider_health_score.labels(provider=provider).set(score)


def update_chain_position(provider: str, position: int) -> None:
    """Update the chain position gauge for a provider."""
    provider_chain_position.labels(provider=provider).set(position)


def record_llm_failover(from_model: str, to_model: str) -> None:
    """Record a LLM failover event for Prometheus metrics."""
    llm_failover_total.labels(from_model=from_model, to_model=to_model).inc()


def record_llm_failover_duration(duration_seconds: float) -> None:
    """Record LLM failover duration for Prometheus histogram."""
    llm_failover_duration_seconds.observe(duration_seconds)


def update_llm_health_score(model: str, score: float) -> None:
    """Update the health score gauge for an LLM model."""
    llm_model_health_score.labels(model=model).set(score)


def update_llm_active_model_index(model: str, index: int) -> None:
    """Update the active model index gauge for an LLM model.

    Sets the 1-based position of the given model in the failover chain.
    """
    llm_active_model_index.labels(model=model).set(index)


# AC16: Counter total checkpoint saves
webfetch_checkpoint_save_total = Counter(
    "webfetch_checkpoint_save_total",
    "Total number of WebFetch checkpoint saves",
    ["tenant_id"],
)

# AC17: Counter total checkpoint resumes
webfetch_checkpoint_resume_total = Counter(
    "webfetch_checkpoint_resume_total",
    "Total number of WebFetch checkpoint resumes",
    ["tenant_id"],
)

# AC18: Histogram checkpoint payload size in bytes
webfetch_checkpoint_size_bytes = Histogram(
    "webfetch_checkpoint_size_bytes",
    "Checkpoint payload size in bytes",
)

# AC19: Gauge active checkpoints per tenant
webfetch_active_checkpoints = Gauge(
    "webfetch_active_checkpoints",
    "Current number of active checkpoints per tenant",
    ["tenant_id"],
)


def record_checkpoint_save(tenant_id: str) -> None:
    """Record a checkpoint save event for Prometheus metrics."""
    webfetch_checkpoint_save_total.labels(tenant_id=tenant_id).inc()


def record_checkpoint_resume(tenant_id: str) -> None:
    """Record a checkpoint resume event for Prometheus metrics."""
    webfetch_checkpoint_resume_total.labels(tenant_id=tenant_id).inc()


def record_checkpoint_size(size_bytes: int) -> None:
    """Record checkpoint payload size for Prometheus histogram."""
    webfetch_checkpoint_size_bytes.observe(size_bytes)


def update_active_checkpoints(tenant_id: str, count: int) -> None:
    """Update the active checkpoints gauge for a tenant."""
    webfetch_active_checkpoints.labels(tenant_id=tenant_id).set(count)


def record_cache_ttl(bucket: str, ttl_seconds: float) -> None:
    """Record a cache TTL value for Prometheus histogram."""
    cache_ttl_distribution.labels(bucket=bucket).observe(ttl_seconds)


def record_cache_stale_invalidation(cache_type: str) -> None:
    """Record a cache stale invalidation event for Prometheus counter."""
    cache_stale_invalidations_total.labels(cache_type=cache_type).inc()


def update_cache_freshness_avg(cache_type: str, avg_freshness: float) -> None:
    """Update the average freshness gauge for a cache type."""
    cache_freshness_avg.labels(cache_type=cache_type).set(avg_freshness)


def record_cache_hit_with_stale(cache_type: str) -> None:
    """Record a stale cache hit event for Prometheus counter."""
    cache_hit_with_stale_total.labels(cache_type=cache_type).inc()


# KG metrics helpers (Epic 5)


def update_kg_concepts_count(count: int) -> None:
    """Update the Knowledge Graph concepts count gauge."""
    knowledge_graph_concepts_count.set(count)


def update_kg_terms_count(count: int) -> None:
    """Update the Knowledge Graph terms count gauge."""
    knowledge_graph_terms_count.set(count)


def record_kg_expansion_applied() -> None:
    """Record a Knowledge Graph semantic expansion event."""
    kg_expansion_applied_total.inc()


def record_kg_enriched_concepts(count: int) -> None:
    """Record enriched concepts count for Prometheus counter."""
    kg_enriched_concepts_total.inc(count)


def get_metrics_bytes() -> bytes:
    """Return Prometheus metrics in standard exposition format.

    This is the raw text output that a Prometheus scraper expects from
    the /metrics HTTP endpoint. Includes both provider and LLM metrics.
    """
    return generate_latest()
