# Configuration Reference

Environment variables for **MCP Web Search**. Copy `.env.example` to `.env` and configure values.

## MCP Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_NAME` | `web-search` | MCP server name |
| `MCP_VERSION` | `1.0.0` | MCP server version |
| `MCP_HOST` | `0.0.0.0` | Bind address |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |

## Redis Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `SEARCH_RESULT_CACHE_TTL` | `3600` | Search results cache TTL (seconds) |
| `CONTENT_CACHE_TTL` | `86400` | Content cache TTL (seconds, 24h) |
| `WEBFETCH_CACHE_TTL` | `1800` | Webfetch cache TTL (seconds, 30m) |

## Search Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_SEARCH_PROVIDER` | `duck` | Primary search provider (`duck`, `tavily`, `google`) |
| `USE_GOOGLE_FALLBACK` | `false` | Enable Google fallback |
| `SEARCH_FALLBACK_CHAIN` | `["duck","searxng","tavily","google"]` | Ordered fallback chain |
| `MAX_RESULTS` | `10` | Maximum results per search |
| `DEFAULT_REGION` | `wt-wt` | Search region code |
| `DEFAULT_LANGUAGE` | `en` | Search language code |
| `SEARXNG_BASE` | *(empty)* | Public SearxNG instance URL (optional) |

## Smart Filter Thresholds

| Variable | Default | Description |
|----------|---------|-------------|
| `QUALITY_SCORE_THRESHOLD` | `0.6` | Minimum quality score for results |
| `BLACKLIST_DOMAINS` | `["example.com"]` | Comma-separated blocked domains |

## Content Extraction

| Variable | Default | Description |
|----------|---------|-------------|
| `TOKEN_LIMIT` | `8000` | Maximum tokens in extracted content |

## WebFetch (LangGraph Agent)

| Variable | Default | Description |
|----------|---------|-------------|
| `FEATURE_LLM_MODEL` | `gpt-4o-2025-04` | LLM model for feature extraction |
| `JUDGE_URL_THRESHOLD` | `0.85` | Minimum score for URL judgment |
| `JUDGE_FEATURES_THRESHOLD` | `0.92` | Minimum score for feature judgment |
| `MAX_CONCURRENT` | `6` | Maximum concurrent search requests |
| `GEN_SRCH_Q_CNT` | `5` | Number of search queries to generate (3–10) |
| `SKIP_JUDGE` | `false` | Skip LLM judge for trusted sites |

## LLM Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_API_KEY` | *(empty)* | LLM provider API key |
| `LLM_BASE_URL` | *(empty)* | LLM provider base URL |
| `LLM_MODEL` | `gpt-4o-2025-04` | Primary LLM model |
| `LLM_MODEL_FALLBACK_CHAIN` | `["gpt-4o-2025-04","gpt-4o","gpt-4"]` | Model fallback chain |
| `LLM_MODEL_FALLBACK_BASE_URLS` | *(empty)* | Per-model base URL override (JSON) |
| `LLM_HEALTH_WINDOW` | `10` | Health tracking window (calls) |
| `LLM_HEALTH_FAILURE_THRESHOLD` | `0.5` | Failure threshold (0.0–1.0) |

## Provider Health Tracker

| Variable | Default | Description |
|----------|---------|-------------|
| `PROVIDER_HEALTH_WINDOW` | `10` | Health tracking window (probes) |
| `PROVIDER_HEALTH_FAILURE_THRESHOLD` | `0.5` | Failure threshold (0.0–1.0) |
| `PROVIDER_HEALTH_PROBE_INTERVAL` | `30` | Probe interval (seconds) |
| `PROVIDER_COOLDOWN_PERIOD` | `300` | Cooldown after failure (seconds) |

## Cache Warming

| Variable | Default | Description |
|----------|---------|-------------|
| `WARM_CACHE_URLS` | *(empty)* | Comma-separated URLs for cache warming |

## API Keys (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `TAVILY_API_KEY` | *(empty)* | Tavily search API key |
| `GOOGLE_API_KEY` | *(empty)* | Google Custom Search API key |
| `GOOGLE_CSE_ID` | *(empty)* | Google Custom Search engine ID |
