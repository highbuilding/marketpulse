# Procfile — honcho 拉起本地 dev 多进程
# 用法: make dev (内部调 honcho start)
#
# 注意: redis 通过 docker-compose 单独管理(不在 honcho 里),
#       因为容器生命周期不该被 honcho Ctrl-C 一起干掉。
#
# 启动顺序 honcho 不保证, 但每个进程内部都做了"Redis 不可用时降级"。
#
# P1 (2026-05-29): collector 拆 ashare/us/crypto 3 进程, 故障隔离 + 独立 DuckDB.

collector_ashare: . .venv/bin/activate && python -m apps.collector.ashare.main
collector_us:     . .venv/bin/activate && python -m apps.collector.us.main
collector_crypto: . .venv/bin/activate && python -m apps.collector.crypto.main
api:              . .venv/bin/activate && uvicorn apps.api.main:app --port 8787
web:              cd apps/web && npm run dev
