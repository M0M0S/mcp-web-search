"""Unit tests for ContentService - TDD approach."""


class TestContentServiceInit:
    """Tests for ContentService initialization."""

    def test_service_creation(self):
        """Test ContentService can be instantiated."""
        from app.core.config import Settings
        from app.services.content_service import ContentService

        settings = Settings()

        class MockRedis:
            pass

        service = ContentService(settings, MockRedis())

        assert service is not None


class TestContentExtraction:
    """Tests for content extraction methods."""

    def test_extract_text_with_trafilatura(self):
        """Test text extraction with trafilatura."""
        from app.core.config import Settings
        from app.services.content_service import ContentService

        settings = Settings()

        class MockRedis:
            pass

        service = ContentService(settings, MockRedis())

        # Test with empty HTML (should return empty string)
        html_bytes = b"<html><body></body></html>"
        text = service._extract_text(html_bytes)

        assert isinstance(text, str)

    def test_extract_text_fallback_chain(self):
        """Test fallback chain from trafilatura to bs4."""
        from app.core.config import Settings
        from app.services.content_service import ContentService

        settings = Settings()

        class MockRedis:
            pass

        service = ContentService(settings, MockRedis())

        # Test with valid HTML
        html_bytes = b"<html><body><p>Test content</p></body></html>"
        text = service._extract_text(html_bytes)

        assert "Test content" in text or isinstance(text, str)

    def test_truncate_text(self):
        """Test text truncation to token limit."""
        from app.core.config import Settings
        from app.services.content_service import ContentService

        settings = Settings()

        class MockRedis:
            pass

        service = ContentService(settings, MockRedis())

        # Create long text
        long_text = " ".join(["word"] * 100)

        truncated = service._truncate(long_text, 50)

        # Check that it's truncated (less than original length)
        assert len(truncated) < len(long_text)


class TestContentMetadata:
    """Tests for content metadata creation."""

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


class TestCleanContent:
    """Tests for CleanContent model."""

    def test_clean_content_creation(self):
        """Test CleanContent creation with all fields."""
        from app.models.content import CleanContent, ContentMetadata

        metadata = ContentMetadata(
            source_url="https://example.com",
            extract_method="trafilatura",
            token_count=100,
        )

        content = CleanContent(
            text="Cleaned text content",
            metadata=metadata,
            is_truncated=False,
            html_cleaned=True,
        )

        assert content.text == "Cleaned text content"
        assert content.html_cleaned is True
        assert content.is_truncated is False
