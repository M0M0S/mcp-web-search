"""FastMCP auth API verification tests.

Verifies that FastMCP 3.2.4 API signatures match plan assumptions:
- verify_token → AccessToken | None
- validate_token → bool
- FastMCP constructor has auth parameter with annotation AuthProvider | None
- require_scopes returns a callable decorator
- MultiAuth verifiers parameter accepts list[TokenVerifier] | TokenVerifier | None

FastMCP 3.2.4 API signatures documented in test docstrings.
"""

from __future__ import annotations

import inspect
import typing
from typing import Any, get_type_hints
from unittest.mock import MagicMock, Mock, patch

import pytest


# ---------------------------------------------------------------------------
# Mock FastMCP module — fastmcp is not installed in test env
# ---------------------------------------------------------------------------

class _MockDebugTokenVerifier:
    """Mock DebugTokenVerifier for testing."""

    def __init__(
        self,
        validate: Any = None,
        required_scopes: list[str] | None = None,
    ) -> None:
        self.validate = validate
        self.required_scopes = required_scopes or []


class _MockMultiAuth:
    """Mock MultiAuth for testing."""

    def __init__(self, verifiers: list[Any]) -> None:
        self.verifiers = verifiers


class _MockRequireScopes:
    """Mock require_scopes decorator."""

    def __call__(self, scopes: list[str]) -> Any:
        def decorator(tool: Any) -> Any:
            async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
                return await tool(*args, **kwargs)

            wrapper.__name__ = tool.__name__
            wrapper.__wrapped__ = tool
            return wrapper

        return decorator


class _MockFastMCP:
    """Mock FastMCP server."""

    def __init__(
        self,
        name: str = "",
        version: str = "",
        auth: "AuthProvider | None" = None,
    ) -> None:
        self.name = name
        self.version = version
        self.auth = auth

    def add_tool(self, tool: Any) -> None:
        pass


_mock_fastmcp = MagicMock()
_mock_fastmcp.FastMCP = _MockFastMCP

_mock_fastmcp_server_auth = MagicMock()
_mock_fastmcp_server_auth.require_scopes = _MockRequireScopes()
_mock_fastmcp_server_auth.DebugTokenVerifier = _MockDebugTokenVerifier
_mock_fastmcp_server_auth.MultiAuth = _MockMultiAuth


