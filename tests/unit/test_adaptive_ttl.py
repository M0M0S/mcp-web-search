"""Unit tests for Adaptive TTL caching (Epic 4).

Tests cover:
- AC 5: SearchResultJudge.freshness_score
- AC 6-10: Adaptive TTL mapping, config, override
- AC 11-15: CacheFreshnessChecker
- AC 16-20: Prometheus metrics
"""

import os

import pytest


class TestFreshnessScoreModel:
    """Tests for AC 5: SearchResultJudge.freshness_score field."""

    def test_search_result_judge_has_freshness_score(self):
        """SearchResultJudge includes freshness_score field."""
        from app.models.search import SearchResultJudge

        judge = SearchResultJudge(
            diversity_score=0.8,
            trustworthiness_score=0.8,
            relevance_to_query=0.85,
            freshness_score=0.75,
        )
        assert judge.freshness_score == 0.75

    def test_search_result_judge_freshness_default(self):
        """SearchResultJudge freshness_score defaults to 0.75."""
        from app.models.search import SearchResultJudge

        judge = SearchResultJudge(
            diversity_score=0.8,
            trustworthiness_score=0.8,
            relevance_to_query=0.85,
        )
        assert judge.freshness_score == 0.75

    def test_search_result_judge_freshness_validation(self):
        """freshness_score must be in [0.0, 1.0]."""
        from app.models.search import SearchResultJudge

        with pytest.raises(ValueError):
            SearchResultJudge(
                diversity_score=0.8,
                trustworthiness_score=0.8,
                relevance_to_query=0.85,
                freshness_score=1.5,
            )

        with pytest.raises(ValueError):
            SearchResultJudge(
                diversity_score=0.8,
                trustworthiness_score=0.8,
                relevance_to_query=0.85,
                freshness_score=-0.1,
            )

    def test_search_result_has_freshness_score(self):
        """SearchResult includes freshness_score field."""
        from app.models.search import SearchResult

        result = SearchResult(url="https://example.com", title="Test")
        assert result.freshness_score == 0.75

    def test_search_result_has_cache_stale(self):
        """SearchResult includes cache_stale field."""
        from app.models.search import SearchResult

        result = SearchResult(url="https://example.com", title="Test")
        assert result.cache_stale is False

    def test_search_response_has_cache_stale(self):
        """SearchResponse includes cache_stale field."""
        from app.models.search import SearchResponse, SearchResult

        response = SearchResponse(
            results=[SearchResult(url="https://example.com", title="Test")],
            provider="duck",
            total_found=1,
        )
        assert response.cache_stale is False

    def test_from_judge_verdict_includes_freshness(self):
        """SearchResultJudge.from_judge_verdict maps freshness_score."""
        from app.models.judge import JudgeVerdict
        from app.models.search import SearchResultJudge

        verdict = JudgeVerdict(
            score=0.85,
            diversity_score=0.8,
            trustworthiness_score=0.8,
            relevance_to_query=0.85,
            freshness_score=0.6,
            verdict="pass",
            reasons=["fresh content"],
        )

        judge = SearchResultJudge.from_judge_verdict(verdict)
        assert judge.freshness_score == 0.6


class TestAdaptiveTTLConfig:
    """Tests for AC 9: TTL configurable via Settings."""

    def test_adaptive_ttl_ranges_default(self):
        """ADAPTIVE_TTL_RANGES has correct default ranges."""
        from app.core.config import Settings

        settings = Settings()
        ranges = settings.ADAPTIVE_TTL_RANGES

        assert "high" in ranges
        assert ranges["high"] == (0.8, 86400)  # freshness > 0.8 → 24h
        assert "medium" in ranges
        assert ranges["medium"] == (0.5, 21600)  # 0.5 ≤ freshness ≤ 0.8 → 6h
        assert "low" in ranges
        assert ranges["low"] == (0.0, 3600)  # freshness < 0.5 → 1h

    def test_freshness_invalidation_threshold_default(self):
        """FRESHNESS_INVALIDATION_THRESHOLD defaults to 0.3."""
        from app.core.config import Settings

        settings = Settings()
        assert settings.FRESHNESS_INVALIDATION_THRESHOLD == 0.3

    def test_cache_invalidation_interval_default(self):
        """CACHE_INVALIDATION_INTERVAL defaults to 3600."""
        from app.core.config import Settings

        settings = Settings()
        assert settings.CACHE_INVALIDATION_INTERVAL == 3600

    def test_ttl_override_enabled_default(self):
        """TTL_OVERRIDE_ENABLED defaults to True."""
        from app.core.config import Settings

        settings = Settings()
        assert settings.TTL_OVERRIDE_ENABLED is True


