"""Tests for error handling and edge cases."""

import pytest

from app.core.ssrf import SSRFConnectionError, SSRFProtection


class TestSSRFEdgeCases:
    """Tests for SSRF protection edge cases."""

    def test_rejects_gopher_scheme(self):
        """Test that gopher:// URLs are rejected."""
        protection = SSRFProtection()

        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            protection._validate_url("gopher://example.com")

    def test_rejects_ldap_scheme(self):
        """Test that ldap:// URLs are rejected."""
        protection = SSRFProtection()

        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            protection._validate_url("ldap://directory.example.com")


class TestSSRFConnectionError:
    """Tests for SSRFConnectionError exception."""

    def test_error_message_contains_url(self):
        """Test that SSRFConnectionError message contains URL."""
        error = SSRFConnectionError("Connection timeout to https://example.com")

        assert "https://example.com" in str(error)
        assert "timeout" in str(error).lower()


class TestConfigEdgeCases:
    """Tests for configuration edge cases."""

    def test_default_settings_creation(self):
        """Test that default settings can be created."""
        from app.core.config import Settings

        settings = Settings()

        assert settings is not None
        assert settings.TOKEN_LIMIT > 0