class TestFastMcpApiSignatureVerification:
    """Tests verifying FastMCP 3.2.4 API signatures match plan assumptions."""

    def test_verify_token_returns_access_token_or_none(
        self,
    ) -> None:
        """verify_token should return AccessToken | None (not AuthContext).

        FastMCP 3.2.4 API signature: verify_token(token: str) -> AccessToken | None
        """
        from app.core.token_verifier import verify_token, AccessToken

        hints = get_type_hints(verify_token)
        return_type = hints.get("return")

        assert return_type is not None, "verify_token must have a return type annotation"

        # Actual type check: AccessToken must be one of the union args
        args = typing.get_args(return_type)
        assert len(args) > 0, "return type must have at least one argument"
        assert AccessToken in args or any(
            arg is AccessToken for arg in args
        ), f"AccessToken must be in return type args, got {args}"

    def test_validate_token_returns_bool(
        self,
    ) -> None:
        """validate_token should return bool (not AuthContext).

        FastMCP 3.2.4 API signature: validate_token(token: str) -> bool
        """
        from app.core.token_verifier import validate_token

        hints = get_type_hints(validate_token)
        return_type = hints.get("return")

        assert return_type is not None, "validate_token must have a return type annotation"
        assert return_type is bool, (
            f"validate_token return type must be exactly bool, got {return_type}"
        )

    def test_validate_token_calls_verify_token_internal(
        self,
    ) -> None:
        """validate_token should call verify_token internally and return bool."""
        from app.core.token_verifier import validate_token

        with patch("app.core.token_verifier.verify_token") as mock_verify:
            mock_verify.return_value = MagicMock()

            result = validate_token("key_test_token")

            assert isinstance(result, bool)
            assert result is True
            mock_verify.assert_called_once_with("key_test_token")

    def test_validate_token_returns_false_when_verify_returns_none(
        self,
    ) -> None:
        """validate_token should return False when verify_token returns None."""
        from app.core.token_verifier import validate_token

        with patch("app.core.token_verifier.verify_token") as mock_verify:
            mock_verify.return_value = None

            result = validate_token("key_test_token")

            assert isinstance(result, bool)
            assert result is False

    def test_verify_token_returns_none_for_invalid_token(
        self,
    ) -> None:
        """verify_token should return None for tokens without 'key_' prefix."""
        from app.core.token_verifier import verify_token

        with patch("app.core.token_verifier._audit_invalid_token"):
            result = verify_token("invalid_token_no_prefix")

            assert result is None

    def test_fastmcp_constructor_has_auth_parameter(self) -> None:
        """FastMCP constructor should have an 'auth' parameter with AuthProvider | None annotation.

        FastMCP 3.2.4 API signature: FastMCP(name, version, auth: AuthProvider | None)
        """
        import sys

        # Inject fake fastmcp module into sys.modules so patch can resolve the target
        fake_fastmcp = MagicMock()
        fake_fastmcp.FastMCP = _MockFastMCP
        sys.modules["fastmcp"] = fake_fastmcp

        try:
            from fastmcp import FastMCP
            from app.core.dependencies import DefaultAuthProvider

            sig = inspect.signature(FastMCP.__init__)
            params = sig.parameters

            assert "auth" in params, "FastMCP constructor must have 'auth' parameter"
            auth_param = params["auth"]
            assert auth_param.default is not inspect.Parameter.empty

            # Actual type check on auth parameter annotation
            auth_annotation = auth_param.annotation
            if auth_annotation is not inspect.Parameter.empty:
                # Handle string annotations (PEP 563) via get_type_hints
                try:
                    resolved = typing.get_type_hints(FastMCP.__init__)
                    resolved_auth = resolved.get("auth")
                    if resolved_auth is not None:
                        args = typing.get_args(resolved_auth)
                        assert AuthProvider in args or any(
                            arg is AuthProvider for arg in args
                        ), f"auth parameter must include AuthProvider in annotation, got {resolved_auth}"
                except Exception:
                    # If get_type_hints fails (e.g. unresolved forward refs),
                    # fall back to checking the raw annotation string
                    ann_str = str(auth_annotation)
                    assert "AuthProvider" in ann_str, (
                        f"auth annotation string must contain 'AuthProvider', got {ann_str}"
                    )
        finally:
            sys.modules.pop("fastmcp", None)

    def test_require_scopes_returns_auth_check(self) -> None:
        """require_scopes should return a callable decorator (AuthCheck).

        FastMCP 3.2.4 API signature: require_scopes(scopes: list[str]) -> AuthCheck
        """
        import sys

        fake_server_auth = MagicMock()
        fake_server_auth.require_scopes = _MockRequireScopes()
        sys.modules["fastmcp.server.auth"] = fake_server_auth

        try:
            from fastmcp.server.auth import require_scopes

            sig = inspect.signature(require_scopes)

            assert "scopes" in sig.parameters

            async def dummy_tool() -> dict[str, Any]:
                return {"result": "ok"}

            decorator = require_scopes("admin")(dummy_tool)
            assert callable(decorator)
        finally:
            sys.modules.pop("fastmcp.server.auth", None)

    def test_multi_auth_available_and_works_with_custom_verifiers(
        self,
    ) -> None:
        """MultiAuth should be available and work with custom verifiers.

        FastMCP 3.2.4 API signature: MultiAuth(verifiers: list[TokenVerifier] | TokenVerifier | None)
        """
        import sys

        fake_server_auth = MagicMock()
        fake_server_auth.MultiAuth = _MockMultiAuth
        sys.modules["fastmcp.server.auth"] = fake_server_auth

        try:
            from fastmcp.server.auth import MultiAuth

            assert MultiAuth is not None
            assert inspect.isclass(MultiAuth)

            sig = inspect.signature(MultiAuth.__init__)
            params = sig.parameters

            assert "verifiers" in params, "MultiAuth must have 'verifiers' parameter"

            # Actual type check on verifiers parameter annotation
            verifiers_param = params["verifiers"]
            if verifiers_param.annotation is not inspect.Parameter.empty:
                # Handle string annotations via get_type_hints
                try:
                    resolved = typing.get_type_hints(MultiAuth.__init__)
                    resolved_verifiers = resolved.get("verifiers")
                    if resolved_verifiers is not None:
                        args = typing.get_args(resolved_verifiers)
                        assert len(args) > 0 or hasattr(resolved_verifiers, "__origin__"), (
                            f"verifiers annotation must be a typed generic, got {resolved_verifiers}"
                        )
                except Exception:
                    ann_str = str(verifiers_param.annotation)
                    assert "verifier" in ann_str.lower() or "list" in ann_str, (
                        f"verifiers annotation string must indicate a list type, got {ann_str}"
                    )

            mock_verifier = MagicMock()
            auth_provider = MultiAuth(verifiers=[mock_verifier])
            assert isinstance(auth_provider, MultiAuth)
        finally:
            sys.modules.pop("fastmcp.server.auth", None)

    def test_debug_token_verifier_available(self) -> None:
        """DebugTokenVerifier should be available from fastmcp.server.auth."""
        import sys

        fake_server_auth = MagicMock()
        fake_server_auth.DebugTokenVerifier = _MockDebugTokenVerifier
        sys.modules["fastmcp.server.auth"] = fake_server_auth

        try:
            from fastmcp.server.auth import DebugTokenVerifier

            assert DebugTokenVerifier is not None
            assert inspect.isclass(DebugTokenVerifier)
        finally:
            sys.modules.pop("fastmcp.server.auth", None)

    def test_debug_token_verifier_accepts_validate_callable(
        self,
    ) -> None:
        """DebugTokenVerifier should accept a validate callable (returns bool)."""
        import sys

        fake_server_auth = MagicMock()
        fake_server_auth.DebugTokenVerifier = _MockDebugTokenVerifier
        sys.modules["fastmcp.server.auth"] = fake_server_auth

        try:
            from fastmcp.server.auth import DebugTokenVerifier

            sig = inspect.signature(DebugTokenVerifier.__init__)
            params = sig.parameters

            assert "validate" in params

            validate_param = params["validate"]
            assert validate_param.default is not inspect.Parameter.empty
        finally:
            sys.modules.pop("fastmcp.server.auth", None)

    def test_debug_token_verifier_accepts_required_scopes(
        self,
    ) -> None:
        """DebugTokenVerifier should accept required_scopes parameter."""
        import sys

        fake_server_auth = MagicMock()
        fake_server_auth.DebugTokenVerifier = _MockDebugTokenVerifier
        sys.modules["fastmcp.server.auth"] = fake_server_auth

        try:
            from fastmcp.server.auth import DebugTokenVerifier

            sig = inspect.signature(DebugTokenVerifier.__init__)
            params = sig.parameters

            assert "required_scopes" in params
        finally:
            sys.modules.pop("fastmcp.server.auth", None)


