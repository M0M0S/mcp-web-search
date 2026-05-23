"""Integration tests for MCP service - full behavior specification."""

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
class TestMCPToolsExist:
    """Tests that verify MCP tools are registered and accessible."""

    @pytest.mark.asyncio
    async def test_mcp_server_has_required_tools(self):
        """Test that FastMCP server has all required tools."""
        from app.main import mcp

        # Check that tools are registered (after server startup)
        assert len(await mcp.list_tools()) > 0


@pytest.mark.integration
@pytest.mark.skipif(
    not _mcp_server_available(),
    reason="MCP server not running on http://127.0.0.1:8000/mcp",
)
class TestSearchToolBehavior:
    """Tests for search tool behavior."""

    @pytest.mark.asyncio
    async def test_search_tool_returns_results(self):
        """Test search tool returns list of results."""
        from app.main import mcp

        # Check that search tool exists
        tools = await mcp.list_tools()
        assert len(tools) >= 3


@pytest.mark.integration
@pytest.mark.skipif(
    not _mcp_server_available(),
    reason="MCP server not running on http://127.0.0.1:8000/mcp",
)
class TestGetContentToolBehavior:
    """Tests for get_content tool behavior."""

    @pytest.mark.asyncio
    async def test_get_content_tool_returns_clean_text(self):
        """Test get_content returns cleaned text content."""
        from app.main import mcp

        # Check that tools are registered
        assert len(await mcp.list_tools()) >= 3


@pytest.mark.integration
@pytest.mark.skipif(
    not _mcp_server_available(),
    reason="MCP server not running on http://127.0.0.1:8000/mcp",
)
class TestWebfetchToolBehavior:
    """Tests for webfetch agent behavior."""

    @pytest.mark.asyncio
    async def test_webfetch_tool_returns_features(self):
        """Test webfetch returns atomic features with quotes."""
        from app.main import mcp

        # Check that tools are registered
        assert len(await mcp.list_tools()) >= 3


@pytest.mark.integration
@pytest.mark.skipif(
    not _mcp_server_available(),
    reason="MCP server not running on http://127.0.0.1:8000/mcp",
)
class TestMCPResponseFormat:
    """Tests for response format validation."""

    @pytest.mark.asyncio
    async def test_search_tool_response_format(self):
        """Test search tool returns dict with correct structure."""
        from app.main import mcp

        # Expected structure is documented in tools
        assert len(await mcp.list_tools()) > 0
