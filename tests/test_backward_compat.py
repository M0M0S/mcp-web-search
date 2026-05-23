"""Backward compatibility tests for MCP authorization system.

Verifies that the auth_enabled property behaves correctly when
MCP_ENCRYPTION_KEY is absent, empty, whitespace-only, or present.
"""

from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings


class TestAuthEnabledProperty:
    """Tests for Settings.auth_enabled property behavior across all key states."""

    def test_auth_enabled_returns_false_when_key_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """auth_enabled should return False when MCP_ENCRYPTION_KEY is absent."""
        monkeypatch.delenv("MCP_ENCRYPTION_KEY", raising=False)
        settings = Settings(_env_file=None)
        assert settings.auth_enabled is False

    def test_auth_enabled_returns_false_when_key_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """auth_enabled should return False when MCP_ENCRYPTION_KEY is empty string."""
        monkeypatch.delenv("MCP_ENCRYPTION_KEY", raising=False)
        settings = Settings(MCP_ENCRYPTION_KEY="")
        assert settings.auth_enabled is False

    def test_auth_enabled_returns_false_when_key_whitespace_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """auth_enabled should return False when MCP_ENCRYPTION_KEY is whitespace-only."""
        monkeypatch.delenv("MCP_ENCRYPTION_KEY", raising=False)
        settings = Settings(MCP_ENCRYPTION_KEY="   ")
        assert settings.auth_enabled is False

    def test_auth_enabled_returns_true_when_key_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """auth_enabled should return True when MCP_ENCRYPTION_KEY is non-empty."""
        fernet_key = Fernet.generate_key().decode("utf-8")
        monkeypatch.setenv("MCP_ENCRYPTION_KEY", fernet_key)
        settings = Settings(MCP_ENCRYPTION_KEY=fernet_key)
        assert settings.auth_enabled is True

    def test_auth_enabled_returns_true_when_key_has_leading_whitespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """auth_enabled should return True when key has leading whitespace (strip handles it)."""
        fernet_key = Fernet.generate_key().decode("utf-8")
        monkeypatch.setenv("MCP_ENCRYPTION_KEY", f"  {fernet_key}")
        settings = Settings(MCP_ENCRYPTION_KEY=f"  {fernet_key}")
        assert settings.auth_enabled is True

    def test_auth_enabled_returns_false_when_env_override_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Setting MCP_ENCRYPTION_KEY to empty string via env should not enable auth."""
        monkeypatch.delenv("MCP_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("MCP_ENCRYPTION_KEY", "")
        settings = Settings(_env_file=None)
        assert settings.auth_enabled is False

    def test_auth_enabled_returns_true_when_env_override_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Setting MCP_ENCRYPTION_KEY to valid key via env should enable auth."""
        monkeypatch.delenv("MCP_ENCRYPTION_KEY", raising=False)
        fernet_key = Fernet.generate_key().decode("utf-8")
        monkeypatch.setenv("MCP_ENCRYPTION_KEY", fernet_key)
        settings = Settings(MCP_ENCRYPTION_KEY=fernet_key)
        assert settings.auth_enabled is True
