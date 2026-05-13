.PHONY: install dev test test-integration test-full lint typecheck web-install web-dev clean

install:
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

dev:
	. .venv/bin/activate && uvicorn apps.api.main:app --reload --port 8787 & \
	cd apps/web && npm run dev

test:
	. .venv/bin/activate && pytest -m "not integration"

test-integration:
	. .venv/bin/activate && pytest -m integration

test-full: test test-integration
	cd apps/web && npx playwright test

lint:
	. .venv/bin/activate && ruff check core apps tests

typecheck:
	. .venv/bin/activate && mypy core apps

web-install:
	cd apps/web && npm install

clean:
	rm -rf .venv apps/web/node_modules apps/web/.next data/*.duckdb data/*.db
