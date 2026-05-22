"""pytest configuration for async testing."""

import pytest

# Configure pytest-asyncio mode
pytest_plugins = ("pytest_asyncio",)


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for async tests."""
    import asyncio

    policy = asyncio.get_event_loop_policy()
    return policy.new_event_loop()


@pytest.fixture
def settings():
    """Get test settings."""
    from app.core.config import Settings

    return Settings()
