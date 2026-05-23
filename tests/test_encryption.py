"""Tests for MCP authorization encryption module.

Covers Fernet encryption/decryption roundtrip, key rotation, entropy
validation, import_keys behavior, and key format validation.
"""

from __future__ import annotations

import base64
import os
import secrets
from typing import Generator

import pytest
from cryptography.fernet import Fernet


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_encryption_globals() -> Generator[None, None, None]:
    """Reset encryption module global state before each test."""
    from app.core import encryption

    encryption._MCP_ENCRYPTION_KEY = None
    encryption._MCP_ENCRYPTION_KEY_BACKUP = None
    yield


@pytest.fixture
def valid_fernet_key() -> str:
    """Generate a valid Fernet key (44-char base64-encoded 32 bytes)."""
    return Fernet.generate_key().decode("utf-8")


@pytest.fixture
def standard_b64_key() -> str:
    """Generate a standard base64 key that passes validate_key_format.

    Fernet keys use URL-safe base64 which Python 3.14 b64decode(validate=True)
    may reject. This fixture generates standard base64 (A-Z, a-z, 0-9, +, /, =)
    that is guaranteed to pass validate_key_format.
    """
    raw: bytes = os.urandom(32)
    return base64.b64encode(raw).decode("utf-8")


@pytest.fixture
def mcp_encryption_key(standard_b64_key: str) -> str:
    """Set MCP_ENCRYPTION_KEY env var and return the key value."""
    os.environ["MCP_ENCRYPTION_KEY"] = standard_b64_key
    return standard_b64_key


@pytest.fixture
def mcp_backup_key() -> str:
    """Set MCP_ENCRYPTION_KEY_BACKUP env var and return the key value."""
    backup_key: str = Fernet.generate_key().decode("utf-8")
    os.environ["MCP_ENCRYPTION_KEY_BACKUP"] = backup_key
    return backup_key


# ---------------------------------------------------------------------------
# 1. Encryption / Decryption roundtrip
# ---------------------------------------------------------------------------

class TestEncryptionRoundtrip:
    """Fernet encrypt → decrypt = original."""

    def test_encrypt_decrypt_roundtrip(self, mcp_encryption_key: str) -> None:
        """Raw key survives encrypt/decrypt cycle unchanged."""
        from app.core.encryption import decrypt_key, encrypt_key

        raw: str = secrets.token_urlsafe(32)
        encrypted: str = encrypt_key(raw)
        decrypted: str = decrypt_key(encrypted)

        assert decrypted == raw
        assert isinstance(encrypted, str)
        assert len(encrypted) > 0

    def test_encrypt_hex_is_valid_hex(self, mcp_encryption_key: str) -> None:
        """Encrypted output is a valid hex string."""
        from app.core.encryption import encrypt_key

        raw: str = secrets.token_urlsafe(32)
        encrypted: str = encrypt_key(raw)

        bytes.fromhex(encrypted)  # raises if invalid hex

    def test_decrypt_invalid_hex_raises(self, mcp_encryption_key: str) -> None:
        """Decrypting invalid hex raises ValueError."""
        from app.core.encryption import decrypt_key

        with pytest.raises(ValueError):
            decrypt_key("not_valid_hex!!")

    def test_decrypt_wrong_key_raises(self, mcp_encryption_key: str) -> None:
        """Decrypting ciphertext produced by a different key raises ValueError."""
        from app.core.encryption import decrypt_key, encrypt_key

        raw: str = secrets.token_urlsafe(32)
        different_key: str = Fernet.generate_key().decode("utf-8")
        os.environ["MCP_ENCRYPTION_KEY"] = different_key

        encrypted: str = encrypt_key(raw)

        os.environ["MCP_ENCRYPTION_KEY"] = mcp_encryption_key
        from app.core import encryption

        encryption._MCP_ENCRYPTION_KEY = None

        with pytest.raises(ValueError):
            decrypt_key(encrypted)


# ---------------------------------------------------------------------------
# 2. Key rotation (migrate_keys)
# ---------------------------------------------------------------------------

