# MCP Server Authorization System — Feature Plan (Revised v2)

## Overview

Implement user-based authorization for MCP Web Search server with:
- Dynamic API key issuance and management
- Per-user rate limiting (daily/weekly/monthly)
- Encrypted key storage in database
- New MCP tool for user management
- Key rotation capability

## Architecture

```
FastMCP Server
├── Auth: DebugTokenVerifier (async lookup via FastMCP auth API)
│   ├── Redis: rate limit counters + user cache (TTL 5min)
│   └── DB: user records + encrypted keys
├── Tools: search, content, webfetch (existing)
└── Tool: user_manage (new) — admin scope
```

## FastMCP Auth Integration (explicit)

```python
from fastmcp import FastMCP
from fastmcp.server.auth import DebugTokenVerifier

async def verify_token(token: str) -> AuthContext | None:
    # Token format: "key_<key_id>"
    key_id = token.removeprefix("key_")
    user = await user_store.lookup_by_key_id(key_id)
    if not user:
        return None
    
    # Check Redis rate limits (after DB lookup, before decrypt)
    exceeded = await rate_limiter.check(user.id)
    if exceeded:
        raise RateLimitExceeded()
    
    # Decrypt key from DB
    decrypted = encryption.decrypt(user.encrypted_key)
    if decrypted != token:
        return None
    
    # Check user status
    if user.status != "active":
        return None
    
    # Increment rate limit counter
    await rate_limiter.increment(user.id)
    
    return AuthContext(
        client_id=user.id,
        scopes=["read"] if user.status == "active" else [],
    )

# Conditional auth setup
if settings.MCP_ENCRYPTION_KEY:
    auth = DebugTokenVerifier(validate=verify_token)
    mcp = FastMCP("web-search", auth=auth)
else:
    mcp = FastMCP("web-search")  # no auth (backward compatible)
```

## Components

### 1. User Model & Schema

```python
class User:
    id: UUID
    name: str
    key_id: str (public identifier, e.g. "key_abc123")
    encrypted_key: bytes (Fernet AES-128-CBC + HMAC-SHA256)
    key_version: int (API key encryption version — tracks which encryption key was used to encrypt this API key)
    status: active | disabled | revoked
    rate_limits: {daily: int, weekly: int, monthly: int}
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | null
```

### 2. DB Schema (SQLite)

```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,          -- UUID hex
    name TEXT NOT NULL,
    key_id TEXT UNIQUE NOT NULL,  -- public identifier (UNIQUE enforced by SQLite)
    encrypted_key TEXT NOT NULL,  -- Fernet encrypted key as hex
    key_version INTEGER NOT NULL DEFAULT 1,  -- encryption key version
    status TEXT NOT NULL DEFAULT 'active',  -- CHECK constraint: active|disabled|revoked
    rate_limits_daily INTEGER NOT NULL DEFAULT 100,
    rate_limits_weekly INTEGER NOT NULL DEFAULT 500,
    rate_limits_monthly INTEGER NOT NULL DEFAULT 2000,
    created_at TEXT NOT NULL,     -- ISO datetime
    updated_at TEXT NOT NULL,     -- ISO datetime
    last_used_at TEXT             -- ISO datetime | null
);

CREATE INDEX idx_users_key_id ON users(key_id);
CREATE INDEX idx_users_status ON users(status);

CREATE TABLE rate_limit_snapshots (
    user_id TEXT NOT NULL,
    tier TEXT NOT NULL,           -- daily|weekly|monthly
    count INTEGER NOT NULL DEFAULT 0,
    last_updated TEXT NOT NULL,   -- ISO datetime
    PRIMARY KEY (user_id, tier)
);

CREATE INDEX idx_snapshots_user ON rate_limit_snapshots(user_id);
```

### 3. Encryption Layer

