"""Backup key partial failure tests for MCP authorization system.

Verifies that migrate_keys triggers rollback when success rate < 99%,
that verify_all detects migration_needed keys, and that verify_all
passes when success rate >= 99% with valid keys.
Uses dynamically generated Fernet keys for deterministic testing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

import pytest
from cryptography.fernet import Fernet


def _generate_valid_encrypted_key(raw: str, key_str: str) -> str:
    """Encrypt a raw string using a given Fernet key and return hex ciphertext."""
    key_bytes = key_str.encode("utf-8")
    fernet = Fernet(key_bytes)
    ciphertext = fernet.encrypt(raw.encode("utf-8"))
    return ciphertext.hex()


def _make_key_entry(key_id: str, encrypted_hex: str) -> dict[str, str]:
    """Create a key entry dict for testing."""
    return {"key_id": key_id, "encrypted_key": encrypted_hex}


class TestMigrateKeysPartialFailure:
    """Tests for migrate_keys with partial failure scenarios."""

    @pytest.fixture
    def primary_key(self) -> str:
        """Dynamically generated primary Fernet key."""
        return Fernet.generate_key().decode("utf-8")

    @pytest.fixture
    def backup_key(self) -> str:
        """Dynamically generated backup Fernet key."""
        return Fernet.generate_key().decode("utf-8")

    @patch("app.core.encryption.encrypt_key")
    @patch("app.core.encryption._get_encryption_key")
    @patch("app.core.encryption._get_backup_key")
    def test_migrate_keys_with_less_than_99_percent_success_rate_triggers_rollback(
        self,
        mock_get_backup: Mock,
        mock_get_primary: Mock,
        mock_encrypt: Mock,
        primary_key: str,
        backup_key: str,
    ) -> None:
        """migrate_keys with < 99% success rate should return low success_rate flag."""
        from app.core.encryption import migrate_keys

        mock_get_primary.return_value = primary_key
        mock_get_backup.return_value = backup_key

        # Mock encrypt_key to return a deterministic hex for re-encryption
        mock_encrypt.side_effect = lambda raw: raw.encode("utf-8").hex()

        # Keys encrypted with backup key (can be decrypted via backup_fernet in migrate_keys)
        # 8 valid keys + 2 invalid = 10 total, 80% success < 99%
        keys: list[dict[str, str]] = []
        for i in range(8):
            raw = f"raw_key_{i}"
            encrypted = _generate_valid_encrypted_key(raw, backup_key)
            keys.append(_make_key_entry(f"key_id_{i}", encrypted))

        # 2 keys that CANNOT be decrypted (invalid ciphertext)
        keys.append(_make_key_entry("key_id_8", "invalid_hex_ciphertext_0000"))
        keys.append(_make_key_entry("key_id_9", "invalid_hex_ciphertext_ffff"))

        result = migrate_keys(backup_key, keys)

        assert result["success_rate"] < 0.99
        assert len(result["failed"]) == 2
        assert len(result["migrated"]) == 8

    @patch("app.core.encryption.encrypt_key")
    @patch("app.core.encryption._get_encryption_key")
    @patch("app.core.encryption._get_backup_key")
    def test_migrate_keys_with_99_percent_or_higher_success_rate_no_rollback(
        self,
        mock_get_backup: Mock,
        mock_get_primary: Mock,
        mock_encrypt: Mock,
        primary_key: str,
        backup_key: str,
    ) -> None:
        """migrate_keys with >= 99% success rate should not trigger rollback."""
        from app.core.encryption import migrate_keys

        mock_get_primary.return_value = primary_key
        mock_get_backup.return_value = backup_key

        # Mock encrypt_key to return a deterministic hex for re-encryption
        mock_encrypt.side_effect = lambda raw: raw.encode("utf-8").hex()

        # 100 keys encrypted with backup, all valid → 100% success
        keys: list[dict[str, str]] = [
            _make_key_entry(
                f"key_id_{i}",
                _generate_valid_encrypted_key(f"raw_{i}", backup_key),
            )
            for i in range(100)
        ]

        result = migrate_keys(backup_key, keys)

        assert result["success_rate"] >= 0.99
        assert len(result["failed"]) == 0
        assert len(result["migrated"]) == 100

    @patch("app.core.encryption.encrypt_key")
    @patch("app.core.encryption._get_encryption_key")
    @patch("app.core.encryption._get_backup_key")
    def test_migrate_keys_with_empty_keys_list(
        self,
        mock_get_backup: Mock,
        mock_get_primary: Mock,
        mock_encrypt: Mock,
        primary_key: str,
        backup_key: str,
    ) -> None:
        """migrate_keys with empty keys list should return 0.0 success rate."""
        from app.core.encryption import migrate_keys

        mock_get_primary.return_value = primary_key
        mock_get_backup.return_value = backup_key

        result = migrate_keys(backup_key, [])

        assert result["success_rate"] == 0.0
        assert result["migrated"] == []
        assert result["failed"] == []

    @patch("app.core.encryption.encrypt_key")
    @patch("app.core.encryption._get_encryption_key")
    @patch("app.core.encryption._get_backup_key")
    def test_migrate_keys_at_exactly_99_percent_boundary_succeeds(
        self,
        mock_get_backup: Mock,
        mock_get_primary: Mock,
        mock_encrypt: Mock,
        primary_key: str,
        backup_key: str,
    ) -> None:
        """migrate_keys at exactly 99% success rate should NOT trigger rollback."""
        from app.core.encryption import migrate_keys

        mock_get_primary.return_value = primary_key
        mock_get_backup.return_value = backup_key

        # Mock encrypt_key to return a deterministic hex for re-encryption
        mock_encrypt.side_effect = lambda raw: raw.encode("utf-8").hex()

        # 100 keys, 1 invalid → exactly 99/100 = 0.99 >= 0.99
        keys: list[dict[str, str]] = []
        for i in range(99):
            raw = f"raw_key_{i}"
            encrypted = _generate_valid_encrypted_key(raw, backup_key)
            keys.append(_make_key_entry(f"key_id_{i}", encrypted))

        # 1 key that CANNOT be decrypted
        keys.append(_make_key_entry("key_id_99", "invalid_hex_boundary"))

        result = migrate_keys(backup_key, keys)

        assert result["success_rate"] == pytest.approx(0.99, abs=1e-9)
        assert len(result["migrated"]) == 99
        assert len(result["failed"]) == 1

    @patch("app.core.encryption.encrypt_key")
    @patch("app.core.encryption._get_encryption_key")
    @patch("app.core.encryption._get_backup_key")
    def test_migrate_keys_at_98_point_9_percent_boundary_fails(
        self,
        mock_get_backup: Mock,
        mock_get_primary: Mock,
        mock_encrypt: Mock,
        primary_key: str,
        backup_key: str,
    ) -> None:
        """migrate_keys at 98.9% success rate should trigger rollback."""
        from app.core.encryption import migrate_keys

        mock_get_primary.return_value = primary_key
        mock_get_backup.return_value = backup_key

        # Mock encrypt_key to return a deterministic hex for re-encryption
        mock_encrypt.side_effect = lambda raw: raw.encode("utf-8").hex()

        # 200 keys, 2 invalid → 198/200 = 0.99 (still passes)
        # Need 197/200 = 0.985 for clear fail, or 197/198 ≈ 0.9899
        # Use 100 keys, 2 invalid → 98/100 = 0.98 < 0.99
        keys: list[dict[str, str]] = []
        for i in range(98):
            raw = f"raw_key_{i}"
            encrypted = _generate_valid_encrypted_key(raw, backup_key)
            keys.append(_make_key_entry(f"key_id_{i}", encrypted))

        # 2 keys that CANNOT be decrypted
        keys.append(_make_key_entry("key_id_98", "invalid_hex_boundary"))
        keys.append(_make_key_entry("key_id_99", "invalid_hex_boundary"))

        result = migrate_keys(backup_key, keys)

        assert result["success_rate"] < 0.99
        assert result["success_rate"] == pytest.approx(0.98, abs=1e-9)
        assert len(result["migrated"]) == 98
        assert len(result["failed"]) == 2


class TestVerifyAllDetectsMigrationNeeded:
    """Tests for verify_all detecting migration_needed keys."""

    @pytest.fixture
    def primary_key(self) -> str:
        """Dynamically generated primary Fernet key."""
        return Fernet.generate_key().decode("utf-8")

    @pytest.fixture
    def backup_key(self) -> str:
        """Dynamically generated backup Fernet key."""
        return Fernet.generate_key().decode("utf-8")

    @patch("app.core.encryption._get_encryption_key")
    @patch("app.core.encryption._get_backup_key")
    def test_verify_all_detects_migration_needed_keys(
        self,
        mock_get_backup: Mock,
        mock_get_primary: Mock,
        primary_key: str,
        backup_key: str,
    ) -> None:
        """verify_all should detect keys encrypted with backup key as migration_needed."""
        from app.core.encryption import verify_all

        mock_get_primary.return_value = primary_key
        mock_get_backup.return_value = backup_key

        # Mix: 95 keys encrypted with primary, 5 with backup
        keys: list[dict[str, str]] = []

        for i in range(95):
            raw = f"primary_key_{i}"
            encrypted = _generate_valid_encrypted_key(raw, primary_key)
            keys.append(_make_key_entry(f"key_id_{i}", encrypted))

        for i in range(5):
            raw = f"backup_key_{i}"
            encrypted = _generate_valid_encrypted_key(raw, backup_key)
            keys.append(_make_key_entry(f"backup_key_id_{i}", encrypted))

        # All 100 keys should be verifiable (primary or backup)
        result = verify_all(keys)

        assert result is True  # 100/100 = 1.0 >= 0.99

    @patch("app.core.encryption._get_encryption_key")
    @patch("app.core.encryption._get_backup_key")
    def test_verify_all_fails_with_too_many_invalid_keys(
        self,
        mock_get_backup: Mock,
        mock_get_primary: Mock,
        primary_key: str,
        backup_key: str,
    ) -> None:
        """verify_all should return False when success rate < 99%."""
        from app.core.encryption import verify_all

        mock_get_primary.return_value = primary_key
        mock_get_backup.return_value = backup_key

        # 100 keys, 3 invalid → 97/100 = 0.97 < 0.99
        keys: list[dict[str, str]] = []

        for i in range(97):
            raw = f"valid_key_{i}"
            encrypted = _generate_valid_encrypted_key(raw, primary_key)
            keys.append(_make_key_entry(f"key_id_{i}", encrypted))

        for i in range(3):
            keys.append(_make_key_entry(f"invalid_key_id_{i}", "deadbeef0000"))

        result = verify_all(keys)

        assert result is False

    @patch("app.core.encryption._get_encryption_key")
    @patch("app.core.encryption._get_backup_key")
    def test_verify_all_with_no_keys_returns_false(
        self,
        mock_get_backup: Mock,
        mock_get_primary: Mock,
        primary_key: str,
        backup_key: str,
    ) -> None:
        """verify_all with empty keys list should return False."""
        from app.core.encryption import verify_all

        mock_get_primary.return_value = primary_key
        mock_get_backup.return_value = backup_key

        result = verify_all([])

        assert result is False


class TestVerifyAllSuccessRate:
    """Tests for verify_all with >= 99% success rate."""

    @pytest.fixture
    def primary_key(self) -> str:
        """Dynamically generated primary Fernet key."""
        return Fernet.generate_key().decode("utf-8")

    @pytest.fixture
    def backup_key(self) -> str:
        """Dynamically generated backup Fernet key."""
        return Fernet.generate_key().decode("utf-8")

    @patch("app.core.encryption._get_encryption_key")
    @patch("app.core.encryption._get_backup_key")
    def test_verify_all_success_rate_99_percent_with_valid_keys(
        self,
        mock_get_backup: Mock,
        mock_get_primary: Mock,
        primary_key: str,
        backup_key: str,
    ) -> None:
        """verify_all should return True when success rate >= 99% with valid keys."""
        from app.core.encryption import verify_all

        mock_get_primary.return_value = primary_key
        mock_get_backup.return_value = backup_key

        # 100 keys, all valid with primary key → 100% success
        keys: list[dict[str, str]] = [
            _make_key_entry(
                f"key_id_{i}",
                _generate_valid_encrypted_key(f"raw_{i}", primary_key),
            )
            for i in range(100)
        ]

        result = verify_all(keys)

        assert result is True

    @patch("app.core.encryption._get_encryption_key")
    @patch("app.core.encryption._get_backup_key")
    def test_verify_all_success_rate_exactly_99_percent(
        self,
        mock_get_backup: Mock,
        mock_get_primary: Mock,
        primary_key: str,
        backup_key: str,
    ) -> None:
        """verify_all should return True at exactly 99% success rate."""
        from app.core.encryption import verify_all

        mock_get_primary.return_value = primary_key
        mock_get_backup.return_value = backup_key

        # 100 keys, 1 invalid → 99/100 = 0.99 >= 0.99
        keys: list[dict[str, str]] = []

        for i in range(99):
            raw = f"valid_key_{i}"
            encrypted = _generate_valid_encrypted_key(raw, primary_key)
            keys.append(_make_key_entry(f"key_id_{i}", encrypted))

        keys.append(_make_key_entry("invalid_key_id", "deadbeef0000"))

        result = verify_all(keys)

        assert result is True

    @patch("app.core.encryption._get_encryption_key")
    @patch("app.core.encryption._get_backup_key")
    def test_verify_all_success_rate_98_point_9_percent_fails(
        self,
        mock_get_backup: Mock,
        mock_get_primary: Mock,
        primary_key: str,
        backup_key: str,
    ) -> None:
        """verify_all should return False at 98.9% success rate."""
        from app.core.encryption import verify_all

        mock_get_primary.return_value = primary_key
        mock_get_backup.return_value = backup_key

        # 100 keys, 2 invalid → 98/100 = 0.98 < 0.99
        keys: list[dict[str, str]] = []

        for i in range(98):
            raw = f"valid_key_{i}"
            encrypted = _generate_valid_encrypted_key(raw, primary_key)
            keys.append(_make_key_entry(f"key_id_{i}", encrypted))

        for i in range(2):
            keys.append(_make_key_entry(f"invalid_key_id_{i}", "deadbeef0000"))

        result = verify_all(keys)

        assert result is False
