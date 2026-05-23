"""SSRF protection utilities."""

import asyncio
import ipaddress

import httpx


class SSRFConnectionError(Exception):
    """Exception raised when connection to URL fails."""

    pass


class SSRFProtection:
    """SSRF-safe URL validation and fetching."""

    async def fetch_async(self, url: str, **kwargs) -> bytes:
        """Async SSRF-safe fetch using httpx."""
        return await self._safe_fetch(url, is_async=True, **kwargs)

    def fetch(self, url: str, **kwargs) -> bytes:
        """Sync SSRF-safe fetch using httpx."""
        self._validate_url(url)

        client = httpx.Client()
        try:
            response = client.get(url, **kwargs)
        except httpx.ConnectTimeout as e:
            raise SSRFConnectionError(f"Connection timeout to {url}: {e}")
        except httpx.RequestError as e:
            raise SSRFConnectionError(f"Request failed for {url}: {e}")
        finally:
            client.close()

        return response.content

    async def _safe_fetch(self, url: str, is_async: bool = True, **kwargs) -> bytes:
        """Perform SSRF-safe fetch with validation."""
        self._validate_url(url)

        if is_async:
            # Set reasonable timeout for fetching

            async with httpx.AsyncClient(timeout=10.0) as client:
                try:
                    response = await client.get(url, **kwargs)
                    # Check for HTTP errors (4xx/5xx)
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    raise SSRFConnectionError(
                        f"HTTP error {e.response.status_code} for {url}"
                    )
                except httpx.ConnectTimeout as e:
                    raise SSRFConnectionError(f"Connection timeout to {url}: {e}")
                except httpx.RequestError as e:
                    raise SSRFConnectionError(f"Request failed for {url}: {e}")

                return response.content
        else:
            def _sync_fetch() -> bytes:
                local_client = httpx.Client()
                try:
                    response = local_client.get(url, **kwargs)
                    # Check for HTTP errors (4xx/5xx)
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    raise SSRFConnectionError(
                        f"HTTP error {e.response.status_code} for {url}"
                    )
                except httpx.ConnectTimeout as e:
                    raise SSRFConnectionError(f"Connection timeout to {url}: {e}")
                except httpx.RequestError as e:
                    raise SSRFConnectionError(f"Request failed for {url}: {e}")
                finally:
                    local_client.close()

                return response.content

            return await asyncio.to_thread(_sync_fetch)

    def _validate_url(self, url: str) -> None:
        """Validate URL to prevent SSRF attacks."""
        from urllib.parse import urlparse

        parsed = urlparse(url)

        # Require HTTPS — HTTP is not allowed
        if parsed.scheme == "http":
            raise ValueError("HTTP scheme not allowed — require HTTPS")

        # Check for dangerous schemes (non-HTTP)
        dangerous_schemes = ["file", "ftp", "tel", "gopher", "ldap"]
        if any(url.lower().startswith(scheme + ":") for scheme in dangerous_schemes):
            raise ValueError(f"Dangerous URL scheme: {url}")

        hostname = parsed.hostname

        # Check for localhost
        if hostname in ("localhost", "127.0.0.1"):
            raise ValueError("Private IP address not allowed")

        # Check for IPv6 localhost (::1) - parse directly from URL string
        # Note: urlparse doesn't handle ::1 without port, so check URL directly
        url_without_scheme = url.split("://", 1)[1] if "://" in url else url
        if "::1" in url_without_scheme:
            raise ValueError("Private IP address not allowed")

        if hostname:
            try:
                ip = ipaddress.ip_address(hostname)
            except (ValueError, TypeError):
                # Not an IP, it's a domain - OK
                pass
            else:
                # Check if it's a private IP
                if ip.is_private:
                    raise ValueError("Private IP address not allowed")


# Singleton instance
ssrf_protection = SSRFProtection()
