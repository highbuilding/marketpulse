.PHONY: install dev test test-integration test-full lint typecheck web-install web-dev clean

install:
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

dev:
	# NOTE: uvicorn --reload 在本项目下不安全 — py_mini_racer V8 状态在 reload 后会污染
	# 导致 worker SIGABRT (见 docs/TODO.md 和 memory/project_mini_racer_lock.md)。
	# 代码变更请手动重启:pkill -f "uvicorn apps.api" + 重跑此命令。
	. .venv/bin/activate && uvicorn apps.api.main:app --port 8787 & \
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

warmup:
	. .venv/bin/activate && NO_PROXY='*' python -m apps.warmup --from-watchlist --days 365
