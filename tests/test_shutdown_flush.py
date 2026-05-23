"""Shutdown flush tests for MCP authorization system.

Verifies that flush_counters_to_db correctly syncs Redis counters
to SQLite DB, and that the on_shutdown lifespan handler flushes
both rate-limit and token-cost counters.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

# Import app.main at module level so patch("app.main.logger") can resolve targets
import app.main  # noqa: F401


class TestFlushCountersToDb:
    """Tests for flush_counters_to_db across all scenarios.

    Note: flush_counters_to_db uses asyncio.to_thread internally —
    sync/async distinction is artificial; all tests are async.
    """

    @pytest.mark.asyncio
    @patch("app.core.rate_limiter._get_pool")
    @patch("app.core.rate_limiter._redis_available", new=True)
    async def test_flush_counters_to_db_syncs_redis_counters_to_db(
        self, mock_get_pool: Mock
    ) -> None:
        """flush_counters_to_db should sync Redis rl:* keys to DB."""
        from app.core.rate_limiter import flush_counters_to_db

        mock_pool = MagicMock()
        mock_get_pool.return_value = mock_pool

        mock_pool.scan.side_effect = [
            (0, ["rl:user1:daily", "rl:user2:weekly"]),
            (0, []),
        ]
        mock_pool.get.side_effect = lambda key: {
            "rl:user1:daily": "42",
            "rl:user2:weekly": "100",
        }.get(key)

        result = await flush_counters_to_db()

        assert result["synced"] == 2
        assert result["failed"] == 0
        mock_pool.scan.assert_called()

    @pytest.mark.asyncio
    @patch("app.core.rate_limiter._get_pool")
    @patch("app.core.rate_limiter._redis_available", new=False)
    async def test_flush_counters_to_db_skipped_when_redis_unavailable(
        self, mock_get_pool: Mock
    ) -> None:
        """flush_counters_to_db returns zero when Redis is unavailable."""
        from app.core.rate_limiter import flush_counters_to_db

        result = await flush_counters_to_db()
        assert result == {"synced": 0, "failed": 0}
        mock_get_pool.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.core.rate_limiter._get_pool")
    async def test_flush_counters_to_db_handles_redis_connection_error(
        self, mock_get_pool: Mock
    ) -> None:
        """flush_counters_to_db handles Redis connection errors gracefully."""
        from app.core.rate_limiter import flush_counters_to_db

        import redis

        mock_get_pool.side_effect = redis.ConnectionError("connection refused")

        result = await flush_counters_to_db()
        assert result == {"synced": 0, "failed": 0}

    @pytest.mark.asyncio
    @patch("app.core.rate_limiter._get_pool")
    @patch("app.core.rate_limiter._redis_available", new=True)
    async def test_flush_counters_to_db_with_single_key(
        self, mock_get_pool: Mock
    ) -> None:
        """flush_counters_to_db handles single rl:* key correctly."""
        from app.core.rate_limiter import flush_counters_to_db

        mock_pool = MagicMock()
        mock_get_pool.return_value = mock_pool

        mock_pool.scan.side_effect = [
            (0, ["rl:user3:monthly"]),
            (0, []),
        ]
        mock_pool.get.return_value = "500"

        result = await flush_counters_to_db()

        assert result["synced"] == 1
        assert result["failed"] == 0


class TestOnShutdownLifespanHandler:
    """Tests for the on_shutdown lifespan handler in main.py."""

    @pytest.fixture
    def mock_server(self) -> MagicMock:
        """Mock FastMCP server instance."""
        return MagicMock()

    @pytest.mark.asyncio
    @patch("app.main.logger")
    @patch("app.core.token_cost_tracker.flush_counters_to_db")
    @patch("app.core.rate_limiter.flush_counters_to_db")
    async def test_on_shutdown_flushes_both_rate_and_token_counters(
        self,
        mock_rl_flush: Mock,
        mock_tc_flush: Mock,
        mock_logger: Mock,
        mock_server: MagicMock,
    ) -> None:
        """on_shutdown should flush both rate-limit and token-cost counters."""
        from app.main import on_shutdown

        mock_rl_flush.return_value = {"synced": 5, "failed": 0}
        mock_tc_flush.return_value = {"synced": 3, "failed": 1}

        await on_shutdown(mock_server)

        mock_rl_flush.assert_called_once()
        mock_tc_flush.assert_called_once()
        mock_logger.info.assert_called()

    @pytest.mark.asyncio
    @patch("app.main.logger")
    @patch("app.core.token_cost_tracker.flush_counters_to_db")
    @patch("app.core.rate_limiter.flush_counters_to_db")
    async def test_on_shutdown_handles_rate_limit_flush_error(
        self,
        mock_rl_flush: Mock,
        mock_tc_flush: Mock,
        mock_logger: Mock,
        mock_server: MagicMock,
    ) -> None:
        """on_shutdown should log error but continue if rate-limit flush fails."""
        from app.main import on_shutdown

        mock_rl_flush.side_effect = RuntimeError("flush failed")
        mock_tc_flush.return_value = {"synced": 3, "failed": 0}

        await on_shutdown(mock_server)

        mock_logger.error.assert_called()
        mock_tc_flush.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.main.logger")
    @patch("app.core.token_cost_tracker.flush_counters_to_db")
    @patch("app.core.rate_limiter.flush_counters_to_db")
    async def test_on_shutdown_handles_token_flush_error(
        self,
        mock_rl_flush: Mock,
        mock_tc_flush: Mock,
        mock_logger: Mock,
        mock_server: MagicMock,
    ) -> None:
        """on_shutdown should log error but continue if token flush fails."""
        from app.main import on_shutdown

        mock_rl_flush.return_value = {"synced": 5, "failed": 0}
        mock_tc_flush.side_effect = RuntimeError("token flush failed")

        await on_shutdown(mock_server)

        mock_logger.error.assert_called()
        mock_rl_flush.assert_called_once()


class TestMockRedisAndSQLite:
    """Tests verifying mock infrastructure works correctly."""

    @pytest.mark.asyncio
    @patch("app.core.rate_limiter._get_pool")
    @patch("app.core.rate_limiter._redis_available", new=True)
    async def test_mock_redis_scan_returns_empty_keys(
        self, mock_get_pool: Mock
    ) -> None:
        """When Redis has no rl:* keys, flush returns zero synced."""
        from app.core.rate_limiter import flush_counters_to_db

        mock_pool = MagicMock()
        mock_get_pool.return_value = mock_pool
        mock_pool.scan.side_effect = [(0, []), (0, [])]

        result = await flush_counters_to_db()
        assert result == {"synced": 0, "failed": 0}

    @pytest.mark.asyncio
    @patch("app.core.token_cost_tracker._get_pool")
    @patch("app.core.token_cost_tracker._redis_available", new=True)
    async def test_mock_token_flush_with_tc_keys(
        self, mock_get_pool: Mock
    ) -> None:
        """Token flush should handle tc:* keys correctly."""
        from app.core.token_cost_tracker import flush_counters_to_db

        mock_pool = MagicMock()
        mock_get_pool.return_value = mock_pool

        mock_pool.scan.side_effect = [
            (0, ["tc:user1:daily:input", "tc:user1:daily:output"]),
            (0, []),
        ]
        mock_pool.get.side_effect = lambda key: {
            "tc:user1:daily:input": "1000",
            "tc:user1:daily:output": "500",
        }.get(key)

        result = await flush_counters_to_db()
        assert result["synced"] == 0
        assert result["failed"] == 0
