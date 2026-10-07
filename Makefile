# Torqrun developer commands. Running the stack needs only Docker; the Python checks need uv.
COMPOSE := docker compose -f deploy/compose/docker-compose.yml --project-directory .
-include .env
POSTGRES_PASSWORD ?= torqrun_dev
POSTGRES_PORT ?= 55432
WEB_PORT ?= 8080
API_PORT ?= 8000
TEST_DB_URL := postgresql://torqrun:$(POSTGRES_PASSWORD)@127.0.0.1:$(POSTGRES_PORT)/postgres
WEB_IN_DOCKER := docker run --rm -e CI=true -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 -v $(CURDIR)/apps/web:/app -w /app node:22-alpine sh -c

.DEFAULT_GOAL := help
PLAYWRIGHT_VERSION := $(shell sed -n 's/.*"@playwright\/test": "[^0-9]*\([0-9.]*\)".*/\1/p' apps/web/package.json)

.PHONY: help up up-tls down restart logs ps clean migrate test test-unit test-integration lint fmt typecheck web-check e2e-ui e2e-ui-isolated backup restore backup-drill bench security check

help: ## Show this help
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-17s\033[0m %s\n", $$1, $$2}'

up-tls: ## Like `up`, plus Caddy HTTPS on 80/443 for remote agents (needs TORQRUN_DOMAIN)
	@tools/dev/init-env.sh
	$(COMPOSE) -f deploy/compose/docker-compose.tls.yml up --build -d

up: ## Build and start the stack (creates .env on first run), wait until healthy
	@tools/dev/init-env.sh
	$(COMPOSE) up --build -d
	@WEB_PORT=$(WEB_PORT) tools/dev/wait-for-ready.sh
	@echo "UI:  http://127.0.0.1:$(WEB_PORT)   API docs: http://127.0.0.1:$(WEB_PORT)/api/docs"

down: ## Stop the stack (keeps data)
	$(COMPOSE) down

restart: ## Restart all services
	$(COMPOSE) restart

logs: ## Follow logs of all services
	$(COMPOSE) logs -f --tail=100

ps: ## Show service status
	$(COMPOSE) ps

clean: ## Stop the stack and DELETE its data (database + agent identity)
	$(COMPOSE) down -v

migrate: ## Apply database migrations in the running stack
	$(COMPOSE) run --rm migrate

test: test-unit test-integration ## Run all Python tests

test-unit: ## Python tests that need no database
	uv run pytest -m "not integration"

test-integration: ## Integration + end-to-end Python tests against the stack's Postgres (run `make up` first)
	TORQRUN_TEST_DATABASE_URL=$(TEST_DB_URL) uv run pytest -m integration

lint: ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-format and fix lint issues
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## mypy (strict)
	uv run mypy

web-check: ## Typecheck, test and build the web UI inside Docker (no local Node needed)
	$(WEB_IN_DOCKER) "corepack enable && pnpm install --frozen-lockfile && pnpm typecheck && pnpm test && pnpm build"

e2e-ui: ## Browser tests (Playwright, in Docker) against the running stack
	docker run --rm --network host -e CI=true -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 -e TORQRUN_E2E_BASE_URL=http://127.0.0.1:$(WEB_PORT) $(E2E_EXTRA) \
	  -v $(CURDIR)/apps/web:/app -w /app mcr.microsoft.com/playwright:v$(PLAYWRIGHT_VERSION)-noble \
	  sh -c "corepack enable && pnpm install --frozen-lockfile && pnpm e2e"

# License for the throwaway stack: the one in .env by default; `E2E_LICENSE=` tests Community.
E2E_LICENSE ?= $(TORQRUN_LICENSE_KEY)
E2E_ENV := WEB_PORT=18080 API_PORT=18000 POSTGRES_PORT=55433 TORQRUN_LICENSE_KEY='$(E2E_LICENSE)' \
  TORQRUN_BOOTSTRAP_ADMIN_EMAIL=e2e-admin@example.com TORQRUN_BOOTSTRAP_ADMIN_PASSWORD=e2e-admin-password
E2E_COMPOSE := $(E2E_ENV) $(COMPOSE) -p torqrun-e2e

e2e-ui-isolated: ## Browser tests against a throwaway stack (own ports + volumes; your stack is untouched)
	@tools/dev/init-env.sh
	$(E2E_COMPOSE) up --build -d
	tools/dev/wait-for-ready.sh http://127.0.0.1:18080 300
	$(MAKE) e2e-ui WEB_PORT=18080 E2E_EXTRA="-e TORQRUN_E2E_EMAIL=e2e-admin@example.com -e TORQRUN_E2E_PASSWORD=e2e-admin-password" \
	  || { $(E2E_COMPOSE) down -v; exit 1; }
	$(E2E_COMPOSE) down -v

backup: ## Back up the database and artifacts to ./backups (not the secret key: keep .env safe)
	tools/ops/backup.sh backups

restore: ## Restore a backup into this stack, replacing its data: make restore BACKUP=backups/torqrun-…
	@test -n "$(BACKUP)" || { echo "usage: make restore BACKUP=backups/torqrun-<timestamp>"; exit 2; }
	tools/ops/restore.sh "$(BACKUP)" --yes

backup-drill: ## Prove backup/restore on a throwaway stack (your stack is untouched)
	tools/ops/backup-drill.sh

bench: ## Load test a throwaway stack (see docs/operations/benchmarks.md)
	tools/bench/run-bench.sh

security: ## Vulnerability scans: Python deps, web deps, built images (run `make up` first)
	tools/ops/security-scan.sh

check: lint typecheck test web-check ## Everything CI runs (except e2e-ui)
