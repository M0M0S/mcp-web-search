.PHONY: run dev fmt lint test unit-tests integration-tests full-test type-check security-check openapi pre-commit-install install version version-bump version-sync version-check docker-build docker-rebuild docker-push help

install: ## Install dependencies
	uv install

dev: ## Run in development mode (auto-reload)
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

run: ## Run production server
	uv run uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 4

# =============================================================================
# Docker commands
# =============================================================================

docker-build: ## Build Docker image
	docker build -t mcp-webs:latest -f docker/Dockerfile .

docker-rebuild: ## Rebuild from scratch
	docker compose -f docker/docker-compose.dev.yml down & docker compose -f docker/docker-compose.dev.yml up -d --build

docker-push: ## Push Docker image to registry
	docker push mcp-webs:latest

docker-setup-env: ## Set up .env file for development mode
	./docker/scripts/setup-env.sh

docker-up: ## Start containers (creates .env if missing)
	@echo "=== Checking .env file ==="
	@if [ ! -f ".env" ]; then \
		echo ".env not found. Creating from .env.example..."; \
		./docker/scripts/setup-env.sh; \
	else \
		echo ".env file already exists."; \
	fi
	@echo ""
	docker-compose -f docker/docker-compose.dev.yml up --build

docker-down: ## Stop containers
	docker-compose -f docker/docker-compose.dev.yml down

# =============================================================================
# Format commands
# =============================================================================

fmt: ## Format code
	uv run ruff check --fix .
	uv run ruff format .

lint: ## Run linters
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy app/ --explicit-package-bases

# =============================================================================
# Unit Tests (fast, isolated, no external dependencies)
# =============================================================================

unit-tests: ## Run unit tests
	uv run pytest -v tests/unit/ tests/tdd/

unit-tests-cover: ## Run unit tests with coverage
	uv run pytest --cov=app --cov-report=html --cov-report=term-missing tests/unit/ tests/tdd/

# =============================================================================
# Integration Tests (test full pipeline, require external services)
# =============================================================================

integration-tests: ## Run integration tests
	uv run pytest -v tests/integration/

integration-tests-cover: ## Run integration tests with coverage
	uv run pytest --cov=app --cov-report=html --cov-report=term-missing tests/integration/

# =============================================================================
# Full Tests (unit + integration)
# =============================================================================

full-test: ## Run all tests (unit + integration, with warnings as errors)
	uv run pytest -v -W error::ResourceWarning

full-test-cover: ## Run all tests with coverage
	uv run pytest --cov=app --cov-report=html --cov-report=term-missing

# =============================================================================
# Pre-commit Hook
# =============================================================================

pre-commit-hook: ## Run pre-commit checks (format + security + all-tests)
	@echo "=== Running format checks ==="
	@if uv run ruff check . && uv run ruff format --check .; then \
		echo "✅ Format checks passed"; \
	else \
		echo "❌ Format checks failed"; exit 1; \
	fi
	@echo ""
	@echo "=== Running security checks ==="
	@if uv run bandit -r app/ -ll; then \
		echo "✅ Security checks passed"; \
	else \
		echo "❌ Security checks failed"; exit 1; \
	fi
	@echo ""
	@echo "=== Running all tests (excluding integration) ==="
	@if uv run pytest tests/ --ignore=tests/integration/ -v --tb=short; then \
		echo "✅ Tests passed"; \
	else \
		echo "❌ Tests failed"; exit 1; \
	fi
	@echo ""
	@echo "=== Running tests with ResourceWarning as errors ==="
	@if uv run pytest tests/ --ignore=tests/integration/ -v --tb=short -W error::ResourceWarning; then \
		echo "✅ ResourceWarning check passed"; \
	else \
		echo "❌ ResourceWarning check failed"; exit 1; \
	fi
	@echo ""
	@echo "=== Pre-commit checks completed successfully ==="

# =============================================================================
# Test and Deploy commands
# =============================================================================

test: ## Run all tests (unit + integration, with warnings as errors)
	uv run pytest -v -W error::ResourceWarning

test-coverage: ## Run tests with coverage (alias for full-test-cover)
	uv run pytest --cov=app --cov-report=html --cov-report=term-missing

type-check: ## Type checking via mypy
	uv run mypy app/ --explicit-package-bases

security-check: ## Security check (bandit + pip-audit)
	uv run bandit -r app/ -ll
	uv run pip-audit --format columns

openapi: ## Generate OpenAPI specification
	uv run python docs/templates/scripts/generate_openapi.py \
		--service-name mcp_internet_search \
		--app-path app.main:app

pre-commit-install: ## Install pre-commit hooks
	pre-commit install

pre-commit-run: ## Run pre-commit checks
	pre-commit run --all-files

# Version management
version: ## Show current version
	@grep '^version = ' pyproject.toml | cut -d'"' -f2

version-bump: ## Bump version (TYPE=patch|minor|major)
	@if [ -z "$(TYPE)" ]; then echo "Usage: make version-bump TYPE=patch|minor|major"; exit 1; fi
	uv run bump-my-version bump $(TYPE)
	@echo "Version bumped to $$(make version)"
	@echo "Don't forget to push the tag: git push origin --tags"

version-sync: ## Sync version across all files
	uv run python scripts/sync_version.py

version-check: ## Check version synchronization
	uv run python scripts/sync_version.py --check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'
