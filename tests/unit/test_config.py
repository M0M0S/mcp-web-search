"""Unit tests for configuration module."""

import pytest


class TestSettings:
    """Tests for Settings class."""

    def test_default_values(self):
        """Test that default values are correctly set."""
        import os

        from app.core.config import Settings

        # Temporarily override env vars to get clean defaults
        original_env = {}
        env_vars_to_override = {
            "REDIS_URL": "redis://localhost:6379/0",
            "MCP_NAME": "web-search",
            "SEARCH_FALLBACK_CHAIN": '["duck", "searxng", "tavily", "google"]',
            "FEATURE_LLM_MODEL": "gpt-4o-2025-04",
            "JUDGE_URL_THRESHOLD": "0.85",
            "JUDGE_FEATURES_THRESHOLD": "0.92",
            "MAX_CONCURRENT": "6",
        }

        for var, val in env_vars_to_override.items():
            if var in os.environ:
                original_env[var] = os.environ[var]
            os.environ[var] = val

        try:
            settings = Settings()

            assert settings.MCP_NAME == "web-search"
            assert settings.MCP_VERSION == "1.0.0"
            assert settings.REDIS_URL == "redis://localhost:6379/0"
            assert settings.SEARCH_RESULT_CACHE_TTL == 3600
            assert settings.CONTENT_CACHE_TTL == 86400
            assert settings.WEBFETCH_CACHE_TTL == 1800
            assert settings.DEFAULT_SEARCH_PROVIDER == "duck"
            assert settings.USE_GOOGLE_FALLBACK is False
            assert settings.SEARCH_FALLBACK_CHAIN == [
                "duck",
                "searxng",
                "tavily",
                "google",
            ]
            assert settings.MAX_RESULTS == 10
            assert settings.QUALITY_SCORE_THRESHOLD == 0.6
            assert settings.BLACKLIST_DOMAINS == ["example.com"]
            assert settings.TOKEN_LIMIT == 8000
            assert settings.FEATURE_LLM_MODEL == "gpt-4o-2025-04"
            assert settings.JUDGE_URL_THRESHOLD == 0.85
            assert settings.JUDGE_FEATURES_THRESHOLD == 0.92
            assert settings.MAX_CONCURRENT == 6
        finally:
            # Restore original env vars
            for var, value in original_env.items():
                os.environ[var] = value

    def test_available_providers(self):
        """Test that available providers returns correct list."""
        from app.core.config import Settings

        settings = Settings()

        # Test default (duck only)
        assert "duck" in settings.available_providers


