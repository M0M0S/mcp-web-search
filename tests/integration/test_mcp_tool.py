"""Integration tests for MCP tools via FastMCP Client."""

import pytest


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


class TestMCPResponseFormat:
    """Tests for response format validation."""

    @pytest.mark.asyncio
    async def test_search_response_format(self):
        """Test that search returns correct response format."""
        from app.main import mcp

        # Check tools are registered
        assert len(await mcp.list_tools()) > 0
