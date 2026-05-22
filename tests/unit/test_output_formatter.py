"""Unit tests for OutputFormatter and related model validation."""

import pytest

from app.models.search import (
    QualityScore,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SearchResultJudge,
    UnifiedJSONResponse,
)
from app.models.webfetch import FeatureSet, JudgeVerdict, WebFetchState
from app.services.output_formatter import (
    OutputFormatter,
    _freshness_badge,
    _quality_badge,
)

# ── _quality_badge ──────────────────────────────────────────────────────


class TestQualityBadge:
    """Tests for _quality_badge score-to-badge mapping."""

    def test_high_badge_for_score_at_threshold(self):
        """Verify score >= 0.8 returns 🟢 high badge."""
        assert _quality_badge(0.8) == "🟢 high"

    def test_high_badge_for_score_above_threshold(self):
        """Verify score > 0.8 returns 🟢 high badge."""
        assert _quality_badge(1.0) == "🟢 high"

    def test_high_badge_for_score_close_to_threshold(self):
        """Verify score slightly above 0.8 returns 🟢 high badge."""
        assert _quality_badge(0.85) == "🟢 high"

    def test_medium_badge_for_score_at_threshold(self):
        """Verify score >= 0.6 returns 🟡 medium badge."""
        assert _quality_badge(0.6) == "🟡 medium"

    def test_medium_badge_for_score_above_threshold(self):
        """Verify score > 0.6 and < 0.8 returns 🟡 medium badge."""
        assert _quality_badge(0.7) == "🟡 medium"

    def test_medium_badge_for_score_close_to_threshold(self):
        """Verify score slightly above 0.6 returns 🟡 medium badge."""
        assert _quality_badge(0.65) == "🟡 medium"

    def test_low_badge_for_score_below_threshold(self):
        """Verify score < 0.6 returns 🔴 low badge."""
        assert _quality_badge(0.59) == "🔴 low"

    def test_low_badge_for_zero_score(self):
        """Verify score 0.0 returns 🔴 low badge."""
        assert _quality_badge(0.0) == "🔴 low"

    def test_low_badge_for_one_score_below_threshold(self):
        """Verify score just below 0.6 returns 🔴 low badge."""
        assert _quality_badge(0.599) == "🔴 low"

    def test_high_badge_at_boundary_with_tolerance(self):
        """Verify score exactly at 0.8 boundary returns high badge."""
        assert _quality_badge(0.8000001) == "🟢 high"

    def test_medium_badge_at_boundary_with_tolerance(self):
        """Verify score exactly at 0.6 boundary returns medium badge."""
        assert _quality_badge(0.6000001) == "🟡 medium"


# ── _quality_badge ASCII fallback ───────────────────────────────────────


class TestQualityBadgeASCII:
    """Tests for _quality_badge ASCII fallback rendering."""

    def test_ascii_high_badge_for_score_at_threshold(self):
        """Verify score >= 0.8 returns [HIGH] badge with ascii style."""
        assert _quality_badge(0.8, badge_style="ascii") == "[HIGH]"

    def test_ascii_medium_and_low_badges(self):
        """Verify score >= 0.6 returns [MEDIUM], < 0.6 returns [LOW] with ascii."""
        assert _quality_badge(0.6, badge_style="ascii") == "[MEDIUM]"
        assert _quality_badge(0.59, badge_style="ascii") == "[LOW]"

    def test_score_boundary_cases_with_ascii(self):
        """Verify score boundary cases 0.8 and 0.6 exactly with ascii style."""
        assert _quality_badge(0.8, badge_style="ascii") == "[HIGH]"
        assert _quality_badge(0.6, badge_style="ascii") == "[MEDIUM]"

    def test_default_style_is_emoji(self):
        """Verify badge_style defaults to emoji when not specified."""
        assert _quality_badge(0.8) == "🟢 high"
        assert _quality_badge(0.6) == "🟡 medium"
        assert _quality_badge(0.0) == "🔴 low"


# ── OutputFormatter.format_markdown_search ──────────────────────────────


class TestOutputFormatterFormatMarkdownSearch:
    """Tests for OutputFormatter.format_markdown_search markdown structure."""

    def _make_response(
        self,
        results: list[SearchResult] | None = None,
        provider: str = "duck",
        total_found: int = 5,
        cache_hit: bool = False,
        judgment: SearchResultJudge | None = None,
        diversity_scores: dict[str, float] | None = None,
    ) -> SearchResponse:
        """Helper to construct a SearchResponse for testing."""
        if results is None:
            results = [
                SearchResult(
                    url="https://example.com/1",
                    title="Example 1",
                    description="Desc 1",
                    quality_score=QualityScore(
                        overall=0.9,
                        content_quality=0.85,
                        seo_spam_score=0.1,
                        clickbait_score=0.05,
                    ),
                ),
                SearchResult(
                    url="https://example.com/2",
                    title="Example 2",
                    description="Desc 2",
                    quality_score=QualityScore(
                        overall=0.5,
                        content_quality=0.4,
                        seo_spam_score=0.6,
                        clickbait_score=0.7,
                    ),
                ),
            ]
        return SearchResponse(
            results=results,
            provider=provider,
            cache_hit=cache_hit,
            total_found=total_found,
            diversity_scores=diversity_scores or {},
            judgment=judgment,
        )

    def _make_request(self, query: str = "test query") -> SearchRequest:
        """Helper to construct a SearchRequest for testing."""
        return SearchRequest(query=query)

    def test_output_contains_query_header(self):
        """Verify markdown output starts with query header."""
        request = self._make_request("search test")
        response = self._make_response()
        result = OutputFormatter.format_markdown_search(request, response)

        assert "# Search: search test" in result

    def test_output_contains_results_count(self):
        """Verify markdown output includes total_found count."""
        request = self._make_request()
        response = self._make_response(total_found=42)
        result = OutputFormatter.format_markdown_search(request, response)

        assert "## Results (42 found)" in result

    def test_output_contains_ranked_results(self):
        """Verify markdown output contains numbered results."""
        request = self._make_request()
        response = self._make_response()
        result = OutputFormatter.format_markdown_search(request, response)

        assert "1. **[Example 1]" in result
        assert "2. **[Example 2]" in result

    def test_output_contains_result_urls(self):
        """Verify markdown output includes result URLs."""
        request = self._make_request()
        response = self._make_response()
        result = OutputFormatter.format_markdown_search(request, response)

        assert "https://example.com/1" in result
        assert "https://example.com/2" in result

    def test_output_contains_quality_badge_high(self):
        """Verify high quality score produces 🟢 high badge."""
        request = self._make_request()
        response = self._make_response()
        result = OutputFormatter.format_markdown_search(request, response)

        assert "🟢 high" in result

    def test_output_contains_quality_badge_low(self):
        """Verify low quality score produces 🔴 low badge."""
        request = self._make_request()
        response = self._make_response()
        result = OutputFormatter.format_markdown_search(request, response)

        assert "🔴 low" in result

    def test_output_contains_provider_metadata(self):
        """Verify markdown output includes provider in metadata section."""
        request = self._make_request()
        response = self._make_response(provider="tavily")
        result = OutputFormatter.format_markdown_search(request, response)

        assert "- Provider: tavily" in result

    def test_output_contains_cache_hit_metadata(self):
        """Verify markdown output includes cache_hit in metadata."""
        request = self._make_request()
        response = self._make_response(cache_hit=True)
        result = OutputFormatter.format_markdown_search(request, response)

        assert "- Cache Hit: True" in result

    def test_output_contains_judgment_metadata(self):
        """Verify markdown output includes judgment verdict and score."""
        request = self._make_request()
        judgment = SearchResultJudge(
            diversity_score=0.7,
            trustworthiness_score=0.8,
            relevance_to_query=0.9,
            score=0.85,
            verdict="pass",
            reasons=["good diversity"],
        )
        response = self._make_response(judgment=judgment)
        result = OutputFormatter.format_markdown_search(request, response)

        assert "- Judgment: pass (0.85)" in result

    def test_output_contains_diversity_scores_metadata(self):
        """Verify markdown output includes diversity_scores in metadata."""
        request = self._make_request()
        response = self._make_response(diversity_scores={"duck": 0.75, "tavily": 0.6})
        result = OutputFormatter.format_markdown_search(request, response)

        assert "Diversity Scores" in result
        assert "0.75" in result
        assert "0.6" in result

    def test_output_truncated_when_exceeds_max_length(self):
        """Verify markdown output is truncated at MAX_MARKDOWN_LENGTH."""
        request = self._make_request(
            "very long query with lots of words to make the header longer"
        )
        # Create many results to exceed the limit
        results = [
            SearchResult(
                url=f"https://example.com/{i}",
                title=f"Title {i} with extra descriptive text to make it longer",
                description=f"Description {i} with lots of filler content to ensure we exceed the maximum length limit",
                quality_score=QualityScore(
                    overall=0.5,
                    content_quality=0.5,
                    seo_spam_score=0.5,
                    clickbait_score=0.5,
                ),
            )
            for i in range(50)
        ]
        response = self._make_response(results=results, total_found=50)
        result = OutputFormatter.format_markdown_search(request, response)

        # Source code adds "\n\n--- [truncated]" after slicing, so total length > MAX_MARKDOWN_LENGTH
        # Verify the content before the marker is within the limit
        truncated_marker = "\n\n--- [truncated]"
        assert truncated_marker in result
        content_before_marker = result.split(truncated_marker)[0]
        assert len(content_before_marker) <= OutputFormatter.MAX_MARKDOWN_LENGTH

    def test_output_contains_diversity_badge_when_judgment_present(self):
        """Verify diversity badge appears when judgment with diversity_score is present."""
        request = self._make_request()
        judgment = SearchResultJudge(
            diversity_score=0.9,
            trustworthiness_score=0.8,
            relevance_to_query=0.85,
            score=0.85,
            verdict="pass",
        )
        response = self._make_response(judgment=judgment)
        result = OutputFormatter.format_markdown_search(request, response)

        assert "🟢 high" in result  # diversity 0.9 → high

    def test_output_structure_has_metadata_section(self):
        """Verify markdown output contains a ## Metadata section."""
        request = self._make_request()
        response = self._make_response()
        result = OutputFormatter.format_markdown_search(request, response)

        assert "## Metadata" in result

    def test_output_with_no_judgment(self):
        """Verify markdown output works when judgment is None."""
        request = self._make_request()
        response = self._make_response(judgment=None)
        result = OutputFormatter.format_markdown_search(request, response)

        assert "# Search: test query" in result
        assert "## Results" in result