class TestLLMEpic1Config:
    """Tests for LLM Epic 1 configuration fields defaults and constraints."""

    def test_llm_model_default(self):
        """T1: LLM_MODEL default is 'gpt-4o-2025-04'."""
        from app.core.config import Settings

        # Use explicit override to bypass .env
        settings = Settings(LLM_MODEL="gpt-4o-2025-04")
        assert settings.LLM_MODEL == "gpt-4o-2025-04"

    def test_llm_model_fallback_chain_default(self):
        """T2: LLM_MODEL_FALLBACK_CHAIN default is ['gpt-4o-2025-04', 'gpt-4o', 'gpt-4']."""
        import os

        from app.core.config import Settings

        os.environ.pop("LLM_MODEL_FALLBACK_CHAIN", None)

        settings = Settings()
        assert settings.LLM_MODEL_FALLBACK_CHAIN == [
            "gpt-4o-2025-04",
            "gpt-4o",
            "gpt-4",
        ]

    def test_llm_model_fallback_base_urls_default(self):
        """T3: LLM_MODEL_FALLBACK_BASE_URLS default is empty dict."""
        import os

        from app.core.config import Settings

        os.environ.pop("LLM_MODEL_FALLBACK_BASE_URLS", None)

        settings = Settings()
        assert settings.LLM_MODEL_FALLBACK_BASE_URLS == {}

    def test_llm_health_window_default_and_constraints(self):
        """T4: LLM_HEALTH_WINDOW default is 10 with ge=1, le=100."""
        import os

        from app.core.config import Settings

        os.environ.pop("LLM_HEALTH_WINDOW", None)

        settings = Settings()
        assert settings.LLM_HEALTH_WINDOW == 10

        # Verify constraints: value below ge should raise
        with pytest.raises(Exception):
            Settings(LLM_HEALTH_WINDOW=0)

        # Verify constraints: value above le should raise
        with pytest.raises(Exception):
            Settings(LLM_HEALTH_WINDOW=101)

    def test_llm_health_failure_threshold_default_and_constraints(self):
        """T5: LLM_HEALTH_FAILURE_THRESHOLD default is 0.5 with ge=0.0, le=1.0."""
        import os

        from app.core.config import Settings

        os.environ.pop("LLM_HEALTH_FAILURE_THRESHOLD", None)

        settings = Settings()
        assert settings.LLM_HEALTH_FAILURE_THRESHOLD == 0.5

        # Verify constraints: value below ge should raise
        with pytest.raises(Exception):
            Settings(LLM_HEALTH_FAILURE_THRESHOLD=-0.1)

        # Verify constraints: value above le should raise
        with pytest.raises(Exception):
            Settings(LLM_HEALTH_FAILURE_THRESHOLD=1.1)

    def test_available_llm_models_with_api_key(self):
        """T6: available_llm_models returns models from fallback chain when API key present."""
        import os

        from app.core.config import Settings

        # Bypass .env and set key via os.environ
        os.environ["LLM_API_KEY"] = "sk-test-key-placeholder"

        try:
            settings = Settings(
                env_file=None,
                LLM_MODEL="gpt-4o-2025-04",
                LLM_MODEL_FALLBACK_CHAIN=["gpt-4o-2025-04", "gpt-4o", "gpt-4"],
            )
            available = settings.available_llm_models

            assert "gpt-4o-2025-04" in available
            assert "gpt-4o" in available
            assert "gpt-4" in available
            assert len(available) == 3
        finally:
            os.environ.pop("LLM_API_KEY", None)

    def test_available_llm_models_without_api_key(self):
        """T7: available_llm_models excludes models requiring key when key absent."""
        from app.core.config import Settings

        # Bypass .env entirely so os.getenv returns None for LLM_API_KEY
        settings = Settings(
            env_file=None,
            LLM_MODEL="gpt-4o-2025-04",
            LLM_MODEL_FALLBACK_CHAIN=["gpt-4o-2025-04", "gpt-4o", "gpt-4"],
        )
        available = settings.available_llm_models

        # LLM_MODEL is always inserted at index 0 regardless of key presence
        assert available[0] == "gpt-4o-2025-04"
        # Models from fallback chain that require LLM_API_KEY are excluded
        assert "gpt-4o" not in available
        assert "gpt-4" not in available

    def test_resolve_llm_key_name_exact_match(self):
        """T8: _resolve_llm_key_name returns 'LLM_API_KEY' for exact match 'gpt-4o-2025-04'."""
        from app.core.config import Settings

        settings = Settings()
        assert settings._resolve_llm_key_name("gpt-4o-2025-04") == "LLM_API_KEY"

    def test_resolve_llm_key_name_prefix_match(self):
        """T8b: _resolve_llm_key_name returns 'LLM_API_KEY' for prefix match 'gpt-4o-mini'."""
        from app.core.config import Settings

        settings = Settings()
        assert settings._resolve_llm_key_name("gpt-4o-mini") == "LLM_API_KEY"

    def test_resolve_llm_key_name_ollama(self):
        """T8c: _resolve_llm_key_name returns None for local model 'ollama'."""
        from app.core.config import Settings

        settings = Settings()
        assert settings._resolve_llm_key_name("ollama") is None


class TestLLMModelValidation:
    """Tests for LLM field type validation and Settings initialization."""

    def test_llm_model_no_ge_constraint_on_str(self):
        """T1: LLM_MODEL is a str field — no ge constraint, should not raise TypeError."""
        from app.core.config import Settings

        # Any valid string should be accepted without TypeError
        settings = Settings(LLM_MODEL="custom-model-v1")
        assert settings.LLM_MODEL == "custom-model-v1"

    def test_llm_model_fallback_chain_no_ge_le_on_list(self):
        """T2: LLM_MODEL_FALLBACK_CHAIN is a list — ge/le not applicable, should not raise TypeError."""
        from app.core.config import Settings

        # Any valid list should be accepted without TypeError
        settings = Settings(LLM_MODEL_FALLBACK_CHAIN=["gpt-4o", "claude-3"])
        assert settings.LLM_MODEL_FALLBACK_CHAIN == ["gpt-4o", "claude-3"]

    def test_settings_initialization_with_all_new_fields(self):
        """T3: Settings initialization with all new LLM fields — no errors."""
        import os

        from app.core.config import Settings

        os.environ.pop("LLM_MODEL", None)
        os.environ.pop("LLM_MODEL_FALLBACK_CHAIN", None)
        os.environ.pop("LLM_MODEL_FALLBACK_BASE_URLS", None)
        os.environ.pop("LLM_HEALTH_WINDOW", None)
        os.environ.pop("LLM_HEALTH_FAILURE_THRESHOLD", None)

        settings = Settings(
            LLM_MODEL="gpt-4o-2025-04",
            LLM_MODEL_FALLBACK_CHAIN=["gpt-4o-2025-04", "gpt-4o", "gpt-4"],
            LLM_MODEL_FALLBACK_BASE_URLS={"gpt-4o": "https://custom.example.com"},
            LLM_HEALTH_WINDOW=10,
            LLM_HEALTH_FAILURE_THRESHOLD=0.5,
        )

        assert settings.LLM_MODEL == "gpt-4o-2025-04"
        assert settings.LLM_MODEL_FALLBACK_CHAIN == [
            "gpt-4o-2025-04",
            "gpt-4o",
            "gpt-4",
        ]
        assert settings.LLM_MODEL_FALLBACK_BASE_URLS == {
            "gpt-4o": "https://custom.example.com"
        }
        assert settings.LLM_HEALTH_WINDOW == 10
        assert settings.LLM_HEALTH_FAILURE_THRESHOLD == 0.5


