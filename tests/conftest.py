"""pytest configuration for async testing."""

import os
import tempfile
from typing import Generator

import pytest
from unittest.mock import MagicMock, patch

# Configure pytest-asyncio mode
pytest_plugins = ("pytest_asyncio",)


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for async tests."""
    import asyncio

    policy = asyncio.get_event_loop_policy()
    return policy.new_event_loop()


@pytest.fixture(scope="module")
def shared_db_path() -> Generator[str, None, None]:
    """Provide a shared temp-file SQLite path for all tests in this module."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    original = os.environ.get("KG_DB_PATH")
    os.environ["KG_DB_PATH"] = path
    yield path
    if original is not None:
        os.environ["KG_DB_PATH"] = original
    else:
        os.environ.pop("KG_DB_PATH", None)
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def shared_db(shared_db_path: str) -> sqlite3.Connection:
    """Return a single SQLite connection with schema created.

    Sets ``row_factory = None`` so queries return plain tuples (index-accessible).
    """
    from app.core.user_store import init_db

    conn: sqlite3.Connection = init_db()
    conn.row_factory = None  # ensure tuple results for index access
    return conn


@pytest.fixture(scope="module")
def mcp_encryption_key(shared_db_path: str) -> Generator[str, None, None]:
    """Generate a valid Fernet encryption key and set it in the environment."""
    from cryptography.fernet import Fernet

    key: str = Fernet.generate_key().decode()
    original = os.environ.get("MCP_ENCRYPTION_KEY")
    os.environ["MCP_ENCRYPTION_KEY"] = key
    yield key
    if original is not None:
        os.environ["MCP_ENCRYPTION_KEY"] = original
    else:
        os.environ.pop("MCP_ENCRYPTION_KEY", None)


@pytest.fixture(scope="module")
def mcp_encryption_key_backup(shared_db_path: str) -> Generator[str, None, None]:
    """Generate a separate valid Fernet backup key and set it in the environment."""
    from cryptography.fernet import Fernet

    key: str = Fernet.generate_key().decode()
    original = os.environ.get("MCP_ENCRYPTION_KEY_BACKUP")
    os.environ["MCP_ENCRYPTION_KEY_BACKUP"] = key
    yield key
    if original is not None:
        os.environ["MCP_ENCRYPTION_KEY_BACKUP"] = original
    else:
        os.environ.pop("MCP_ENCRYPTION_KEY_BACKUP", None)


@pytest.fixture
def settings(mcp_encryption_key: str):
    """Get test settings with MCP encryption key configured."""
    from app.core.config import Settings

    return Settings()


@pytest.fixture
def mock_redis_pool() -> Generator[MagicMock, None, None]:
    """Mock Redis pool providing get, incr, incrby, ping, delete, scan methods.

    Patches ``redis.from_url`` to return a MagicMock with configurable behavior.
    Default state: Redis is available and all operations succeed.

    Use this fixture in tests that need Redis operations without a real Redis
    server. Configure the mock via ``pool.get.return_value``, ``pool.incr.return_value``
    etc. before running test logic.
    """
    with patch("redis.from_url") as mock_from_url:
        pool: MagicMock = MagicMock()

        # Default: Redis available and working
        pool.get.return_value = None
        pool.incr.return_value = 1
        pool.incrby.return_value = 1
        pool.ping.return_value = True
        pool.delete.return_value = 1
        pool.scan.return_value = iter([(0, [])])
        pool.expire.return_value = True

        mock_from_url.return_value = pool
        yield pool


@pytest.fixture
def force_db_fallback_rate_limiter() -> Generator[None, None, None]:
    """Force rate_limiter to use DB fallback (Redis unavailable).

    Sets ``_redis_available = False`` in ``app.core.rate_limiter`` and
    patches ``redis.from_url`` so the module never attempts a real connection.
    """
    from app.core import rate_limiter

    with patch("redis.from_url"):
        rate_limiter._redis_available = False
        rate_limiter._redis_pool = None
        rate_limiter._redis_last_check = 0.0
        yield
    # Restore original state
    rate_limiter._redis_available = True
    rate_limiter._redis_pool = None
    rate_limiter._redis_last_check = 0.0


@pytest.fixture
def force_db_fallback_token_cost_tracker() -> Generator[None, None, None]:
    """Force token_cost_tracker to use DB fallback (Redis unavailable).

    Sets ``_redis_available = False`` in ``app.core.token_cost_tracker`` and
    patches ``redis.from_url`` so the module never attempts a real connection.
    """
    from app.core import token_cost_tracker

    with patch("redis.from_url"):
        token_cost_tracker._redis_available = False
        token_cost_tracker._redis_pool = None
        token_cost_tracker._redis_last_check = 0.0
        yield
    # Restore original state
    token_cost_tracker._redis_available = True
    token_cost_tracker._redis_pool = None
    token_cost_tracker._redis_last_check = 0.0
