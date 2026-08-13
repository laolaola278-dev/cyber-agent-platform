.PHONY: up down logs test lint format migrate frontend-build

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

migrate:
	cd backend && alembic upgrade head

test:
	cd backend && pytest

lint:
	cd backend && ruff check . && black --check .

format:
	cd backend && ruff check . --fix && black .

frontend-build:
	cd frontend && npm run build
