.PHONY: run dev fmt lint test unit-tests integration-tests full-test type-check security-check openapi pre-commit-install install version version-bump version-sync version-check docker-build docker-rebuild docker-push help

install: ## Установить зависимости
	uv install

dev: ## Запустить в режиме разработки (auto-reload)
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

run: ## Запустить production сервер
	uv run uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 4

# =============================================================================
# Docker commands
# =============================================================================

docker-build: ## Собрать Docker образ
	docker build -t mcp-webs:latest -f docker/Dockerfile .

docker-rebuild: ## Пересобрать начисто
	docker compose -f docker/docker-compose.dev.yml down & docker compose -f docker/docker-compose.dev.yml up -d --build

docker-push: ## Отправить Docker образ в registry
	docker push mcp-webs:latest

docker-setup-env: ## Настроить .env файл для development режима
	./docker/scripts/setup-env.sh

docker-up: ## Запустить контейнеры (создает .env если нет)
	@echo "=== Проверка .env файла ==="
	@if [ ! -f ".env" ]; then \
		echo ".env не найден. Создание из .env.example..."; \
		./docker/scripts/setup-env.sh; \
	else \
		echo ".env файл уже существует."; \
	fi
	@echo ""
	docker-compose -f docker/docker-compose.dev.yml up --build

docker-down: ## Остановить контейнеры
	docker-compose -f docker/docker-compose.dev.yml down

# =============================================================================
# Format commands
# =============================================================================

fmt: ## Форматировать код
	uv run ruff check --fix .
	uv run ruff format .

lint: ## Запустить линтеры
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy app/ --ignore-missing-imports --explicit-package-bases

# =============================================================================
# Unit Tests (fast, isolated, no external dependencies)
# =============================================================================

unit-tests: ## Запустить юнит-тесты
	uv run pytest -v tests/unit/ tests/tdd/

unit-tests-cover: ## Запустить юнит-тесты с покрытием
	uv run pytest --cov=app --cov-report=html --cov-report=term-missing tests/unit/ tests/tdd/

# =============================================================================
# Integration Tests (test full pipeline, require external services)
# =============================================================================

integration-tests: ## Запустить интеграционные тесты
	uv run pytest -v tests/integration/

integration-tests-cover: ## Запустить интеграционные тесты с покрытием
	uv run pytest --cov=app --cov-report=html --cov-report=term-missing tests/integration/

# =============================================================================
# Full Tests (unit + integration)
# =============================================================================

full-test: ## Запустить все тесты (unit + integration)
	uv run pytest -v

full-test-cover: ## Запустить все тесты с покрытием
	uv run pytest --cov=app --cov-report=html --cov-report=term-missing

# =============================================================================
# Pre-commit Hook
# =============================================================================

pre-commit-hook: ## Запустить pre-commit проверки (format + security + unit-tests)
	@echo "=== Running format checks ==="
	@if uv run ruff check . || uv run ruff format --check .; then \
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
	@echo "=== Running unit tests ==="
	@if uv run pytest -v tests/unit/ tests/tdd/; then \
		echo "✅ Unit tests passed"; \
	else \
		echo "❌ Unit tests failed"; exit 1; \
	fi
	@echo ""
	@echo "=== Pre-commit checks completed successfully ==="

# =============================================================================
# Test and Deploy commands
# =============================================================================

test: ## Запустить все тесты (alias для full-test)
	uv run pytest -v

test-coverage: ## Запустить тесты с покрытием (alias для full-test-cover)
	uv run pytest --cov=app --cov-report=html --cov-report=term-missing

type-check: ## Проверка типов через mypy
	uv run mypy app/ --ignore-missing-imports --explicit-package-bases

security-check: ## Проверка безопасности (bandit + pip-audit)
	uv run bandit -r app/ -ll
	uv run pip-audit --format columns

openapi: ## Сгенерировать OpenAPI спецификацию
	uv run python docs/templates/scripts/generate_openapi.py \
		--service-name mcp_internet_search \
		--app-path app.main:app

pre-commit-install: ## Установить pre-commit хуки
	pre-commit install

pre-commit-run: ## Запустить pre-commit проверки
	pre-commit run --all-files

# Version management
version: ## Показать текущую версию
	@grep '^version = ' pyproject.toml | cut -d'"' -f2

version-bump: ## Поднять версию (TYPE=patch|minor|major)
	@if [ -z "$(TYPE)" ]; then echo "Usage: make version-bump TYPE=patch|minor|major"; exit 1; fi
	uv run bump-my-version bump $(TYPE)
	@echo "Version bumped to $$(make version)"
	@echo "Don't forget to push the tag: git push origin --tags"

version-sync: ## Синхронизировать версию во всех файлах
	uv run python scripts/sync_version.py

version-check: ## Проверить синхронизацию версии
	uv run python scripts/sync_version.py --check

help: ## Показать эту справку
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'
