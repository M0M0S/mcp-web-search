"""Tests for MCP authorization token_verifier module.

Covers verify_token, validate_token, invalid token handling, user status
checks, rate limit enforcement, and constant-time comparison.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from typing import Generator
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


@pytest.fixture(autouse=True)
def _ensure_schema(shared_db_path: str) -> Generator[None, None, None]:
    """Ensure all tables exist before each test."""
    from app.core.user_store import init_db

    conn = init_db()
    conn.close()
    yield


@pytest.fixture(autouse=True)
def _mock_redis_available() -> Generator[None, None, None]:
    """Force rate_limiter and token_cost_tracker to use DB fallback."""
    from app.core import rate_limiter, token_cost_tracker

    rate_limiter._redis_available = False
    rate_limiter._redis_pool = None
    rate_limiter._redis_last_check = 0.0

    token_cost_tracker._redis_available = False
    token_cost_tracker._redis_pool = None
    token_cost_tracker._redis_last_check = 0.0
    yield


@pytest.fixture
def shared_db(shared_db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """Return a single SQLite connection with schema created.

    Closes the connection on teardown to prevent ResourceWarning.
    """
    from app.core.user_store import init_db

    conn: sqlite3.Connection = init_db()
    yield conn
    conn.close()


@pytest.fixture
def valid_user(
    shared_db: sqlite3.Connection, mcp_encryption_key: str
) -> Generator[dict, None, None]:
    """Create a user with a valid encrypted key.

    Uses mock decrypt_key to return raw_key so that verify_token passes hmac.compare_digest.
    Patches persist during test.
    """
    from app.core.user_store import create_user as _store_create_user

    raw_key: str = os.urandom(32).hex()

    # Mock decrypt_key to return raw_key (identity for test purposes)
    # Patch at token_verifier module level because it imports decrypt_key directly
    with patch("app.core.token_verifier.decrypt_key") as mock_decrypt:
        mock_decrypt.return_value = raw_key

        user = _store_create_user(name="valid_user")

        # Set encrypted_key to raw_key so verify_token can "decrypt" it
        shared_db.execute(
            "UPDATE users SET encrypted_key = ? WHERE id = ?",
            (raw_key, user["user_id"]),
        )
        shared_db.commit()

        # raw_key must equal token for hmac.compare_digest
        token_value: str = f"key_{user['key_id']}"
        mock_decrypt.return_value = token_value

        yield {
            "user_id": user["user_id"],
            "key_id": user["key_id"],
            "raw_key": token_value,
            "encrypted_key": token_value,
        }


@pytest.fixture
def valid_token(valid_user: dict) -> str:
    """Return a valid token string for the test user.

    Token format: key_<key_id> — verifier strips key_ prefix to get key_id
    which is used to lookup user in DB. The raw_key is compared via hmac.
    """
    return f"key_{valid_user['key_id']}"


@pytest.fixture(autouse=True)
def _mock_decrypt_key(valid_user: dict | None) -> Generator[None, None, None]:
    """Mock decrypt_key to return raw_key for valid_user fixture.

    This is required because verify_token calls decrypt_key which needs
    MCP_ENCRYPTION_KEY and real Fernet decryption. For tests we mock it
    to return the raw_key that was stored as encrypted_key.
    """
    if valid_user is None:
        yield
        return

    raw_key: str = valid_user["raw_key"]

    with patch("app.core.encryption.decrypt_key") as mock_decrypt:
        mock_decrypt.return_value = raw_key
        yield


# ---------------------------------------------------------------------------
# 1. verify_token — valid token → AccessToken with claims
# ---------------------------------------------------------------------------


class TestVerifyTokenValid:
    """verify_token returns AccessToken for valid tokens."""

    @pytest.fixture
    def mock_user(self, mcp_encryption_key: str) -> dict:
        """Create a mock user record for testing."""
        hex_id: str = os.urandom(6).hex()
        key_id: str = f"key_{hex_id}"
        token_value: str = f"key_{key_id}"  # token = key_<DB key_id>
        return {
            "user_id": os.urandom(16).hex(),
            "key_id": key_id,
            "raw_key": token_value,  # raw_key = token for hmac.compare_digest
            "encrypted_key": token_value,
            "status": "active",
            "scopes": ["read"],
        }

    @pytest.fixture
    def valid_token(self, mock_user: dict) -> str:
        """Return a valid token string for the test user."""
        return f"key_{mock_user['key_id']}"

    def test_verify_token_returns_access_token(
        self, valid_token: str, mock_user: dict
    ) -> None:
        """Valid token → AccessToken (not None)."""
        from app.core.token_verifier import verify_token

        with (
            patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user,
            patch("app.core.token_verifier.decrypt_key") as mock_decrypt,
            patch("app.core.token_verifier.check_rate_limit") as mock_rate,
            patch("app.core.token_verifier.increment_counter") as mock_incr,
            patch("app.core.token_verifier.check_token_limits") as mock_token,
        ):
            mock_get_user.return_value = mock_user
            mock_decrypt.return_value = mock_user["raw_key"]
            mock_rate.return_value = {
                "allowed": True,
                "current": 0,
                "limit": 100,
                "remaining": 100,
            }
            mock_incr.return_value = 1
            mock_token.return_value = {"exceeded": False, "warning": None}

            result = verify_token(valid_token)

            assert result is not None
            assert hasattr(result, "user_id")
            assert hasattr(result, "key_id")
            assert hasattr(result, "scopes")
            assert hasattr(result, "status")
            assert hasattr(result, "claims")

    def test_verify_token_claims_contain_user_id(
        self, valid_token: str, mock_user: dict
    ) -> None:
        """AccessToken claims include user_id."""
        from app.core.token_verifier import verify_token

        with (
            patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user,
            patch("app.core.token_verifier.decrypt_key") as mock_decrypt,
            patch("app.core.rate_limiter.check_rate_limit") as mock_rate,
            patch("app.core.rate_limiter.increment_counter"),
            patch("app.core.token_cost_tracker.check_token_limits"),
        ):
            mock_get_user.return_value = mock_user
            mock_decrypt.return_value = mock_user["raw_key"]
            mock_rate.return_value = {
                "allowed": True,
                "current": 0,
                "limit": 100,
                "remaining": 100,
            }

            result = verify_token(valid_token)

            assert result.claims["user_id"] == mock_user["user_id"]

    def test_verify_token_claims_contain_key_id(
        self, valid_token: str, mock_user: dict
    ) -> None:
        """AccessToken claims include key_id."""
        from app.core.token_verifier import verify_token

        with (
            patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user,
            patch("app.core.token_verifier.decrypt_key") as mock_decrypt,
            patch("app.core.rate_limiter.check_rate_limit") as mock_rate,
            patch("app.core.rate_limiter.increment_counter"),
            patch("app.core.token_cost_tracker.check_token_limits"),
        ):
            mock_get_user.return_value = mock_user
            mock_decrypt.return_value = mock_user["raw_key"]
            mock_rate.return_value = {
                "allowed": True,
                "current": 0,
                "limit": 100,
                "remaining": 100,
            }

            result = verify_token(valid_token)

            assert result.claims["key_id"] == mock_user["key_id"]

    def test_verify_token_claims_contain_scopes(
        self, valid_token: str, mock_user: dict
    ) -> None:
        """AccessToken claims include scopes."""
        from app.core.token_verifier import verify_token

        with (
            patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user,
            patch("app.core.token_verifier.decrypt_key") as mock_decrypt,
            patch("app.core.rate_limiter.check_rate_limit") as mock_rate,
            patch("app.core.rate_limiter.increment_counter"),
            patch("app.core.token_cost_tracker.check_token_limits"),
        ):
            mock_get_user.return_value = mock_user
            mock_decrypt.return_value = mock_user["raw_key"]
            mock_rate.return_value = {
                "allowed": True,
                "current": 0,
                "limit": 100,
                "remaining": 100,
            }

            result = verify_token(valid_token)

            assert "scopes" in result.claims

    def test_verify_token_claims_contain_status(
        self, valid_token: str, mock_user: dict
    ) -> None:
        """AccessToken claims include status."""
        from app.core.token_verifier import verify_token

        with (
            patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user,
            patch("app.core.token_verifier.decrypt_key") as mock_decrypt,
            patch("app.core.rate_limiter.check_rate_limit") as mock_rate,
            patch("app.core.rate_limiter.increment_counter"),
            patch("app.core.token_cost_tracker.check_token_limits"),
        ):
            mock_get_user.return_value = mock_user
            mock_decrypt.return_value = mock_user["raw_key"]
            mock_rate.return_value = {
                "allowed": True,
                "current": 0,
                "limit": 100,
                "remaining": 100,
            }

            result = verify_token(valid_token)

            assert result.claims["status"] == "active"

    def test_verify_token_user_id_matches(
        self, valid_token: str, mock_user: dict
    ) -> None:
        """AccessToken.user_id matches DB user."""
        from app.core.token_verifier import verify_token

        with (
            patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user,
            patch("app.core.token_verifier.decrypt_key") as mock_decrypt,
            patch("app.core.rate_limiter.check_rate_limit") as mock_rate,
            patch("app.core.rate_limiter.increment_counter"),
            patch("app.core.token_cost_tracker.check_token_limits"),
        ):
            mock_get_user.return_value = mock_user
            mock_decrypt.return_value = mock_user["raw_key"]
            mock_rate.return_value = {
                "allowed": True,
                "current": 0,
                "limit": 100,
                "remaining": 100,
            }

            result = verify_token(valid_token)

            assert result.user_id == mock_user["user_id"]

    def test_verify_token_key_id_matches(
        self, valid_token: str, mock_user: dict
    ) -> None:
        """AccessToken.key_id matches DB user."""
        from app.core.token_verifier import verify_token

        with (
            patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user,
            patch("app.core.token_verifier.decrypt_key") as mock_decrypt,
            patch("app.core.rate_limiter.check_rate_limit") as mock_rate,
            patch("app.core.rate_limiter.increment_counter"),
            patch("app.core.token_cost_tracker.check_token_limits"),
        ):
            mock_get_user.return_value = mock_user
            mock_decrypt.return_value = mock_user["raw_key"]
            mock_rate.return_value = {
                "allowed": True,
                "current": 0,
                "limit": 100,
                "remaining": 100,
            }

            result = verify_token(valid_token)

            assert result.key_id == mock_user["key_id"]


# ---------------------------------------------------------------------------
# 2. validate_token — valid → True, invalid → False
# ---------------------------------------------------------------------------


class TestValidateToken:
    """validate_token returns bool."""

    @pytest.fixture
    def mock_valid_user(self) -> dict:
        hex_id: str = os.urandom(6).hex()
        key_id: str = f"key_{hex_id}"
        token_value: str = f"key_{key_id}"
        return {
            "user_id": os.urandom(16).hex(),
            "key_id": key_id,
            "raw_key": token_value,
            "encrypted_key": token_value,
            "status": "active",
            "scopes": ["read"],
        }

    @pytest.fixture
    def mock_valid_token(self, mock_valid_user: dict) -> str:
        return f"key_{mock_valid_user['key_id']}"

    def test_validate_valid_token(
        self, mock_valid_token: str, mock_valid_user: dict
    ) -> None:
        """Valid token → True."""
        from app.core.token_verifier import validate_token

        with (
            patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user,
            patch("app.core.token_verifier.decrypt_key") as mock_decrypt,
            patch("app.core.rate_limiter.check_rate_limit") as mock_rate,
            patch("app.core.rate_limiter.increment_counter"),
            patch("app.core.token_cost_tracker.check_token_limits"),
        ):
            mock_get_user.return_value = mock_valid_user
            mock_decrypt.return_value = mock_valid_user["raw_key"]
            mock_rate.return_value = {
                "allowed": True,
                "current": 0,
                "limit": 100,
                "remaining": 100,
            }

            assert validate_token(mock_valid_token) is True

    def test_validate_invalid_token(self) -> None:
        """Invalid token → False."""
        from app.core.token_verifier import validate_token

        assert validate_token("invalid_token_string") is False

    def test_validate_empty_token(self) -> None:
        """Empty token → False."""
        from app.core.token_verifier import validate_token

        assert validate_token("") is False


# ---------------------------------------------------------------------------
# 3. Invalid token → None + audit event invalid_token
# ---------------------------------------------------------------------------


class TestInvalidToken:
    """Invalid token handling."""

    def test_invalid_token_returns_none(self) -> None:
        """Token without key_ prefix → None."""
        from app.core.token_verifier import verify_token

        assert verify_token("no_prefix") is None

    def test_invalid_key_id_returns_none(self) -> None:
        """key_id not in DB → None."""
        from app.core.token_verifier import verify_token

        with patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user:
            mock_get_user.return_value = None
            assert verify_token("key_nonexistent_user") is None

    def test_wrong_token_returns_none(self) -> None:
        """Token that doesn't match decrypted key → None."""
        from app.core.token_verifier import verify_token

        mock_user: dict = {
            "user_id": os.urandom(16).hex(),
            "key_id": "key_test",
            "raw_key": "correct_key",
            "encrypted_key": "correct_key",
            "status": "active",
            "scopes": ["read"],
        }

        with (
            patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user,
            patch("app.core.token_verifier.decrypt_key") as mock_decrypt,
            patch("app.core.token_verifier.check_rate_limit") as mock_rate,
        ):
            mock_get_user.return_value = mock_user
            mock_decrypt.return_value = mock_user["raw_key"]
            mock_rate.return_value = {
                "allowed": True,
                "current": 0,
                "limit": 100,
                "remaining": 100,
            }
            assert verify_token("key_wrong_key_value") is None

    def test_none_encrypted_key_returns_none(self) -> None:
        """User with no encrypted_key → None."""
        from app.core.token_verifier import verify_token

        mock_user: dict = {
            "user_id": os.urandom(16).hex(),
            "key_id": "key_test",
            "raw_key": "any_key",
            "encrypted_key": None,
            "status": "active",
            "scopes": ["read"],
        }

        with patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user:
            mock_get_user.return_value = mock_user
            assert verify_token("key_test") is None


