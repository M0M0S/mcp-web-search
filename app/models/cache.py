"""Cache-related Pydantic models."""

from typing import Optional

from pydantic import BaseModel


class CacheKey(BaseModel):
    """Cache key structure."""

    prefix: str = "isearch"
    resource_type: str  # search, content, webfetch
    identifier: str  # query hash or URL hash
    tenant_id: Optional[str] = None

    def generate_key(self) -> str:
        """Generate Redis cache key."""
        if self.tenant_id:
            return (
                f"{self.prefix}:{self.resource_type}:{self.identifier}:{self.tenant_id}"
            )
        return f"{self.prefix}:{self.resource_type}:{self.identifier}"


class CacheMetadata(BaseModel):
    """Cache metadata for search results."""

    provider: str
    timestamp: str
    ttl: int
    cache_hit: bool = False