# ── OutputFormatter.format_json_search ──────────────────────────────────


class TestOutputFormatterFormatJsonSearch:
    """Tests for OutputFormatter.format_json_search unified JSON schema."""

    def _make_response(
        self,
        results: list[SearchResult] | None = None,
        provider: str = "duck",
        total_found: int = 3,
        cache_hit: bool = False,
        judgment: SearchResultJudge | None = None,
        diversity_scores: dict[str, float] | None = None,
    ) -> SearchResponse:
        """Helper to construct a SearchResponse for testing."""
        if results is None:
            results = [
                SearchResult(
                    url="https://example.com/1",
                    title="Example 1",
                    description="Desc 1",
                    quality_score=QualityScore(
                        overall=0.9,
                        content_quality=0.85,
                        seo_spam_score=0.1,
                        clickbait_score=0.05,
                    ),
                ),
            ]
        return SearchResponse(
            results=results,
            provider=provider,
            cache_hit=cache_hit,
            total_found=total_found,
            diversity_scores=diversity_scores or {},
            judgment=judgment,
        )

    def test_output_contains_query_field(self):
        """Verify JSON output includes query field."""
        response = self._make_response()
        result = OutputFormatter.format_json_search(response, query="test query")

        assert result.model_dump()["query"] == "test query"

    def test_output_contains_format_field(self):
        """Verify JSON output includes format field set to json."""
        response = self._make_response()
        result = OutputFormatter.format_json_search(response)

        assert result.model_dump()["format"] == "json"

    def test_output_contains_results_field(self):
        """Verify JSON output includes results field with dumped data."""
        response = self._make_response()
        result = OutputFormatter.format_json_search(response)

        assert isinstance(result.model_dump()["results"], list)
        assert len(result.model_dump()["results"]) > 0
        assert "url" in result.model_dump()["results"][0]
        assert "title" in result.model_dump()["results"][0]

    def test_output_contains_metadata_field(self):
        """Verify JSON output includes metadata field."""
        response = self._make_response()
        result = OutputFormatter.format_json_search(response)

        assert isinstance(result.model_dump()["metadata"], dict)

    def test_metadata_contains_provider(self):
        """Verify metadata includes provider."""
        response = self._make_response(provider="tavily")
        result = OutputFormatter.format_json_search(response)

        assert result.model_dump()["metadata"]["provider"] == "tavily"

    def test_metadata_contains_cache_hit(self):
        """Verify metadata includes cache_hit."""
        response = self._make_response(cache_hit=True)
        result = OutputFormatter.format_json_search(response)

        assert result.model_dump()["metadata"]["cache_hit"] is True

    def test_metadata_contains_total_found(self):
        """Verify metadata includes total_found."""
        response = self._make_response(total_found=42)
        result = OutputFormatter.format_json_search(response)

        assert result.model_dump()["metadata"]["total_found"] == 42

    def test_metadata_contains_diversity_scores(self):
        """Verify metadata includes diversity_scores."""
        response = self._make_response(diversity_scores={"duck": 0.75})
        result = OutputFormatter.format_json_search(response)

        assert result.model_dump()["metadata"]["diversity_scores"] == {"duck": 0.75}

    def test_metadata_contains_judgment_when_present(self):
        """Verify metadata includes judgment when judgment is set."""
        judgment = SearchResultJudge(
            diversity_score=0.7,
            trustworthiness_score=0.8,
            relevance_to_query=0.9,
            score=0.85,
            verdict="pass",
        )
        response = self._make_response(judgment=judgment)
        result = OutputFormatter.format_json_search(response)

        assert "judgment" in result.model_dump()["metadata"]
        assert result.model_dump()["metadata"]["judgment"]["verdict"] == "pass"

    def test_metadata_contains_parameters_when_present(self):
        """Verify metadata includes parameters when parameters is set."""
        from app.models.search import SearchParameters

        params = SearchParameters(engines="duck,tavily", time_range="week")
        response = self._make_response()
        response.parameters = params
        result = OutputFormatter.format_json_search(response)

        assert "parameters" in result.model_dump()["metadata"]
        assert result.model_dump()["metadata"]["parameters"]["engines"] == "duck,tavily"

    def test_empty_query_field(self):
        """Verify query field can be empty string."""
        response = self._make_response()
        result = OutputFormatter.format_json_search(response, query="")

        assert result.model_dump()["query"] == ""


# ── AC9: format_json_search with empty results ─────────────────────────────


class TestOutputFormatterFormatJsonSearchEmptyResults:
    """AC9: Tests for format_json_search with empty results list."""

    def test_empty_results_produces_valid_json(self):
        """Verify format_json_search with empty results produces valid UnifiedJSONResponse."""
        response = SearchResponse(
            results=[],
            provider="duck",
            cache_hit=False,
            total_found=0,
            diversity_scores={},
            judgment=None,
        )
        result = OutputFormatter.format_json_search(response, query="test query")

        dumped = result.model_dump()
        assert dumped["query"] == "test query"
        assert dumped["format"] == "json"
        assert isinstance(dumped["results"], list)
        assert len(dumped["results"]) == 0
        assert isinstance(dumped["metadata"], dict)

    def test_empty_results_metadata_contains_zero_counts(self):
        """Verify metadata has correct zero-value fields for empty results."""
        response = SearchResponse(
            results=[],
            provider="tavily",
            cache_hit=True,
            total_found=0,
            diversity_scores={},
            judgment=None,
        )
        result = OutputFormatter.format_json_search(response)

        dumped = result.model_dump()
        assert dumped["metadata"]["provider"] == "tavily"
        assert dumped["metadata"]["cache_hit"] is True
        assert dumped["metadata"]["total_found"] == 0
        assert dumped["metadata"]["freshness_score"] == 0.0

    def test_empty_results_json_serializable(self):
        """Verify empty results output is valid JSON via model_dump_json."""
        response = SearchResponse(
            results=[],
            provider="duck",
            cache_hit=False,
            total_found=0,
            diversity_scores={},
            judgment=None,
        )
        result = OutputFormatter.format_json_search(response, query="")

        json_str = result.model_dump_json()
        assert json_str is not None
        assert len(json_str) > 0

    def test_empty_results_no_warning_when_within_size_limit(self):
        """Verify empty results does NOT add truncation warning when within size limit."""
        response = SearchResponse(
            results=[],
            provider="duck",
            cache_hit=False,
            total_found=0,
            diversity_scores={},
            judgment=None,
        )
        result = OutputFormatter.format_json_search(response, query="test")

        assert "warning" not in result.model_dump()["metadata"]


# ── OutputFormatter.format_markdown_webfetch ────────────────────────────