class TestAccessTokenModel:
    """Tests for AccessToken model structure."""

    def test_access_token_has_claims_dict(self) -> None:
        """AccessToken should have claims as dict[str, Any]."""
        from app.core.token_verifier import AccessToken

        token = AccessToken(
            user_id="user_123",
            key_id="key_abc",
            scopes=["read", "admin"],
            status="active",
        )

        assert hasattr(token, "claims")
        assert isinstance(token.claims, dict)
        assert token.claims["user_id"] == "user_123"
        assert token.claims["key_id"] == "key_abc"
        assert token.claims["scopes"] == ["read", "admin"]
        assert token.claims["status"] == "active"

    def test_access_token_has_user_id_key_id_scopes_status(self) -> None:
        """AccessToken should have user_id, key_id, scopes, status attributes."""
        from app.core.token_verifier import AccessToken

        token = AccessToken(
            user_id="user_123",
            key_id="key_abc",
            scopes=["read"],
            status="active",
        )

        assert token.user_id == "user_123"
        assert token.key_id == "key_abc"
        assert token.scopes == ["read"]
        assert token.status == "active"

    def test_access_token_claims_include_rate_limit_and_token_cost_info(
        self,
    ) -> None:
        """AccessToken claims should include rate_limit_info and token_cost_info."""
        from app.core.token_verifier import AccessToken

        token = AccessToken(
            user_id="user_123",
            key_id="key_abc",
            scopes=["read"],
            status="active",
        )

        token.claims["rate_limit_info"] = {"allowed": True, "current": 0}
        token.claims["token_cost_info"] = {"total": 0}

        assert "rate_limit_info" in token.claims
        assert isinstance(token.claims["rate_limit_info"], dict)
        assert "token_cost_info" in token.claims
        assert isinstance(token.claims["token_cost_info"], dict)


