"""Content-related Pydantic models."""

from typing import Optional

from pydantic import BaseModel, Field

from .judge import JudgeVerdict


class ContentMetadata(BaseModel):
    """Metadata about extracted content."""

    source_url: str
    language: Optional[str] = None
    extract_method: str = "trafilatura"
    is_cached: bool = False
    token_count: int = 0
    freshness_score: float = Field(default=0.75, ge=0.0, le=1.0)
    cache_stale: bool = False


class ContentQualityJudgment(JudgeVerdict):
    """LLM-as-Judge verdict for content quality assessment."""

    readability_score: float = Field(ge=0.0, le=1.0)  # Flesch score
    ads_menues_ratio: float = Field(ge=0.0, le=1.0)
    relevance_to_query: float = Field(ge=0.0, le=1.0)


class CleanContent(BaseModel):
    """Cleaned and sanitized content from URL."""

    text: str
    metadata: ContentMetadata
    is_truncated: bool = False
    html_cleaned: bool = True
    judgment: Optional[ContentQualityJudgment] = None