class TestRevokedUser:
    """Revoked user handling."""

    def test_revoked_user_returns_none(self) -> None:
        """Revoked user → None."""
        from app.core.token_verifier import verify_token

        mock_user: dict = {
            "user_id": os.urandom(16).hex(),
            "key_id": "key_test",
            "raw_key": "key_test",
            "encrypted_key": "key_test",
            "status": "revoked",
            "scopes": ["read"],
        }

        with (
            patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user,
            patch("app.core.token_verifier.decrypt_key") as mock_decrypt,
            patch("app.core.token_verifier.check_rate_limit") as mock_rate,
        ):
            mock_get_user.return_value = mock_user
            mock_decrypt.return_value = mock_user["encrypted_key"]
            mock_rate.return_value = {
                "allowed": True,
                "current": 0,
                "limit": 100,
                "remaining": 100,
            }
            assert verify_token("key_test") is None

    def test_revoked_user_status_in_db(self, shared_db: sqlite3.Connection) -> None:
        """After revoke, user status is 'revoked' in DB."""
        from app.core.user_store import create_user, get_user_by_id, revoke_user

        user = create_user(name="revoked_test_user")
        revoke_user(user["user_id"])
        user = get_user_by_id(user["user_id"])
        assert user["status"] == "revoked"


class TestDisabledUser:
    """Disabled user handling."""

    def test_disabled_user_returns_none(self) -> None:
        """Disabled user → None."""
        from app.core.token_verifier import verify_token

        mock_user: dict = {
            "user_id": os.urandom(16).hex(),
            "key_id": "key_test",
            "raw_key": "key_test",
            "encrypted_key": "key_test",
            "status": "disabled",
            "scopes": ["read"],
        }

        with (
            patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user,
            patch("app.core.token_verifier.decrypt_key") as mock_decrypt,
            patch("app.core.token_verifier.check_rate_limit") as mock_rate,
        ):
            mock_get_user.return_value = mock_user
            mock_decrypt.return_value = mock_user["encrypted_key"]
            mock_rate.return_value = {
                "allowed": True,
                "current": 0,
                "limit": 100,
                "remaining": 100,
            }
            assert verify_token("key_test") is None

    def test_disabled_user_status_in_db(self, shared_db: sqlite3.Connection) -> None:
        """After disable, user status is 'disabled' in DB."""
        from app.core.user_store import create_user, get_user_by_id, update_user

        user = create_user(name="disabled_test_user")
        update_user(user["user_id"], status="disabled")
        user = get_user_by_id(user["user_id"])
        assert user["status"] == "disabled"


