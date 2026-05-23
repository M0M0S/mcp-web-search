"""Fernet encryption utilities for MCP authorization system.

Provides key generation, encryption/decryption, validation, and migration
functions. All secrets are sourced exclusively from environment variables.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from structlog import get_logger

logger = get_logger()

# ---------------------------------------------------------------------------
# Environment keys — never hardcoded
# ---------------------------------------------------------------------------
_MCP_ENCRYPTION_KEY: str | None = None
_MCP_ENCRYPTION_KEY_BACKUP: str | None = None


def _get_encryption_key() -> str:
    """Return the primary MCP encryption key from environment.

    Raises RuntimeError if the key is not configured.
    """
    global _MCP_ENCRYPTION_KEY
    if _MCP_ENCRYPTION_KEY is None:
        _MCP_ENCRYPTION_KEY = _load_from_env("MCP_ENCRYPTION_KEY")
    if _MCP_ENCRYPTION_KEY is None:
        raise RuntimeError(
            "MCP_ENCRYPTION_KEY environment variable is not set. "
            "This is required for the encryption layer to start."
        )
    return _MCP_ENCRYPTION_KEY


def _get_backup_key() -> str | None:
    """Return the backup MCP encryption key from environment, if configured."""
    global _MCP_ENCRYPTION_KEY_BACKUP
    if _MCP_ENCRYPTION_KEY_BACKUP is None:
        _MCP_ENCRYPTION_KEY_BACKUP = _load_from_env("MCP_ENCRYPTION_KEY_BACKUP")
    return _MCP_ENCRYPTION_KEY_BACKUP


def _load_from_env(name: str) -> str | None:
    """Read an environment variable, returning None if absent or empty."""
    value = os.environ.get(name)
    if not value:
        return None
    return value


# ---------------------------------------------------------------------------
# 1. generate_api_key
# ---------------------------------------------------------------------------
def generate_api_key() -> str:
    """Generate a URL-safe API key with 192 bits of entropy.

    Uses ``secrets.token_urlsafe(32)`` which produces 32 base64-encoded
    bytes (192 bits of randomness).
    """
    key: str = secrets.token_urlsafe(32)
    logger.info("api_key_generated", entropy_bits=192)
    return key


# ---------------------------------------------------------------------------
# 2. encrypt_key
# ---------------------------------------------------------------------------
def encrypt_key(raw_key: str) -> str:
    """Encrypt a raw key string using Fernet and return a hex-encoded ciphertext.

    Args:
        raw_key: The plaintext key to encrypt.

    Returns:
        Hex-encoded Fernet ciphertext string.

    Raises:
        RuntimeError: If MCP_ENCRYPTION_KEY is not configured in the environment.
    """
    key_bytes: bytes = _get_encryption_key().encode("utf-8")
    fernet: Fernet = Fernet(key_bytes)
    ciphertext: bytes = fernet.encrypt(raw_key.encode("utf-8"))
    hex_result: str = ciphertext.hex()
    logger.info("key_encrypted", length=len(raw_key))
    return hex_result


# ---------------------------------------------------------------------------
# 3. decrypt_key
# ---------------------------------------------------------------------------
def decrypt_key(encrypted_hex: str) -> str:
    """Decrypt a hex-encoded Fernet ciphertext back to the raw key string.

    Args:
        encrypted_hex: Hex-encoded Fernet ciphertext.

    Returns:
        The decrypted plaintext key string.

    Raises:
        ValueError: If the token is invalid or cannot be decrypted.
    """
    key_bytes: bytes = _get_encryption_key().encode("utf-8")
    fernet: Fernet = Fernet(key_bytes)
    ciphertext: bytes = bytes.fromhex(encrypted_hex)
    try:
        plaintext: bytes = fernet.decrypt(ciphertext)
    except InvalidToken:
        logger.warning("key_decrypt_failed", encrypted_hex_length=len(encrypted_hex))
        raise ValueError(
            "Failed to decrypt key: invalid or tampered ciphertext."
        ) from None
    logger.info("key_decrypted", length=len(plaintext))
    return plaintext.decode("utf-8")


# ---------------------------------------------------------------------------
# 4. validate_key_format
# ---------------------------------------------------------------------------
def validate_key_format(key: str) -> bool:
    """Validate that a string is a proper Fernet base64-encoded 32-byte key.

    A valid Fernet key is exactly 44 characters of base64-encoded 32 bytes.

    Args:
        key: The candidate key string to validate.

    Returns:
        True if the key passes format validation, False otherwise.
    """
    if len(key) != 44:
        return False
    try:
        base64.b64decode(key, validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


# ---------------------------------------------------------------------------
# 5. validate_backup_key_format
# ---------------------------------------------------------------------------
def validate_backup_key_format(key: str) -> bool:
    """Validate a backup key using the same rules as the primary key.

    Args:
        key: The candidate backup key string.

    Returns:
        True if the key has valid Fernet format, False otherwise.
    """
    return validate_key_format(key)


# ---------------------------------------------------------------------------
# 6. migrate_keys
# ---------------------------------------------------------------------------
def migrate_keys(backup_key: str, keys: list[dict[str, str]]) -> dict[str, Any]:
    """Migrate encrypted keys from a backup key to the current primary key.

    Decrypts each key using the provided backup key, then re-encrypts it
    with the current MCP_ENCRYPTION_KEY.

    Args:
        backup_key: The backup Fernet key used for decryption.
        keys: List of dicts with ``key_id`` and ``encrypted_key`` (hex).

    Returns:
        Dict with ``migrated`` (successful list), ``failed`` (failed list),
        and ``success_rate`` (float 0.0–1.0).
    """
    migrated: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []

    backup_key_bytes: bytes = backup_key.encode("utf-8")
    backup_fernet: Fernet = Fernet(backup_key_bytes)

    total: int = len(keys)
    if total == 0:
        logger.info("key_migration_no_keys")
        return {"migrated": [], "failed": [], "success_rate": 0.0}

    for entry in keys:
        key_id: str = entry.get("key_id", "unknown")
        encrypted_hex: str = entry.get("encrypted_key", "")
        try:
            raw_key: str = decrypt_with_key(backup_fernet, encrypted_hex)
            new_encrypted: str = encrypt_key(raw_key)
            migrated.append({"key_id": key_id, "encrypted_key": new_encrypted})
        except Exception:
            failed.append({"key_id": key_id, "encrypted_key": encrypted_hex})
            logger.warning("key_migration_failed", key_id=key_id)

    success_rate: float = len(migrated) / total if total > 0 else 0.0
    logger.info(
        "key_migration_complete",
        total=total,
        migrated=len(migrated),
        failed=len(failed),
        success_rate=success_rate,
    )

    if success_rate < 0.99:
        logger.error(
            "key_migration_low_success_rate",
            success_rate=success_rate,
            threshold=0.99,
        )

    return {
        "migrated": migrated,
        "failed": failed,
        "success_rate": success_rate,
    }


def decrypt_with_key(fernet: Fernet, encrypted_hex: str) -> str:
    """Decrypt hex ciphertext using a given Fernet instance.

    Args:
        fernet: The Fernet instance to use for decryption.
        encrypted_hex: Hex-encoded ciphertext.

    Returns:
        Decrypted plaintext string.

    Raises:
        ValueError: If decryption fails.
    """
    ciphertext: bytes = bytes.fromhex(encrypted_hex)
    try:
        plaintext: bytes = fernet.decrypt(ciphertext)
    except InvalidToken:
        raise ValueError("Invalid token for provided Fernet key.") from None
    return plaintext.decode("utf-8")


# ---------------------------------------------------------------------------
# 7. verify_all
# ---------------------------------------------------------------------------
def verify_all(keys: list[dict[str, str]]) -> bool:
    """Verify that at least 99% of encrypted keys can be successfully decrypted.

    Uses primary key first; if decryption fails, attempts cross-verification
    with the backup key. Keys decryptable by the backup key are counted as
    valid (they were encrypted with the backup and need migration).

    Args:
        keys: List of dicts with ``key_id`` and ``encrypted_key`` (hex).

    Returns:
        True if the decryption success rate >= 0.99, False otherwise.
    """
    total: int = len(keys)
    if total == 0:
        logger.info("key_verify_no_keys")
        return False

    success_count: int = 0
    failed_count: int = 0
    migration_needed: list[str] = []
    for entry in keys:
        key_id: str = entry.get("key_id", "unknown")
        encrypted_hex: str = entry.get("encrypted_key", "")
        try:
            decrypt_key(encrypted_hex)
            success_count += 1
        except ValueError:
            # Cross-verify with backup key
            backup_key = _get_backup_key()
            if backup_key is not None:
                try:
                    backup_key_bytes: bytes = backup_key.encode("utf-8")
                    backup_fernet: Fernet = Fernet(backup_key_bytes)
                    decrypt_with_key(backup_fernet, encrypted_hex)
                    success_count += 1
                    logger.info(
                        "key_verify_backup_match",
                        key_id=key_id,
                        note="encrypted with backup key — migration needed",
                    )
                    migration_needed.append(key_id)
                    continue
                except ValueError:
                    pass
            failed_count += 1
            logger.warning("key_verify_failed", key_id=key_id)

    rate: float = success_count / total
    passed: bool = rate >= 0.99
    logger.info(
        "key_verify_result",
        total=total,
        success=success_count,
        failed=failed_count,
        rate=rate,
        passed=passed,
        migration_needed=len(migration_needed),
    )
    return passed


# ---------------------------------------------------------------------------
# 8. import_keys
# ---------------------------------------------------------------------------
def import_keys(keys: list[dict[str, str]]) -> list[dict[str, str]]:
    """Import raw keys, validate them, and return encrypted versions.

    Args:
        keys: List of dicts with ``key_id`` and ``raw_key``.

    Returns:
        List of dicts with ``key_id`` and ``encrypted_key`` (hex).
        Invalid raw keys are skipped; duplicate key_ids are auto-renamed.
    """
    result: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    for entry in keys:
        key_id: str = entry.get("key_id", "")
        raw_key: str = entry.get("raw_key", "")

        # Validate raw_key length
        if len(raw_key) < 32:
            logger.warning(
                "key_import_skipped_short_key",
                key_id=key_id,
                length=len(raw_key),
                min_length=32,
            )
            continue

        # Validate Fernet format
        if not validate_key_format(raw_key):
            logger.warning(
                "key_import_skipped_invalid_format",
                key_id=key_id,
                note="raw key does not match Fernet base64 32-byte format",
            )
            continue

        # Handle duplicate key_id
        effective_id: str = key_id
        if effective_id in seen_ids:
            counter: int = 1
            while True:
                renamed: str = f"{key_id}_imported_{counter}"
                if renamed not in seen_ids:
                    effective_id = renamed
                    logger.info(
                        "key_import_renamed_duplicate",
                        original=key_id,
                        renamed=renamed,
                    )
                    break
                counter += 1

        seen_ids.add(effective_id)

        # Encrypt the raw key
        encrypted_hex: str = encrypt_key(raw_key)
        result.append({"key_id": effective_id, "encrypted_key": encrypted_hex})

    logger.info(
        "key_import_complete",
        imported=len(result),
        total=len(keys),
    )
    return result


# ---------------------------------------------------------------------------
# 9. clear_backup_key
# ---------------------------------------------------------------------------
def clear_backup_key() -> None:
    """Remove the backup encryption key from memory."""
    global _MCP_ENCRYPTION_KEY_BACKUP
    _MCP_ENCRYPTION_KEY_BACKUP = None
    logger.info("backup_key_cleared")