class TestOutputFormatterFormatMarkdownWebfetch:
    """Tests for OutputFormatter.format_markdown_webfetch markdown structure."""

    def _make_state(
        self,
        prompt: str = "test prompt",
        final_result: str = "Final answer text",
        search_queries: list[str] | None = None,
        selected_urls: list | None = None,
        fetched_content: list | None = None,
        features: FeatureSet | None = None,
        url_judgment: JudgeVerdict | None = None,
        feature_judgment: JudgeVerdict | None = None,
    ) -> WebFetchState:
        """Helper to construct a WebFetchState for testing."""
        # Convert selected_urls dicts to URLSelectionItem instances if needed
        processed_selected_urls: list = []
        if selected_urls:
            for item in selected_urls:
                if isinstance(item, dict):
                    from app.models.webfetch import URLSelectionItem

                    processed_selected_urls.append(URLSelectionItem(**item))
                else:
                    processed_selected_urls.append(item)

        return WebFetchState(
            prompt=prompt,
            tenant_id="test-tenant",
            final_result=final_result,
            search_queries=search_queries or ["q1", "q2"],
            selected_urls=processed_selected_urls,
            fetched_content=fetched_content or [],
            features=features,
            url_judgment=url_judgment,
            feature_judgment=feature_judgment,
        )

    def test_output_contains_prompt_header(self):
        """Verify markdown output starts with prompt header."""
        state = self._make_state(prompt="my search")
        result = OutputFormatter.format_markdown_webfetch("my search", state, [])

        assert "# WebFetch: my search" in result

    def test_output_contains_final_answer_section(self):
        """Verify markdown output includes ## Final Answer section."""
        state = self._make_state(final_result="The answer is 42")
        result = OutputFormatter.format_markdown_webfetch("prompt", state, [])

        assert "## Final Answer" in result
        assert "The answer is 42" in result

    def test_output_contains_sources_section(self):
        """Verify markdown output includes ## Sources section."""
        state = self._make_state()
        sources = [
            {"url": "https://example.com/1", "features": ["feat1", "feat2"]},
        ]
        result = OutputFormatter.format_markdown_webfetch("prompt", state, sources)

        assert "## Sources" in result

    def test_output_contains_source_urls(self):
        """Verify markdown output includes source URLs."""
        state = self._make_state()
        sources = [
            {"url": "https://example.com/1", "features": ["feat1"]},
        ]
        result = OutputFormatter.format_markdown_webfetch("prompt", state, sources)

        assert "https://example.com/1" in result

    def test_output_contains_source_features(self):
        """Verify markdown output includes source features."""
        state = self._make_state()
        sources = [
            {"url": "https://example.com/1", "features": ["feat1", "feat2"]},
        ]
        result = OutputFormatter.format_markdown_webfetch("prompt", state, sources)

        assert "feat1" in result
        assert "feat2" in result

    def test_output_contains_state_summary_section(self):
        """Verify markdown output includes ## State Summary section."""
        state = self._make_state()
        result = OutputFormatter.format_markdown_webfetch("prompt", state, [])

        assert "## State Summary" in result

    def test_state_summary_contains_queries_count(self):
        """Verify state summary includes queries generated count."""
        state = self._make_state(search_queries=["q1", "q2", "q3"])
        result = OutputFormatter.format_markdown_webfetch("prompt", state, [])

        assert "- Queries generated: 3" in result

    def test_state_summary_contains_urls_judged_count(self):
        """Verify state summary includes URLs judged count."""
        state = self._make_state(
            selected_urls=[
                {"url": "https://example.com/1", "priority": 1, "reason": "test"}
            ]
        )
        result = OutputFormatter.format_markdown_webfetch("prompt", state, [])

        assert "- URLs judged: 1" in result

    def test_state_summary_contains_content_fetched_count(self):
        """Verify state summary includes content fetched count."""
        state = self._make_state(
            fetched_content=[{"url": "u1"}, {"url": "u2"}, {"url": "u3"}]
        )
        result = OutputFormatter.format_markdown_webfetch("prompt", state, [])

        assert "- Content fetched: 3" in result

    def test_state_summary_contains_features_extracted_count(self):
        """Verify state summary includes features extracted count."""
        features = FeatureSet(features=["f1", "f2", "f3", "f4"])
        state = self._make_state(features=features)
        result = OutputFormatter.format_markdown_webfetch("prompt", state, [])

        assert "- Features extracted: 4" in result

    def test_state_summary_features_zero_when_no_features(self):
        """Verify features extracted count is 0 when features is None."""
        state = self._make_state(features=None)
        result = OutputFormatter.format_markdown_webfetch("prompt", state, [])

        assert "- Features extracted: 0" in result

    def test_output_truncated_when_exceeds_max_length(self):
        """Verify markdown webfetch output is truncated at MAX_MARKDOWN_LENGTH."""
        state = self._make_state(final_result="x" * 12000)
        result = OutputFormatter.format_markdown_webfetch("prompt", state, [])

        # Source code adds "\n\n--- [truncated]" after slicing
        truncated_marker = "\n\n--- [truncated]"
        assert truncated_marker in result
        content_before_marker = result.split(truncated_marker)[0]
        assert len(content_before_marker) <= OutputFormatter.MAX_MARKDOWN_LENGTH

    def test_output_with_empty_sources(self):
        """Verify markdown output works with empty sources list."""
        state = self._make_state()
        result = OutputFormatter.format_markdown_webfetch(state.prompt, state, [])

        assert f"# WebFetch: {state.prompt}" in result
        assert "## Sources" in result


# ── OutputFormatter.format_json_webfetch ────────────────────────────────


class TestOutputFormatterFormatJsonWebfetch:
    """Tests for OutputFormatter.format_json_webfetch unified JSON schema."""

    def _make_state(
        self,
        prompt: str = "test prompt",
        final_result: str = "Final answer",
        search_queries: list[str] | None = None,
        selected_urls: list | None = None,
        fetched_content: list | None = None,
        features: FeatureSet | None = None,
        url_judgment: JudgeVerdict | None = None,
        feature_judgment: JudgeVerdict | None = None,
    ) -> WebFetchState:
        """Helper to construct a WebFetchState for testing."""
        # Convert selected_urls dicts to URLSelectionItem instances if needed
        processed_selected_urls: list = []
        if selected_urls:
            for item in selected_urls:
                if isinstance(item, dict):
                    from app.models.webfetch import URLSelectionItem

                    processed_selected_urls.append(URLSelectionItem(**item))
                else:
                    processed_selected_urls.append(item)

        return WebFetchState(
            prompt=prompt,
            tenant_id="test-tenant",
            final_result=final_result,
            search_queries=search_queries or ["q1"],
            selected_urls=processed_selected_urls,
            fetched_content=fetched_content or [],
            features=features,
            url_judgment=url_judgment,
            feature_judgment=feature_judgment,
        )

    def test_output_contains_prompt_field(self):
        """Verify JSON output includes query field (prompt mapped to query)."""
        state = self._make_state(prompt="my prompt")
        result = OutputFormatter.format_json_webfetch("my prompt", state, [])

        assert result.model_dump()["query"] == "my prompt"

    def test_output_contains_format_field(self):
        """Verify JSON output includes format field set to json."""
        state = self._make_state()
        result = OutputFormatter.format_json_webfetch("prompt", state, [])

        assert result.model_dump()["format"] == "json"

    def test_output_contains_result_field(self):
        """Verify JSON output includes final_result in metadata."""
        state = self._make_state(final_result="The answer")
        result = OutputFormatter.format_json_webfetch("prompt", state, [])

        assert result.model_dump()["metadata"]["final_result"] == "The answer"

    def test_output_contains_result_empty_when_no_final_result(self):
        """Verify result field is absent when final_result is None."""
        state = self._make_state(final_result=None)
        result = OutputFormatter.format_json_webfetch("prompt", state, [])

        assert "final_result" not in result.model_dump()["metadata"]

    def test_output_contains_sources_field(self):
        """Verify JSON output includes results field (sources mapped to results)."""
        state = self._make_state()
        sources = [{"url": "https://example.com/1", "features": ["f1"]}]
        result = OutputFormatter.format_json_webfetch("prompt", state, sources)

        assert isinstance(result.model_dump()["results"], list)
        assert len(result.model_dump()["results"]) > 0

    def test_output_contains_sources_from_objects(self):
        """Verify JSON output dumps SourceFeature objects correctly."""
        from app.models.webfetch import SourceFeature

        state = self._make_state()
        sources = [SourceFeature(url="https://example.com/1", features=["f1"])]
        result = OutputFormatter.format_json_webfetch("prompt", state, sources)

        assert result.model_dump()["results"][0]["url"] == "https://example.com/1"
        assert result.model_dump()["results"][0]["features"] == ["f1"]

    def test_output_contains_sources_from_dict_sources(self):
        """Verify JSON output correctly handles raw dict sources (AC35)."""
        state = self._make_state()
        sources = [
            {"url": "https://example.com/1", "features": ["f1", "f2"]},
            {
                "url": "https://example.com/2",
                "text": "sample content",
                "features": ["f3"],
            },
        ]
        result = OutputFormatter.format_json_webfetch("prompt", state, sources)

        dumped = result.model_dump()
        assert isinstance(dumped["results"], list)
        assert len(dumped["results"]) == 2
        assert dumped["results"][0]["url"] == "https://example.com/1"
        assert dumped["results"][0]["features"] == ["f1", "f2"]
        assert dumped["results"][1]["url"] == "https://example.com/2"
        assert dumped["results"][1]["text"] == "sample content"
        assert dumped["results"][1]["features"] == ["f3"]

    def test_metadata_contains_queries_generated(self):
        """Verify metadata includes queries_generated count."""
        state = self._make_state(search_queries=["q1", "q2"])
        result = OutputFormatter.format_json_webfetch("prompt", state, [])

        assert result.model_dump()["metadata"]["queries_generated"] == 2

    def test_metadata_contains_urls_judged(self):
        """Verify metadata includes urls_judged count."""
        state = self._make_state(
            selected_urls=[
                {"url": "https://example.com/1", "priority": 1, "reason": "test"}
            ]
        )
        result = OutputFormatter.format_json_webfetch("prompt", state, [])

        assert result.model_dump()["metadata"]["urls_judged"] == 1

    def test_metadata_contains_content_fetched(self):
        """Verify metadata includes content_fetched count."""
        state = self._make_state(
            fetched_content=[{"url": "u1"}, {"url": "u2"}, {"url": "u3"}]
        )
        result = OutputFormatter.format_json_webfetch("prompt", state, [])

        assert result.model_dump()["metadata"]["content_fetched"] == 3

    def test_metadata_contains_features_extracted(self):
        """Verify metadata includes features_extracted count."""
        features = FeatureSet(features=["f1", "f2"])
        state = self._make_state(features=features)
        result = OutputFormatter.format_json_webfetch("prompt", state, [])

        assert result.model_dump()["metadata"]["features_extracted"] == 2

    def test_metadata_features_extracted_zero_when_no_features(self):
        """Verify features_extracted is 0 when features is None."""
        state = self._make_state(features=None)
        result = OutputFormatter.format_json_webfetch("prompt", state, [])

        assert result.model_dump()["metadata"]["features_extracted"] == 0

    def test_metadata_contains_url_judgment_when_present(self):
        """Verify metadata includes url_judgment when set."""
        url_judgment = JudgeVerdict(
            score=0.85,
            diversity_score=0.7,
            trustworthiness_score=0.8,
            verdict="pass",
        )
        state = self._make_state(url_judgment=url_judgment)
        result = OutputFormatter.format_json_webfetch("prompt", state, [])

        assert "url_judgment" in result.model_dump()["metadata"]
        assert result.model_dump()["metadata"]["url_judgment"]["verdict"] == "pass"

    def test_metadata_contains_feature_judgment_when_present(self):
        """Verify metadata includes feature_judgment when set."""
        feature_judgment = JudgeVerdict(
            score=0.9,
            diversity_score=0.8,
            trustworthiness_score=0.85,
            verdict="pass",
        )
        state = self._make_state(feature_judgment=feature_judgment)
        result = OutputFormatter.format_json_webfetch("prompt", state, [])

        assert "feature_judgment" in result.model_dump()["metadata"]
        assert result.model_dump()["metadata"]["feature_judgment"]["score"] == 0.9