class TestAdaptiveTTLMapping:
    """Tests for AC 6: TTL mapping from freshness_score."""

    def test_high_freshness_ttl(self):
        """freshness >= 0.8 → 24h (86400s)."""

        from app.core.config import Settings
        from app.services.search_service import SearchService

        original = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "PLACEHOLDER_KEY"
        try:
            settings = Settings()
            service = SearchService(settings, type("MockRedis", (), {}))

            ttl = service.compute_adaptive_ttl(0.9)
            assert ttl == 86400
        finally:
            if original is not None:
                os.environ["LLM_API_KEY"] = original
            else:
                os.environ.pop("LLM_API_KEY", None)

    def test_high_freshness_boundary(self):
        """freshness == 0.8 → 24h (boundary)."""

        from app.core.config import Settings
        from app.services.search_service import SearchService

        original = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "PLACEHOLDER_KEY"
        try:
            settings = Settings()
            service = SearchService(settings, type("MockRedis", (), {}))

            ttl = service.compute_adaptive_ttl(0.8)
            assert ttl == 86400
        finally:
            if original is not None:
                os.environ["LLM_API_KEY"] = original
            else:
                os.environ.pop("LLM_API_KEY", None)

    def test_medium_freshness_ttl(self):
        """freshness >= 0.5 and < 0.8 → 6h (21600s)."""

        from app.core.config import Settings
        from app.services.search_service import SearchService

        original = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "PLACEHOLDER_KEY"
        try:
            settings = Settings()
            service = SearchService(settings, type("MockRedis", (), {}))

            ttl = service.compute_adaptive_ttl(0.6)
            assert ttl == 21600
        finally:
            if original is not None:
                os.environ["LLM_API_KEY"] = original
            else:
                os.environ.pop("LLM_API_KEY", None)

    def test_medium_freshness_boundary(self):
        """freshness == 0.5 → 6h (boundary)."""

        from app.core.config import Settings
        from app.services.search_service import SearchService

        original = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "PLACEHOLDER_KEY"
        try:
            settings = Settings()
            service = SearchService(settings, type("MockRedis", (), {}))

            ttl = service.compute_adaptive_ttl(0.5)
            assert ttl == 21600
        finally:
            if original is not None:
                os.environ["LLM_API_KEY"] = original
            else:
                os.environ.pop("LLM_API_KEY", None)

    def test_low_freshness_ttl(self):
        """freshness < 0.5 → 1h (3600s)."""

        from app.core.config import Settings
        from app.services.search_service import SearchService

        original = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "PLACEHOLDER_KEY"
        try:
            settings = Settings()
            service = SearchService(settings, type("MockRedis", (), {}))

            ttl = service.compute_adaptive_ttl(0.3)
            assert ttl == 3600
        finally:
            if original is not None:
                os.environ["LLM_API_KEY"] = original
            else:
                os.environ.pop("LLM_API_KEY", None)

    def test_low_freshness_zero(self):
        """freshness == 0.0 → 1h (3600s)."""

        from app.core.config import Settings
        from app.services.search_service import SearchService

        original = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "PLACEHOLDER_KEY"
        try:
            settings = Settings()
            service = SearchService(settings, type("MockRedis", (), {}))

            ttl = service.compute_adaptive_ttl(0.0)
            assert ttl == 3600
        finally:
            if original is not None:
                os.environ["LLM_API_KEY"] = original
            else:
                os.environ.pop("LLM_API_KEY", None)

    def test_custom_adaptive_ttl_ranges(self):
        """Custom ADAPTIVE_TTL_RANGES are respected."""

        from app.core.config import Settings
        from app.services.search_service import SearchService

        original = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "PLACEHOLDER_KEY"
        try:
            settings = Settings(
                ADAPTIVE_TTL_RANGES={
                    "high": (0.9, 43200),  # 12h
                    "medium": (0.6, 21600),  # 6h
                    "low": (0.0, 1800),  # 30m
                }
            )
            service = SearchService(settings, type("MockRedis", (), {}))

            assert service.compute_adaptive_ttl(0.95) == 43200
            assert service.compute_adaptive_ttl(0.7) == 21600
            assert service.compute_adaptive_ttl(0.4) == 1800
        finally:
            if original is not None:
                os.environ["LLM_API_KEY"] = original
            else:
                os.environ.pop("LLM_API_KEY", None)


