# Caching Strategy

## Cache-Aside Pattern

The project uses the **cache-aside** (lazy loading) pattern with **Redis** for all search and content operations.

### How It Works

1. **Read**: Check cache first → if hit, return cached value → if miss, fetch from source → write to cache
2. **Write**: Write to source → write to cache → invalidate related entries

### Cache Layers

| Layer | Key Pattern | TTL | Purpose |
|-------|-------------|-----|---------|
| **Search results** | `search:{query}:{provider}` | 3600s (1h) | Search result caching |
| **Content** | `content:{hash(url)}` | 86400s (24h) | Extracted content caching |
| **Webfetch** | `webfetch:{query_hash}` | 1800s (30m) | Agent result caching |
| **Checkpoints** | `checkpoint:{thread_id}` | TTL-based | LangGraph state persistence |
| **Provider health** | `provider:{name}:health` | 300s (5m) | Provider health tracking |
| **LLM health** | `llm:{model}:health` | 300s (5m) | LLM health tracking |

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `SEARCH_RESULT_CACHE_TTL` | `3600` | Search results TTL (seconds) |
| `CONTENT_CACHE_TTL` | `86400` | Content TTL (seconds) |
| `WEBFETCH_CACHE_TTL` | `1800` | Webfetch TTL (seconds) |

### Cache Keys

Search keys use the raw query string:

```
search:duck:query=python+tutorial:provider=duck
```

Content keys use a hash of the URL to avoid key length issues:

```
content:sha256(https://example.com/article)
```

### Cache Metrics

All cache operations are logged with `cache_status`:

| Status | Meaning |
|--------|---------|
| `hit` | Value found in cache, returned immediately |
| `miss` | Cache empty, fetched from source, written to cache |
| `skip` | Cache bypassed (e.g., force refresh, TTL expired) |

### Redis Checkpoint Store

LangGraph checkpoints use a Redis-backed store with **MemorySaver fallback**:

- Primary: `RedisCheckpointStore` — persists agent state to Redis
- Fallback: `MemorySaver` — in-memory store if Redis is unavailable
- Periodic cleanup removes expired checkpoints automatically

### Cache Warming

Optional cache warming via `WARM_CACHE_URLS` env var:

```bash
WARM_CACHE_URLS=https://docs.python.org/3,https://example.com/popular-article
```

Warmed URLs are fetched and cached on startup.

### Redis Requirements

- Redis ≥ 6.0 recommended
- Minimum: Redis 5.0 (for basic operations)
- No authentication required for default `REDIS_URL`
- Network isolation recommended in Docker deployments