# ── AC16: format_json_webfetch with empty state ─────────────────────────────


class TestOutputFormatterFormatJsonWebfetchEmptyState:
    """AC16: Tests for format_json_webfetch with empty/zero state."""

    def test_empty_state_produces_valid_json(self):
        """Verify format_json_webfetch with empty state produces valid UnifiedJSONResponse."""
        state = WebFetchState(
            prompt="test prompt",
            tenant_id="test-tenant",
            final_result=None,
            search_queries=[],
            selected_urls=[],
            fetched_content=[],
            features=None,
            url_judgment=None,
            feature_judgment=None,
        )
        result = OutputFormatter.format_json_webfetch("test prompt", state, [])

        dumped = result.model_dump()
        assert dumped["query"] == "test prompt"
        assert dumped["format"] == "json"
        assert isinstance(dumped["results"], list)
        assert len(dumped["results"]) == 0
        assert isinstance(dumped["metadata"], dict)

    def test_empty_state_metadata_contains_zero_counts(self):
        """Verify metadata has correct zero-value fields for empty state."""
        state = WebFetchState(
            prompt="test prompt",
            tenant_id="test-tenant",
            final_result=None,
            search_queries=[],
            selected_urls=[],
            fetched_content=[],
            features=None,
            url_judgment=None,
            feature_judgment=None,
        )
        result = OutputFormatter.format_json_webfetch("test prompt", state, [])

        dumped = result.model_dump()
        assert dumped["metadata"]["queries_generated"] == 0
        assert dumped["metadata"]["urls_judged"] == 0
        assert dumped["metadata"]["content_fetched"] == 0
        assert dumped["metadata"]["features_extracted"] == 0
        assert dumped["metadata"]["freshness_score"] == 0.75  # WebFetchState default

    def test_empty_state_no_final_result_in_metadata(self):
        """Verify final_result absent from metadata when state has no final_result."""
        state = WebFetchState(
            prompt="test prompt",
            tenant_id="test-tenant",
            final_result=None,
            search_queries=[],
            selected_urls=[],
            fetched_content=[],
            features=None,
            url_judgment=None,
            feature_judgment=None,
        )
        result = OutputFormatter.format_json_webfetch("test prompt", state, [])

        assert "final_result" not in result.model_dump()["metadata"]

    def test_empty_state_json_serializable(self):
        """Verify empty state output is valid JSON via model_dump_json."""
        state = WebFetchState(
            prompt="test prompt",
            tenant_id="test-tenant",
            final_result=None,
            search_queries=[],
            selected_urls=[],
            fetched_content=[],
            features=None,
            url_judgment=None,
            feature_judgment=None,
        )
        result = OutputFormatter.format_json_webfetch("test prompt", state, [])

        json_str = result.model_dump_json()
        assert json_str is not None
        assert len(json_str) > 0

    def test_empty_state_no_warning_when_within_size_limit(self):
        """Verify empty state does NOT add truncation warning when within size limit."""
        state = WebFetchState(
            prompt="test prompt",
            tenant_id="test-tenant",
            final_result=None,
            search_queries=[],
            selected_urls=[],
            fetched_content=[],
            features=None,
            url_judgment=None,
            feature_judgment=None,
        )
        result = OutputFormatter.format_json_webfetch("test prompt", state, [])

        assert "warning" not in result.model_dump()["metadata"]


# ── SearchRequest.output_format ─────────────────────────────────────────


class TestSearchRequestOutputFormat:
    """Tests for SearchRequest.output_format validation."""

    def test_default_output_format_is_markdown(self):
        """Verify default output_format is 'markdown'."""
        request = SearchRequest(query="test")
        assert request.output_format == "markdown"

    def test_output_format_accepts_markdown(self):
        """Verify output_format accepts 'markdown' value."""
        request = SearchRequest(query="test", output_format="markdown")
        assert request.output_format == "markdown"

    def test_output_format_accepts_json(self):
        """Verify output_format accepts 'json' value."""
        request = SearchRequest(query="test", output_format="json")
        assert request.output_format == "json"

    def test_output_format_rejects_invalid_value(self):
        """Verify output_format rejects non-Literal values."""
        with pytest.raises(Exception):  # pydantic validation error
            SearchRequest(query="test", output_format="xml")

    def test_output_format_is_literal_type(self):
        """Verify output_format is constrained to Literal['markdown', 'json']."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SearchRequest(query="test", output_format="html")


# ── WebFetchState.output_format ─────────────────────────────────────────


class TestWebFetchStateOutputFormat:
    """Tests for WebFetchState.output_format validation."""

    def test_default_output_format_is_markdown(self):
        """Verify default output_format is 'markdown'."""
        state = WebFetchState(prompt="test", tenant_id="test-tenant")
        assert state.output_format == "markdown"

    def test_output_format_accepts_markdown(self):
        """Verify output_format accepts 'markdown' value."""
        state = WebFetchState(
            prompt="test", tenant_id="test-tenant", output_format="markdown"
        )
        assert state.output_format == "markdown"

    def test_output_format_accepts_json(self):
        """Verify output_format accepts 'json' value."""
        state = WebFetchState(
            prompt="test", tenant_id="test-tenant", output_format="json"
        )
        assert state.output_format == "json"

    def test_output_format_rejects_invalid_value(self):
        """Verify output_format rejects non-Literal values."""
        with pytest.raises(Exception):  # pydantic validation error
            WebFetchState(prompt="test", tenant_id="test-tenant", output_format="xml")

    def test_output_format_is_literal_type(self):
        """Verify output_format is constrained to Literal['markdown', 'json']."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WebFetchState(prompt="test", tenant_id="test-tenant", output_format="html")


# ── JudgeVerdict.freshness_score ────────────────────────────────────────