class TestTTLOverride:
    """Tests for AC 10: TTL override via request parameter."""

    def test_ttl_override_in_search_request(self):
        """SearchRequest supports ttl_override parameter."""
        from app.models.search import SearchRequest

        request = SearchRequest(query="test", ttl_override=7200)
        assert request.ttl_override == 7200

    def test_ttl_override_validation(self):
        """ttl_override must be in [60, 86400]."""
        from app.models.search import SearchRequest

        with pytest.raises(ValueError):
            SearchRequest(query="test", ttl_override=30)

        with pytest.raises(ValueError):
            SearchRequest(query="test", ttl_override=100000)

    def test_ttl_override_none_default(self):
        """ttl_override defaults to None."""
        from app.models.search import SearchRequest

        request = SearchRequest(query="test")
        assert request.ttl_override is None

    def test_ttl_override_in_search_parameters(self):
        """SearchParameters supports ttl_override."""
        from app.models.search import SearchParameters

        params = SearchParameters(engines="duck", ttl_override=3600)
        assert params.ttl_override == 3600


class TestCacheFreshnessChecker:
    """Tests for AC 11-15: CacheFreshnessChecker."""

    def test_checker_init(self):
        """CacheFreshnessChecker can be instantiated."""
        from app.core.config import Settings
        from app.services.cache_freshness_checker import CacheFreshnessChecker

        settings = Settings()

        class MockRedis:
            class Client:
                async def keys(self, pattern):
                    return []

                async def get(self, key):
                    return None

                async def delete(self, key):
                    return 0

            client = Client()

        checker = CacheFreshnessChecker(settings, MockRedis())
        assert checker.settings == settings
        assert checker._running is False

    def test_checker_threshold_from_settings(self):
        """Checker uses FRESHNESS_INVALIDATION_THRESHOLD from settings."""
        from app.core.config import Settings
        from app.services.cache_freshness_checker import CacheFreshnessChecker

        settings = Settings(FRESHNESS_INVALIDATION_THRESHOLD=0.5)

        class MockRedis:
            class Client:
                async def keys(self, pattern):
                    return []

                async def get(self, key):
                    return None

                async def delete(self, key):
                    return 0

            client = Client()

        checker = CacheFreshnessChecker(settings, MockRedis())
        assert checker.settings.FRESHNESS_INVALIDATION_THRESHOLD == 0.5

    def test_checker_interval_from_settings(self):
        """Checker uses CACHE_INVALIDATION_INTERVAL from settings."""
        from app.core.config import Settings
        from app.services.cache_freshness_checker import CacheFreshnessChecker

        settings = Settings(CACHE_INVALIDATION_INTERVAL=7200)

        class MockRedis:
            class Client:
                async def keys(self, pattern):
                    return []

                async def get(self, key):
                    return None

                async def delete(self, key):
                    return 0

            client = Client()

        checker = CacheFreshnessChecker(settings, MockRedis())
        assert checker.settings.CACHE_INVALIDATION_INTERVAL == 7200