# ---------------------------------------------------------------------------
# 6. Rate limit exceeded → None + audit event rate_limit_exceeded
# ---------------------------------------------------------------------------


class TestRateLimitExceeded:
    """Rate limit enforcement."""

    def test_rate_limit_exceeded_returns_none(self) -> None:
        """User at rate limit → None."""
        from app.core.token_verifier import verify_token

        mock_user: dict = {
            "user_id": os.urandom(16).hex(),
            "key_id": "key_test",
            "raw_key": "key_test",
            "encrypted_key": "key_test",
            "status": "active",
            "scopes": ["read"],
        }

        with (
            patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user,
            patch("app.core.token_verifier.decrypt_key") as mock_decrypt,
            patch("app.core.token_verifier.check_rate_limit") as mock_rate,
        ):
            mock_get_user.return_value = mock_user
            mock_decrypt.return_value = mock_user["encrypted_key"]
            mock_rate.return_value = {
                "allowed": False,
                "current": 100,
                "limit": 100,
                "remaining": 0,
            }
            assert verify_token("key_test") is None

    def test_rate_limit_boundary_one_below(self) -> None:
        """User one below limit → still allowed."""
        from app.core.token_verifier import verify_token

        mock_user: dict = {
            "user_id": os.urandom(16).hex(),
            "key_id": "key_test",
            "raw_key": "key_test",
            "encrypted_key": "key_test",
            "status": "active",
            "scopes": ["read"],
        }

        with (
            patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user,
            patch("app.core.token_verifier.decrypt_key") as mock_decrypt,
            patch("app.core.token_verifier.check_rate_limit") as mock_rate,
            patch("app.core.token_verifier.increment_counter") as mock_incr,
            patch("app.core.token_verifier.check_token_limits"),
        ):
            mock_get_user.return_value = mock_user
            mock_decrypt.return_value = mock_user["encrypted_key"]
            mock_rate.return_value = {
                "allowed": True,
                "current": 99,
                "limit": 100,
                "remaining": 1,
            }
            mock_incr.return_value = 100
            result = verify_token("key_test")
            assert result is not None


