# Security Policy

## Reporting Security Vulnerabilities

If you discover a security vulnerability in **MCP Web Search**, please report it responsibly:

1. **Do NOT open a public issue** — this could expose the vulnerability to attackers
2. Send a detailed report to the maintainers via private channel (GitHub Security Advisories or direct email)
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We will acknowledge receipt within **48 hours** and work to resolve the issue promptly.

## SSRF Protection

This project implements **Server-Side Request Forgery (SSRF)** protection in the `content` tool and all HTTP request paths:

### Protection Layers

1. **Private IP blocklist** — requests to private/reserved IP ranges are rejected:
   - `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
   - `127.0.0.0/8` (localhost)
   - `0.0.0.0`, `169.254.0.0/16` (link-local)
   - `::1` (IPv6 localhost)

2. **URL validation** — only `http://` and `https://` schemes allowed

3. **DNS resolution check** — resolved IP is verified against the blocklist before the request is made

4. **Redirect follow protection** — redirects are validated at each hop against the same rules

### Implementation

SSRF checks are applied in:
- `app/services/content_service.py` — main content extraction path
- `app/core/ssrf.py` — shared HTTP client wrapper
- `app/services/webfetch_service.py` — webfetch agent HTTP paths

## Secret Handling Policy

### Rules

1. **Never commit secrets** — API keys, passwords, tokens must never be in the codebase
2. **Use `.env` files** — all configuration via environment variables (see `.env.example`)
3. **Never log secrets** — sensitive values are masked in logs
4. **`.gitignore` protects** — `.env`, `.venv`, and credential files are excluded from git

### What is NOT a secret

- `.env.example` — template with placeholder values (safe to commit)
- Public API endpoints (DuckDuckGo, SearxNG public instances)
- Open-source library URLs

### What IS a secret

- `LLM_API_KEY` — any LLM provider API key
- `TAVILY_API_KEY` — Tavily search API key
- `GOOGLE_API_KEY`, `GOOGLE_CSE_ID` — Google Custom Search credentials
- `REDIS_URL` — if it contains authentication credentials
- Any other API token or credential

### If a secret is accidentally committed

1. **Rotate the secret immediately** — generate a new key/credential
2. **Do NOT just delete the commit** — the secret remains in git history
3. Use `git filter-branch` or `BFG Repo-Cleaner` to remove the secret from history
4. Report the incident to maintainers

## Dependency Security

- Production dependencies are scanned with **bandit** (`uv run bandit -r app/`)
- Vulnerable dependencies are updated promptly
- `uv.lock` is committed to track exact dependency versions

## Encryption Details

### Algorithm

API keys are encrypted using **Fernet** (from `cryptography` library):

- **Cipher:** AES-128-CBC
- **MAC:** HMAC-SHA256
- **Key format:** base64-encoded 32-byte key (44 characters)
- **Storage format:** hex-encoded ciphertext in SQLite (`app/core/user_store.py`)

Fernet provides authenticated encryption — any tampering with ciphertext is detected via HMAC verification.

### Key Management

| Environment Variable | Purpose | Validation |
|----------------------|---------|------------|
| `MCP_ENCRYPTION_KEY` | Primary encryption key | Validated at startup via `encryption.validate_key_format()`; server fails to start if invalid |
| `MCP_ENCRYPTION_KEY_BACKUP` | Backup key for recovery | Same validation as primary; invalid backup → warning logged, skipped (primary key still used) |

**Key sourcing rules:**

- Keys are read exclusively from environment variables (`app/core/encryption.py`)
- Never hardcoded in code, database, or logs
- `_load_from_env()` returns `None` for absent or empty values

### Key Rotation Procedure

1. Call `user_manage rotate_key` action (admin scope)
2. Server increments `key_version` in DB, generates new `key_id`
3. Old key is auto-revoked (status → `revoked`)
4. Redis cache cleared for old `key_id`
5. Rate limit counters carried over to new `key_id` (user_id unchanged)

### Backup Key Recovery Procedure

Used when the primary `MCP_ENCRYPTION_KEY` is lost:

1. Set `MCP_ENCRYPTION_KEY_BACKUP` environment variable
2. Restart server — encryption layer auto-detects backup key:
   - Primary key succeeds → backup stored dormant (never used)
   - `Fernet.InvalidToken` → backup key activated
   - **Dual-key behavior:** both keys in memory; primary attempted first; backup on `InvalidToken`
3. Run `encryption.migrate_keys()` — decrypt all keys with backup, re-encrypt with new key
4. Verify via `encryption.verify_all()` — success rate must be ≥ 99%
5. Remove backup key from memory via `encryption.clear_backup_key()`
6. Clear `MCP_ENCRYPTION_KEY_BACKUP` environment variable

**Rollback:** if migration success rate < 99% → restore primary key, log failure, manual intervention required.

### Migration Walkthrough: Key Rotation with Active Users

Concrete step-by-step example for rotating encryption keys while preserving existing user data:

**Pre-rotation state:**
- Primary key: `MCP_ENCRYPTION_KEY=Ck8x...44-char-Fernet-key` (active)
- 15 active users with encrypted keys in SQLite
- Redis cache populated with decrypted keys for active sessions

