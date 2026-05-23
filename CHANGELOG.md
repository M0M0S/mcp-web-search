# Changelog

All notable changes to this project will be documented in this file.

## [1.1.0] - 2026-05-23

### Added
- **MCP Authorization System** — полный набор модулей авторизации:
  - `encryption` — шифрование/дешифрование ключей (AES-GCM, Fernet)
  - `user_store` — управление пользователями и API ключами (Redis)
  - `rate_limiter` — rate limiting по пользователям (Redis sliding window)
  - `token_cost_tracker` — трекинг token cost на запрос
  - `token_verifier` — верификация токенов с fallback на backup key
  - `user_manage` — MCP tools: `create_user`, `delete_user`, `list_users`, `rotate_key`
- **Auth test suite** — 11 тестовых файлов, 246 тестов (backward compat, shutdown flush, backup key, admin scope, FastMCP API)
- **Документация**: README.md (auth section), SECURITY.md (auth policy)

### Fixed
- `encrypted_key` storage — корректное сохранение зашифрованного ключа в Redis
- `rotate_key` — корректное обновление `key_id` при ротации

### Security
- Bandit: 0 High, 0 Medium
- mypy: 0 errors
- ruff: 0 violations

---

## [1.0.3] - 2026-05-22

### Changed
- PyPI long description: added `readme = "README.md"` в `pyproject.toml`

---

## [1.0.2] - 2026-05-22

### Changed
- PyPI package name: `mcp-web-search` → `mcp-webs` (registered on pypi.org)

---

## [1.0.1] - 2026-05-22

### Fixed
- **CI stabilization:** resolve mypy errors (missing type stubs) и test failures (LLM_API_KEY dependency)
- WebFetchService/SearchService: optional `llm_client` parameter для test injection
- TavilyClient: lazy import вместо module-level для mypy `import-untyped`
- content_service: rationale comments для `# type: ignore[import-untyped]` (bleach, readability)
- provider_registry: rationale comment для `# type: ignore[import-untyped]` (tavily)
- tests: shared `mock_llm_client` fixture в `fixtures.py` + patch при usage site
- test_provider_health: correct patch target для lazy import TavilyClient (`tavily.TavilyClient`)
- README.md: update GitHub username

---

## [1.0.0] - 2026-05-22

### Public Release Preparation

#### Added
- `LICENSE` (MIT)
- `CONTRIBUTING.md` — contribution guide, PR process, standards, conventional commits
- `SECURITY.md` — vulnerability reporting, SSRF policy, secret handling
- `.github/workflows/ci.yml` — CI pipeline (test, lint, typecheck, security)
- `docs/standards/` — 8 standards files + README index
- `.gitignore` — build artifacts (`dist/`, `build/`, `*.egg-info/`, `*.pyd`)

#### Changed
- `pyproject.toml` version: `0.6.0` → `1.0.0`
- `.env.example` — all secrets → placeholders, LOG_LEVEL → INFO, synced defaults with `config.py`
- README.md — badges, install/usage/dev sections, generic clone URL
- Docker container names: `dev-` → `mcp-webs`
- Pre-commit hooks: `uv run` for mypy, pytest, bandit

#### Removed
- Internal/private references (`searx.nulltx.org`, `dev-web-search`)
- Test placeholder keys (`sk-test-placeholder` → `PLACEHOLDER_KEY`)
- 14 internal documentation files

---

## [0.6.0] - 2026-05-22

### Added
- **Epic 4: Adaptive TTL caching** — CacheFreshnessChecker, adaptive TTL ranges (high/medium/low), Prometheus metrics (`webfetch_content_cache_ttl_seconds`, `webfetch_cache_freshness_invalidations_total`)
- **Epic 3: Checkpoint metrics** — `webfetch_checkpoint_save_total`, `webfetch_checkpoint_resume_total`, `webfetch_checkpoint_size_bytes`, `webfetch_active_checkpoints`
- **Epic 6: UnifiedJSONResponse** — model with JSON validation and bounding by size, freshness badges
- **webfetch pipeline: Gap 1-7 fixes** — judge_urls_with_content (content always populated), URL deduplication (select_urls + fetch_content), unconditional scoring, sources always populated, smarter checkpoint truncation (first 500 + last 200), main query prepend + case-insensitive dedup
- `DEFAULT_SEL_TOP_LEVEL` and `MAX_SEARCH_QUERIES` in Settings config