class TestCacheFreshnessCheckerAsync:
    """Async tests for AC 11-15: CacheFreshnessChecker._check_and_invalidate."""

    @pytest.fixture
    def mock_redis(self):
        """Provide a mock Redis client with async methods."""

        class MockRedisClient:
            def __init__(self):
                self._data: dict[bytes, bytes] = {}

            async def keys(self, pattern: str) -> list[bytes]:
                import re

                regex = pattern.replace("*", ".*")
                return [k for k in self._data.keys() if re.match(regex, k.decode())]

            async def get(self, key: bytes) -> bytes | None:
                return self._data.get(key)

            async def delete(self, key: bytes) -> int:
                if key in self._data:
                    del self._data[key]
                    return 1
                return 0

        class MockRedis:
            client = MockRedisClient()

        return MockRedis()

    @pytest.mark.asyncio
    async def test_check_and_invalidate_no_keys(self, mock_redis):
        """_check_and_invalidate returns 0 when no cache keys exist."""
        from app.core.config import Settings
        from app.services.cache_freshness_checker import CacheFreshnessChecker

        settings = Settings()
        checker = CacheFreshnessChecker(settings, mock_redis)

        result = await checker._check_and_invalidate(0.3)
        assert result == 0

    @pytest.mark.asyncio
    async def test_check_and_invalidate_search_cache_fresh(self, mock_redis):
        """Fresh search cache entries are NOT invalidated."""
        import json

        from app.core.config import Settings
        from app.services.cache_freshness_checker import CacheFreshnessChecker

        settings = Settings(FRESHNESS_INVALIDATION_THRESHOLD=0.3)
        checker = CacheFreshnessChecker(settings, mock_redis)

        # Insert fresh entry (freshness > threshold)
        fresh_data = json.dumps(
            {
                "results": [],
                "provider": "duck",
                "judgment": {"freshness_score": 0.8},
            }
        ).encode("utf-8")
        mock_redis.client._data[b"isearch:test-key-1"] = fresh_data

        result = await checker._check_and_invalidate(0.3)
        assert result == 0
        # Key should still exist
        assert await mock_redis.client.get(b"isearch:test-key-1") is not None

    @pytest.mark.asyncio
    async def test_check_and_invalidate_search_cache_stale(self, mock_redis):
        """Stale search cache entries ARE invalidated."""
        import json

        from app.core.config import Settings
        from app.services.cache_freshness_checker import CacheFreshnessChecker

        settings = Settings(FRESHNESS_INVALIDATION_THRESHOLD=0.3)
        checker = CacheFreshnessChecker(settings, mock_redis)

        # Insert stale entry (freshness < threshold)
        stale_data = json.dumps(
            {
                "results": [],
                "provider": "duck",
                "judgment": {"freshness_score": 0.2},
            }
        ).encode("utf-8")
        mock_redis.client._data[b"isearch:test-key-stale"] = stale_data

        result = await checker._check_and_invalidate(0.3)
        assert result == 1
        # Key should be removed
        assert await mock_redis.client.get(b"isearch:test-key-stale") is None

    @pytest.mark.asyncio
    async def test_check_and_invalidate_content_cache_stale(self, mock_redis):
        """Stale content cache entries ARE invalidated."""
        import json

        from app.core.config import Settings
        from app.services.cache_freshness_checker import CacheFreshnessChecker

        settings = Settings(FRESHNESS_INVALIDATION_THRESHOLD=0.3)
        checker = CacheFreshnessChecker(settings, mock_redis)

        # Insert stale content entry (freshness < threshold)
        stale_data = json.dumps(
            {
                "content": "some text",
                "metadata": {"freshness_score": 0.15},
            }
        ).encode("utf-8")
        mock_redis.client._data[b"content:test-url"] = stale_data

        result = await checker._check_and_invalidate(0.3)
        assert result == 1
        assert await mock_redis.client.get(b"content:test-url") is None

    @pytest.mark.asyncio
    async def test_check_and_invalidate_content_cache_fresh(self, mock_redis):
        """Fresh content cache entries are NOT invalidated."""
        import json

        from app.core.config import Settings
        from app.services.cache_freshness_checker import CacheFreshnessChecker

        settings = Settings(FRESHNESS_INVALIDATION_THRESHOLD=0.3)
        checker = CacheFreshnessChecker(settings, mock_redis)

        # Insert fresh content entry (freshness > threshold)
        fresh_data = json.dumps(
            {
                "content": "some text",
                "metadata": {"freshness_score": 0.9},
            }
        ).encode("utf-8")
        mock_redis.client._data[b"content:test-url-fresh"] = fresh_data

        result = await checker._check_and_invalidate(0.3)
        assert result == 0
        assert await mock_redis.client.get(b"content:test-url-fresh") is not None

    @pytest.mark.asyncio
    async def test_check_and_invalidate_mixed_entries(self, mock_redis):
        """Mixed fresh/stale entries: only stale invalidated."""
        import json

        from app.core.config import Settings
        from app.services.cache_freshness_checker import CacheFreshnessChecker

        settings = Settings(FRESHNESS_INVALIDATION_THRESHOLD=0.3)
        checker = CacheFreshnessChecker(settings, mock_redis)

        # Fresh search entry
        fresh_search = json.dumps({"judgment": {"freshness_score": 0.7}}).encode(
            "utf-8"
        )
        mock_redis.client._data[b"isearch:fresh-search"] = fresh_search

        # Stale search entry
        stale_search = json.dumps({"judgment": {"freshness_score": 0.2}}).encode(
            "utf-8"
        )
        mock_redis.client._data[b"isearch:stale-search"] = stale_search

        # Fresh content entry
        fresh_content = json.dumps({"metadata": {"freshness_score": 0.8}}).encode(
            "utf-8"
        )
        mock_redis.client._data[b"content:fresh-content"] = fresh_content

        # Stale content entry
        stale_content = json.dumps({"metadata": {"freshness_score": 0.1}}).encode(
            "utf-8"
        )
        mock_redis.client._data[b"content:stale-content"] = stale_content

        # Entry without judgment (should be skipped)
        no_judgment = json.dumps({"results": []}).encode("utf-8")
        mock_redis.client._data[b"isearch:no-judgment"] = no_judgment

        result = await checker._check_and_invalidate(0.3)
        assert result == 2  # only stale-search and stale-content invalidated

        # Verify remaining keys
        assert await mock_redis.client.get(b"isearch:fresh-search") is not None
        assert await mock_redis.client.get(b"content:fresh-content") is not None
        assert await mock_redis.client.get(b"isearch:no-judgment") is not None

    @pytest.mark.asyncio
    async def test_check_and_invalidate_boundary_threshold(self, mock_redis):
        """freshness == threshold is NOT invalidated (strict <)."""
        import json

        from app.core.config import Settings
        from app.services.cache_freshness_checker import CacheFreshnessChecker

        settings = Settings(FRESHNESS_INVALIDATION_THRESHOLD=0.5)
        checker = CacheFreshnessChecker(settings, mock_redis)

        # Entry with freshness exactly at threshold
        boundary_data = json.dumps({"judgment": {"freshness_score": 0.5}}).encode(
            "utf-8"
        )
        mock_redis.client._data[b"isearch:boundary"] = boundary_data

        result = await checker._check_and_invalidate(0.5)
        assert result == 0  # boundary should NOT be invalidated
        assert await mock_redis.client.get(b"isearch:boundary") is not None

    @pytest.mark.asyncio
    async def test_check_and_invalidate_invalid_json(self, mock_redis):
        """Invalid JSON entries are skipped without error."""
        from app.core.config import Settings
        from app.services.cache_freshness_checker import CacheFreshnessChecker

        settings = Settings()
        checker = CacheFreshnessChecker(settings, mock_redis)

        # Insert invalid JSON
        mock_redis.client._data[b"isearch:invalid-json"] = b"not valid json {{{"

        result = await checker._check_and_invalidate(0.3)
        assert result == 0  # no invalidations, no crash
        # Key should still exist (not deleted)
        assert await mock_redis.client.get(b"isearch:invalid-json") is not None


