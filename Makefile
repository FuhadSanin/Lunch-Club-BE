# Everyday commands. `make` alone lists them.
SHELL := bash
.DEFAULT_GOAL := help

# Tests run on this machine against the compose database (published on DB_HOST_PORT);
# CI points them at its own Postgres service instead.
DB_HOST_PORT ?= 5434
TEST_DB := DB_HOST=127.0.0.1 DB_PORT=$(DB_HOST_PORT) DB_USER=postgres DB_PASSWORD=postgres
ifdef CI
TEST_DB :=
endif

# Checks that read settings run without a .env, and never with the local sign-in shortcut.
CHECK_ENV := DJANGO_SETTINGS_MODULE=config.test_settings DEV_AUTH_EMAIL=
PROD_ENV := DJANGO_DEBUG=false DEV_AUTH_EMAIL= DJANGO_SECRET_KEY=$$(openssl rand -base64 48)

.PHONY: help build image up down logs migrate shell lint format typecheck check \
	security test ci hooks

help: ## List the commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

## Docker

build: ## Build the local development images
	docker compose build

image: ## Build the production image (what Cloud Run runs)
	docker build --target prod -t lunch-backend:latest .

up: ## Start Postgres, the API and the web app; code changes reload them
	docker compose up --watch

bot-api: ## Run the API on this Mac (not in Docker) so the order bot can drive your Chrome
	docker compose up -d db
	docker compose stop api
	set -a; . ./.env; set +a; export DB_HOST=127.0.0.1 DB_PORT=$(DB_HOST_PORT); \
		uv run --group bot python manage.py migrate --noinput && \
		uv run --group bot python manage.py runserver --noreload 127.0.0.1:8001

down: ## Stop everything (data is kept)
	docker compose down

logs: ## Follow the API and web app logs
	docker compose logs -f api web

migrate: ## Apply migrations in the running API container
	docker compose exec api python manage.py migrate

shell: ## Django shell in the running API container
	docker compose exec api python manage.py shell

## Code checks (the pre-commit hook and CI run these)

lint: ## Ruff lint (rules, naming, imports, complexity, security) and format check
	uv run ruff check .
	uv run ruff format --check .

format: ## Fix what Ruff can fix and format the code
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## MyPy with the Django and DRF plugins
	uv run mypy apps config tests

check: ## Django's system checks, plus the production security checks
	$(CHECK_ENV) uv run python manage.py check --fail-level WARNING
	$(PROD_ENV) uv run python manage.py check --deploy --fail-level WARNING

security: ## Bandit on the code, pip-audit on the locked dependencies
	uv run bandit -c pyproject.toml -r apps config -q
	uv run pip-audit --strict --progress-spinner off \
		-r <(uv export --no-hashes --no-emit-project --format requirements-txt -q)

test: ## Tests with coverage (fails under 80%)
ifndef CI
	docker compose up -d --wait db
endif
	$(TEST_DB) uv run pytest --cov --cov-report=term

ci: lint typecheck check security test ## Everything CI runs

hooks: ## Install the git hooks that run these checks on every commit
	uv run pre-commit install
