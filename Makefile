.PHONY: install dev dev-redis dev-stop test test-integration test-full lint typecheck web-install web-dev clean init-data warmup

install:
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

dev-redis:
	docker compose -f docker-compose.dev.yml up -d redis

dev: dev-redis
	# Redis 单独由 docker-compose 管理(不进 honcho)
	. .venv/bin/activate && honcho start -f Procfile

dev-stop:
	pkill -9 -f "apps.collector.ashare" 2>/dev/null || true
	pkill -9 -f "apps.collector.us" 2>/dev/null || true
	pkill -9 -f "apps.collector.crypto" 2>/dev/null || true
	pkill -9 -f "apps.collector.main" 2>/dev/null || true
	pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null || true
	docker compose -f docker-compose.dev.yml stop redis
	@echo "stopped"

# === 数据初始化 (新机器部署 Step 1) ===
# 补齐全市场全周期 K 线。运行前需先 make dev-stop (DuckDB 单写锁).
# 只初始化某市场: make init-data ARGS="--market us"
init-data: dev-redis
	. .venv/bin/activate && NO_PROXY='*' python scratch/init_data.py $(ARGS)
	@echo "init-data done. 现在可以 make dev 启动服务"

# 旧 warmup (保留兼容)
warmup:
	. .venv/bin/activate && NO_PROXY='*' python -m apps.warmup --from-watchlist --days 365