**Step 1 — Generate new key:**
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Output: Nw9y...44-char-new-Fernet-key
```

**Step 2 — Set both keys in .env:**
```bash
# .env
MCP_ENCRYPTION_KEY=Ck8x...44-char-Fernet-key        # keep primary (still valid)
MCP_ENCRYPTION_KEY_BACKUP=Nw9y...44-char-new-key     # new key as backup
```

**Step 3 — Restart server:**
```bash
uv run python -m app.main
# Server starts: primary key validated, backup stored dormant
```

**Step 4 — Rotate via admin tool (from connected MCP client):**
```json
{
  "tool": "user_manage",
  "action": "rotate_key",
  "parameters": {
    "user_id": "a1b2c3d4e5f6..."
  }
}
```
- Server increments `key_version` → 2
- Generates new `key_id` (e.g., `key_7f8g9h0i1j2k`)
- Encrypts raw key with primary key (still active)
- Returns raw key **one-time only** to admin
- Old key for this user → status `revoked`

**Step 5 — Verify migration:**
```json
{
  "tool": "encryption",
  "action": "verify_all"
}
```
Expected output: `{"success_rate": 1.0, "total_keys": 15, "failed": 0}`

**Step 6 — Remove backup key:**
```bash
# Remove MCP_ENCRYPTION_KEY_BACKUP from .env
# Restart server to clear from memory
```

**Post-rotation state:**
- All user keys encrypted with primary key (key_version = 2)
- Old keys revoked, Redis cache invalidated
- Rate limit counters preserved (user_id unchanged, key_id updated)

### Audit Log Example Walkthrough

Example of what operators see in structured logs after a key rotation:

```
2026-05-23T14:32:01Z  INFO  key_rotated  user_id=a1b2c3d4e5f6...  user_name=api-client-01  key_version=2  actor=admin
2026-05-23T14:32:01Z  INFO  limits_updated  user_id=a1b2c3d4e5f6...  user_name=api-client-01  daily=200  weekly=1000  monthly=5000  actor=admin
2026-05-23T14:32:02Z  INFO  user_created  user_id=f6e5d4c3b2a1...  user_name=new-service  rate_limits={"daily":100,"weekly":500,"monthly":2000}  actor=admin
2026-05-23T14:35:17Z  WARN  invalid_token  user_id=a1b2c3d4e5f6...  key_id=key_old_revoked  tool_name=search  result=denied  timestamp=2026-05-23T14:35:17Z
```

**Rollback:** if migration success rate < 99% → restore primary key, log failure, manual intervention required.

### Audit Logging Events

| Event | Source | Schema |
|-------|--------|--------|
| `user_created` | `user_manage create` | `{event, user_id, user_name, timestamp, actor}` |
| `user_revoked` | `user_manage revoke` | `{event, user_id, user_name, timestamp, actor}` |
| `key_rotated` | `user_manage rotate_key` | `{event, user_id, user_name, timestamp, actor}` |
| `limits_updated` | `user_manage update_limits` | `{event, user_id, user_name, timestamp, actor}` |
| `token_limits_updated` | `user_manage update_token_limits` | `{event, user_id, user_name, timestamp, actor}` |
| `invalid_token` | `token_verifier` | `{event, user_id, key_id, tool_name, result, timestamp}` |
| `rate_limit_exceeded` | `rate_limiter` | `{event, user_id, key_id, tool_name, result, timestamp}` |
| `user_disabled` | `token_verifier` | `{event, user_id, key_id, tool_name, result, timestamp}` |

**Note:** token cost data (input/output tokens) is NOT included in audit logs — audit logs contain event metadata only.

### Security Requirements

| Requirement | Implementation |
|-------------|----------------|
| Encryption keys env-only | `MCP_ENCRYPTION_KEY`, `MCP_ENCRYPTION_KEY_BACKUP` from `.env`, never in code/DB/logs |
| Keys encrypted in DB | Fernet hex-encoded ciphertext in `app/core/user_store.py` |
| Constant-time comparison | `hmac.compare_digest()` in `token_verifier` (prevents timing attacks) |
| Key entropy validation | `secrets.token_urlsafe(32)` → 192 bits; imported keys min 32 chars |
| Key format validation | Startup validation via `validate_key_format()` / `validate_backup_key_format()` |
| HMAC integrity | Fernet HMAC-SHA256 detects ciphertext tampering |
| Admin scope enforcement | `require_scopes("admin")` on `user_manage` tool |
| Key revocation immediate | Redis cache invalidation + DB status update |

## Known Security Considerations

| Area | Risk | Mitigation |
|------|------|------------|
| External URL fetching | SSRF | IP blocklist + DNS check + redirect validation |
| LLM API keys | Credential exposure | `.env` only, never logged, `.gitignore` |
| Redis connection | Unauthorized access | `REDIS_URL` in `.env`, network isolation in Docker |
| HTML sanitization | XSS in extracted content | `bleach` library for HTML cleanup |
| User-provided URLs | Malicious payloads | URL scheme validation + SSRF checks |
