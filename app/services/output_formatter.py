"""Output formatter service for search and webfetch results."""

from app.models.search import SearchRequest, SearchResponse, UnifiedJSONResponse
from app.models.webfetch import SourceFeature, WebFetchState


def _quality_badge(score: float, badge_style: str = "emoji") -> str:
    """Return a quality badge string based on score threshold.

    Args:
        score: Quality score value (0.0–1.0).
        badge_style: Badge rendering style — 'emoji' (default) or 'ascii'.

    Returns:
        Badge string: emoji ('🟢 high', '🟡 medium', '🔴 low') or
        ascii ('[HIGH]', '[MEDIUM]', '[LOW]').
    """
    if score >= 0.8:
        return "🟢 high" if badge_style == "emoji" else "[HIGH]"
    if score >= 0.6:
        return "🟡 medium" if badge_style == "emoji" else "[MEDIUM]"
    return "🔴 low" if badge_style == "emoji" else "[LOW]"


def _freshness_badge(score: float, badge_style: str = "emoji") -> str:
    """Return a freshness badge string based on score threshold.

    Args:
        score: Freshness score value (0.0–1.0).
        badge_style: Badge rendering style — 'emoji' (default) or 'ascii'.

    Returns:
        Badge string: emoji ('🟢 fresh', '🟡 moderate', '🔴 stale') or
        ascii ('[FRESH]', '[MODERATE]', '[STALE]').
    """
    if score >= 0.8:
        return "🟢 fresh" if badge_style == "emoji" else "[FRESH]"
    if score >= 0.6:
        return "🟡 moderate" if badge_style == "emoji" else "[MODERATE]"
    return "🔴 stale" if badge_style == "emoji" else "[STALE]"