class TestAdaptiveTTLMetrics:
    """Tests for AC 16-20: Prometheus metrics for adaptive TTL."""

    def test_cache_ttl_distribution_metric_exists(self):
        """cache_ttl_distribution histogram exists."""
        from app.core.metrics import cache_ttl_distribution

        assert cache_ttl_distribution is not None
        assert cache_ttl_distribution._type == "histogram"

    def test_cache_stale_invalidations_metric_exists(self):
        """cache_stale_invalidations_total counter exists."""
        from app.core.metrics import cache_stale_invalidations_total

        assert cache_stale_invalidations_total is not None
        assert cache_stale_invalidations_total._type == "counter"
        assert "cache_type" in cache_stale_invalidations_total._labelnames

    def test_cache_freshness_avg_metric_exists(self):
        """cache_freshness_avg gauge exists."""
        from app.core.metrics import cache_freshness_avg

        assert cache_freshness_avg is not None
        assert cache_freshness_avg._type == "gauge"
        assert "cache_type" in cache_freshness_avg._labelnames

    def test_cache_hit_with_stale_metric_exists(self):
        """cache_hit_with_stale_total counter exists."""
        from app.core.metrics import cache_hit_with_stale_total

        assert cache_hit_with_stale_total is not None
        assert cache_hit_with_stale_total._type == "counter"
        assert "cache_type" in cache_hit_with_stale_total._labelnames

    def test_record_cache_ttl_function(self):
        """record_cache_ttl records TTL value."""
        from app.core.metrics import record_cache_ttl

        # Should not raise
        record_cache_ttl(bucket="high", ttl_seconds=86400.0)

    def test_record_cache_stale_invalidation_function(self):
        """record_cache_stale_invalidation records invalidation."""
        from app.core.metrics import record_cache_stale_invalidation

        # Should not raise
        record_cache_stale_invalidation(cache_type="search")

    def test_update_cache_freshness_avg_function(self):
        """update_cache_freshness_avg updates gauge."""
        from app.core.metrics import update_cache_freshness_avg

        # Should not raise
        update_cache_freshness_avg(cache_type="search", avg_freshness=0.75)

    def test_record_cache_hit_with_stale_function(self):
        """record_cache_hit_with_stale records stale hit."""
        from app.core.metrics import record_cache_hit_with_stale

        # Should not raise
        record_cache_hit_with_stale(cache_type="content")

    def test_get_metrics_bytes_includes_adaptive_metrics(self):
        """get_metrics_bytes includes adaptive TTL metrics in output."""
        from app.core.metrics import get_metrics_bytes

        metrics = get_metrics_bytes()
        text = metrics.decode("utf-8")

        assert "cache_ttl_distribution_seconds" in text
        assert "cache_stale_invalidations_total" in text
        assert "cache_freshness_avg" in text
        assert "cache_hit_with_stale_total" in text