### Changed
- MAX_CHECKPOINT_SIZE: 1 MiB → 2 MiB
- Scoring formula: weighted sum → weighted average (sum(scores) / len(scores))
- Hardcoded values → configurable: `[:6]` → `MAX_SEARCH_QUERIES`, `sel_top_level=30` → `DEFAULT_SEL_TOP_LEVEL`, token cost formula → `MAX_SEARCH_QUERIES`
- Auto-reduce: decoupled triggers (sel_top_level > 50, token cost > 15000)
- WebFetchService: removed `_node_fallback` dead code, removed `record_checkpoint_resume` from "no checkpoint" branch
- Pre-commit: dev dependencies (bandit, mypy, ruff), local hooks (GitHub unavailable), 72 ruff I001 fixes

### Fixed
- Epic 5: AC9/AC16 empty results tests, AC31 metadata convergence, AC35 dict sources test
- Epic 3: computed_field read-only, hardcoded URL removed
- Epic 6: freshness badges, search_results type: ignore cleanup
- Knowledge Graph: SQLite CREATE TABLE IF NOT EXISTS, auto-save trigger, enrichment rate limit per-concept
- KG lookup fix, overflow tests, KG extract improvement
- LLM_API_KEY fixture, Settings forward ref, pre-commit E501/mypy fixes
- WebFetch checkpoint: getattr for redis._client in checkpoint store init

### Testing
- 31 new tests: 13 unit (Gap 1-7), 10 integration (pipeline), 8 edge case
- 621 tests total — all passing
- Final llm-judge score: 4.87/5 (97.3%)

---

## [0.5.0] - 2026-05-21

### Added
- **Epic 1: LLM Model Failover Chain** — full implementation (19/19 AC)
- Prometheus metrics for LLM failover: `llm_failover_total`, `llm_failover_duration_seconds`, `llm_model_health_score`, `llm_active_model_index`
- `LLMHealthProbe` — background periodic health probe for LLM models (configurable interval, default 60s)
- Redis persistence for `LLMHealthTracker` — cross-process recovery (key `llm_health:{model}`)
- MCP tool `llm_health` — health status of all LLM models in failover chain
- Per-call failover counter (`last_call_failover_count`) in `LLMClient`
- Config field `LLM_HEALTH_PROBE_INTERVAL` in Settings
- 109 unit tests: `test_llm_health_tracker.py` (21), `test_llm_health_probe.py` (8), `test_llm_failover.py` (80)

### Changed
- `LLMClient.__init__` — added optional `redis_client`, `settings` params for health persistence
- `create_llm_client()` factory — extended with optional `redis_client`, `settings` params
- `LLMHealthTracker` — unified signature `persist_health()`/`restore_health()` (no params, internal state)
- `LLMClient._call_with_failover` — `time.time()` → `time.monotonic()` for duration tracking
- Strict type hints: `Settings | None` instead of `Any | None`
- Inline import in `llm_health.py` → module level (eliminated circular dependency risk)

### Fixed
- Scope ambiguity in `restore_health` except handler — removed `model=model` from scope leakage
- Silent `except Exception: pass` → `logger.debug` for Redis failures in persist/restore
- Unused variables (ruff F841) in `_call_with_failover` and `_probe_single`

---

## [0.4.0] - 2026-05-01

### Added
- `skip_judge` parameter in `SearchRequest` for skipping LLM-as-Judge (for trusted sites)
- `gen_srch_q_cnt` and `sel_top_level` parameters in `webfetch` tool
- Unified fallback search via `_perform_fallback_search` with full content extraction

### Changed
- Dependency migration: `duckduckgo-search>=8.1` → `ddgs>=8.0`
- DuckDuckGo API call: fixed `qwery` → `query`, kept `region` support
- SearchService: exception handling in `_node_perform_search` with fallback on full failure
- SearchRequest: added `skip_judge` parameter, default `False`

