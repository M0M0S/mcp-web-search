"""Unit tests for Pydantic models."""


class TestSearchRequest:
    """Tests for SearchRequest model."""

    def test_valid_request(self):
        """Test valid search request."""
        from app.models.search import SearchRequest

        request = SearchRequest(
            query="test query",
            max_results=20,
            region="us-en",
            language="en",
            filter_blacklist=True,
            calculate_quality=True,
            apply_smart_filter=True,
            auto_detect_language=False,
        )

        assert request.query == "test query"
        assert request.max_results == 20
        assert request.region == "us-en"

    def test_request_with_defaults(self):
        """Test request with default parameters."""
        from app.models.search import SearchRequest

        request = SearchRequest(query="test")

        assert request.max_results == 10
        assert request.region == "wt-wt"
        assert request.filter_blacklist is True


class TestSearchResult:
    """Tests for SearchResult model."""

    def test_search_result_creation(self):
        """Test SearchResult creation."""
        from app.models.search import SearchResult

        result = SearchResult(
            url="https://example.com",
            title="Test Title",
            description="Test description",
            provider="duck",
        )

        assert result.url == "https://example.com"
        assert result.title == "Test Title"


class TestQualityScore:
    """Tests for QualityScore model."""

    def test_quality_score_creation(self):
        """Test QualityScore creation."""
        from app.models.search import QualityScore

        score = QualityScore(
            overall=0.8,
            content_quality=0.9,
            seo_spam_score=0.1,
            clickbait_score=0.2,
        )

        assert 0 <= score.overall <= 1
        assert 0 <= score.content_quality <= 1


class TestContentMetadata:
    """Tests for ContentMetadata model."""

    def test_metadata_creation(self):
        """Test ContentMetadata is created correctly."""
        from app.models.content import ContentMetadata

        metadata = ContentMetadata(
            source_url="https://example.com",
            extract_method="trafilatura",
            token_count=100,
        )

        assert metadata.source_url == "https://example.com"
        assert metadata.extract_method == "trafilatura"

    def test_optional_language(self):
        """Test optional language field."""
        from app.models.content import ContentMetadata

        metadata = ContentMetadata(
            source_url="https://example.com",
        )

        assert metadata.language is None


class TestWebFetchState:
    """Tests for WebFetchState model."""

    def test_state_creation(self):
        """Test WebFetchState creation."""
        from app.models.webfetch import WebFetchState

        state = WebFetchState(
            prompt="test prompt",
            tenant_id="tenant-1",
            version="1.0",
        )

        assert state.prompt == "test prompt"
        assert state.tenant_id == "tenant-1"
        assert state.version == "1.0"

    def test_optional_fields(self):
        """Test optional fields in WebFetchState."""
        from app.models.webfetch import WebFetchState

        state = WebFetchState(
            prompt="test",
            tenant_id="tenant-1",
        )

        assert state.search_queries == []
        assert state.selected_urls == []
        assert state.fetched_content == []
        assert state.features is None
        assert state.url_judgment is None
        assert state.feature_judgment is None
        assert state.fallback_applied is False


class TestCacheKey:
    """Tests for CacheKey model."""

    def test_cache_key_generation(self):
        """Test cache key generation."""
        from app.models.cache import CacheKey

        key = CacheKey(
            prefix="isearch",
            resource_type="search",
            identifier="abc123",
            tenant_id="tenant-1",
        )

        assert key.generate_key() == "isearch:search:abc123:tenant-1"

    def test_cache_key_without_tenant(self):
        """Test cache key without tenant."""
        from app.models.cache import CacheKey

        key = CacheKey(
            prefix="isearch",
            resource_type="content",
            identifier="def456",
        )

        assert key.generate_key() == "isearch:content:def456"