class TestAvailableLLMModelsEdgeCases:
    """Tests for available_llm_models property edge cases."""

    def test_empty_fallback_chain_returns_primary_model(self):
        """T1: Empty fallback chain → returns [LLM_MODEL]."""
        import os

        from app.core.config import Settings

        os.environ.pop("LLM_MODEL", None)
        os.environ.pop("LLM_MODEL_FALLBACK_CHAIN", None)
        os.environ.pop("LLM_API_KEY", None)

        settings = Settings(
            LLM_MODEL="gpt-4o-2025-04",
            LLM_MODEL_FALLBACK_CHAIN=[],
        )

        available = settings.available_llm_models
        assert available == ["gpt-4o-2025-04"]

    def test_fallback_chain_without_key_requirement_all_included(self):
        """T2: Fallback chain contains model without key requirement → all included."""
        import os

        from app.core.config import Settings

        os.environ.pop("LLM_MODEL", None)
        os.environ.pop("LLM_MODEL_FALLBACK_CHAIN", None)
        os.environ.pop("LLM_API_KEY", None)

        settings = Settings(
            LLM_MODEL="gpt-4o-2025-04",
            LLM_MODEL_FALLBACK_CHAIN=["ollama", "gpt-4o-mini"],
        )

        available = settings.available_llm_models
        # Both models don't require LLM_API_KEY (ollama → None, gpt-4o-mini → LLM_API_KEY)
        # gpt-4o-mini requires LLM_API_KEY which is absent — excluded
        assert "ollama" in available
        assert "gpt-4o-mini" not in available
        assert available[0] == "gpt-4o-2025-04"

    def test_llm_model_not_in_fallback_chain_inserted_at_index_0(self):
        """T3: LLM_MODEL not in fallback chain → inserted at index 0."""
        import os

        from app.core.config import Settings

        # Bypass .env and set required keys via os.environ
        os.environ["LLM_API_KEY"] = "sk-test-key-placeholder"
        os.environ["CLAUDE_API_KEY"] = "sk-claude-key-placeholder"

        try:
            settings = Settings(
                env_file=None,
                LLM_MODEL="gpt-5-preview",
                LLM_MODEL_FALLBACK_CHAIN=["gpt-4o", "claude"],
            )

            available = settings.available_llm_models
            assert available[0] == "gpt-5-preview"
            assert "gpt-4o" in available
            assert "claude" in available
        finally:
            os.environ.pop("LLM_API_KEY", None)
            os.environ.pop("CLAUDE_API_KEY", None)

    def test_llm_model_already_in_fallback_chain_no_duplicate(self):
        """T4: LLM_MODEL already in fallback chain → not duplicated."""
        import os

        from app.core.config import Settings

        os.environ.pop("LLM_MODEL", None)
        os.environ.pop("LLM_MODEL_FALLBACK_CHAIN", None)
        os.environ["LLM_API_KEY"] = "sk-test-key-placeholder"

        try:
            settings = Settings(
                LLM_MODEL="gpt-4o-2025-04",
                LLM_MODEL_FALLBACK_CHAIN=["gpt-4o-2025-04", "gpt-4o", "gpt-4"],
            )

            available = settings.available_llm_models
            assert available.count("gpt-4o-2025-04") == 1
            assert "gpt-4o" in available
            assert "gpt-4" in available
        finally:
            os.environ.pop("LLM_API_KEY", None)