- **Algorithm:** Fernet (AES-128-CBC + HMAC-SHA256) — audited, production-ready
- **Key source:** `MCP_ENCRYPTION_KEY` from env (never in code/DB/logs)
- **Backup key:** `MCP_ENCRYPTION_KEY_BACKUP` optional env var (recovery after primary key loss)
- **API key generation:** `secrets.token_urlsafe(32)` (192 bits entropy)
- **Encryption:** key → Fernet.encrypt() → hex string in DB
- **Decryption:** hex string → bytes → Fernet.decrypt() → API key
- **Key rotation:** `rotate_key` action in user_manage — generates new key, encrypts, stores; old key revoked automatically
- **Backup key recovery procedure:**
  1. Set `MCP_ENCRYPTION_KEY_BACKUP` env var
  2. Restart server — encryption layer auto-detects backup key:
     - **Backup key is dormant until primary decryption fails** — env var presence = storage only, NOT activation trigger
     - Activation condition: Fernet.InvalidToken exception during primary key decryption
     - Priority: attempt decrypt with primary key first; if Fernet.InvalidToken → fallback to backup key
     - Dual-key behavior: both keys stored in encryption layer, primary preferred, backup as fallback
     - Cleanup: backup key removed from memory after successful migration
  3. Run `encryption.migrate_keys()` — decrypt all keys with backup, re-encrypt with new key
  4. Verify all keys via `encryption.verify_all()` — check decryption success rate ≥ 99%
  5. Clear `MCP_ENCRYPTION_KEY_BACKUP` after successful migration
  6. **Rollback:** if migration fails (< 99%): restore primary key, log failure, manual intervention required

### 4. Token Verifier

`DebugTokenVerifier` с async `validate` callable:

```python
async def verify_token(token: str) -> AuthContext | None:
    # 1. Extract key_id from token (prefix "key_")
    # 2. Lookup user by key_id in DB
    # 3. Check Redis rate limits (after DB lookup, before decrypt)
    # 4. Decrypt key from DB, compare with token
    # 5. Check user status
    # 6. Increment rate limit counter
    # 7. Return AuthContext(user_id, scopes)
```

**Order:** DB lookup (key_id → user) → Redis rate limit check → decrypt key → status check → increment counter → return AuthContext → return

### 5. Rate Limiting

| Tier | Default | Redis TTL | DB sync |
|------|---------|-----------|---------|
| Daily | 100 | 86400s | on checkpoint (1h interval) |
| Weekly | 500 | 604800s | on checkpoint (1h interval) |
| Monthly | 2000 | 2592000s | on checkpoint (1h interval) |

- Redis counters: `rl:{user_id}:{tier}` (e.g. `rl:uuid_123:daily`)
- Configurable per user via `user_manage` tool
- Sync to DB every 1h (existing CHECKPOINT_CLEANUP_INTERVAL)
- **Fallback:** Redis unavailable → direct DB counter (slower but safe):
  - Detect: connection timeout > 2s → mark Redis as unavailable
  - Switch: rate_limiter.auto_fallback() → use DB counters instead
  - **DB counter storage:** `rate_limit_snapshots` table (user_id, tier, count, last_updated)
  - **Race conditions:** DB fallback uses threading.Lock for atomic counter increment (single-process only)
  - **Distributed rate limiting:** Redis required for correct distributed rate limiting across multiple instances. DB fallback is single-instance only. Multi-instance deployment requires Redis availability.
  - **Shutdown when Redis unavailable:** if Redis already unavailable (DB fallback mode), skip Redis flush — counters already stored in DB via rate_limit_snapshots table
  - Recovery: periodic Redis health check (every 30s), switch back when available

### 6. User Management Tool

New MCP tool `user_manage` with scopes `admin`:

| Action | Parameters | Output |
|--------|------------|--------|
| create | name, rate_limits (optional, bounds: daily[10-1000], weekly[50-5000], monthly[200-10000]) | user_id, key_id, raw_key |
| list | status filter (active/revoked/all), page, page_size | paginated list of users |
| revoke | user_id (requires confirmation) | status changed to revoked, Redis cache cleared |
| rotate_key | user_id (requires confirmation) | new key_id, raw_key, old key revoked, counters carried over |
| check_limits | user_id | current usage per tier |
| update_limits | user_id, new limits (bounds validated) | updated config |

### 7. Storage