class TestAdaptiveTTLRangesBoundsValidation:
    """Tests for AC 20: ADAPTIVE_TTL_RANGES ge/le bounds validation."""

    def test_default_ranges_pass_validation(self):
        """Default ADAPTIVE_TTL_RANGES values are within [60, 86400]."""
        from app.core.config import Settings

        settings = Settings()
        ranges = settings.ADAPTIVE_TTL_RANGES

        for key, (_, ttl) in ranges.items():
            assert 60 <= ttl <= 86400, f"{key}: ttl={ttl} out of bounds"

    def test_ttl_below_minimum_raises(self):
        """ttl_seconds < 60 raises ValueError."""
        from app.core.config import Settings

        with pytest.raises(ValueError, match="below minimum ge=60"):
            Settings(
                ADAPTIVE_TTL_RANGES={
                    "high": (0.8, 30),
                    "medium": (0.5, 21600),
                    "low": (0.0, 3600),
                }
            )

    def test_ttl_above_maximum_raises(self):
        """ttl_seconds > 86400 raises ValueError."""
        from app.core.config import Settings

        with pytest.raises(ValueError, match="above maximum le=86400"):
            Settings(
                ADAPTIVE_TTL_RANGES={
                    "high": (0.8, 172800),
                    "medium": (0.5, 21600),
                    "low": (0.0, 3600),
                }
            )

    def test_boundary_minimum_ttl_passes(self):
        """ttl_seconds == 60 passes validation."""
        from app.core.config import Settings

        settings = Settings(
            ADAPTIVE_TTL_RANGES={
                "high": (0.8, 86400),
                "medium": (0.5, 21600),
                "low": (0.0, 60),
            }
        )
        assert settings.ADAPTIVE_TTL_RANGES["low"][1] == 60

    def test_boundary_maximum_ttl_passes(self):
        """ttl_seconds == 86400 passes validation."""
        from app.core.config import Settings

        settings = Settings(
            ADAPTIVE_TTL_RANGES={
                "high": (0.8, 86400),
                "medium": (0.5, 86400),
                "low": (0.0, 3600),
            }
        )
        assert settings.ADAPTIVE_TTL_RANGES["medium"][1] == 86400

    def test_multiple_ranges_one_invalid_raises(self):
        """One invalid range among valid ones raises ValueError."""
        from app.core.config import Settings

        with pytest.raises(ValueError, match="below minimum ge=60"):
            Settings(
                ADAPTIVE_TTL_RANGES={
                    "high": (0.8, 86400),
                    "medium": (0.5, 30),
                    "low": (0.0, 3600),
                }
            )

    def test_all_ranges_invalid_raises(self):
        """All ranges invalid — still raises ValueError."""
        from app.core.config import Settings

        with pytest.raises(ValueError):
            Settings(
                ADAPTIVE_TTL_RANGES={
                    "high": (0.8, 10),
                    "medium": (0.5, 20),
                    "low": (0.0, 30),
                }
            )
