# MCP Web Search

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Build Status](https://img.shields.io/badge/build-placeholder-gray.svg)](https://github.com/M0M0S/mcp-webs/actions)
[![ruff](https://img.shields.io/badge/lint-ruff-ff69b4.svg)](https://github.com/astral-sh/ruff)
[![mypy](https://img.shields.io/badge/typecheck-mypy-white.svg)](https://mypy.readthedocs.io/)

MCP service for web search and content extraction, implemented via **Model Context Protocol** (FastMCP).

## Features

Three MCP tools:

1. **`search`** — web search with smart filtering and fallback chain
2. **`content`** — clean text extraction from URLs with SSRF protection
3. **`webfetch`** — agent-based search via LangGraph StateGraph + LLM-as-Judge
4. **`llm_health`** — LLM model health status in failover chain

## Architecture

```
FastMCP (primary server)
├── search tool    → DuckDuckGo + fallback chain + smart filtering
├── content tool   → Trafilatura + SSRF protection + cache
└── webfetch tool  → LangGraph StateGraph (8 nodes) + LLM-as-Judge
```

## Installation

```bash
# Clone the repository
git clone https://github.com/M0M0S/mcp-webs.git
cd mcp-webs

# Install dependencies
uv sync

# Configure environment variables
cp .env.example .env
# fill .env (LLM_API_KEY, LLM_BASE_URL, etc.)
```

## Usage

### Start MCP Server

```bash
uv run python -m app.main
```

### Connect to Claude Desktop (example)

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "web-search": {
      "command": "uv",
      "args": ["run", "python", "-m", "app.main"],
      "env": {
        "LLM_API_KEY": "your-key",
        "LLM_BASE_URL": "https://api.openai.com/v1"
      }
    }
  }
}
```

### MCP Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `search` | Web search with fallback chain | `query`, `max_results`, `provider` |
| `content` | Extract text content from URL | `url`, `token_limit` |
| `webfetch` | Agent-based search via LangGraph | `query`, `max_concurrent` |

### Authorization (MCP_ENCRYPTION_KEY)

When `MCP_ENCRYPTION_KEY` is configured, the server enables user-based authorization:

**API key format**

API keys are issued via the `user_manage` tool. The token presented to the server has the format:

```
key_<key_id>
```

where `<key_id>` is a unique identifier (e.g. `key_abc123def456`). The raw key is delivered **one-time only** during user creation — it is never stored in the database or logs after delivery.

**Configuration**

| Variable | Required | Description |
|----------|----------|-------------|
| `MCP_ENCRYPTION_KEY` | Yes (for auth) | Fernet encryption key (44-char base64, 32 bytes). Validated at startup. |
| `MCP_ENCRYPTION_KEY_BACKUP` | No | Optional backup Fernet key for recovery after primary key loss. |

**Rate limits** (defaults per user)

| Tier | Default | Redis TTL |
|------|---------|-----------|
| Daily | 100 | 86400s |
| Weekly | 500 | 604800s |
| Monthly | 2000 | 2592000s |

Rate limits are configurable per user via `user_manage update_limits`.

**Token cost tracking**

Per-user LLM token consumption is tracked per tier (daily/weekly/monthly) for billing and quota visibility:

- **Input tokens** — query length + context
- **Output tokens** — response + extracted content
- Token limits default to **unlimited** (NULL) — configurable via `user_manage update_token_limits`
- Token cost tracking is **informational only** (warning on limit exceeded, not hard block)
- Rate limits enforce actual usage (hard block on limit exceeded)

**`user_manage` tool** (admin scope required)

| Action | Parameters | Output |
|--------|------------|--------|
| `create` | `name`, `rate_limits` (opt), `token_limits` (opt) | `user_id`, `key_id`, raw key (one-time) |
| `list` | `status` filter, `page`, `page_size` | Paginated user list |
| `revoke` | `user_id` (confirmation) | Status → revoked, Redis cache cleared |
| `rotate_key` | `user_id` (confirmation) | New `key_id`, raw key (one-time), old key revoked |
| `check_limits` | `user_id` | Current usage per tier (rate + token cost) |
| `check_token_usage` | `user_id` | Current token usage per tier (input/output/total) |
| `update_limits` | `user_id`, new rate limits | Updated config |
| `update_token_limits` | `user_id`, new token limits | Updated config |

### Audit Log Examples

Operators can reference these structured log entries for troubleshooting and compliance:

**User creation:**
```
2026-05-23T10:15:30Z  INFO  user_created  user_id=a1b2c3d4e5f6...  user_name=api-client-01  rate_limits={"daily":100,"weekly":500,"monthly":2000}  actor=admin
```

**Key rotation:**
```
2026-05-23T14:32:01Z  INFO  key_rotated  user_id=a1b2c3d4e5f6...  user_name=api-client-01  key_version=2  actor=admin
```

**Rate limit update:**
```
2026-05-23T14:32:01Z  INFO  limits_updated  user_id=a1b2c3d4e5f6...  user_name=api-client-01  daily=200  weekly=1000  monthly=5000  actor=admin
```

**Token limit update:**
```
2026-05-23T14:32:02Z  INFO  token_limits_updated  user_id=a1b2c3d4e5f6...  user_name=api-client-01  daily=5000000  weekly=25000000  monthly=100000000  actor=admin
```

**Invalid token (revoked key used):**
```
2026-05-23T14:35:17Z  WARN  invalid_token  user_id=a1b2c3d4e5f6...  key_id=key_old_revoked  tool_name=search  result=denied  timestamp=2026-05-23T14:35:17Z
```

**Rate limit exceeded:**
```
2026-05-23T15:01:42Z  WARN  rate_limit_exceeded  user_id=f6e5d4c3b2a1...  key_id=key_7f8g9h0i1j2k  tool_name=content  result=denied  timestamp=2026-05-23T15:01:42Z
```

**User disabled:**
```
2026-05-23T16:20:05Z  WARN  user_disabled  user_id=c3b2a1f6e5d4...  key_id=key_revoked_01  tool_name=webfetch  result=denied  timestamp=2026-05-23T16:20:05Z
```

**Note:** token cost data (input/output tokens) is NOT included in audit logs — audit logs contain event metadata only.

## Development

### Project Standards

- [CONTRIBUTING.md](CONTRIBUTING.md) — how to contribute, process, standards
- [SECURITY.md](SECURITY.md) — security policy, SSRF, secret handling
- [docs/standards/](docs/standards/) — detailed standards reference

### Commands

```bash
# Tests
uv run pytest tests/ -v

# Coverage
uv run pytest tests/ --cov=app --cov-report=term-missing

# Linting
uv run ruff check app/ tests/

# Formatting
uv run ruff format app/ tests/

# Type checking
uv run mypy app/

# Security scan
uv run bandit -r app/
```

### Configuration

Environment variables documented in [docs/standards/configuration.md](docs/standards/configuration.md).

## Search Logic

### `search` — search with fallback chain:
1. Caching (Redis cache-aside)
2. DuckDuckGo → SearxNG → Tavily → Google (fallback chain)
3. Smart filtering (SEO spam, clickbait, blacklist)
4. Result caching

### `content` — content extraction:
1. SSRF protection (whitelist + private IP check)
2. Trafilatura → readability-lxml → bs4 (fallback chain)
3. HTML sanitization (bleach)
4. Caching (TTL: 24h)

### `webfetch` — agent-based search:
1. **Stage 1**: Generate queries via LLM
2. **Stage 2**: Parallel searches (6 concurrent)
3. **Stage 3**: Select URLs for extraction
4. **Stage 4**: Judge URLs (LLM-as-Judge, threshold ≥0.85)
5. **Stage 5**: Fetch content (Trafilatura)
6. **Stage 6**: Generate features (Pydantic models)
7. **Stage 7**: Judge Features (threshold ≥0.92)
8. **Fallback**: Simple search on agent failure

## Prometheus Metrics

Implemented metrics (via `app/core/metrics.py`):

| Metric | Type | Description |
|--------|------|-------------|
| `provider_search_total` | Counter | Search attempts per provider |
| `provider_search_failure_total` | Counter | Failed searches per provider |
| `provider_health_score` | Gauge | Provider health (0.0–1.0) |
| `provider_chain_position` | Gauge | Provider position in fallback chain |
| `llm_failover_total` | Counter | LLM failover events (from→to model) |
| `llm_failover_duration_seconds` | Histogram | Failover duration |
| `llm_model_health_score` | Gauge | LLM model health (0.0–1.0) |
| `llm_active_model_index` | Gauge | Active LLM model index |
| `webfetch_checkpoint_save_total` | Counter | WebFetch checkpoint saves |
| `webfetch_checkpoint_resume_total` | Counter | WebFetch checkpoint resumes |
| `webfetch_checkpoint_size_bytes` | Histogram | Checkpoint payload size |
| `webfetch_active_checkpoints` | Gauge | Active checkpoints per tenant |
| `cache_ttl_distribution_seconds` | Histogram | Cache TTL distribution |
| `cache_stale_invalidations_total` | Counter | Cache stale invalidations |
| `cache_freshness_avg` | Gauge | Average cache freshness |
| `knowledge_graph_concepts_count` | Gauge | KG concepts count |
| `knowledge_graph_terms_count` | Gauge | KG related terms count |
| `kg_expansion_applied_total` | Counter | KG expansion events |
| `kg_enriched_concepts_total` | Counter | KG enriched concepts |

## See Also

- [CHANGELOG.md](./CHANGELOG.md) — version history
- [pyproject.toml](./pyproject.toml) — dependencies and configuration
