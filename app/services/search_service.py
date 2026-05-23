"""Search service with fallback chain, smart filtering, and provider health tracking."""

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ddgs import DDGS

# Lazy import tavily to avoid mypy import-untyped error at module level
# TavilyClient is imported inside _search_tavily() method only
from app.core.config import Provider, Settings
from app.core.llm_client import create_llm_client
from app.core.logging import get_logger
from app.core.metrics import record_cache_ttl
from app.core.provider_registry import ProviderRegistry
from app.core.ssrf import ssrf_protection
from app.models.search import (
    QualityScore,
    SearchParameters,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SearchResultJudge,
)

if TYPE_CHECKING:
    from app.core.dependencies import RedisClient
    from app.core.llm_client import LLMClient


logger = get_logger(__name__)


class SearchError(Exception):
    """Custom exception for search errors."""

    pass


class SearchService:
    """Search service with fallback chain, smart filtering, and provider health tracking."""

    def __init__(
        self,
        settings: Settings,
        redis: "RedisClient",
        llm_client: "LLMClient | None" = None,
    ):
        self.settings = settings
        self.redis = redis
        self.llm = llm_client or create_llm_client(
            redis_client=getattr(self.redis, "_client", None),
            settings=settings,
        )
        self._registry = ProviderRegistry(settings)

    @property
    def provider_registry(self) -> ProviderRegistry:
        """Return the provider registry instance."""
        return self._registry

    async def search(self, request: SearchRequest) -> SearchResponse:
        """Execute search with fallback chain and caching."""
        cache_key = self._generate_cache_key(request)

        # Check cache first
        cached = await self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        # Execute fallback chain
        result = await self._execute_fallback_chain(request)

        # Cache successful results
        await self._set_in_cache(cache_key, result)

        return result

    async def _execute_fallback_chain(self, request: SearchRequest) -> SearchResponse:
        """Execute search with dynamic health-aware fallback chain."""
        providers = self._registry.get_providers()

        for provider in providers:
            try:
                results = await self._search_provider(provider, request)
                if results and len(results) > 0:
                    # Record success in health tracker
                    self._registry.health_tracker.record_success(provider)

                    # Calculate diversity scores
                    diversity = await self._calculate_diversity(results)

                    # Apply smart filter
                    filtered = self._apply_smart_filter(results, request)

                    # Skip LLM judge if skip_judge=True (for trusted sites)
                    if request.skip_judge or self.settings.SKIP_JUDGE:
                        judgment = SearchResultJudge(
                            diversity_score=diversity["diversity_scores"].get(
                                "overall", 0.8
                            ),
                            trustworthiness_score=0.9,
                            relevance_to_query=0.9,
                            score=0.9,
                            verdict="pass",
                            reasons=["skip_judge=True for trusted site"],
                        )
                    else:
                        # Judge search results using LLM-as-Judge
                        judgment = await self._judge_search_results(
                            request.query, filtered
                        )

                        # Integrate verdict into decision logic: reject → retry with next provider
                        if judgment.verdict == "reject":
                            logger.warning(
                                f"Search results rejected by LLM judge for provider {provider}. "
                                f"Retrying with next provider.",
                                extra={"reasons": judgment.reasons},
                            )
                            continue

                    return SearchResponse(
                        results=filtered,
                        provider=provider,
                        cache_hit=False,
                        total_found=len(filtered),
                        diversity_scores=diversity["diversity_scores"],
                        parameters=SearchParameters(
                            engines=request.engines,
                            time_range=request.time_range,
                            site=request.site,
                        ),
                        judgment=judgment,
                    )
            except Exception as e:
                # Record failure in health tracker for this provider
                self._registry.health_tracker.record_failure(provider)
                logger.warning(f"Provider {provider} failed: {e}")
                continue

        # If we reach here, all providers returned results but LLM rejected them
        # Return error with clear message about relevance issue
        raise SearchError(
            "No relevant results found. The search engine did return results, "
            "but they were not relevant to your query based on quality assessment."
        )

    async def _search_provider(
        self, provider: str, request: SearchRequest
    ) -> list | None:
        """Search with specific provider. Returns None if API unavailable."""
        if Provider(provider) == Provider.duck:
            return await self._search_duckduckgo(request)
        elif Provider(provider) == Provider.searxng:  # NEW
            return await self._search_searxng(request)
        elif Provider(provider) == Provider.tavily:
            return await self._search_tavily(request)
        elif Provider(provider) == Provider.google:
            return await self._search_google(request)

        return None

    async def _search_searxng(
        self, request: SearchRequest
    ) -> list[SearchResult] | None:
        """Search with SearxNG provider."""
        base_url = self.settings.SEARXNG_BASE
        if not base_url:
            logger.warning("SearxNG provider unavailable: SEARXNG_BASE not configured")
            return None

        # Build query parameters including tunability
        params = {
            "q": request.query,
            "format": "json",
            "limit": str(request.max_results),
        }

        # Apply tunable parameters via URL encoding
        if request.engines:
            params["engines"] = request.engines
        if request.time_range:
            params["time_range"] = request.time_range
        if request.site:
            params["q"] += f" site:{request.site}"
        if request.language:
            params["language"] = request.language

        try:
            # Use SSRF-protected async fetch (validates URL internally via ssrf_protection)
            content = await ssrf_protection.fetch_async(
                f"{base_url}/search",
                params=params,
            )

            parsed = json.loads(content.decode("utf-8"))
            results_list = parsed.get("results", [])

            search_results = []
            for r in results_list:
                result = SearchResult(
                    url=r.get("url", ""),
                    title=r.get("title", ""),
                    description=r.get("content", ""),
                    provider="searxng",
                )
                search_results.append(result)

            # Edge case: return None if no results
            return search_results if search_results else None

        except Exception as e:
            logger.warning(f"SearxNG request failed: {e}")
            return None

    async def _search_duckduckgo(self, request: SearchRequest) -> list[SearchResult]:
        """Search with DuckDuckGo (non-blocking via thread pool)."""

        def fetch_results_sync():
            """Fetch results in thread pool to avoid blocking event loop."""
            # ddgs.text() supports query, max_results, region via kwargs
            with DDGS() as ddgs:
                return list(
                    ddgs.text(
                        query=request.query,
                        max_results=request.max_results,
                        region=request.region or "wt-wt",
                    )
                )

        # Run synchronous HTTP calls in thread pool
        results = await asyncio.to_thread(fetch_results_sync)

        # Convert dict results to SearchResult objects
        search_results = []
        for r in results:
            result = SearchResult(
                url=r.get("href", ""),
                title=r.get("title", ""),
                description=r.get("body", ""),
                provider=Provider.duck,
            )
            search_results.append(result)

        return search_results

    async def _search_tavily(self, request: SearchRequest) -> list[SearchResult] | None:
        """Search with Tavily API."""
        provider = "tavily"
        api_key = self.settings._get_api_key("TAVILY_API_KEY")
        if not api_key:
            logger.warning(f"Provider {provider} unavailable (missing TAVILY_API_KEY)")
            return None

        from tavily import TavilyClient  # type: ignore[import-untyped]

        client = TavilyClient(api_key=api_key)

        # Map request parameters to Tavily API format
        search_depth = "fast"  # Default: fast mode for balance
        if request.max_results > 20:
            search_depth = "advanced"

        response = client.search(
            query=request.query,
            search_depth=search_depth,
            max_results=request.max_results,
        )

        # Convert Tavily results to SearchResult objects
        search_results = []
        for r in response.results:
            result = SearchResult(
                url=r.url,
                title=r.title,
                description=r.content or "",
                provider=Provider.tavily,
            )
            search_results.append(result)

        return search_results

    async def _search_google(self, request: SearchRequest) -> list[SearchResult] | None:
        """Search with Google Custom Search API."""
        api_key = self.settings._get_google_api_key()
        if not api_key:
            logger.warning(
                f"Provider {Provider.google} unavailable (missing GOOGLE_API_KEY)"
            )
            return None

        cse_id = self.settings.GOOGLE_CSE_ID
        if not cse_id:
            logger.warning(
                f"Provider {Provider.google} unavailable (missing GOOGLE_CSE_ID)"
            )
            return None

        base_url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": api_key,
            "q": request.query,
            "cx": cse_id,
            "num": str(request.max_results),
        }

        if request.time_range:
            params["dateRestrict"] = request.time_range
        if request.language:
            params["lr"] = f"lang_{request.language}"

        try:
            content = await ssrf_protection.fetch_async(base_url, params=params)
            data = json.loads(content.decode("utf-8"))
            items = data.get("items", [])

            search_results = []
            for item in items:
                result = SearchResult(
                    url=item.get("link", ""),
                    title=item.get("title", ""),
                    description=item.get("snippet", ""),
                    provider=Provider.google,
                )
                search_results.append(result)

            return search_results if search_results else None

        except Exception as e:
            logger.warning(f"Google search request failed: {e}")
            return None

    def _apply_smart_filter(
        self, results: list[SearchResult], request: SearchRequest
    ) -> list[SearchResult]:
        """Apply smart filtering: blacklist, quality score, SEO spam."""
        filtered = []

        for result in results:
            # Check blacklist
            if request.filter_blacklist and self._is_blacklisted(result):
                continue

            # Calculate quality score
            if request.calculate_quality:
                quality_score = self._calculate_quality_score(result)
                result.quality_score = quality_score

                if quality_score.overall < self.settings.QUALITY_SCORE_THRESHOLD:
                    continue

            # Apply smart filter (SEO spam, clickbait)
            if request.apply_smart_filter and self._is_seo_spam(result):
                continue

            filtered.append(result)

        return filtered

    async def _calculate_diversity(self, results: list[SearchResult]) -> dict:
        """Calculate diversity metrics for search results."""
        domains = set(r.url for r in results)
        providers = set(r.provider for r in results)

        # Source diversity: different domains/providers
        source_diversity = len(domains) / max(len(providers), 1)

        # Temporal diversity: based on provider (duck has timestamps, others may not)
        temporal_diversity = 0.8 if any(r.timestamp for r in results) else 0.5

        # Content diversity: estimate based on diversity of providers
        content_diversity = min(source_diversity + 0.1, 1.0)

        weights = self.settings.DIVERSITY_WEIGHTS
        overall = (
            source_diversity * weights["source"]
            + temporal_diversity * weights["temporal"]
            + content_diversity * weights["content"]
        )

        return {
            "diversity_scores": {
                "source_diversity": round(source_diversity, 2),
                "temporal_diversity": round(temporal_diversity, 2),
                "content_diversity": round(content_diversity, 2),
                "overall": round(overall, 2),
            },
        }

    async def _judge_search_results(
        self, query: str, results: list[SearchResult]
    ) -> SearchResultJudge:
        """Evaluate search results quality using LLM-as-Judge."""
        try:
            # Prepare results for LLM judgment (extract key info from SearchResult)
            results_info: list[dict[str, str]] = []
            for r in results:
                url = str(r.url)
                title = str(r.title) if r.title else ""
                description = str(r.description) if r.description else ""

                results_info.append(
                    {
                        "url": url,
                        "title": title,
                        "description": description,
                    }
                )

            # Use LLM client to judge search results via judge_urls_with_content
            judgment = await self.llm.judge_urls_with_content(
                prompt=query,
                url_content_pairs=results_info[:5],  # Limit to first 5 for token budget
            )

            # Map LLM judgment (JudgeVerdict) to SearchResultJudge using from_judge_verdict
            judgment_result = SearchResultJudge.from_judge_verdict(judgment)

            return judgment_result
        except Exception as e:
            logger.warning(f"LLM judgment failed: {e}. Using fallback scores.")
            # Fallback to heuristic scores based on available results
            domains: set[str] = set()
            for item in results_info:
                url = str(item.get("url", ""))
                if "/" in url:
                    domain = url.split("/")[2] if len(url.split("/")) > 2 else ""
                    if domain:
                        domains.add(domain)

            return SearchResultJudge(
                diversity_score=min(len(domains) / max(len(results_info), 1), 1.0),
                trustworthiness_score=0.75,
                relevance_to_query=0.85,
                score=0.0,  # Fallback: explicitly set to 0.0 instead of None
                verdict="reject",
                reasons=["LLM judgment failed - using fallback heuristic scores"],
            )

    def _is_blacklisted(self, result: SearchResult) -> bool:
        """Check if URL is in blacklist."""
        domain = urlparse(str(result.url)).netloc
        if domain in self.settings.BLACKLIST_DOMAINS:
            return True

        # Wildcard matching via fnmatch
        import fnmatch

        for pattern in self.settings.BLACKLIST_DOMAIN_PATTERNS:
            if fnmatch.fnmatch(domain, pattern):
                return True

        return False

    def _calculate_quality_score(self, result: SearchResult) -> QualityScore:
        """Calculate quality score for search result based on URL characteristics."""
        url = str(result.url)
        domain = url.lower()

        # Calculate SEO spam indicators
        seo_spam_score = 0.0
        if "?" in url and "q=" in url:
            seo_spam_score += 0.1
        if ".xyz" in domain or ".blog" in domain:
            seo_spam_score += 0.2

        # Calculate clickbait indicators
        clickbait_score = 0.0
        title_lower = result.title.lower() if result.title else ""
        if any(
            word in title_lower for word in ["you won't believe", "shocking", "secret"]
        ):
            clickbait_score += 0.5

        # Calculate content quality (based on provider)
        content_quality = 0.8
        if result.provider == Provider.tavily:
            content_quality = 0.85
        elif result.provider == Provider.duck:
            content_quality = 0.75

        overall = (
            content_quality * 1.0
            + (1.0 - seo_spam_score) * 0.5
            + (1.0 - clickbait_score) * 0.3
        ) / 2.5

        return QualityScore(
            overall=round(overall, 2),
            content_quality=round(content_quality, 2),
            seo_spam_score=round(seo_spam_score, 2),
            clickbait_score=round(clickbait_score, 2),
        )

    def _is_seo_spam(self, result: SearchResult) -> bool:
        """Check if result is SEO spam based on URL characteristics."""
        url = str(result.url)
        domain = url.lower()

        # URL shorteners — always flagged as spam
        if any(d in domain for d in ["bit.ly", "t.co", "goo.gl", "tinyurl"]):
            return True

        # High-risk domains
        if ".xyz" in domain or ".blog" in domain:
            return True

        # Check URL path patterns
        path = url.lower()
        if any(word in path for word in ["click", "free", "winner"]):
            return True

        # Ad-heavy patterns — add penalty to quality score
        if any(word in path for word in ["ad", "ads", "affiliate", "sponsor"]):
            result.seo_spam_score = (result.seo_spam_score or 0.0) + 0.15

        return False

    def _generate_cache_key(self, request: SearchRequest) -> str:
        """Generate cache key for search request with versioning."""
        data = f"v{self.settings.CACHE_VERSION}:{request.query}:{request.region}:{request.language}"
        if request.engines:
            data += f":engines={request.engines}"
        if request.time_range:
            data += f":time_range={request.time_range}"
        if request.site:
            data += f":site={request.site}"

        hash_val = hashlib.sha256(data.encode()).hexdigest()[:8]
        return f"search:v{self.settings.CACHE_VERSION}:{hash_val}"

    def compute_adaptive_ttl(self, freshness_score: float) -> int:
        """Compute adaptive TTL based on freshness score.

        Mapping (AC 6):
        - freshness > 0.8 → 24h (86400s)
        - 0.5 ≤ freshness ≤ 0.8 → 6h (21600s)
        - freshness < 0.5 → 1h (3600s)

        Uses Settings.ADAPTIVE_TTL_RANGES for configurability (AC 9).
        """
        ranges = self.settings.ADAPTIVE_TTL_RANGES

        # Check high freshness bucket
        high_range = ranges.get("high", (0.8, 86400))
        if freshness_score >= high_range[0]:
            return high_range[1]

        # Check medium freshness bucket
        medium_range = ranges.get("medium", (0.5, 21600))
        if freshness_score >= medium_range[0]:
            return medium_range[1]

        # Low freshness bucket
        low_range = ranges.get("low", (0.0, 3600))
        return low_range[1]

    async def _get_from_cache(self, cache_key: str) -> SearchResponse | None:
        """Get results from Redis cache with stale detection (AC 14)."""
        cached = await self.redis.client.get(f"isearch:{cache_key}")
        if cached:
            data = json.loads(cached)
            judgment_data = data.get("judgment")
            freshness = (
                judgment_data.get("freshness_score", 0.75) if judgment_data else 0.75
            )

            # Check freshness against invalidation threshold (AC 12)
            cache_stale = freshness < self.settings.FRESHNESS_INVALIDATION_THRESHOLD

            if cache_stale:
                # Record stale hit metric (AC 19)
                from app.core.metrics import record_cache_hit_with_stale

                record_cache_hit_with_stale(cache_type="search")
                logger.warning(
                    "served_stale_cache",
                    cache_key=cache_key,
                    freshness_score=freshness,
                    threshold=self.settings.FRESHNESS_INVALIDATION_THRESHOLD,
                )

            return SearchResponse(
                results=[SearchResult(**r) for r in data["results"]],
                provider=data["provider"],
                cache_hit=True,
                total_found=data["total_found"],
                diversity_scores=data.get("diversity_scores", {}),
                parameters=SearchParameters(
                    engines=data.get("parameters", {}).get("engines"),
                    time_range=data.get("parameters", {}).get("time_range"),
                    site=data.get("parameters", {}).get("site"),
                    ttl_override=data.get("parameters", {}).get("ttl_override"),
                )
                if data.get("parameters")
                else None,
                cache_stale=cache_stale,
            )
        return None

    async def _set_in_cache(self, cache_key: str, response: SearchResponse) -> None:
        """Set results in Redis cache with adaptive TTL (AC 7, AC 10)."""
        # Determine TTL: override > adaptive > default (CONTENT_CACHE_TTL for content)
        ttl = self.settings.CONTENT_CACHE_TTL

        if response.judgment and response.judgment.freshness_score is not None:
            adaptive_ttl = self.compute_adaptive_ttl(response.judgment.freshness_score)
            ttl = adaptive_ttl

        # Apply TTL override if enabled (AC 10)
        if (
            self.settings.TTL_OVERRIDE_ENABLED
            and response.parameters
            and response.parameters.ttl_override is not None
        ):
            ttl = response.parameters.ttl_override

        # Record metrics (AC 16)
        bucket = "high" if ttl >= 86400 else "medium" if ttl >= 21600 else "low"
        record_cache_ttl(bucket=bucket, ttl_seconds=float(ttl))

        await self.redis.client.set(
            f"isearch:{cache_key}",
            response.model_dump_json(),
            ex=ttl,
        )