### Fixed
- Search query generation: now passes `query_count` to LLM call
- Exception handling: filtering all errors from parallel search calls

---

## [0.3.0] - 2026-05-31

### Search Resilience & Quality
Critical improvements for fault tolerance and search result completeness:

- **Guaranteed queries:** `_node_generate_search_queries` now guarantees minimum 3 queries on LLM failure, eliminating code duplication and ensuring full coverage.
- **Full content response:** `_node_fetch_content` extracts full content from **all** selected URLs after deduplication, not just a limited slice (`sel_top_level`).
- **Diversity scoring:** Added source diversity metric across providers, timestamps, and content quality.

### Architecture & Security
- **Cache hashing:** Search cache key hashing migrated to **SHA256** (from MD5), improving cryptographic reliability.
- **SSRF Protection:** Improved exception handling in `ssrf` module with `httpx`, significantly strengthening Server-Side Request Forgery protection.

### Testing & Debugging
- Extensive unit tests for all WebFetch Service fault tolerance scenarios: empty results, timeouts, LLM judge failures, content extraction errors.

---

## [0.2.3] - 2026-04-28

### Added
- Configurable search: `engines`, `time_range`, `site` parameters in `SearchRequest`
- SearxNG fallback provider (fourth in chain: DDG → SearxNG → Tavily → Google)
- LLM-as-Judge for search results via `_judge_search_results()`
- Diversity scoring with metrics: source, temporal, content diversity
- Cache versioning via `CACHE_VERSION` constant

### Changed
- Provider enum moved to centralized `app/core/config.py`
- SearchService: LLM-as-Judge integration in fallback chain
- SearchResponse: added `diversity_scores`, `parameters`, `judgment`
- Fallback chain logic: verdict check and retry with next provider

### Fixed
- `SearchResultJudge.from_judge_verdict()` with correct fallback score=0.0
- SearxNG search with SSRF protection via `ssrf_protection.fetch_async()`

### Testing
- All 8 unit tests for SearchService passing
- Docker build successful
- MCP server verified with real client request

---

## [0.2.2] - 2026-04-26

### Added
- Performance audit report in `/report/performance_audit.md`
- Cache warming on startup via `WARM_CACHE_URLS` and `warm_cache_on_startup()`

### Changed
- DuckDuckGo search: blocking calls → `asyncio.to_thread()` in `_search_duckduckgo()`
- Redis connection pooling: `max_connections=20`, `socket_timeout=5.0` for production scalability
- LLM rate limiting: `asyncio.Semaphore(10)` + exponential retry in `LLMClient._call_with_retry()`
- WebFetch parallelism: limited to 6 concurrent searches via `MAX_CONCURRENT` semaphore
- ContentService: LRU cache eviction with `OrderedDict` max 1000 entries
- Tests: fixed unit tests for env overrides, removed obsolete search TDD tests

### Fixed
- Performance issues from audit report:
  - Blocking DuckDuckGo calls causing event loop stall
  - Redis conflict under high load (missing connection pool)
  - LLM API quota exhaustion without rate limiting
  - Unlimited parallel WebFetch requests
  - Memory leak in ContentService cache without eviction
  - Cold start latency without cache warming

### Testing
- All 36 unit + TDD ContentService tests passing
- Ruff linter clean
- Docker rebuild successful
- MCP tools verified with client (search, content, webfetch)

---

## [0.2.1] - 2026-04-26

### Changed
- Methods renamed for clarity
- Project renamed
- README.md modernized

---

## [0.2.0] - 2026-04-26

### Added
- webfetch_tool implementation with LangGraph StateGraph (8 nodes)
- LLM-as-Judge for URL and feature scoring
- Parallel search execution via asyncio.gather
- SSRF protection in ContentService
- Search result caching via Redis

### Changed
- WebFetchService: sequential execution instead of parallel gather
- SearchRequest: default language="en", auto_detect_language=False
- .env.example: added LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

### Fixed
- llm_client.py: fixed content_str error (\n → escape)
- webfetch_service.py: unified type handling in _node_select_urls and _node_judge_urls

### Testing
- Updated TDD tests for all 8 StateGraph nodes
- Added MockLLMClient fixture for unit tests
