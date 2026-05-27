.PHONY: install dev dev-redis dev-stop test test-integration test-full lint typecheck web-install web-dev clean warmup

install:
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

dev-redis:
	docker compose -f docker-compose.dev.yml up -d redis

dev: dev-redis
	# 用 honcho 拉起 collector + api + web (Procfile 定义)
	# Redis 单独由 docker-compose 管理(不进 honcho,Ctrl-C 不会停容器)
	# 雷区 2: 不能加 uvicorn --reload — V8 状态会污染。
	# 代码变更请 Ctrl-C 退出 honcho 再重新 make dev。
	. .venv/bin/activate && honcho start -f Procfile

dev-stop:
	pkill -9 -f "apps.collector.main" 2>/dev/null || true
	pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null || true
	docker compose -f docker-compose.dev.yml stop redis
	@echo "stopped collector / api / redis (web 由 honcho/Ctrl-C 管理)"

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