class TestJudgeVerdictFreshnessScore:
    """Tests for JudgeVerdict.freshness_score default and validation."""

    def test_default_freshness_score_is_0_75(self):
        """Verify default freshness_score is 0.75."""
        verdict = JudgeVerdict()
        assert verdict.freshness_score == 0.75

    def test_freshness_score_accepts_ge_0_0(self):
        """Verify freshness_score accepts values >= 0.0."""
        verdict = JudgeVerdict(freshness_score=0.0)
        assert verdict.freshness_score == 0.0

    def test_freshness_score_accepts_le_1_0(self):
        """Verify freshness_score accepts values <= 1.0."""
        verdict = JudgeVerdict(freshness_score=1.0)
        assert verdict.freshness_score == 1.0

    def test_freshness_score_accepts_custom_value(self):
        """Verify freshness_score accepts custom value within range."""
        verdict = JudgeVerdict(freshness_score=0.9)
        assert verdict.freshness_score == 0.9

    def test_freshness_score_rejects_below_0_0(self):
        """Verify freshness_score rejects values < 0.0."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            JudgeVerdict(freshness_score=-0.1)

    def test_freshness_score_rejects_above_1_0(self):
        """Verify freshness_score rejects values > 1.0."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            JudgeVerdict(freshness_score=1.1)


# ── TestQualityBadgeEdgeCases ───────────────────────────────────────────


class TestQualityBadgeEdgeCases:
    """Tests for _quality_badge score boundary edge cases."""

    def test_score_zero_returns_low_badge(self):
        """Verify score = 0.0 returns 🔴 low badge."""
        assert _quality_badge(0.0) == "🔴 low"

    def test_score_one_returns_high_badge(self):
        """Verify score = 1.0 returns 🟢 high badge."""
        assert _quality_badge(1.0) == "🟢 high"

    def test_score_just_below_0_8_returns_medium_badge(self):
        """Verify score = 0.799999 returns 🟡 medium (just below 0.8 threshold)."""
        assert _quality_badge(0.799999) == "🟡 medium"

    def test_score_just_above_0_6_returns_medium_badge(self):
        """Verify score = 0.600001 returns 🟡 medium (just above 0.6 threshold)."""
        assert _quality_badge(0.600001) == "🟡 medium"

    def test_ascii_badges_for_all_thresholds(self):
        """Verify ASCII badges work for all score thresholds."""
        assert _quality_badge(0.0, badge_style="ascii") == "[LOW]"
        assert _quality_badge(0.59, badge_style="ascii") == "[LOW]"
        assert _quality_badge(0.6, badge_style="ascii") == "[MEDIUM]"
        assert _quality_badge(0.79, badge_style="ascii") == "[MEDIUM]"
        assert _quality_badge(0.8, badge_style="ascii") == "[HIGH]"
        assert _quality_badge(1.0, badge_style="ascii") == "[HIGH]"


# ── TestOutputFormatterEdgeCases ────────────────────────────────────────


class TestOutputFormatterEdgeCases:
    """Tests for OutputFormatter edge cases: empty inputs, None values, truncation."""

    def test_format_markdown_search_empty_results_valid_structure(self):
        """Verify format_markdown_search with empty results produces valid markdown structure."""
        request = SearchRequest(query="empty search")
        response = SearchResponse(
            results=[],
            provider="duck",
            total_found=0,
            cache_hit=False,
            judgment=None,
        )
        result = OutputFormatter.format_markdown_search(request, response)

        assert "# Search: empty search" in result
        assert "## Results (0 found)" in result
        assert "## Metadata" in result
        assert "- Provider: duck" in result
        assert "- Cache Hit: False" in result

    def test_format_markdown_search_long_description_truncation_applied(self):
        """Verify format_markdown_search with very long description triggers truncation."""
        request = SearchRequest(query="test query")
        results = [
            SearchResult(
                url=f"https://example.com/{i}",
                title=f"Title {i}",
                description=f"{'x' * 500} description {i} with extremely long content to force truncation",
                quality_score=QualityScore(
                    overall=0.5,
                    content_quality=0.5,
                    seo_spam_score=0.5,
                    clickbait_score=0.5,
                ),
            )
            for i in range(30)
        ]
        response = SearchResponse(
            results=results,
            provider="duck",
            total_found=30,
            cache_hit=False,
            judgment=None,
        )
        result = OutputFormatter.format_markdown_search(request, response)

        truncated_marker = "\n\n--- [truncated]"
        assert truncated_marker in result
        content_before_marker = result.split(truncated_marker)[0]
        assert len(content_before_marker) <= OutputFormatter.MAX_MARKDOWN_LENGTH

    def test_format_json_search_none_judgment_metadata_valid(self):
        """Verify format_json_search with None judgment produces valid metadata."""
        response = SearchResponse(
            results=[
                SearchResult(
                    url="https://example.com/1",
                    title="Example",
                    description="Desc",
                    quality_score=QualityScore(
                        overall=0.5,
                        content_quality=0.5,
                        seo_spam_score=0.5,
                        clickbait_score=0.5,
                    ),
                ),
            ],
            provider="duck",
            total_found=1,
            cache_hit=False,
            judgment=None,
        )
        result = OutputFormatter.format_json_search(response, query="test")

        assert result.model_dump()["query"] == "test"
        assert result.model_dump()["format"] == "json"
        assert isinstance(result.model_dump()["results"], list)
        assert isinstance(result.model_dump()["metadata"], dict)
        assert "judgment" not in result.model_dump()["metadata"]
        assert result.model_dump()["metadata"]["provider"] == "duck"
        assert result.model_dump()["metadata"]["total_found"] == 1

    def test_format_markdown_webfetch_empty_state_valid_structure(self):
        """Verify format_markdown_webfetch with empty state produces valid markdown structure."""
        state = WebFetchState(
            prompt="test prompt",
            tenant_id="test-tenant",
            final_result="",
            search_queries=[],
            selected_urls=[],
            fetched_content=[],
            features=None,
        )
        result = OutputFormatter.format_markdown_webfetch(state.prompt, state, [])

        assert f"# WebFetch: {state.prompt}" in result
        assert "## Final Answer" in result
        assert "## Sources" in result
        assert "## State Summary" in result
        assert "- Queries generated: 0" in result
        assert "- URLs judged: 0" in result
        assert "- Content fetched: 0" in result

    def test_format_json_webfetch_none_features_features_extracted_zero(self):
        """Verify format_json_webfetch with None features produces features_extracted = 0."""
        state = WebFetchState(
            prompt="test prompt",
            tenant_id="test-tenant",
            final_result="answer",
            search_queries=["q1"],
            selected_urls=[],
            fetched_content=[],
            features=None,
        )
        result = OutputFormatter.format_json_webfetch(state.prompt, state, [])

        assert result.model_dump()["query"] == state.prompt
        assert result.model_dump()["format"] == "json"
        assert result.model_dump()["metadata"]["final_result"] == "answer"
        assert isinstance(result.model_dump()["results"], list)
        assert isinstance(result.model_dump()["metadata"], dict)
        assert result.model_dump()["metadata"]["features_extracted"] == 0


# ── TestUnifiedJSONResponseSizeBounding ─────────────────────────────────


