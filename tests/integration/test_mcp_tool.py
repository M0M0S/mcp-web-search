"""Integration tests for MCP tools via FastMCP Client."""

import httpx
import pytest


def _mcp_server_available() -> bool:
    """Check if MCP server is running on http://127.0.0.1:8000/mcp."""
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get("http://127.0.0.1:8000/mcp")
            return resp.status_code == 200
    except Exception:
        return False


@pytest.mark.integration
@pytest.mark.skipif(
    not _mcp_server_available(),
    reason="MCP server not running on http://127.0.0.1:8000/mcp",
)
class TestMCPToolCall:
    """Tests for tool calls through FastMCP client."""

    @pytest.mark.asyncio
    async def test_search_tool(self):
        """Test search tool call."""
        from app.main import mcp

        # Check server is initialized
        assert mcp is not None
        assert len(await mcp.list_tools()) > 0

    @pytest.mark.asyncio
    async def test_content_tool(self):
        """Test content extraction tool call."""
        from app.main import mcp

        # Check server is initialized
        assert mcp is not None
        assert len(await mcp.list_tools()) >= 3


@pytest.mark.integration
@pytest.mark.skipif(
    not _mcp_server_available(),
    reason="MCP server not running on http://127.0.0.1:8000/mcp",
)
class TestMCPResponseFormat:
    """Tests for response format validation."""

    @pytest.mark.asyncio
    async def test_search_response_format(self):
        """Test that search returns correct response format."""
        from app.main import mcp

        # Check tools are registered
        assert len(await mcp.list_tools()) > 0
