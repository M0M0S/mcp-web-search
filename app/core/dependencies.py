"""Dependency injection and authentication setup."""

from typing import Optional

import redis.asyncio as aioredis


class RedisClient:
    """Redis client wrapper for cache operations."""

    def __init__(self, url: str = "redis://localhost:6379/0"):
        self._client: Optional[aioredis.Redis] = None
        self.url = url

    async def connect(self) -> None:
        """Initialize Redis connection with connection pool."""
        # Use connection pool with max 20 connections to prevent connection contention
        self._client = aioredis.from_url(
            self.url,
            max_connections=20,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )

    async def close(self) -> None:
        """Close Redis connection."""
        if self._client:
            await self._client.close()

    @property
    def client(self) -> aioredis.Redis:
        """Return Redis client instance."""
        if not self._client:
            raise RuntimeError("Redis client not initialized. Call connect() first.")
        return self._client


_redis_client: Optional[RedisClient] = None


def get_redis(url: str = "redis://localhost:6379/0") -> RedisClient:
    """Get Redis client instance (singleton)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient(url)
    return _redis_client


async def init_redis(settings) -> RedisClient:
    """Initialize Redis client with settings."""
    client = get_redis(settings.REDIS_URL)
    await client.connect()
    return client


class PlaceholderAuthClient:
    """Placeholder for auth provider - should be fastmcp.server.auth.RemoteAuthProvider."""

    pass


def default_auth_provider():
    """Return authentication provider."""
    # Will be set up in main.py with FastMCP's built-in auth
    return None