class TestUnifiedJSONResponseSizeBounding:
    """Tests for UnifiedJSONResponse.validate_json_size() overflow handling."""

    def _make_large_model(
        self,
        result_count: int = 100,
    ) -> UnifiedJSONResponse:
        """Helper to construct a UnifiedJSONResponse that exceeds MAX_JSON_SIZE.

        Guarantees initial JSON > 524288 bytes by using large fillers.
        """
        # Pydantic JSON is compact — need ~9KB per result to exceed 500KB reliably
        filler_per_result = 9000

        large_results = [
            {
                "url": f"https://example.com/result-{i}",
                "title": f"Title {i} with {'x' * (filler_per_result // 3)} filler",
                "description": f"Description {i} with {'x' * (filler_per_result // 2)} filler content",
                "provider": "duck",
                "quality_score": {
                    "overall": 0.5,
                    "content_quality": 0.5,
                    "seo_spam_score": 0.5,
                    "clickbait_score": 0.5,
                },
            }
            for i in range(result_count)
        ]

        metadata: dict = {
            "provider": "duck",
            "cache_hit": False,
            "total_found": result_count,
            "diversity_scores": {"duck": 0.75, "tavily": 0.6},
            "judgment": {
                "diversity_score": 0.7,
                "trustworthiness_score": 0.8,
                "relevance_to_query": 0.9,
                "freshness_score": 0.75,
                "score": 0.85,
                "verdict": "pass",
                "reasons": ["good diversity", "high trustworthiness"],
            },
            "parameters": {
                "engines": "duck,tavily",
                "time_range": "week",
                "site": None,
                "ttl_override": None,
            },
        }

        return UnifiedJSONResponse(
            query="very long test query with lots of words to increase baseline size",
            format="json",
            results=large_results,
            metadata=metadata,
        )

    def test_overflow_truncation_reduces_results(self):
        """Verify validate_json_size() truncates results when JSON exceeds MAX_JSON_SIZE."""
        model = self._make_large_model(result_count=100)

        # Verify initial size exceeds limit
        initial_json = model.model_dump_json().encode("utf-8")
        assert len(initial_json) > model.MAX_JSON_SIZE

        result = model.validate_json_size()

        # Results should be reduced
        assert len(result.results) < len(model.results)
        # Warning should be present
        assert "warning" in result.metadata
        assert "truncated" in result.metadata["warning"]
        # Final size should be within limit
        final_json = result.model_dump_json().encode("utf-8")
        assert len(final_json) <= model.MAX_JSON_SIZE

    def test_binary_search_convergence_find_max_fit(self):
        """Verify binary search finds the maximum number of results that fit."""
        model = self._make_large_model(result_count=80)

        initial_json = model.model_dump_json().encode("utf-8")
        assert len(initial_json) > model.MAX_JSON_SIZE

        result = model.validate_json_size()

        # Verify truncation warning contains correct counts
        warning = result.metadata["warning"]
        assert "80 results" in warning
        assert "reduced to" in warning

        # Verify the truncated JSON fits
        final_json = result.model_dump_json().encode("utf-8")
        assert len(final_json) <= model.MAX_JSON_SIZE

        # Verify results are a prefix of original (not shuffled/reordered)
        for i, truncated_result in enumerate(result.results):
            assert truncated_result == model.results[i]

    def test_empty_results_after_truncation(self):
        """Verify graceful fallback when even empty results exceed size limit."""
        # Build a model with massive metadata but no results — metadata alone > 500KB
        huge_metadata: dict = {
            "provider": "duck",
            "cache_hit": False,
            "total_found": 0,
            "diversity_scores": {f"provider-{i}": 0.5 for i in range(500)},
            "judgment": {
                "diversity_score": 0.7,
                "trustworthiness_score": 0.8,
                "relevance_to_query": 0.9,
                "freshness_score": 0.75,
                "score": 0.85,
                "verdict": "pass",
                "reasons": ["x" * 3000 for _ in range(50)],
            },
            "parameters": {
                "engines": "x" * 500,
                "time_range": "week",
                "site": "x" * 500,
                "ttl_override": None,
            },
            "extra_large_field": "x" * 500000,
        }

        model = UnifiedJSONResponse(
            query="test",
            format="json",
            results=[],  # already empty
            metadata=huge_metadata,
        )

        initial_json = model.model_dump_json().encode("utf-8")
        assert len(initial_json) > model.MAX_JSON_SIZE

        # Should NOT raise ValueError — graceful fallback
        result = model.validate_json_size()

        assert result.results == []
        assert "warning" in result.metadata
        assert "metadata alone exceeds" in result.metadata["warning"]

    def test_metadata_only_overflow_no_exception(self):
        """Verify metadata-only overflow returns empty results without raising."""
        # Model with results that fit, but adding warning pushes over
        # This tests the edge case where final_json > MAX_JSON_SIZE after truncation
        huge_metadata: dict = {
            "provider": "duck",
            "cache_hit": False,
            "total_found": 0,
            "diversity_scores": {f"p-{i}": 0.5 for i in range(300)},
            "judgment": {
                "diversity_score": 0.7,
                "trustworthiness_score": 0.8,
                "relevance_to_query": 0.9,
                "freshness_score": 0.75,
                "score": 0.85,
                "verdict": "pass",
                "reasons": ["x" * 1500 for _ in range(30)],
            },
            "overflow_field": "x" * 500000,
        }

        model = UnifiedJSONResponse(
            query="test",
            format="json",
            results=[{"url": "u1", "title": "t1"}],  # small result
            metadata=huge_metadata,
        )

        initial_json = model.model_dump_json().encode("utf-8")
        assert len(initial_json) > model.MAX_JSON_SIZE

        result = model.validate_json_size()

        # Should have empty results (graceful fallback triggered)
        assert result.results == []
        assert "warning" in result.metadata

    def test_no_truncation_when_within_limit(self):
        """Verify validate_json_size() returns self when JSON is within limit."""
        model = UnifiedJSONResponse(
            query="test",
            format="json",
            results=[{"url": "https://example.com/1", "title": "Example"}],
            metadata={"provider": "duck", "cache_hit": True},
        )

        initial_json = model.model_dump_json().encode("utf-8")
        assert len(initial_json) <= model.MAX_JSON_SIZE

        result = model.validate_json_size()

        # Should return the same model (no truncation)
        assert result.results == model.results
        assert result.metadata == model.metadata
        assert "warning" not in result.metadata

    def test_truncation_preserves_metadata_fields(self):
        """Verify truncate preserves all original metadata fields."""
        model = self._make_large_model(result_count=80)

        initial_json = model.model_dump_json().encode("utf-8")
        assert len(initial_json) > model.MAX_JSON_SIZE

        result = model.validate_json_size()

        # All original metadata keys should be preserved
        for key in model.metadata:
            assert key in result.metadata

        # Values for non-warning keys should match
        assert result.metadata["provider"] == model.metadata["provider"]
        assert result.metadata["cache_hit"] == model.metadata["cache_hit"]
        assert result.metadata["total_found"] == model.metadata["total_found"]

    def test_json_size_exactly_max_no_truncation(self):
        """Verify JSON size == MAX_JSON_SIZE does NOT trigger truncation."""
        # Build a model and then iteratively adjust result count to hit exactly MAX_JSON_SIZE.
        # Start with a model that is slightly over, then remove results until we hit the boundary.
        model = self._make_large_model(result_count=60)

        initial_json = model.model_dump_json().encode("utf-8")
        initial_len = len(initial_json)

        # If already at or below the limit, use as-is; otherwise trim results to hit the boundary
        if initial_len <= model.MAX_JSON_SIZE:
            assert (
                initial_len == model.MAX_JSON_SIZE or initial_len < model.MAX_JSON_SIZE
            )
        else:
            # Trim one result at a time until we reach exactly MAX_JSON_SIZE
            while len(model.model_dump_json().encode("utf-8")) > model.MAX_JSON_SIZE:
                model = model.model_copy(update={"results": model.results[:-1]})

            # After trimming, the JSON should be exactly at the boundary
            assert len(model.model_dump_json().encode("utf-8")) == model.MAX_JSON_SIZE

        # validate_json_size() should return self unchanged — no truncation
        result = model.validate_json_size()

        assert result.results == model.results
        assert result.metadata == model.metadata
        assert "warning" not in result.metadata

    def test_binary_search_converges_to_exact_max_count(self):
        """Verify binary search converges to the exact maximum count that fits."""
        # Build a model that definitely exceeds MAX_JSON_SIZE using _make_large_model.
        model = self._make_large_model(result_count=100)

        initial_json = model.model_dump_json().encode("utf-8")
        assert len(initial_json) > model.MAX_JSON_SIZE

        # Independently compute the exact max count by checking each candidate
        max_fit: int = 0
        for i in range(0, len(model.results) + 1):
            candidate = model.model_copy(update={"results": model.results[:i]})
            candidate_json = candidate.model_dump_json().encode("utf-8")
            if len(candidate_json) <= candidate.MAX_JSON_SIZE:
                max_fit = i
            else:
                break

        # Now run validate_json_size() and verify it converges to the same count
        result = model.validate_json_size()

        # Binary search must converge to exactly max_fit
        assert len(result.results) == max_fit

        # Verify the converged JSON fits
        final_json = result.model_dump_json().encode("utf-8")
        assert len(final_json) <= model.MAX_JSON_SIZE

        # Verify that max_fit + 1 would NOT fit (confirming exactness)
        if max_fit < len(model.results):
            one_more = model.model_copy(
                update={"results": model.results[: max_fit + 1]}
            )
            assert (
                len(one_more.model_dump_json().encode("utf-8")) > one_more.MAX_JSON_SIZE
            )

    def test_combined_oversized_results_and_metadata_converges_to_zero(self):
        """Verify binary search converges to 0 when both results and metadata are oversized."""
        # Build a model with both large results AND large metadata — combined size >> 500KB
        large_results = [
            {
                "url": f"https://example.com/result-{i}",
                "title": f"Title {i} with {'x' * 5000} filler",
                "description": f"Description {i} with {'x' * 5000} filler content",
                "provider": "duck",
                "quality_score": {
                    "overall": 0.5,
                    "content_quality": 0.5,
                    "seo_spam_score": 0.5,
                    "clickbait_score": 0.5,
                },
            }
            for i in range(50)
        ]

        large_metadata: dict = {
            "provider": "duck",
            "cache_hit": False,
            "total_found": 50,
            "diversity_scores": {f"provider-{i}": 0.5 for i in range(400)},
            "judgment": {
                "diversity_score": 0.7,
                "trustworthiness_score": 0.8,
                "relevance_to_query": 0.9,
                "freshness_score": 0.75,
                "score": 0.85,
                "verdict": "pass",
                "reasons": ["x" * 5000 for _ in range(60)],
            },
            "parameters": {
                "engines": "x" * 500,
                "time_range": "week",
                "site": "x" * 500,
                "ttl_override": None,
            },
            "extra_large_field": "x" * 400000,
        }

        model = UnifiedJSONResponse(
            query="combined oversized test query with lots of words",
            format="json",
            results=large_results,
            metadata=large_metadata,
        )

        initial_json = model.model_dump_json().encode("utf-8")
        assert len(initial_json) > model.MAX_JSON_SIZE

        result = model.validate_json_size()

        # Graceful fallback: metadata alone exceeds limit — return empty results
        assert len(result.results) == 0

        # Warning must indicate graceful fallback (metadata alone exceeds)
        assert "warning" in result.metadata
        assert "metadata alone exceeds" in result.metadata["warning"]

        # Final JSON may still exceed MAX_JSON_SIZE because metadata alone is oversized
        # — the graceful fallback cannot reduce metadata, only results
        assert len(result.model_dump_json().encode("utf-8")) > model.MAX_JSON_SIZE


