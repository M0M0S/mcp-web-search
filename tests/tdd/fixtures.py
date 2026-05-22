"""TDD fixtures - Mock services for testing without real dependencies."""


class MockRedis:
    """Mock Redis client for TDD tests. Returns None (cache miss) by default."""

    def __init__(self):
        self.client = self
        self._cache: dict[str, str] = {}

    async def get(self, key: str):
        # Return None for cache miss (now handled by in-memory cache)
        return self._cache.get(key)

    async def set(self, key: str, value: str, **kwargs):
        """Mock Redis set operation."""
        self._cache[key] = value


class MockSearchService:
    """Mock SearchService for TDD tests. Raises NotImplementedError on call."""

    async def search(self, request):
        raise NotImplementedError(
            "Search service not implemented yet — this is a Red-Green TDD test."
            "The test should FAIL before implementation (NotImplementedError) and PASS after."
        )


class MockContentService:
    """Mock ContentService for TDD tests. Raises NotImplementedError on call."""

    async def extract_content(self, url: str) -> None:
        raise NotImplementedError(
            "Content extraction service not implemented yet — this is a Red-Green TDD test."
            "The test should FAIL before implementation (NotImplementedError) and PASS after."
        )
