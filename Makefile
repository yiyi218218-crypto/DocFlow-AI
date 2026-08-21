.DEFAULT_GOAL := help
SHELL := /bin/bash
API := apps/api
WEB := apps/web

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: setup
setup: ## Install everything and start infra
	@test -f .env || cp .env.example .env
	cd $(API) && uv sync --extra dev
	cd $(WEB) && npm install --no-audit --no-fund
	docker compose up -d
	@echo "waiting for postgres..."
	@until docker compose exec -T postgres pg_isready -U postgres -d docflow >/dev/null 2>&1; do sleep 1; done
	$(MAKE) migrate
	@echo "✓ setup complete — run 'make seed' then 'make dev'"

.PHONY: up
up: ## Start infra containers
	docker compose up -d

.PHONY: down
down: ## Stop infra containers
	docker compose down

.PHONY: reset
reset: ## Destroy and recreate all local data
	docker compose down -v && docker compose up -d
	@until docker compose exec -T postgres pg_isready -U postgres -d docflow >/dev/null 2>&1; do sleep 1; done
	$(MAKE) migrate seed

.PHONY: migrate
migrate: ## Apply database migrations
	cd $(API) && uv run alembic upgrade head

.PHONY: revision
revision: ## Autogenerate a migration: make revision m="add x"
	cd $(API) && uv run alembic revision --autogenerate -m "$(m)"

.PHONY: seed
seed: ## Load the demo workspace (no LLM calls)
	cd $(API) && uv run python -m docflow.scripts.seed

.PHONY: api
api: ## Run the API with reload
	cd $(API) && uv run uvicorn docflow.main:app --reload --port 8000

.PHONY: worker
worker: ## Run the background worker
	cd $(API) && uv run arq docflow.worker.main.WorkerSettings

.PHONY: web
web: ## Run the Next.js dev server
	cd $(WEB) && npm run dev

.PHONY: dev
dev: ## Run api + worker + web together
	@trap 'kill 0' EXIT; \
	$(MAKE) api & $(MAKE) worker & $(MAKE) web & wait

.PHONY: test
test: ## Run the Python test suite
	cd $(API) && uv run pytest -q

.PHONY: test-unit
test-unit: ## Run only tests that need no infra
	cd $(API) && uv run pytest -q -m "not integration and not eval"

.PHONY: test-web
test-web: ## Run frontend unit tests
	cd $(WEB) && npm test

.PHONY: e2e
e2e: ## Run Playwright end-to-end tests
	cd $(WEB) && npx playwright test

.PHONY: lint
lint: ## Lint and type-check everything
	cd $(API) && uv run ruff check . && uv run ruff format --check . && uv run mypy src
	cd $(WEB) && npm run typecheck

.PHONY: fmt
fmt: ## Auto-format everything
	cd $(API) && uv run ruff check --fix . && uv run ruff format .
	cd $(WEB) && npx prettier --write .

.PHONY: check
check: lint test-unit ## Everything CI runs, locally

.PHONY: eval
eval: ## Run the AI evaluation suite (costs money)
	cd $(API) && uv run python -m docflow.eval.cli run --suite all

.PHONY: demo
demo: ## One command: infra + migrate + seed + run
	$(MAKE) up
	@until docker compose exec -T postgres pg_isready -U postgres -d docflow >/dev/null 2>&1; do sleep 1; done
	$(MAKE) migrate seed dev

.PHONY: diagrams
diagrams: ## Validate every mermaid diagram in the repo
	node scripts/check-mermaid.mjs
