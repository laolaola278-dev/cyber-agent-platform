.PHONY: help install lint test check up down logs migrate frontend-install frontend-lint frontend-build

# Everything below runs the same commands the README "Quality gates" section and
# .github/workflows/ci.yml declare authoritative. Two rules for this file:
#   1. never reinvent a gate -- if CI does not run it, it is not a gate;
#   2. never assume an activated venv -- go through `uv run --project backend`
#      so the target works from a fresh clone on any supported shell.
UV ?= uv

help:
	@echo "install          Backend (uv, frozen) + Console (npm ci) dependencies"
	@echo "lint             Ruff + ESLint -- exactly the CI lint gates"
	@echo "test             Backend test suite"
	@echo "frontend-test    Console unit and core-flow tests (Vitest)"
	@echo "frontend-build   Console typecheck + production build"
	@echo "check            lint + test + frontend-test + frontend-build (pre-delivery gate)"
	@echo "up / down / logs docker compose lifecycle (single-node evaluation)"
	@echo "migrate          alembic upgrade head"

install:
	$(UV) sync --project backend --extra dev --frozen
	npm ci --prefix frontend

lint:
	$(UV) run --project backend ruff check backend/app backend/tests benchmarks/phase22
	npm run lint --prefix frontend -- --max-warnings=0

test:
	$(UV) run --project backend pytest backend/tests -p no:cacheprovider

frontend-install:
	npm ci --prefix frontend

frontend-lint:
	npm run lint --prefix frontend -- --max-warnings=0

frontend-test:
	npm test --prefix frontend

frontend-build:
	npm run build --prefix frontend

check: lint test frontend-test frontend-build

migrate:
	$(UV) run --project backend alembic -c backend/alembic.ini upgrade head

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f
