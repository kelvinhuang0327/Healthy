SHELL := /bin/bash

DATABASE_URL := postgresql+psycopg://healthy@127.0.0.1:55432/healthy_test
TEST_ENV := PGTZ=UTC TZ=UTC HEALTHY_ENV=test HEALTHY_DATABASE_URL=$(DATABASE_URL) HEALTHY_COOKIE_SECURE=false HEALTHY_ALLOWED_ORIGINS=http://127.0.0.1:3000
NPM_ENV := npm_config_cache=$(CURDIR)/.npm-cache
NODE_VERSION := 24.18.0

.PHONY: node-check install db-up db-down migrate migration-cycle openapi-check api-test api-lint api-typecheck web-typecheck web-lint web-build browser-test focused test

node-check:
	@node -e 'if (process.versions.node !== "$(NODE_VERSION)") { console.error("Expected Node $(NODE_VERSION), received " + process.versions.node); process.exit(1) }'

install: node-check
	uv sync --all-groups --frozen
	$(NPM_ENV) npm ci
	PLAYWRIGHT_BROWSERS_PATH=.playwright npx playwright install chromium

db-up:
	docker compose -f compose.test.yml up -d --wait

db-down:
	docker compose -f compose.test.yml down -v --remove-orphans

migrate:
	$(TEST_ENV) uv run alembic -c migrations/alembic.ini upgrade head

migration-cycle:
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

browser-test:
	$(TEST_ENV) $(NPM_ENV) PLAYWRIGHT_BROWSERS_PATH=.playwright npm run test:browser

focused: api-test openapi-check web-typecheck browser-test

test: node-check db-up migration-cycle api-lint api-typecheck api-test openapi-check web-typecheck web-lint web-build browser-test