class TestKeyRotation:
    """rotate_key via migrate_keys — old key decrypts, new key decrypts."""

    def test_migrate_keys_decrypts_with_backup(self, mcp_encryption_key: str, mcp_backup_key: str) -> None:
        """Keys encrypted with backup key are successfully migrated."""
        from app.core.encryption import encrypt_key, migrate_keys

        raw: str = secrets.token_urlsafe(32)
        encrypted_with_backup: str = Fernet(mcp_backup_key.encode()).encrypt(raw.encode()).hex()

        keys: list[dict[str, str]] = [
            {"key_id": "user_1", "encrypted_key": encrypted_with_backup},
        ]

        result: dict = migrate_keys(mcp_backup_key, keys)

        assert result["success_rate"] == 1.0
        assert len(result["migrated"]) == 1
        assert len(result["failed"]) == 0

    def test_migrate_keys_fails_with_wrong_key(self, mcp_encryption_key: str) -> None:
        """Keys encrypted with a key different from backup_key fail."""
        from app.core.encryption import encrypt_key, migrate_keys

        # Create a key that is NOT the backup key
        wrong_key: str = Fernet.generate_key().decode("utf-8")
        raw: str = secrets.token_urlsafe(32)
        encrypted: str = Fernet(wrong_key.encode()).encrypt(raw.encode()).hex()

        keys: list[dict[str, str]] = [
            {"key_id": "user_1", "encrypted_key": encrypted},
        ]

        # Pass a different key as backup — should fail
        different_key: str = Fernet.generate_key().decode("utf-8")
        result: dict = migrate_keys(different_key, keys)

        assert result["success_rate"] == 0.0
        assert len(result["failed"]) == 1

    def test_migrate_keys_empty_list(self, mcp_encryption_key: str, mcp_backup_key: str) -> None:
        """Migrating empty list returns 0.0 success rate."""
        from app.core.encryption import migrate_keys

        result: dict = migrate_keys(mcp_backup_key, [])

        assert result["success_rate"] == 0.0
        assert result["migrated"] == []
        assert result["failed"] == []


# ---------------------------------------------------------------------------
# 3. Key entropy validation
# ---------------------------------------------------------------------------

class TestKeyEntropy:
    """secrets.token_urlsafe(32) produces 192 bits of entropy."""

    def test_generate_api_key_entropy(self) -> None:
        """Generated key length matches secrets.token_urlsafe(32) output."""
        from app.core.encryption import generate_api_key

        key: str = generate_api_key()

        assert len(key) >= 43
        assert len(key) <= 44

    def test_generate_api_key_is_random(self) -> None:
        """Two generated keys are never identical."""
        from app.core.encryption import generate_api_key

        key_a: str = generate_api_key()
        key_b: str = generate_api_key()

        assert key_a != key_b


# ---------------------------------------------------------------------------
# 4. import_keys — duplicate detection, min 32 chars validation
# ---------------------------------------------------------------------------

class TestImportKeys:
    """import_keys behavior: validation, duplicate handling."""

    def test_import_valid_keys(self, mcp_encryption_key: str, standard_b64_key: str) -> None:
        """Valid keys (standard base64, 44 chars) are encrypted and returned."""
        from app.core.encryption import import_keys, validate_key_format

        assert validate_key_format(standard_b64_key) is True

        keys: list[dict[str, str]] = [
            {"key_id": "user_1", "raw_key": standard_b64_key},
            {"key_id": "user_2", "raw_key": standard_b64_key},
        ]

        result: list[dict[str, str]] = import_keys(keys)

        assert len(result) == 2
        assert all("encrypted_key" in entry for entry in result)

    def test_import_skips_short_key(self, mcp_encryption_key: str, standard_b64_key: str) -> None:
        """Keys shorter than 32 chars are skipped."""
        from app.core.encryption import import_keys

        keys: list[dict[str, str]] = [
            {"key_id": "short_1", "raw_key": "abc"},
            {"key_id": "short_2", "raw_key": "a" * 31},
            {"key_id": "ok", "raw_key": standard_b64_key},
        ]

        result: list[dict[str, str]] = import_keys(keys)

        assert len(result) == 1
        assert result[0]["key_id"] == "ok"

    def test_import_skips_invalid_format(self, mcp_encryption_key: str, standard_b64_key: str) -> None:
        """Keys that are not valid Fernet format (standard base64, 44 chars) are skipped."""
        from app.core.encryption import import_keys

        keys: list[dict[str, str]] = [
            {"key_id": "bad_1", "raw_key": "not_base64!!!"},
            {"key_id": "bad_2", "raw_key": "!x" * 22},  # '!' not valid base64
            {"key_id": "ok", "raw_key": standard_b64_key},
        ]

        result: list[dict[str, str]] = import_keys(keys)

        assert len(result) == 1
        assert result[0]["key_id"] == "ok"

    def test_import_duplicate_key_id_renamed(self, mcp_encryption_key: str, standard_b64_key: str) -> None:
        """Duplicate key_ids are auto-renamed."""
        from app.core.encryption import import_keys

        keys: list[dict[str, str]] = [
            {"key_id": "dup", "raw_key": standard_b64_key},
            {"key_id": "dup", "raw_key": standard_b64_key},
            {"key_id": "dup", "raw_key": standard_b64_key},
        ]

        result: list[dict[str, str]] = import_keys(keys)

        assert len(result) == 3
        ids: list[str] = [entry["key_id"] for entry in result]
        assert "dup" in ids
        assert "dup_imported_1" in ids
        assert "dup_imported_2" in ids
        assert len(set(ids)) == 3


