# Logging Format

## Structlog Configuration

The project uses **structlog** (v25.1+) for structured logging. Configuration is in `app/core/logging.py`.

### Development (Console)

In dev mode, structlog uses `ConsoleRenderer` with colors:

```json
{
  "event": "search executed",
  "logger": "search-service",
  "level": "info",
  "timestamp": "2026-05-22T14:30:00+03:00",
  "provider": "duck",
  "results_count": 8,
  "duration_ms": 1240
}
```

### Docker / Production

In Docker, Python `logging` uses a standard text format:

```
2026-05-22 14:30:00,123 - search-service - INFO - search executed provider=duck results_count=8 duration_ms=1240
```

### Processor Chain

```
add_logger_name
→ add_log_level
→ TimeStamper (fmt="iso")
→ ConsoleRenderer (colors=True)  [dev]
```

### Log Levels

| Level | When |
|-------|------|
| `debug` | Detailed trace (cache hits, provider probes, LLM calls) |
| `info` | Normal operations (search results, content extraction, cache ops) |
| `warning` | Recoverable issues (fallback switches, cache misses, slow responses) |
| `error` | Failures (provider errors, SSRF blocks, LLM timeouts) |

### Logger Names

| Logger | Module |
|--------|--------|
| `web-search` | Main server |
| `search-service` | Search service |
| `content-service` | Content extraction |
| `webfetch` | Webfetch agent |
| `knowledge-graph` | Knowledge graph module |
| `provider-registry` | Provider health tracking |
| `cache` | Redis cache operations |

### Bound Context Keys

Standard keys bound to all log events:

| Key | Value |
|-----|-------|
| `request_id` | Unique request identifier (when available) |
| `tool` | MCP tool name (`search`, `content`, `webfetch`) |
| `provider` | Active search provider |
| `cache_status` | `hit`, `miss`, or `skip` |

### Example Log Lines

```
# Search result with cache hit
{"event": "search completed", "logger": "search-service", "level": "info",
 "timestamp": "2026-05-22T14:30:00+03:00", "provider": "duck",
 "results_count": 8, "duration_ms": 1240, "cache_status": "hit"}

# Fallback switch
{"event": "provider fallback", "logger": "provider-registry", "level": "warning",
 "timestamp": "2026-05-22T14:31:00+03:00", "from": "duck", "to": "searxng",
 "reason": "connection_timeout"}

# SSRF block
{"event": "ssrf blocked", "logger": "content-service", "level": "error",
 "timestamp": "2026-05-22T14:32:00+03:00", "url": "http://10.0.0.1/admin",
 "reason": "private_ip_range"}
```