# ---------------------------------------------------------------------------
# 7. Constant-time comparison — hmac.compare_digest
# ---------------------------------------------------------------------------


class TestConstantTimeComparison:
    """verify_token uses hmac.compare_digest for token comparison."""

    def test_hmac_compare_digest_used(self) -> None:
        """verify_token source contains hmac.compare_digest call."""
        import inspect

        from app.core import token_verifier

        source: str = inspect.getsource(token_verifier.verify_token)

        assert "hmac.compare_digest" in source

    def test_hmac_module_imported(self) -> None:
        """hmac module is imported in token_verifier."""
        from app.core import token_verifier

        assert hasattr(token_verifier, "hmac")


# ---------------------------------------------------------------------------
# 8. Additional helpers
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    """get_auth_context, create_access_token, get_admin_key_ids, check_user_status."""

    def test_get_auth_context_valid(self) -> None:
        """get_auth_context returns dict for valid token."""
        from app.core.token_verifier import get_auth_context

        mock_user: dict = {
            "user_id": os.urandom(16).hex(),
            "key_id": "key_test",
            "raw_key": "key_test",
            "encrypted_key": "key_test",
            "status": "active",
            "scopes": ["read"],
        }

        with (
            patch("app.core.token_verifier.get_user_by_key_id") as mock_get_user,
            patch("app.core.token_verifier.decrypt_key") as mock_decrypt,
            patch("app.core.rate_limiter.check_rate_limit") as mock_rate,
            patch("app.core.rate_limiter.increment_counter"),
            patch("app.core.token_cost_tracker.check_token_limits"),
        ):
            mock_get_user.return_value = mock_user
            mock_decrypt.return_value = mock_user["encrypted_key"]
            mock_rate.return_value = {
                "allowed": True,
                "current": 0,
                "limit": 100,
                "remaining": 100,
            }
            context = get_auth_context("key_test")
            assert context is not None
            assert "user_id" in context
            assert "key_id" in context
            assert "scopes" in context
            assert "status" in context

    def test_get_auth_context_invalid(self) -> None:
        """get_auth_context returns None for invalid token."""
        from app.core.token_verifier import get_auth_context

        assert get_auth_context("invalid") is None

    def test_create_access_token(self) -> None:
        """create_access_token returns claims dict."""
        from app.core.token_verifier import create_access_token

        claims = create_access_token(
            user_id="uid",
            key_id="key_abc",
            scopes=["read"],
            status="active",
        )

        assert claims["user_id"] == "uid"
        assert claims["key_id"] == "key_abc"
        assert claims["scopes"] == ["read"]
        assert claims["status"] == "active"

    def test_check_user_status_active(self, shared_db: sqlite3.Connection) -> None:
        """Active user → 'active'."""
        from app.core.token_verifier import check_user_status
        from app.core.user_store import create_user

        user = create_user(name="active_status_test")
        assert check_user_status(user["user_id"]) == "active"

    def test_check_user_status_revoked(self, shared_db: sqlite3.Connection) -> None:
        """Revoked user → 'revoked'."""
        from app.core.token_verifier import check_user_status
        from app.core.user_store import create_user, revoke_user

        user = create_user(name="revoked_status_test")
        revoke_user(user["user_id"])
        assert check_user_status(user["user_id"]) == "revoked"

    def test_check_user_status_not_found(self) -> None:
        """Unknown user_id → 'revoked'."""
        from app.core.token_verifier import check_user_status

        assert check_user_status("nonexistent") == "revoked"

    def test_get_admin_key_ids(self) -> None:
        """get_admin_key_ids returns list from Settings."""
        from app.core.token_verifier import get_admin_key_ids

        result = get_admin_key_ids()

        assert isinstance(result, list)