class OutputFormatter:
    """Unified output formatter for search and webfetch results.

    Supports markdown and JSON output formats with consistent structure
    and metadata inclusion.
    """

    MAX_MARKDOWN_LENGTH = 10000

    @staticmethod
    def format_markdown_search(request: SearchRequest, response: SearchResponse) -> str:
        """Format search results as structured markdown.

        Produces a markdown document with query header, ranked results
        with quality badges, and metadata section. Truncated at
        MAX_MARKDOWN_LENGTH characters with a warning marker.

        Args:
            request: The original SearchRequest payload.
            response: The SearchResponse with results and metadata.

        Returns:
            Markdown string (truncated if exceeds MAX_MARKDOWN_LENGTH).
        """

        query = request.query
        results = response.results
        total_found = response.total_found
        provider = response.provider
        cache_hit = response.cache_hit
        diversity_scores = response.diversity_scores or {}
        judgment = response.judgment

        lines: list[str] = []
        lines.append(f"# Search: {query}")
        lines.append("")
        lines.append(f"## Results ({total_found} found)")
        lines.append("")

        for idx, result in enumerate(results, start=1):
            url = result.url
            title = result.title
            description = result.description or ""

            lines.append(f"{idx}. **[{title}]({url})** — {description}")

            quality_str = ""
            if result.quality_score:
                overall = result.quality_score.overall
                quality_str = _quality_badge(overall)

            freshness_str = _freshness_badge(result.freshness_score)

            diversity_str = ""
            if judgment and judgment.diversity_score is not None:
                diversity_str = _quality_badge(judgment.diversity_score)

            lines.append(f"   - Provider: {provider} | Quality: {quality_str}")
            lines.append(f"   - Freshness: {freshness_str}")
            lines.append(f"   - Diversity: {diversity_str}")
            lines.append("")

        lines.append("## Metadata")
        lines.append(f"- Provider: {provider}")
        lines.append(f"- Cache Hit: {cache_hit}")

        if judgment:
            verdict = judgment.verdict
            score = judgment.score or 0.0
            lines.append(f"- Judgment: {verdict} ({score})")

        lines.append(f"- Diversity Scores: {diversity_scores}")
        lines.append("")

        markdown = "\n".join(lines)

        if len(markdown) > OutputFormatter.MAX_MARKDOWN_LENGTH:
            markdown = markdown[: OutputFormatter.MAX_MARKDOWN_LENGTH]
            markdown += "\n\n--- [truncated]"

        return markdown

    @staticmethod
    def format_json_search(
        response: SearchResponse, query: str = ""
    ) -> UnifiedJSONResponse:
        """Format search results as unified JSON structure.

        Produces a UnifiedJSONResponse with query, results, and metadata
        sections including provider info, diversity scores, and judgment data.
        Validates JSON output size (max 500KB) with automatic truncation.

        Args:
            response: The SearchResponse with results and metadata.
            query: The search query string (passed from tool layer since
                   SearchResponse does not contain the query).

        Returns:
            UnifiedJSONResponse model validated via model_dump_json.
        """
        results_dump = [r.model_dump() for r in response.results]

        # Compute average freshness_score across results
        avg_freshness = (
            sum(r.freshness_score for r in response.results) / len(response.results)
            if response.results
            else 0.0
        )

        metadata: dict = {
            "provider": response.provider,
            "cache_hit": response.cache_hit,
            "total_found": response.total_found,
            "diversity_scores": response.diversity_scores or {},
            "freshness_score": avg_freshness,
        }

        if response.judgment:
            metadata["judgment"] = response.judgment.model_dump()

        if response.parameters:
            metadata["parameters"] = response.parameters.model_dump()

        model = UnifiedJSONResponse(
            query=query,
            format="json",
            results=results_dump,
            metadata=metadata,
        )

        return model.validate_json_size()

    @staticmethod
    def format_markdown_webfetch(
        prompt: str, state: WebFetchState, sources: list[dict | SourceFeature]
    ) -> str:
        """Format webfetch results as structured markdown.

        Produces a markdown document with prompt header, final answer,
        source list with features, and state summary. Truncated at
        MAX_MARKDOWN_LENGTH characters with a warning marker.

        Args:
            prompt: The original search prompt.
            state: The WebFetchState with execution metadata.
            sources: List of SourceFeature dicts or SourceFeature objects.

        Returns:
            Markdown string (truncated if exceeds MAX_MARKDOWN_LENGTH).
        """
        result_text = state.final_result or ""

        lines: list[str] = []
        lines.append(f"# WebFetch: {prompt}")
        lines.append("")
        lines.append("## Final Answer")
        lines.append("")
        lines.append(result_text)
        lines.append("")
        lines.append("## Sources")
        lines.append("")

        for idx, source in enumerate(sources, start=1):
            if isinstance(source, dict):
                url = source.get("url", "")
                features = source.get("features", [])
            else:
                url = source.url
                features = source.features

            feature_list = ", ".join(features) if features else "no features"
            lines.append(f"{idx}. **[{url}]({url})** — {feature_list}")

        lines.append("")
        lines.append("## State Summary")

        search_queries_count = len(state.search_queries)
        selected_urls_count = len(state.selected_urls)
        fetched_content_count = len(state.fetched_content)
        features_count = len(state.features.features) if state.features else 0

        lines.append(f"- Queries generated: {search_queries_count}")
        lines.append(f"- URLs judged: {selected_urls_count}")
        lines.append(f"- Content fetched: {fetched_content_count}")
        lines.append(f"- Features extracted: {features_count}")

        freshness_str = _freshness_badge(state.freshness_score)
        lines.append(f"- Content Freshness: {freshness_str}")
        lines.append("")

        markdown = "\n".join(lines)

        if len(markdown) > OutputFormatter.MAX_MARKDOWN_LENGTH:
            markdown = markdown[: OutputFormatter.MAX_MARKDOWN_LENGTH]
            markdown += "\n\n--- [truncated]"

        return markdown

    @staticmethod
    def format_json_webfetch(
        prompt: str, state: WebFetchState, sources: list[dict | SourceFeature]
    ) -> UnifiedJSONResponse:
        """Format webfetch results as unified JSON structure.

        Produces a UnifiedJSONResponse with prompt as query, sources as
        results, and metadata including execution state counts and
        judgment data. Validates JSON output size (max 500KB) with
        automatic truncation.

        Args:
            prompt: The original search prompt.
            state: The WebFetchState with execution metadata.
            sources: List of SourceFeature dicts or SourceFeature objects.

        Returns:
            UnifiedJSONResponse model validated via model_dump_json.
        """
        sources_dump = []
        for source in sources:
            if hasattr(source, "model_dump"):
                sources_dump.append(source.model_dump())
            elif isinstance(source, dict):
                sources_dump.append(source)
            else:
                sources_dump.append(str(source))

        metadata: dict = {
            "queries_generated": len(state.search_queries),
            "urls_judged": len(state.selected_urls),
            "content_fetched": len(state.fetched_content),
            "features_extracted": len(state.features.features) if state.features else 0,
            "freshness_score": state.freshness_score,
        }

        if state.url_judgment:
            metadata["url_judgment"] = state.url_judgment.model_dump()

        if state.feature_judgment:
            metadata["feature_judgment"] = state.feature_judgment.model_dump()

        if state.final_result:
            metadata["final_result"] = state.final_result

        model = UnifiedJSONResponse(
            query=prompt,
            format="json",
            results=sources_dump,
            metadata=metadata,
        )

        return model.validate_json_size()
