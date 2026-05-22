"""MCP tool for LLM health status reporting."""

from app.core.llm_client import create_llm_client
from app.core.logging import get_logger

logger = get_logger(__name__)


async def llm_health() -> list[dict]:
    """Return health status of all LLM models in the failover chain.

    This tool provides a snapshot of LLM model health metrics including
    health scores, consecutive failures, and exclusion status.

    Creates an LLMClient instance internally to access the health tracker.

    Returns:
        List of dicts with model health information:
        - model: LLM model name
        - health_score: Health score (0.0 — dead, 1.0 — healthy)
        - last_success_time: Timestamp of last successful call (or null)
        - consecutive_failures: Number of consecutive failures
        - excluded: Whether model is currently excluded from failover chain
        - success_count: Total successful calls tracked
        - failure_count: Total failed calls tracked
        - is_active: Whether this model is the currently active one
    """
    # Create a transient LLMClient to access health tracker state
    # This is a read-only operation — no actual LLM calls are made
    llm_client = create_llm_client()
    tracker = llm_client.health_tracker
    summary = tracker.get_health_summary()

    # Add active model info
    active = llm_client.active_model
    for entry in summary:
        if entry["model"] == active:
            entry["is_active"] = True
        else:
            entry["is_active"] = False

    # Add last call failover count
    summary.append(
        {
            "model": "_meta",
            "last_call_failover_count": llm_client.last_call_failover_count,
            "description": "Per-call failover count for the most recent LLM invocation",
        }
    )

    return summary