# ── AC8: freshness badge in format_markdown_search ───────────────────────


class TestAC8FreshnessBadgeMarkdownSearch:
    """Tests for AC8: freshness badge 🟢/🟡/🔴 for each result in format_markdown_search."""

    def test_freshness_badge_high_score(self):
        """Verify freshness badge 🟢 fresh for score >= 0.8."""
        request = SearchRequest(query="test")
        results = [
            SearchResult(
                url="https://example.com/1",
                title="Title 1",
                description="Desc 1",
                freshness_score=0.95,
            ),
        ]
        response = SearchResponse(results=results, provider="duck", total_found=1)
        result = OutputFormatter.format_markdown_search(request, response)

        assert "🟢 fresh" in result

    def test_freshness_badge_medium_score(self):
        """Verify freshness badge 🟡 moderate for score >= 0.6 and < 0.8."""
        request = SearchRequest(query="test")
        results = [
            SearchResult(
                url="https://example.com/1",
                title="Title 1",
                description="Desc 1",
                freshness_score=0.7,
            ),
        ]
        response = SearchResponse(results=results, provider="duck", total_found=1)
        result = OutputFormatter.format_markdown_search(request, response)

        assert "🟡 moderate" in result

    def test_freshness_badge_low_score(self):
        """Verify freshness badge 🔴 stale for score < 0.6."""
        request = SearchRequest(query="test")
        results = [
            SearchResult(
                url="https://example.com/1",
                title="Title 1",
                description="Desc 1",
                freshness_score=0.3,
            ),
        ]
        response = SearchResponse(results=results, provider="duck", total_found=1)
        result = OutputFormatter.format_markdown_search(request, response)

        assert "🔴 stale" in result

    def test_freshness_line_present_per_result(self):
        """Verify each result has a '- Freshness:' line in markdown output."""
        request = SearchRequest(query="test")
        results = [
            SearchResult(
                url="https://example.com/1",
                title="Title 1",
                description="Desc 1",
                freshness_score=0.9,
            ),
            SearchResult(
                url="https://example.com/2",
                title="Title 2",
                description="Desc 2",
                freshness_score=0.4,
            ),
        ]
        response = SearchResponse(results=results, provider="duck", total_found=2)
        result = OutputFormatter.format_markdown_search(request, response)

        assert "- Freshness: 🟢 fresh" in result
        assert "- Freshness: 🔴 stale" in result


# ── AC9: freshness_score in format_json_search metadata ──────────────────


class TestAC9FreshnessScoreJsonSearch:
    """Tests for AC9: freshness_score in format_json_search metadata."""

    def test_freshness_score_in_metadata(self):
        """Verify freshness_score is present in JSON metadata."""
        results = [
            SearchResult(
                url="https://example.com/1",
                title="Title 1",
                description="Desc 1",
                freshness_score=0.9,
            ),
            SearchResult(
                url="https://example.com/2",
                title="Title 2",
                description="Desc 2",
                freshness_score=0.5,
            ),
        ]
        response = SearchResponse(results=results, provider="duck", total_found=2)
        result = OutputFormatter.format_json_search(response, query="test")

        assert "freshness_score" in result.model_dump()["metadata"]

    def test_freshness_score_is_average(self):
        """Verify freshness_score is the average of all results."""
        results = [
            SearchResult(
                url="https://example.com/1",
                title="Title 1",
                description="Desc 1",
                freshness_score=0.8,
            ),
            SearchResult(
                url="https://example.com/2",
                title="Title 2",
                description="Desc 2",
                freshness_score=0.6,
            ),
        ]
        response = SearchResponse(results=results, provider="duck", total_found=2)
        result = OutputFormatter.format_json_search(response, query="test")

        assert result.model_dump()["metadata"]["freshness_score"] == 0.7

    def test_freshness_score_zero_for_empty_results(self):
        """Verify freshness_score is 0.0 when results are empty."""
        response = SearchResponse(results=[], provider="duck", total_found=0)
        result = OutputFormatter.format_json_search(response, query="test")

        assert result.model_dump()["metadata"]["freshness_score"] == 0.0


# ── AC10: freshness badge in format_markdown_webfetch ────────────────────


class TestAC10FreshnessBadgeMarkdownWebfetch:
    """Tests for AC10: freshness badge in State Summary for format_markdown_webfetch."""

    def test_freshness_badge_high_score(self):
        """Verify freshness badge 🟢 fresh in State Summary for score >= 0.8."""
        state = WebFetchState(
            prompt="test prompt",
            tenant_id="test-tenant",
            freshness_score=0.95,
        )
        result = OutputFormatter.format_markdown_webfetch("test prompt", state, [])

        assert "🟢 fresh" in result
        assert "- Content Freshness: 🟢 fresh" in result

    def test_freshness_badge_low_score(self):
        """Verify freshness badge 🔴 stale in State Summary for score < 0.6."""
        state = WebFetchState(
            prompt="test prompt",
            tenant_id="test-tenant",
            freshness_score=0.3,
        )
        result = OutputFormatter.format_markdown_webfetch("test prompt", state, [])

        assert "🔴 stale" in result
        assert "- Content Freshness: 🔴 stale" in result


# ── AC11: freshness_score in format_json_webfetch metadata ───────────────


class TestAC11FreshnessScoreJsonWebfetch:
    """Tests for AC11: freshness_score in format_json_webfetch metadata."""

    def test_freshness_score_in_metadata(self):
        """Verify freshness_score is present in JSON metadata."""
        state = WebFetchState(
            prompt="test prompt",
            tenant_id="test-tenant",
            freshness_score=0.85,
        )
        result = OutputFormatter.format_json_webfetch("test prompt", state, [])

        assert "freshness_score" in result.model_dump()["metadata"]
        assert result.model_dump()["metadata"]["freshness_score"] == 0.85

    def test_freshness_score_default_value(self):
        """Verify freshness_score uses default 0.75 when not explicitly set."""
        state = WebFetchState(
            prompt="test prompt",
            tenant_id="test-tenant",
        )
        result = OutputFormatter.format_json_webfetch("test prompt", state, [])

        assert result.model_dump()["metadata"]["freshness_score"] == 0.75


# ── AC12: SearchResponse.search_results field ────────────────────────────


class TestAC12SearchResponseSearchResults:
    """Tests for AC12: SearchResponse.search_results: list[SearchResponse] | None."""

    def test_search_results_default_is_none(self):
        """Verify search_results defaults to None."""
        response = SearchResponse(
            results=[SearchResult(url="https://example.com/1", title="T1")],
            provider="duck",
            total_found=1,
        )
        assert response.search_results is None

    def test_search_results_accepts_list(self):
        """Verify search_results accepts a list of SearchResponse."""
        nested = SearchResponse(
            results=[SearchResult(url="https://example.com/2", title="T2")],
            provider="tavily",
            total_found=1,
        )
        response = SearchResponse(
            results=[SearchResult(url="https://example.com/1", title="T1")],
            provider="duck",
            total_found=1,
            search_results=[nested],
        )
        assert response.search_results is not None
        assert len(response.search_results) == 1
        assert response.search_results[0].provider == "tavily"

    def test_search_results_serialization(self):
        """Verify search_results serializes correctly in model_dump."""
        nested = SearchResponse(
            results=[SearchResult(url="https://example.com/2", title="T2")],
            provider="tavily",
            total_found=1,
        )
        response = SearchResponse(
            results=[SearchResult(url="https://example.com/1", title="T1")],
            provider="duck",
            total_found=1,
            search_results=[nested],
        )
        dumped = response.model_dump()
        assert "search_results" in dumped
        assert dumped["search_results"] is not None
        assert len(dumped["search_results"]) == 1


