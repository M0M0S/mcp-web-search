"""Unit tests for SearchService - TDD approach."""


class TestSearchServiceInit:
    """Tests for SearchService initialization."""

    def test_service_creation(self):
        """Test SearchService can be instantiated."""
        import os

        from app.core.config import Settings
        from app.services.search_service import SearchService

        # Set LLM_API_KEY for create_llm_client factory
        original = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "PLACEHOLDER_KEY"
        try:
            settings = Settings()

            # Mock redis client
            class MockRedis:
                pass

            service = SearchService(settings, MockRedis())

            assert service is not None
        finally:
            if original is not None:
                os.environ["LLM_API_KEY"] = original
            else:
                os.environ.pop("LLM_API_KEY", None)


class TestSearchRequestValidation:
    """Tests for search request validation."""

    def test_request_with_valid_params(self):
        """Test valid search request parameters."""
        from app.models.search import SearchRequest

        # Test minimum valid request
        request = SearchRequest(query="test", max_results=1)
        assert request.query == "test"
        assert request.max_results == 1

    def test_request_with_default_params(self):
        """Test request with default parameters."""
        from app.models.search import SearchRequest

        request = SearchRequest(query="test")

        assert request.max_results == 10
        assert request.region == "wt-wt"
        assert request.filter_blacklist is True
        assert request.calculate_quality is True
        assert request.apply_smart_filter is True
        # auto_detect_language defaults to False (explicitly use DEFAULT_LANGUAGE)
        assert request.auto_detect_language is False


class TestSearchServiceFallbackChain:
    """Tests for fallback chain logic."""

    def test_fallback_chain_order(self):
        """Test that fallback chain follows correct order."""
        import os

        from app.core.config import Settings

        # Override env to get clean defaults
        original = os.environ.get("SEARCH_FALLBACK_CHAIN")
        os.environ["SEARCH_FALLBACK_CHAIN"] = '["duck", "searxng", "tavily", "google"]'
        try:
            settings = Settings()

            assert "duck" in settings.SEARCH_FALLBACK_CHAIN
            assert "tavily" in settings.SEARCH_FALLBACK_CHAIN
            assert "google" in settings.SEARCH_FALLBACK_CHAIN
        finally:
            if original is not None:
                os.environ["SEARCH_FALLBACK_CHAIN"] = original
            else:
                os.environ.pop("SEARCH_FALLBACK_CHAIN", None)


class TestSearchServiceCache:
    """Tests for cache key generation."""

    def test_cache_key_generation(self):
        """Test that cache keys are generated correctly."""
        import os

        from app.core.config import Settings
        from app.models.search import SearchRequest
        from app.services.search_service import SearchService

        original = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "PLACEHOLDER_KEY"
        try:
            settings = Settings()

            # Mock redis client
            class MockRedis:
                pass

            service = SearchService(settings, MockRedis())

            request = SearchRequest(
                query="test query",
                region="us-en",
                language="en",
            )

            cache_key = service._generate_cache_key(request)

            # Check that key format is correct and uses settings.CACHE_VERSION
            assert "search:" in cache_key
            assert f"v{settings.CACHE_VERSION}" in cache_key
        finally:
            if original is not None:
                os.environ["LLM_API_KEY"] = original
            else:
                os.environ.pop("LLM_API_KEY", None)

    def test_cache_version_from_settings(self):
        """Test that CACHE_VERSION is accessible via settings."""
        import os

        from app.core.config import Settings
        from app.services.search_service import SearchService

        original = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "PLACEHOLDER_KEY"
        try:
            settings = Settings()

            # Mock redis client
            class MockRedis:
                pass

            service = SearchService(settings, MockRedis())

            assert hasattr(service, "settings")
            assert hasattr(service.settings, "CACHE_VERSION")
            assert service.settings.CACHE_VERSION == 1
        finally:
            if original is not None:
                os.environ["LLM_API_KEY"] = original
            else:
                os.environ.pop("LLM_API_KEY", None)


class TestSearchServiceFiltering:
    """Tests for smart filtering logic."""

    def test_blacklist_detection(self):
        """Test blacklist domain detection."""
        import os

        from app.core.config import Settings
        from app.models.search import SearchResult
        from app.services.search_service import SearchService

        original = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "PLACEHOLDER_KEY"
        try:
            settings = Settings()

            class MockRedis:
                pass

            service = SearchService(settings, MockRedis())

            result = SearchResult(
                url="https://example.com",
                title="Test",
            )

            # Test blacklist detection
            is_blacklisted = service._is_blacklisted(result)

            assert is_blacklisted is True  # example.com is in default blacklist
        finally:
            if original is not None:
                os.environ["LLM_API_KEY"] = original
            else:
                os.environ.pop("LLM_API_KEY", None)


class TestSearchServiceQualityScore:
    """Tests for quality score calculation."""

    def test_quality_score_calculation(self):
        """Test quality score returns valid range."""
        import os

        from app.core.config import Settings
        from app.models.search import QualityScore, SearchResult
        from app.services.search_service import SearchService

        original = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "PLACEHOLDER_KEY"
        try:
            settings = Settings()

            class MockRedis:
                pass

            service = SearchService(settings, MockRedis())

            result = SearchResult(
                url="https://example.com",
                title="Test",
            )

            # Test score is in valid range
            score = service._calculate_quality_score(result)

            assert isinstance(score, QualityScore)
            assert 0 <= score.overall <= 1
        finally:
            if original is not None:
                os.environ["LLM_API_KEY"] = original
            else:
                os.environ.pop("LLM_API_KEY", None)
