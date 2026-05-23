"""Settings configuration using Pydantic v2."""

import os
from enum import Enum
from typing import Optional, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Maximum checkpoint payload size in bytes (default: 2 MiB)
# Increased from 1 MiB to reduce aggressive truncation that loses >90% of fetched content.
# Smarter truncation (first 500 + last 200 chars per item) handles remaining overflow cases.
MAX_CHECKPOINT_SIZE: int = 2_097_152


class Provider(str, Enum):
    """Search provider enumeration."""

    duck = "duck"
    searxng = "searxng"
    tavily = "tavily"
    google = "google"


class Settings(BaseSettings):
    """MCP Web Search settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="allow",  # Allow extra fields from .env
    )

    # MCP
    MCP_NAME: str = "web-search"
    MCP_VERSION: str = "1.0.0"
    MCP_HOST: str = "0.0.0.0"  # nosec B104
    LOG_LEVEL: str = "INFO"

    # Redis (cache-aside)
    REDIS_URL: str = "redis://localhost:6379/0"
    SEARCH_RESULT_CACHE_TTL: int = 3600
    CONTENT_CACHE_TTL: int = 86400
    WEBFETCH_CACHE_TTL: int = 1800

    # Redis checkpoint (Epic 3 Phase 3.2)
    REDIS_CHECKPOINT_TTL: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="TTL in seconds for checkpoint keys in Redis (default: 1h)",
    )
    CHECKPOINT_CLEANUP_INTERVAL: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Interval in seconds for periodic checkpoint cleanup (default: 1h)",
    )

    # Search
    DEFAULT_SEARCH_PROVIDER: str = "duck"
    USE_GOOGLE_FALLBACK: bool = False
    SEARCH_FALLBACK_CHAIN: list[str] = ["duck", "searxng", "tavily", "google"]
    MAX_RESULTS: int = 10
    DEFAULT_REGION: str = "wt-wt"  # Global search (worldwide)
    DEFAULT_LANGUAGE: str = "en"  # English language for search

    # Smart filter thresholds
    QUALITY_SCORE_THRESHOLD: float = 0.6
    BLACKLIST_DOMAINS: list[str] = ["example.com"]

    # get_content
    TOKEN_LIMIT: int = 8000

    # webfetch (LangGraph)
    FEATURE_LLM_MODEL: str = "gpt-4o-2025-04"
    JUDGE_URL_THRESHOLD: float = 0.85
    JUDGE_FEATURES_THRESHOLD: float = 0.92
    MAX_CONCURRENT: int = 6

    # WebFetch parameters
    GEN_SRCH_Q_CNT: int = Field(
        default=5,
        ge=3,
        le=10,
        description="Number of search queries to generate (default: 5)",
    )
    SEL_TOP_LEVEL: int = Field(
        default=20, ge=5, le=50, description="Number of URLs to select (default: 20)"
    )
    DEFAULT_SEL_TOP_LEVEL: int = Field(
        default=5,
        ge=5,
        le=50,
        description="Default sel_top_level for auto-reduce fallback (default: 5)",
    )
    MAX_SEARCH_QUERIES: int = Field(
        default=6,
        ge=3,
        le=10,
        description="Maximum number of search queries to generate (default: 6)",
    )

    # LLM-as-Judge configuration
    SKIP_JUDGE: bool = Field(
        default=False,
        description="Skip LLM judge for trusted sites",
    )

    # Tavily API (required for fallback)
    TAVILY_API_KEY: Optional[str] = None

    # Cache warming (pre-populate frequently accessed URLs)
    WARM_CACHE_URLS: list[str] = []  # List of URLs to warm on startup

    # SearxNG configuration
    SEARXNG_BASE: Optional[str] = None  # https://searx.example.com

    # Cache version for migration
    CACHE_VERSION: int = 1

    # Adaptive TTL (Epic 4)
    ADAPTIVE_TTL_RANGES: dict[str, tuple[float, int]] = Field(
        default_factory=lambda: {
            "high": (0.8, 86400),  # freshness > 0.8 → 24h
            "medium": (0.5, 21600),  # 0.5 ≤ freshness ≤ 0.8 → 6h
            "low": (0.0, 3600),  # freshness < 0.5 → 1h
        },
        description="Adaptive TTL ranges: key → (min_freshness, ttl_seconds)",
    )
    FRESHNESS_INVALIDATION_THRESHOLD: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Freshness threshold below which content is marked stale",
    )

    @model_validator(mode="after")
    def _validate_adaptive_ttl_bounds(self) -> Self:
        """Validate ADAPTIVE_TTL_RANGES ttl_seconds are in [60, 86400]."""
        for key, (_, ttl) in self.ADAPTIVE_TTL_RANGES.items():
            if ttl < 60:
                raise ValueError(
                    f"ADAPTIVE_TTL_RANGES['{key}'] ttl_seconds={ttl} "
                    f"below minimum ge=60"
                )
            if ttl > 86400:
                raise ValueError(
                    f"ADAPTIVE_TTL_RANGES['{key}'] ttl_seconds={ttl} "
                    f"above maximum le=86400"
                )
        return self

    CACHE_INVALIDATION_INTERVAL: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Interval in seconds for CacheFreshnessChecker background task (default: 1h)",
    )
    TTL_OVERRIDE_ENABLED: bool = Field(
        default=True,
        description="Allow request-level TTL override via ttl_override parameter",
    )

    # LLM Model Failover Chain (Epic 1)
    LLM_MODEL: str = Field(
        default="gpt-4o-2025-04",
        description="Primary LLM model for webfetch operations",
    )
    LLM_MODEL_FALLBACK_CHAIN: list[str] = Field(
        default_factory=lambda: ["gpt-4o-2025-04", "gpt-4o", "gpt-4"],
        description="Ordered fallback chain of LLM models (primary first)",
    )
    LLM_MODEL_FALLBACK_BASE_URLS: dict[str, str] = Field(
        default_factory=dict,
        description="Per-model base_url override for fallback models",
    )
    LLM_HEALTH_WINDOW: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Size of the circular buffer for health tracking (events)",
    )
    LLM_HEALTH_FAILURE_THRESHOLD: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Failure rate threshold to exclude a model from the chain",
    )
    LLM_HEALTH_PROBE_INTERVAL: int = Field(
        default=60,
        ge=10,
        le=300,
        description="Interval in seconds for LLM background health probe (default: 60s)",
    )

    # Provider Health Tracker (Epic 2)
    PROVIDER_HEALTH_WINDOW: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Size of the circular buffer for provider health tracking (events)",
    )
    PROVIDER_HEALTH_FAILURE_THRESHOLD: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Failure rate threshold to exclude a provider from the chain",
    )
    PROVIDER_HEALTH_PROBE_INTERVAL: int = Field(
        default=30,
        ge=10,
        le=300,
        description="Interval in seconds for background health probe (default: 30s)",
    )
    PROVIDER_COOLDOWN_PERIOD: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="Cooldown period in seconds for excluded providers (default: 5min)",
    )
    REDIS_HEALTH_TTL: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="TTL in seconds for provider health keys in Redis (default: 1h)",
    )

    # Google Custom Search
    GOOGLE_CSE_ID: Optional[str] = None  # Custom Search Engine ID for GCS API

    # Knowledge Graph (Epic 5)
    KG_STORAGE_BACKEND: str = Field(
        default="sqlite",
        description="Knowledge Graph storage backend ('sqlite' or 'json')",
    )
    KG_DB_PATH: str = Field(
        default="data/knowledge_graph.db",
        description="Path to Knowledge Graph database file (SQLite) or JSON file",
    )
    KG_SEED_DATA_PATH: Optional[str] = Field(
        default=None,
        description="Path to seed data JSON file for Knowledge Graph initialization",
    )
    KG_ENRICHMENT_RATE_LIMIT: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum new concepts per hour for Knowledge Graph enrichment (default: 10)",
    )

    # MCP Authorization
    MCP_ENCRYPTION_KEY: Optional[str] = None  # Fernet encryption key (env only)
    MCP_ENCRYPTION_KEY_BACKUP: Optional[str] = None  # Backup encryption key (optional)
    ADMIN_KEY_IDS: list[str] = []  # Comma-separated key_ids with admin scope

    @model_validator(mode="after")
    def _parse_admin_key_ids(self) -> Self:
        """Parse ADMIN_KEY_IDS from comma-separated string in .env into list."""
        raw = os.getenv("ADMIN_KEY_IDS", "")
        if raw:
            self.ADMIN_KEY_IDS = [k.strip() for k in raw.split(",") if k.strip()]
        return self

    # Rate Limit Defaults
    DEFAULT_RATE_LIMIT_DAILY: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Default daily rate limit for new users",
    )
    DEFAULT_RATE_LIMIT_WEEKLY: int = Field(
        default=500,
        ge=1,
        le=10000,
        description="Default weekly rate limit for new users",
    )
    DEFAULT_RATE_LIMIT_MONTHLY: int = Field(
        default=2000,
        ge=1,
        le=100000,
        description="Default monthly rate limit for new users",
    )

    # Token Limit Defaults
    DEFAULT_TOKEN_LIMIT_DAILY: int | None = Field(
        default=None,
        description="Default daily token limit for new users (None = unlimited)",
    )
    DEFAULT_TOKEN_LIMIT_WEEKLY: int | None = Field(
        default=None,
        description="Default weekly token limit for new users (None = unlimited)",
    )
    DEFAULT_TOKEN_LIMIT_MONTHLY: int | None = Field(
        default=None,
        description="Default monthly token limit for new users (None = unlimited)",
    )

    @property
    def auth_enabled(self) -> bool:
        """Whether MCP authorization is enabled."""
        return bool(self.MCP_ENCRYPTION_KEY and self.MCP_ENCRYPTION_KEY.strip())

    @property
    def available_providers(self) -> list[str]:
        """Return list of available search providers based on configured keys."""
        providers = []

        # Always available (no token required)
        if "duck" in self.SEARCH_FALLBACK_CHAIN:
            providers.append("duck")

        # SearxNG is always available (public instance by default)
        if "searxng" in self.SEARCH_FALLBACK_CHAIN:
            providers.append("searxng")

        # Requires API key - Tavily
        if "tavily" in self.SEARCH_FALLBACK_CHAIN and self._has_api_key(
            "TAVILY_API_KEY"
        ):
            providers.append("tavily")

        # Requires API key - Google
        if (
            "google" in self.SEARCH_FALLBACK_CHAIN
            and self._has_google_api_key()
            and self.USE_GOOGLE_FALLBACK
        ):
            providers.append("google")

        return providers

    @property
    def available_llm_models(self) -> list[str]:
        """Return list of available LLM models from the fallback chain.

        Checks API key presence for each model without exposing key values.
        Models requiring an API key are included only if the corresponding
        key is present in the environment.
        """
        available: list[str] = []
        for model in self.LLM_MODEL_FALLBACK_CHAIN:
            # Check if the model requires an API key
            key_name = self._resolve_llm_key_name(model)
            if key_name and not os.getenv(key_name):
                continue
            available.append(model)

        # Always include the primary model if not already present
        if self.LLM_MODEL not in available:
            available.insert(0, self.LLM_MODEL)

        return available

    def _resolve_llm_key_name(self, model: str) -> str | None:
        """Map a model name to its required API key environment variable.

        Returns None if the model does not require a dedicated API key
        (e.g. local models like Ollama). Uses exact match first, then
        prefix-based fallback for model variants (e.g. 'gpt-4o-mini' →
        'LLM_API_KEY').
        """
        # Known model-to-key mappings (exact match, sorted by specificity)
        key_map: dict[str, str] = {
            "gpt-4o-2025-04": "LLM_API_KEY",
            "gpt-4o": "LLM_API_KEY",
            "gpt-4": "LLM_API_KEY",
            "claude": "CLAUDE_API_KEY",
        }

        # Exact match first
        if model in key_map:
            return key_map[model]

        # Prefix-based fallback for OpenAI models
        openai_prefixes = ["gpt-", "gpt-4o-", "o1-", "o3-"]
        for prefix in openai_prefixes:
            if model.startswith(prefix):
                return "LLM_API_KEY"

        return None

    def _has_api_key(self, key_name: str) -> bool:
        """Check if API key is available."""
        return bool(os.getenv(key_name))

    def _get_api_key(self, key_name: str) -> str | None:
        """Get API key value."""
        return os.getenv(key_name)

    def _has_google_api_key(self) -> bool:
        """Check if Google API key is available."""
        return bool(os.getenv("GOOGLE_API_KEY"))

    def _get_google_api_key(self) -> str | None:
        """Get Google API key value."""
        return os.getenv("GOOGLE_API_KEY")
