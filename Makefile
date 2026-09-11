SHELL := /bin/bash

SQLITE_DATABASE_PATH ?= $(CURDIR)/.healthy-test.db
DATABASE_URL ?= sqlite+pysqlite:///$(SQLITE_DATABASE_PATH)
TEST_ENV := TZ=UTC HEALTHY_ENV=test HEALTHY_DATABASE_URL=$(DATABASE_URL) HEALTHY_COOKIE_SECURE=false HEALTHY_ALLOWED_ORIGINS=http://127.0.0.1:3000
SQLITE_TEST_DATABASE_URL := sqlite+pysqlite:///$(SQLITE_DATABASE_PATH)
LEGACY_POSTGRES_TEST_ENV := TZ=UTC HEALTHY_ENV=test HEALTHY_DATABASE_URL=$(SQLITE_TEST_DATABASE_URL) HEALTHY_COOKIE_SECURE=false HEALTHY_ALLOWED_ORIGINS=http://127.0.0.1:3000
NPM_ENV := npm_config_cache=$(CURDIR)/.npm-cache
NODE_VERSION := 24.18.0

.PHONY: node-check install db-up db-down db-reset migrate migration-cycle openapi-check api-test api-lint api-typecheck web-typecheck web-lint web-build browser-test legacy-postgres-test focused test

node-check:
	@node -e 'if (process.versions.node !== "$(NODE_VERSION)") { console.error("Expected Node $(NODE_VERSION), received " + process.versions.node); process.exit(1) }'

install: node-check
	uv sync --all-groups --frozen
	$(NPM_ENV) npm ci
	PLAYWRIGHT_BROWSERS_PATH=.playwright npx playwright install chromium

db-up:
	$(MAKE) migrate

db-down:
	$(MAKE) db-reset

db-reset:
	rm -f -- "$(SQLITE_DATABASE_PATH)" "$(SQLITE_DATABASE_PATH)-shm" "$(SQLITE_DATABASE_PATH)-wal"

migrate:
	$(TEST_ENV) uv run alembic -c migrations/alembic.ini upgrade head

migration-cycle: db-reset
	$(TEST_ENV) uv run alembic -c migrations/alembic.ini upgrade head
	$(TEST_ENV) uv run alembic -c migrations/alembic.ini downgrade base
	$(TEST_ENV) uv run alembic -c migrations/alembic.ini upgrade head
	$(TEST_ENV) uv run alembic -c migrations/alembic.ini upgrade head

openapi-check:
	$(TEST_ENV) uv run python scripts/check_openapi.py

api-test:
	$(TEST_ENV) uv run pytest tests/api

api-lint:
	uv run ruff format --check apps/api tests/api scripts migrations
	uv run ruff check apps/api tests/api scripts migrations

api-typecheck:
	uv run mypy apps/api

web-typecheck:
	$(NPM_ENV) npm run web:typecheck

web-lint:
	$(NPM_ENV) npm run web:lint

web-build:
	$(NPM_ENV) npm run web:build

browser-test: db-reset migrate web-build
	$(TEST_ENV) $(NPM_ENV) PLAYWRIGHT_BROWSERS_PATH=.playwright npm run test:browser

legacy-postgres-test:
	@test -n "$(LEGACY_POSTGRES_DATABASE_URL)" || { echo "Set LEGACY_POSTGRES_DATABASE_URL to a synthetic postgresql+psycopg URL"; exit 2; }
	$(LEGACY_POSTGRES_TEST_ENV) HEALTHY_RUN_LEGACY_POSTGRES_TESTS=true HEALTHY_LEGACY_POSTGRES_DATABASE_URL="$(LEGACY_POSTGRES_DATABASE_URL)" uv run --extra legacy-postgres pytest -m legacy_postgres tests/api/test_legacy_metric_export.py tests/api/test_legacy_metric_bridge_rehearsal.py

focused: api-test openapi-check web-typecheck browser-test

test: node-check migration-cycle api-lint api-typecheck api-test openapi-check web-typecheck web-lint web-build browser-test