# ── AC13: _freshness_badge helper ───────────────────────────────────────


class TestFreshnessBadgeHelper:
    """Tests for _freshness_badge helper function."""

    def test_high_freshness_returns_fresh(self):
        """Verify score >= 0.8 returns 🟢 fresh."""
        assert _freshness_badge(0.8) == "🟢 fresh"
        assert _freshness_badge(1.0) == "🟢 fresh"

    def test_medium_freshness_returns_moderate(self):
        """Verify score >= 0.6 and < 0.8 returns 🟡 moderate."""
        assert _freshness_badge(0.6) == "🟡 moderate"
        assert _freshness_badge(0.75) == "🟡 moderate"

    def test_low_freshness_returns_stale(self):
        """Verify score < 0.6 returns 🔴 stale."""
        assert _freshness_badge(0.59) == "🔴 stale"
        assert _freshness_badge(0.0) == "🔴 stale"

    def test_ascii_freshness_badges(self):
        """Verify ASCII freshness badges."""
        assert _freshness_badge(0.8, badge_style="ascii") == "[FRESH]"
        assert _freshness_badge(0.6, badge_style="ascii") == "[MODERATE]"
        assert _freshness_badge(0.59, badge_style="ascii") == "[STALE]"

    def test_freshness_badge_default_style_is_emoji(self):
        """Verify badge_style defaults to emoji for freshness."""
        assert _freshness_badge(0.8) == "🟢 fresh"
        assert _freshness_badge(0.6) == "🟡 moderate"
        assert _freshness_badge(0.0) == "🔴 stale"

    def test_freshness_badge_boundary_at_0_8(self):
        """Verify score exactly at 0.8 returns 🟢 fresh."""
        assert _freshness_badge(0.8) == "🟢 fresh"
        assert _freshness_badge(0.799999) == "🟡 moderate"

    def test_freshness_badge_boundary_at_0_6(self):
        """Verify score exactly at 0.6 returns 🟡 moderate."""
        assert _freshness_badge(0.6) == "🟡 moderate"
        assert _freshness_badge(0.599999) == "🔴 stale"


# ── AC14: SearchResponse.search_results integration ─────────────────────


class TestAC14SearchResultsIntegration:
    """Tests for AC14: integration of SearchResponse.search_results with formatter."""

    def test_search_results_serialized_in_json_search(self):
        """Verify nested search_results are serialized in format_json_search metadata."""
        nested = SearchResponse(
            results=[SearchResult(url="https://example.com/2", title="T2")],
            provider="tavily",
            total_found=1,
        )
        response = SearchResponse(
            results=[SearchResult(url="https://example.com/1", title="T1")],
            provider="duck",
            total_found=1,
            search_results=[nested],
        )
        result = OutputFormatter.format_json_search(response, query="test")

        # search_results is not added to metadata by format_json_search —
        # it's part of the model but metadata only includes specific fields
        assert "search_results" not in result.model_dump()["metadata"]

    def test_search_results_none_does_not_break_json_search(self):
        """Verify None search_results does not cause errors in format_json_search."""
        response = SearchResponse(
            results=[SearchResult(url="https://example.com/1", title="T1")],
            provider="duck",
            total_found=1,
            search_results=None,
        )
        result = OutputFormatter.format_json_search(response, query="test")

        assert result.model_dump()["format"] == "json"
        assert len(result.model_dump()["results"]) == 1

    def test_search_results_with_multiple_nested_responses(self):
        """Verify search_results with multiple nested SearchResponse."""
        nested1 = SearchResponse(
            results=[SearchResult(url="https://example.com/2", title="T2")],
            provider="tavily",
            total_found=1,
        )
        nested2 = SearchResponse(
            results=[SearchResult(url="https://example.com/3", title="T3")],
            provider="google",
            total_found=1,
        )
        response = SearchResponse(
            results=[SearchResult(url="https://example.com/1", title="T1")],
            provider="duck",
            total_found=1,
            search_results=[nested1, nested2],
        )
        assert len(response.search_results) == 2
        assert response.search_results[0].provider == "tavily"
        assert response.search_results[1].provider == "google"


# ── AC31: metadata convergence with progressive truncation ───────────────


class TestMetadataConvergenceProgressiveTruncation:
    """AC31: Extended tests for metadata convergence validation during progressive truncation."""

    def _make_large_response(self, count: int) -> SearchResponse:
        """Helper to construct a SearchResponse with many large results."""
        results = [
            SearchResult(
                url=f"https://example.com/{i}",
                title=f"Title {i} with lots of descriptive filler text to increase JSON size",
                description=f"Description {i} with extensive content padding to ensure we exceed the 500KB limit when many results are included",
                quality_score=QualityScore(
                    overall=0.5,
                    content_quality=0.5,
                    seo_spam_score=0.5,
                    clickbait_score=0.5,
                ),
            )
            for i in range(count)
        ]
        return SearchResponse(
            results=results,
            provider="duck",
            cache_hit=False,
            total_found=count,
            diversity_scores={"duck": 0.75, "tavily": 0.6},
            judgment=SearchResultJudge(
                diversity_score=0.7,
                trustworthiness_score=0.8,
                relevance_to_query=0.9,
                score=0.85,
                verdict="pass",
                reasons=["good diversity"],
            ),
        )

    def test_truncation_adds_warning_to_metadata(self):
        """Verify truncated output adds 'warning' key to metadata."""
        response = self._make_large_response(2000)
        result = OutputFormatter.format_json_search(response, query="test")

        assert "warning" in result.model_dump()["metadata"]
        warning = result.model_dump()["metadata"]["warning"]
        assert "truncated" in warning.lower()

    def test_truncation_preserves_core_metadata_fields(self):
        """Verify core metadata fields preserved after truncation."""
        response = self._make_large_response(2000)
        result = OutputFormatter.format_json_search(response, query="test")

        dumped = result.model_dump()["metadata"]
        assert dumped["provider"] == "duck"
        assert dumped["cache_hit"] is False
        assert dumped["diversity_scores"] == {"duck": 0.75, "tavily": 0.6}
        assert "judgment" in dumped
        assert dumped["judgment"]["verdict"] == "pass"

    def test_truncation_results_count_matches_warning(self):
        """Verify truncated results count matches the number stated in warning."""
        response = self._make_large_response(2000)
        result = OutputFormatter.format_json_search(response, query="test")

        dumped = result.model_dump()
        actual_count = len(dumped["results"])
        warning = dumped["metadata"]["warning"]

        # Warning format: "X results reduced to Y"
        assert f"reduced to {actual_count}" in warning

    def test_truncation_output_fits_within_size_limit(self):
        """Verify truncated JSON output fits within MAX_JSON_SIZE (500KB)."""
        response = self._make_large_response(2000)
        result = OutputFormatter.format_json_search(response, query="test")

        json_bytes = result.model_dump_json().encode("utf-8")
        assert len(json_bytes) <= UnifiedJSONResponse.MAX_JSON_SIZE

    def test_no_truncation_when_within_limit(self):
        """Verify no truncation warning when output fits within limit."""
        response = self._make_large_response(5)
        result = OutputFormatter.format_json_search(response, query="test")

        assert "warning" not in result.model_dump()["metadata"]
        json_bytes = result.model_dump_json().encode("utf-8")
        assert len(json_bytes) <= UnifiedJSONResponse.MAX_JSON_SIZE
        assert len(result.model_dump()["results"]) == 5

    def test_convergence_binary_search_monotonic(self):
        """Verify progressive truncation via binary search produces monotonically decreasing counts."""
        response = self._make_large_response(2000)
        result = OutputFormatter.format_json_search(response, query="test")

        original_count = 2000
        final_count = len(result.model_dump()["results"])

        assert final_count < original_count
        assert final_count >= 0

    def test_metadata_warning_format_consistent(self):
        """Verify warning message follows consistent format with all required elements."""
        response = self._make_large_response(2000)
        result = OutputFormatter.format_json_search(response, query="test")

        warning = result.model_dump()["metadata"]["warning"]
        assert "results" in warning
        assert "reduced to" in warning
        assert "500 KB" in warning or "500KB" in warning or "limit" in warning.lower()

    def test_empty_results_graceful_fallback_no_crash(self):
        """Verify even extreme cases do not crash — graceful fallback returns valid JSON."""
        response = self._make_large_response(3000)
        result = OutputFormatter.format_json_search(response, query="test")

        dumped = result.model_dump()
        assert isinstance(dumped["results"], list)
        assert isinstance(dumped["metadata"], dict)
        assert "warning" in dumped["metadata"]

        json_bytes = result.model_dump_json().encode("utf-8")
        assert len(json_bytes) <= UnifiedJSONResponse.MAX_JSON_SIZE
