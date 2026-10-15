.PHONY: up down logs migrate openapi check

COMPOSE := docker compose
COMPOSE_TEST := docker compose -f compose.yaml -f compose.test.yaml

# One command to bring up the whole local stack (ADR-0005).
up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

migrate:
	$(COMPOSE) exec api uv run --no-sync python -m novel_llm.migrate

# Regenerate the OpenAPI export and the typed frontend client (ADR-0008).
openapi:
	cd backend && uv run python -m novel_llm.contracts.export_openapi --output ../frontend/openapi.json
	cd frontend && npm run gen:client

# The merge gate (ADR-0008): Python formatting/static checks and tests,
# frontend checks and tests, OpenAPI client drift check.
check:
	$(COMPOSE_TEST) up -d --wait postgres
	cd backend && uv run ruff format --check . && uv run ruff check . && uv run mypy src tests && uv run pytest
	$(MAKE) openapi
	cd frontend && npm run typecheck && npm test
	cd frontend && git diff --exit-code -- openapi.json src/lib/api/schema.d.ts
