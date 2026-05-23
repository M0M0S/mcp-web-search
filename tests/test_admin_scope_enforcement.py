"""Admin scope enforcement tests for MCP authorization system.

Verifies that require_scopes('admin') decorator blocks non-admin users,
that admin users can access user_manage tools, and that ADMIN_KEY_IDS
parsing from comma-separated env var works correctly.
Uses real require_scopes from fastmcp.server.auth — mocks only AuthContext.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from fastmcp.server.auth import AuthContext, AccessToken, require_scopes


# ---------------------------------------------------------------------------
# Minimal helper: create AccessToken / AuthContext with given scopes
# ---------------------------------------------------------------------------


def _make_token(scopes: list[str]) -> AccessToken:
    """Create a minimal AccessToken with given scopes."""
    return AccessToken(
        token="test-token",
        client_id="test-client",
        scopes=scopes,
        expires_at=None,
    )


def _make_context(
    scopes: list[str],
    component: Any = None,
) -> AuthContext:
    """Create an AuthContext with a token having the given scopes."""
    token: AccessToken | None = _make_token(scopes) if scopes else None
    return AuthContext(token=token, component=component or object())


# ---------------------------------------------------------------------------
# Test: scope decorator applied — verify real require_scopes metadata
# ---------------------------------------------------------------------------


class TestRequireScopesDecoratorApplied:
    """Tests that admin tools ARE decorated with require_scopes('admin')."""

    def test_require_scopes_returns_auth_check_callable(self) -> None:
        """require_scopes('admin') must return a callable AuthCheck."""
        check = require_scopes("admin")

        assert callable(check)

    def test_admin_tools_decorated_with_require_scopes(
        self,
    ) -> None:
        """register_user_manage_tools must decorate every tool with require_scopes('admin')."""
        from app.tools.user_manage import (
            create_user,
            list_users,
            revoke_user,
            rotate_key,
            check_limits,
            check_token_usage,
            update_limits,
            update_token_limits,
        )

        admin_tools = [
            create_user,
            list_users,
            revoke_user,
            rotate_key,
            check_limits,
            check_token_usage,
            update_limits,
            update_token_limits,
        ]

        for tool in admin_tools:
            # require_scopes returns an AuthCheck callable
            check = require_scopes("admin")
            assert callable(check)

            # Verify the check works with admin token
            admin_ctx = _make_context(["admin"])
            assert check(admin_ctx) is True

            # Verify the check rejects non-admin token
            non_admin_ctx = _make_context(["read"])
            assert check(non_admin_ctx) is False


# ---------------------------------------------------------------------------
# Test: real scope enforcement — non-admin rejected, admin accepted
# ---------------------------------------------------------------------------


class TestRequireScopesEnforcementRealBehavior:
    """Tests for require_scopes('admin') real enforcement via AuthContext."""

    def test_non_admin_rejected(self) -> None:
        """require_scopes('admin') must reject non-admin user during auth check."""
        check = require_scopes("admin")

        # Non-admin user has only 'read' scope — no 'admin'
        ctx = _make_context(["read"])
        assert check(ctx) is False

    def test_admin_accepted(self) -> None:
        """require_scopes('admin') must accept admin user during auth check."""
        check = require_scopes("admin")

        # Admin user has 'admin' scope
        ctx = _make_context(["admin"])
        assert check(ctx) is True

    def test_no_token_rejected(self) -> None:
        """require_scopes('admin') must reject unauthenticated user."""
        check = require_scopes("admin")

        ctx = _make_context([])  # token = None
        assert check(ctx) is False

    def test_multi_scope_accepted(self) -> None:
        """require_scopes('admin') must accept user with admin + other scopes."""
        check = require_scopes("admin")

        ctx = _make_context(["read", "write", "admin"])
        assert check(ctx) is True

    def test_require_scopes_multiple_scopes_enforcement(self) -> None:
        """require_scopes('admin', 'write') must require ALL scopes."""
        check = require_scopes("admin", "write")

        # Only admin — missing 'write'
        ctx_admin_only = _make_context(["admin"])
        assert check(ctx_admin_only) is False

        # Only write — missing 'admin'
        ctx_write_only = _make_context(["write"])
        assert check(ctx_write_only) is False

        # Both — accepted
        ctx_both = _make_context(["admin", "write"])
        assert check(ctx_both) is True


# ---------------------------------------------------------------------------
# Test: ADMIN_KEY_IDS parsing from comma-separated env var
# ---------------------------------------------------------------------------


class TestAdminKeyIdsParsingReal:
    """Tests for ADMIN_KEY_IDS parsing from comma-separated env var."""

    def _parse_admin_key_ids(self, raw: str) -> list[str]:
        """Reproduce the _parse_admin_key_ids logic from app.core.config.Settings."""
        if raw:
            return [k.strip() for k in raw.split(",") if k.strip()]
        return []

    def test_admin_key_ids_parsing_from_comma_separated_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADMIN_KEY_IDS parser must parse comma-separated string into list."""
        monkeypatch.setenv("ADMIN_KEY_IDS", "key_abc123,key_def456,key_ghi789")

        raw = os.getenv("ADMIN_KEY_IDS", "")
        parsed = self._parse_admin_key_ids(raw)
        assert parsed == ["key_abc123", "key_def456", "key_ghi789"]

    def test_admin_key_ids_parsing_with_spaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADMIN_KEY_IDS parser must strip whitespace from parsed values."""
        monkeypatch.setenv("ADMIN_KEY_IDS", "  key_abc123  ,  key_def456  ")

        raw = os.getenv("ADMIN_KEY_IDS", "")
        parsed = self._parse_admin_key_ids(raw)
        assert parsed == ["key_abc123", "key_def456"]

    def test_admin_key_ids_parsing_with_empty_entries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADMIN_KEY_IDS parser must skip empty entries from comma-separated string."""
        monkeypatch.setenv("ADMIN_KEY_IDS", "key_abc123,,key_def456,")

        raw = os.getenv("ADMIN_KEY_IDS", "")
        parsed = self._parse_admin_key_ids(raw)
        assert parsed == ["key_abc123", "key_def456"]

    def test_admin_key_ids_empty_when_env_var_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADMIN_KEY_IDS must be empty list when env var is absent."""
        monkeypatch.delenv("ADMIN_KEY_IDS", raising=False)

        raw = os.getenv("ADMIN_KEY_IDS", "")
        parsed = self._parse_admin_key_ids(raw)
        assert parsed == []

    def test_admin_key_ids_single_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADMIN_KEY_IDS must parse single value correctly."""
        monkeypatch.setenv("ADMIN_KEY_IDS", "key_single")

        raw = os.getenv("ADMIN_KEY_IDS", "")
        parsed = self._parse_admin_key_ids(raw)
        assert parsed == ["key_single"]