- **DB:** SQLite (KG_DB_PATH) — users table + encrypted keys
- **Redis:** rate limit counters + user cache (TTL 5min)
- **Encryption key:** env `MCP_ENCRYPTION_KEY` (never in code/DB/logs)

## Implementation Plan

### Phase 1: Core Infrastructure

**Files:**
- `app/core/encryption.py` — Fernet encryption/decryption utilities
- `app/core/user_store.py` — User DB operations (SQLite)
- `app/core/rate_limiter.py` — Rate limit counters (Redis + DB sync + fallback)
- `app/core/token_verifier.py` — DebugTokenVerifier with async lookup

**Changes to existing:**
- `app/core/config.py` — add `MCP_ENCRYPTION_KEY`, `MCP_ENCRYPTION_KEY_BACKUP`, `USER_DB_BACKEND`, rate limit defaults
- `.env.example` — add `MCP_ENCRYPTION_KEY`, `MCP_ENCRYPTION_KEY_BACKUP`

### Phase 2: Auth Integration

**Files:**
- `app/main.py` — integrate auth verifier into FastMCP constructor

**Changes:**
- Conditional auth setup based on `MCP_ENCRYPTION_KEY` presence
- DebugTokenVerifier initialization with verify_token callable
- FastMCP auth API integration (explicit middleware injection via `auth` parameter)
- **Shutdown lifecycle:** Redis connection close, DB session cleanup via `mcp.on_shutdown()` handler:
  1. Close Redis connection pool (release all connections)
  2. Flush Redis rate limit counters to DB (sync remaining data)
  3. Close DB session/connection
  4. Handle errors: log failures via structlog, continue cleanup on error

### Phase 3: User Management Tool

**Files:**
- `app/tools/user_manage.py` — new MCP tool

**Features:**
- create_user, list_users, revoke_user, rotate_key, check_limits, update_limits
- Admin scope requirement
- Key generation + encryption + DB storage
- Redis cache invalidation on revoke/rotate

### Phase 4: Rate Limit Enforcement (merged into Phase 2 + Phase 3)

**Integration:**
- TokenVerifier checks limits before returning AuthContext (Redis → DB fallback)
- 429 response on exceeded limits
- Counter increment on each tool call
- Redis → DB sync on checkpoint
- **Rate limit defaults justification:** daily=100, weekly=500, monthly=2000 (based on typical MCP usage patterns, configurable per user)

### Phase 5: Tests

**Files:**
- `tests/test_encryption.py` — encryption/decryption, key rotation, backup key recovery
- `tests/test_user_store.py` — DB operations, schema validation
- `tests/test_rate_limiter.py` — rate limit checks, Redis fallback, boundary cases
- `tests/test_token_verifier.py` — auth verification, invalid token, revoked user
- `tests/test_user_manage.py` — user management tool, create/revoke/rotate
- `tests/test_auth_integration.py` — integration: auth + tool execution flow, conditional auth, fallback scenarios
- `tests/test_backward_compat.py` — backward compatibility: empty MCP_ENCRYPTION_KEY → no auth flow
- `tests/test_shutdown_flush.py` — shutdown counter preservation (flush Redis → DB)
- `tests/test_backup_key_partial_failure.py` — migration with < 99% success rate → rollback verification
- `tests/test_distributed_rate_limit.py` — multi-instance behavior (per-instance limits)

**Edge cases:**
- MCP_ENCRYPTION_KEY absent → no auth (backward compatible)
- Redis unavailable → DB fallback (slower but safe)
- Rate limit exactly at boundary (user=limit, user=limit+1)
- Key tampering (invalid encrypted data → Fernet HMAC detects)
- Concurrent rate limit updates: simulate parallel requests, verify Redis `incr` atomic + DB fallback with threading lock
- Backup key recovery: test migrate_keys() with known test keys, verify ≥ 99% success rate
- Backup key partial failure: test migration with < 99% → verify rollback
- Distributed rate limiting: test multi-instance behavior

### Phase 6: Documentation

**Files:**
- `README.md` — add auth section (how to use API key, MCP_ENCRYPTION_KEY)
- `features_plans/mcp_authorization.md` — this plan (already exists)
- `SECURITY.md` — update with encryption details, key rotation procedure

