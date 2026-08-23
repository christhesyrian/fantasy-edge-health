# Common operations.
#
# Every target works on macOS, Linux, and Windows under Git Bash or WSL. Where
# a target cannot be made portable it says so rather than failing obscurely.
# Windows users without make can read this file as a list of the commands to run.

PYTHON ?= ./.venv/bin/python
PIP    ?= ./.venv/bin/pip
RUFF   ?= ./.venv/bin/ruff
MYPY   ?= ./.venv/bin/mypy
PYTEST ?= ./.venv/bin/python -m pytest
NPM    ?= npm

.DEFAULT_GOAL := help
.PHONY: help setup dev dev-api dev-web test test-py test-web test-live lint format \
        typecheck quality quality-data migrate migration seed ingest simulate build \
        clean up down logs

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Create the virtualenv and install everything
	python3.14 -m venv .venv || python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev,ml]"
	$(NPM) install
	@echo "Ready. Run 'make dev' in one terminal and 'make dev-web' in another."

dev-api: ## Run the API with reload
	$(PYTHON) -m uvicorn fhe.api.app:app --reload --host 127.0.0.1 --port 8000

dev-web: ## Run the web app
	$(NPM) run dev

dev: dev-api ## Alias for dev-api

# ---------------------------------------------------------------- quality ---

lint: ## Lint Python and TypeScript
	$(RUFF) check src tests
	$(NPM) run lint

format: ## Format everything in place
	$(RUFF) format src tests
	$(RUFF) check src tests --fix
	$(NPM) run format

typecheck: ## Type-check Python and TypeScript
	$(MYPY)
	$(NPM) run typecheck

test-py: ## Run the Python suite
	$(PYTEST) -q

test-web: ## Run the frontend suite
	$(NPM) run test

test: test-py test-web ## Run every suite

test-live: ## Run the opt-in tests that hit real providers
	$(PYTEST) -q -m live --no-header

quality: format lint typecheck test ## Everything CI runs, in order

# ------------------------------------------------------------------- data ---

migrate: ## Apply database migrations
	./.venv/bin/alembic upgrade head

migration: ## Generate a migration: make migration m="describe the change"
	./.venv/bin/alembic revision --autogenerate -m "$(m)"

ingest: ## Sync players, then backfill injuries and workload
	$(PYTHON) -m fhe.cli ingest players
	$(PYTHON) -m fhe.cli ingest injuries --seasons 2023,2024,2025
	$(PYTHON) -m fhe.cli ingest workload --seasons 2024,2025

quality-data: ## Run data-quality checks against the database
	$(PYTHON) -m fhe.cli quality

seed: ## Create the schema and load demo data
	$(PYTHON) -m fhe.cli seed

simulate: ## Run a headless mock draft and print the board
	$(PYTHON) -m fhe.cli simulate --seed 42

# ------------------------------------------------------------------ infra ---

up: ## Start the full stack in Docker
	docker compose up --build

down: ## Stop the stack and remove volumes
	docker compose down -v

logs: ## Tail the stack logs
	docker compose logs -f --tail=100

build: ## Build the production web bundle
	$(NPM) run build

clean: ## Remove caches and build output
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov .coverage
	rm -rf apps/web/.next apps/web/out
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
