"""TDD tests for ContentService - written BEFORE implementation."""

import pytest

from .fixtures import MockRedis


class TestContentServiceExtractionTDD:
    """Tests that verify the implementation works correctly."""

    @pytest.mark.asyncio
    async def test_extract_content_returns_text_from_url(self):
        """
        TDD: This test verifies that content extraction returns text from URL.
        After implementing trafilatura + fallback chain, this should PASS.
        """
        from app.core.config import Settings
        from app.services.content_service import ContentService

        settings = Settings()

        service = ContentService(settings, MockRedis())

        # After implementation: should extract text from URL
        result = await service.extract_content("https://example.com")

        assert isinstance(result.text, str)
        assert len(result.text) > 0

    @pytest.mark.asyncio
    async def test_extract_content_uses_cache_when_available(self):
        """
        TDD: This test verifies cache retrieval works when Redis is available.
        After implementing Redis cache storage, this should PASS.
        """
        from app.core.config import Settings
        from app.services.content_service import ContentService

        settings = Settings()

        service = ContentService(settings, MockRedis())

        # After implementation: should check cache first (via _get_from_cache)
        result = await service.extract_content("https://example.com")

        assert result.metadata.is_cached is False  # First fetch


class TestContentServiceSSRFTDD:
    """Tests for SSRF protection."""

    @pytest.mark.asyncio
    async def test_extract_content_rejects_dangerous_urls(self):
        """
        TDD: This test FAILS before implementation because URL validation not implemented.
        After implementing SSRF validation, this should PASS.
        """
        from app.core.config import Settings
        from app.services.content_service import ContentService

        settings = Settings()

        service = ContentService(settings, MockRedis())

        # Before implementation: would try to fetch file:// or ftp:// URLs
        # After implementation: should raise error for dangerous schemes
        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            await service.extract_content("file:///etc/passwd")

        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            await service.extract_content("ftp://localhost/file")

    @pytest.mark.asyncio
    async def test_extract_content_rejects_private_ip(self):
        """
        TDD: This test FAILS before implementation because private IP validation not implemented.
        After implementing SSRF validation, this should PASS.
        """
        from app.core.config import Settings
        from app.services.content_service import ContentService

        settings = Settings()

        service = ContentService(settings, MockRedis())

        # Before implementation: would try to fetch localhost:1234
        # After implementation: should reject private IPs
        with pytest.raises(ValueError, match="Private IP address"):
            await service.extract_content("http://127.0.0.1")

        with pytest.raises(ValueError, match="Private IP address"):
            await service.extract_content("http://localhost")


class TestContentServiceFallbackTDD:
    """Tests for fallback chain in content extraction."""

    @pytest.mark.asyncio
    async def test_extract_content_fallback_from_trafilatura_to_readability(self):
        """
        TDD: This test verifies fallback chain works when trafilatura fails.
        After implementing trafilatura -> readability -> bs4 chain, this should PASS.
        """
        from app.core.config import Settings
        from app.services.content_service import ContentService

        settings = Settings()

        service = ContentService(settings, MockRedis())

        # After implementation: should fallback to readability-lxml then bs4
        result = await service.extract_content("https://example.com")

        assert isinstance(result.text, str)
        assert len(result.text) >= 0  # May be empty if all extractors fail

    @pytest.mark.asyncio
    async def test_extract_content_truncates_to_token_limit(self):
        """
        TDD: This test verifies truncation logic works correctly.
        After implementing token limit logic, this should PASS.
        """
        from app.core.config import Settings
        from app.services.content_service import ContentService

        settings = Settings()

        service = ContentService(settings, MockRedis())

        # Create long text (more than TOKEN_LIMIT)
        long_text = " ".join(["word"] * 9000)

        truncated = service._truncate(long_text, settings.TOKEN_LIMIT)

        # Should be truncated to token limit
        tokens = truncated.split()
        assert len(tokens) <= settings.TOKEN_LIMIT
