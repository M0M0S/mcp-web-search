"""Judge verdict models."""

from pydantic import BaseModel, Field


class JudgeVerdict(BaseModel):
    """LLM-as-Judge verdict for URLs or features."""

    score: float = Field(default=0.85, ge=0.0, le=1.0)
    diversity_score: float = Field(default=0.8, ge=0.0, le=1.0)
    trustworthiness_score: float = Field(default=0.8, ge=0.0, le=1.0)
    relevance_to_query: float = Field(default=0.85, ge=0.0, le=1.0)
    freshness_score: float = Field(default=0.75, ge=0.0, le=1.0)
    verdict: str = "pass"  # pass | retry | reject
    reasons: list[str] = []
