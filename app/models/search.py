"""Search-related Pydantic models."""

from typing import TYPE_CHECKING, ClassVar, Literal, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.models.webfetch import JudgeVerdict


class QualityScore(BaseModel):
    """Quality score for search results."""

    overall: float = Field(ge=0.0, le=1.0)
    content_quality: float = Field(ge=0.0, le=1.0)
    seo_spam_score: float = Field(ge=0.0, le=1.0)
    clickbait_score: float = Field(ge=0.0, le=1.0)


class SearchResultJudge(BaseModel):
    """LLM-as-Judge verdict for search results."""

    diversity_score: float = Field(ge=0.0, le=1.0)
    trustworthiness_score: float = Field(ge=0.0, le=1.0)
    relevance_to_query: float = Field(ge=0.0, le=1.0)
    freshness_score: float = Field(default=0.75, ge=0.0, le=1.0)

    # Fields from JudgeVerdict for LLM judgment
    score: Optional[float] = None  # Primary judgment score from LLM
    verdict: str = "pass"  # pass | retry | reject
    reasons: list[str] = []

    @classmethod
    def from_judge_verdict(cls, verdict: "JudgeVerdict") -> "SearchResultJudge":
        """Create SearchResultJudge from JudgeVerdict."""
        return cls(
            diversity_score=verdict.diversity_score,
            trustworthiness_score=verdict.trustworthiness_score,
            relevance_to_query=verdict.relevance_to_query,
            freshness_score=verdict.freshness_score,
            score=verdict.score,
            verdict=verdict.verdict,
            reasons=verdict.reasons or [],
        )


class SearchResult(BaseModel):
    """Single search result item."""

    url: str  # Changed from HttpUrl to str for easier testing
    title: str
    description: Optional[str] = None
    provider: str = "duck"
    quality_score: Optional[QualityScore] = None
    is_blacklisted: bool = False
    timestamp: Optional[str] = None
    judgment: Optional[SearchResultJudge] = None
    freshness_score: float = Field(default=0.75, ge=0.0, le=1.0)
    cache_stale: bool = False


class SearchRequest(BaseModel):
    """Search request payload."""

    query: str = Field(..., min_length=1, max_length=1000)
    max_results: int = Field(default=10, ge=1, le=50)
    region: str = Field(default="wt-wt")
    language: Optional[str] = Field(
        default="en"
    )  # English by default for global search
    filter_blacklist: bool = True
    calculate_quality: bool = True
    apply_smart_filter: bool = True
    auto_detect_language: bool = (
        False  # Explicitly use DEFAULT_LANGUAGE instead of auto-detect
    )
    skip_judge: bool = Field(
        default=False,
        description="Skip LLM-as-Judge relevance check (for trusted sites)",
    )
    output_format: Literal["markdown", "json"] = Field(
        default="markdown",
        description="Output format for search results (default: markdown)",
    )

    # New tunability parameters
    engines: Optional[str] = None  # "duck,tavily,google" or provider names
    time_range: Optional[Literal["day", "week", "month", "year"]] = None
    site: Optional[str] = None  # domain restriction
    ttl_override: Optional[int] = Field(
        default=None,
        ge=60,
        le=86400,
        description="Override cache TTL in seconds (must be 60-86400)",
    )


class SearchParameters(BaseModel):
    """Search parameters metadata."""

    engines: Optional[str] = None
    time_range: Optional[Literal["day", "week", "month", "year"]] = None
    site: Optional[str] = None
    ttl_override: Optional[int] = None


class SearchResponse(BaseModel):
    """Search response payload."""

    results: list[SearchResult]
    provider: str
    cache_hit: bool = False
    total_found: int
    diversity_scores: dict[str, float] = Field(default_factory=dict)
    parameters: Optional[SearchParameters] = None
    judgment: Optional[SearchResultJudge] = None
    cache_stale: bool = False  # True if served from stale cache entry
    search_results: Optional[list["SearchResponse"]] = (
        None  # Nested search results (Epic 6 AC6)
    )


class SearchErrorDetail(BaseModel):
    """Detailed error information for unavailable providers."""

    status: str = "error"
    message: str
    reason: str  # "missing_api_key", "not_implemented", etc.


class UnifiedJSONResponse(BaseModel):
    """Unified JSON response schema for search and webfetch outputs.

    Schema:
        query — search query or webfetch prompt
        format — Literal["json"]
        results — list[dict] (search results or webfetch sources)
        metadata — dict (provider info, diversity scores, judgment, etc.)

    Provides built-in JSON serialization validation with optional
    size bounding (max 500KB).
    """

    query: str
    format: Literal["json"] = "json"
    results: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    MAX_JSON_SIZE: ClassVar[int] = 524288  # 500 KB in bytes

    def validate_json_size(self) -> "UnifiedJSONResponse":
        """Validate JSON output size and truncate if exceeds limit.

        Serializes the model to JSON, checks byte size against MAX_JSON_SIZE.
        If exceeded, progressively truncates the results list and adds
        a warning to metadata. Returns self after validation.

        Uses binary search to find the maximum number of results that fit
        within the size limit.

        Returns:
            Self with potentially truncated results and warning metadata.

        Graceful fallback:
            If even an empty results list exceeds the size limit, returns
            the model with empty results and a warning — never raises.
        """
        json_bytes = self.model_dump_json().encode("utf-8")

        if len(json_bytes) <= self.MAX_JSON_SIZE:
            return self

        # Truncate results to fit within size limit
        original_count = len(self.results)
        low, high = 0, original_count

        while low <= high:
            mid = (low + high) // 2
            truncated = self.model_copy(update={"results": self.results[:mid]})
            json_bytes = truncated.model_dump_json().encode("utf-8")

            if len(json_bytes) <= self.MAX_JSON_SIZE:
                low = mid + 1  # try larger count
            else:
                high = mid - 1  # need fewer results

        final_count = high if high >= 0 else 0
        truncated = self.model_copy(
            update={
                "results": self.results[:final_count],
                "metadata": {
                    **self.metadata,
                    "warning": (
                        f"JSON output truncated: {original_count} results "
                        f"reduced to {final_count} to fit within "
                        f"{self.MAX_JSON_SIZE} bytes (500 KB) limit"
                    ),
                },
            }
        )

        final_json_bytes = truncated.model_dump_json().encode("utf-8")
        if len(final_json_bytes) > self.MAX_JSON_SIZE:
            # Graceful fallback: metadata alone exceeds limit — return empty results
            return self.model_copy(
                update={
                    "results": [],
                    "metadata": {
                        **self.metadata,
                        "warning": (
                            f"JSON output truncated: metadata alone exceeds "
                            f"{self.MAX_JSON_SIZE} bytes (500 KB) limit — "
                            f"all {original_count} results removed"
                        ),
                    },
                }
            )

        return truncated