## Security Requirements

1. **Encryption key** — env only (`MCP_ENCRYPTION_KEY`), never in code/DB/logs
2. **Backup key** — env `MCP_ENCRYPTION_KEY_BACKUP` (optional, for recovery)
3. **API keys** — encrypted in DB (Fernet), never stored plaintext
4. **Rate limits** — checked before tool execution (Redis → DB fallback)
5. **Admin tool** — requires `admin` scope
6. **Key revocation** — immediate effect (Redis cache invalidation + DB status update)
7. **Key rotation** — via `rotate_key` action, old key auto-revoked
8. **Audit logging** — structlog for user management actions:
   - Events: `user_created`, `user_revoked`, `key_rotated`, `limits_updated`
   - Schema: `{event, user_id, user_name, timestamp, actor}`
   - Destination: stdout (JSON structured via structlog) + file (`app/core/logging.py`)
   - File rotation: RotatingFileHandler, maxBytes=10MB, backupCount=5, gzip compression on rotate
   - **Auth events:** `invalid_token`, `rate_limit_exceeded`, `user_disabled`
   - **Auth schema:** `{event, user_id, key_id, tool_name, result (success/failure), timestamp}`
   - **Note:** ip_address not included — MCP protocol doesn't provide client IP from transport layer

## Migration Strategy (no-auth → auth)

### Phase 1: Empty key (no auth) — backward compatible
- `MCP_ENCRYPTION_KEY` empty → server runs without auth
- Existing clients continue working without changes

### Phase 2: Key enabled (auth active)
1. Set `MCP_ENCRYPTION_KEY` env var
2. Server restarts — auth enabled
3. **Initial users table seeding:**
   - Create initial admin user via `user_manage create` tool
   - Users table created automatically on first access (SQLite auto-create)
4. **Existing API keys handling:**
   - No existing keys (fresh deployment) — all keys issued via `user_manage`
   - If migrating from another auth system: import keys via `encryption.import_keys(keys: list[dict])`:
     - Input format: list of `{key_id: str, raw_key: str}`
     - Validation: key_id uniqueness check, raw_key format validation (min length 32 chars)
     - Error handling: duplicate key_id → skip with warning; invalid raw_key → skip with error log
     - Workflow: bulk encrypt all keys → batch insert to DB → verify_all() → success rate ≥ 99%
5. **Client notification:** existing clients notified via admin channel (email/webhook) to update API key
6. **Grace period:** 30 days after auth enable — warnings for expired keys, no hard block
7. **Rollback:** clear `MCP_ENCRYPTION_KEY` → server restarts → auth disabled (backward compatible)

### Phase 3: Key rotation
- Via `user_manage rotate_key` action
- Redis counters carried over to new key (user_id unchanged)
- Old key auto-revoked, Redis cache cleared for old key_id

## Acceptance Criteria

- [ ] Auth verifier validates token via Redis → DB lookup
- [ ] Rate limits enforced per user per tier with Redis fallback
- [ ] User management tool creates/revoke/rotate/checks users
- [ ] API keys encrypted in DB (Fernet)
- [ ] Encryption key from env only
- [ ] Key rotation mechanism implemented
- [ ] Tests cover all auth scenarios + edge cases + integration
- [ ] Documentation updated
- [ ] bandit scan passes
- [ ] mypy type check passes
- [ ] ruff lint passes
- [ ] Backward compatible: empty MCP_ENCRYPTION_KEY → no auth

## Dependencies

- `cryptography` — Fernet encryption (add to pyproject.toml dev + prod)
- Existing: Redis, SQLite (via KG_DB_PATH)

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Encryption key loss | Backup env key (`MCP_ENCRYPTION_KEY_BACKUP`), documented recovery procedure |
| Redis unavailable | DB fallback for rate limits (slower but safe) |
| DB migration (no-auth → auth) | SQLite schema added to user_store.py, backward compatible via empty key |
| Key rotation | user_manage tool `rotate_key` action, old key auto-revoked |
| Rate limit data loss | Redis → DB sync on checkpoint (1h interval, existing) |
| Token tampering | Fernet HMAC verification detects any modification |