# ---------------------------------------------------------------------------
# 5. validate_key_format — valid / invalid keys
# ---------------------------------------------------------------------------

class TestValidateKeyFormat:
    """validate_key_format correctness."""

    def test_valid_fernet_key(self, standard_b64_key: str) -> None:
        """A proper standard base64 key passes validation."""
        from app.core.encryption import validate_key_format

        assert validate_key_format(standard_b64_key) is True

    def test_wrong_length(self) -> None:
        """Keys with wrong length fail validation."""
        from app.core.encryption import validate_key_format

        assert validate_key_format("short") is False
        assert validate_key_format("x" * 43) is False
        assert validate_key_format("x" * 45) is False

    def test_invalid_base64(self) -> None:
        """Non-base64 strings fail validation even at correct length."""
        from app.core.encryption import validate_key_format

        assert validate_key_format("!" * 44) is False
        assert validate_key_format(" " * 44) is False

    def test_valid_base64_wrong_length(self) -> None:
        """Valid base64 but wrong length fails."""
        from app.core.encryption import validate_key_format

        valid_b64_short: str = base64.b64encode(os.urandom(16)).decode("utf-8")
        assert validate_key_format(valid_b64_short) is False


# ---------------------------------------------------------------------------
# 6. validate_backup_key_format
# ---------------------------------------------------------------------------

class TestValidateBackupKeyFormat:
    """validate_backup_key_format mirrors validate_key_format."""

    def test_valid_backup_key(self, standard_b64_key: str) -> None:
        """Valid Fernet key passes backup validation."""
        from app.core.encryption import validate_backup_key_format

        assert validate_backup_key_format(standard_b64_key) is True

    def test_invalid_backup_key(self) -> None:
        """Invalid Fernet key fails backup validation."""
        from app.core.encryption import validate_backup_key_format

        assert validate_backup_key_format("not_a_key") is False
        assert validate_backup_key_format("x" * 43) is False

    def test_same_as_primary(self, standard_b64_key: str) -> None:
        """Backup validation result matches primary for same key."""
        from app.core.encryption import validate_backup_key_format, validate_key_format

        assert validate_backup_key_format(standard_b64_key) == validate_key_format(standard_b64_key)


# ---------------------------------------------------------------------------
# 7. verify_all
# ---------------------------------------------------------------------------

class TestVerifyAll:
    """verify_all — 99% threshold."""

    def test_verify_all_all_valid(self, mcp_encryption_key: str) -> None:
        """All keys encrypted with current key decryptable → True."""
        from app.core.encryption import encrypt_key, verify_all

        raw: str = secrets.token_urlsafe(32)
        encrypted: str = encrypt_key(raw)

        keys: list[dict[str, str]] = [
            {"key_id": f"user_{i}", "encrypted_key": encrypted}
            for i in range(10)
        ]

        assert verify_all(keys) is True

    def test_verify_all_below_threshold(self, mcp_encryption_key: str) -> None:
        """More than 1% failures → False."""
        from app.core.encryption import verify_all

        valid_key: str = Fernet.generate_key().decode("utf-8")
        raw: str = secrets.token_urlsafe(32)
        encrypted: str = Fernet(valid_key.encode()).encrypt(raw.encode()).hex()

        valid_entry: dict[str, str] = {"key_id": "user_0", "encrypted_key": encrypted}
        invalid_entries: list[dict[str, str]] = [
            {"key_id": f"user_{i}", "encrypted_key": "bad_hex!!!"}
            for i in range(1, 101)
        ]
        keys: list[dict[str, str]] = [valid_entry] + invalid_entries

        assert verify_all(keys) is False

    def test_verify_all_empty(self) -> None:
        """Empty list → False."""
        from app.core.encryption import verify_all

        assert verify_all([]) is False


# ---------------------------------------------------------------------------
# 8. clear_backup_key
# ---------------------------------------------------------------------------

class TestClearBackupKey:
    """clear_backup_key removes backup from memory."""

    def test_clear_removes_backup(self, mcp_backup_key: str) -> None:
        """After clear, backup key is None."""
        from app.core import encryption

        encryption.clear_backup_key()

        assert encryption._MCP_ENCRYPTION_KEY_BACKUP is None

    def test_clear_then_get_returns_none(self, mcp_backup_key: str) -> None:
        """clear_backup_key followed by _get_backup_key returns None."""
        from app.core import encryption

        encryption.clear_backup_key()
        # Also reset the env var cache
        os.environ.pop("MCP_ENCRYPTION_KEY_BACKUP", None)

        assert encryption._get_backup_key() is None
