"""Tests for SSRF protection utilities."""

import pytest

from app.core.ssrf import SSRFProtection


class TestSSRFValidation:
    """Tests for URL validation in SSRFProtection."""

    def test_rejects_file_scheme(self):
        """Test that file:// URLs are rejected."""
        protection = SSRFProtection()

        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            protection._validate_url("file:///etc/passwd")

    def test_rejects_ftp_scheme(self):
        """Test that ftp:// URLs are rejected."""
        protection = SSRFProtection()

        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            protection._validate_url("ftp://example.com/file.txt")

    def test_rejects_tel_scheme(self):
        """Test that tel:// URLs are rejected."""
        protection = SSRFProtection()

        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            protection._validate_url("tel:+1234567890")

    def test_rejects_localhost(self):
        """Test that localhost is rejected."""
        protection = SSRFProtection()

        with pytest.raises(ValueError, match="Private IP address"):
            protection._validate_url("https://127.0.0.1")

    def test_rejects_localhost_ipv6(self):
        """Test that IPv6 localhost is rejected."""
        protection = SSRFProtection()

        with pytest.raises(ValueError, match="Private IP address"):
            protection._validate_url("https://[::1]")

    def test_rejects_private_ip_range_10(self):
        """Test that 10.x.x.x private IPs are rejected."""
        protection = SSRFProtection()

        with pytest.raises(ValueError, match="Private IP address"):
            protection._validate_url("https://10.0.0.1")

    def test_rejects_private_ip_range_172(self):
        """Test that 172.16.x.x private IPs are rejected."""
        protection = SSRFProtection()

        with pytest.raises(ValueError, match="Private IP address"):
            protection._validate_url("https://172.16.0.1")

    def test_rejects_private_ip_range_192(self):
        """Test that 192.168.x.x private IPs are rejected."""
        protection = SSRFProtection()

        with pytest.raises(ValueError, match="Private IP address"):
            protection._validate_url("https://192.168.1.1")

    def test_rejects_http_scheme(self):
        """Test that http:// scheme is rejected."""
        protection = SSRFProtection()

        with pytest.raises(ValueError, match="HTTP scheme not allowed"):
            protection._validate_url("http://example.com")

    def test_accepts_public_ip(self):
        """Test that public IPs are accepted."""
        protection = SSRFProtection()

        # Should not raise exception for public IP
        protection._validate_url("https://8.8.8.8")

    def test_accepts_domain_name(self):
        """Test that domain names are accepted."""
        protection = SSRFProtection()

        # Should not raise exception for valid domains
        protection._validate_url("https://example.com")
        protection._validate_url("https://www.google.com")

    def test_accepts_https_scheme(self):
        """Test that https:// scheme is accepted."""
        protection = SSRFProtection()

        # Should not raise exception for https
        protection._validate_url("https://example.com")


class TestSSRFFetchSync:
    """Tests for sync fetch operation."""

    def test_fetch_sync_with_valid_domain(self):
        """Test sync fetch with valid domain (mocked)."""
        from unittest.mock import MagicMock, patch

        protection = SSRFProtection()

        mock_response = MagicMock()
        mock_response.content = b"<html>test content</html>"

        with patch("httpx.Client") as mock_client:
            mock_instance = mock_client.return_value
            mock_instance.get.return_value = mock_response

            result = protection.fetch("https://example.com")

            assert result == b"<html>test content</html>"
            mock_instance.get.assert_called_once()

    def test_fetch_sync_with_file_scheme_raises_error(self):
        """Test sync fetch rejects file:// URLs."""
        protection = SSRFProtection()

        with pytest.raises(ValueError, match="Dangerous URL scheme"):
            protection.fetch("file:///etc/passwd")