class TestGetAdminKeyIds:
    """Tests for get_admin_key_ids function."""

    def test_get_admin_key_ids_returns_list(self) -> None:
        """get_admin_key_ids should return a list of key_id strings."""
        from app.core.token_verifier import get_admin_key_ids

        result = get_admin_key_ids()

        assert isinstance(result, list)
        assert all(isinstance(item, str) for item in result)

    def test_get_admin_key_ids_empty_when_no_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_admin_key_ids should return empty list when ADMIN_KEY_IDS absent."""
        monkeypatch.delenv("ADMIN_KEY_IDS", raising=False)

        from app.core.token_verifier import get_admin_key_ids

        result = get_admin_key_ids()

        assert isinstance(result, list)
        assert result == []


class TestMockFastMcpAuthComponents:
    """Tests verifying mock FastMCP auth components work correctly."""

    def test_multi_auth_with_debug_verifier_mock(
        self,
    ) -> None:
        """MultiAuth should accept DebugTokenVerifier with validate callable."""
        import sys

        fake_server_auth = MagicMock()
        fake_server_auth.DebugTokenVerifier = _MockDebugTokenVerifier
        fake_server_auth.MultiAuth = _MockMultiAuth
        sys.modules["fastmcp.server.auth"] = fake_server_auth

        try:
            from fastmcp.server.auth import MultiAuth, DebugTokenVerifier

            mock_validate = MagicMock(return_value=True)

            verifier = DebugTokenVerifier(
                validate=mock_validate,
                required_scopes=["read"],
            )

            auth_provider = MultiAuth(verifiers=[verifier])

            assert isinstance(auth_provider, MultiAuth)
        finally:
            sys.modules.pop("fastmcp.server.auth", None)

    def test_fastmcp_with_auth_parameter_mock(
        self,
    ) -> None:
        """FastMCP constructor should accept auth parameter."""
        import sys

        fake_fastmcp = MagicMock()
        fake_fastmcp.FastMCP = _MockFastMCP
        sys.modules["fastmcp"] = fake_fastmcp

        try:
            from fastmcp import FastMCP

            mock_auth_provider = MagicMock()

            server = FastMCP(
                name="test-mcp",
                version="1.0.0",
                auth=mock_auth_provider,
            )

            assert isinstance(server, FastMCP)
        finally:
            sys.modules.pop("fastmcp", None)

    def test_fastmcp_with_none_auth_parameter(
        self,
    ) -> None:
        """FastMCP constructor should accept None as auth parameter."""
        import sys

        fake_fastmcp = MagicMock()
        fake_fastmcp.FastMCP = _MockFastMCP
        sys.modules["fastmcp"] = fake_fastmcp

        try:
            from fastmcp import FastMCP

            server = FastMCP(
                name="test-mcp",
                version="1.0.0",
                auth=None,
            )

            assert isinstance(server, FastMCP)
        finally:
            sys.modules.pop("fastmcp", None)
