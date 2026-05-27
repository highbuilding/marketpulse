# Procfile — honcho 拉起本地 dev 多进程
# 用法: make dev (内部调 honcho start)
#
# 注意: redis 通过 docker-compose 单独管理(不在 honcho 里),
#       因为容器生命周期不该被 honcho Ctrl-C 一起干掉。
#
# 启动顺序 honcho 不保证, 但每个进程内部都做了"Redis 不可用时降级"。

collector: . .venv/bin/activate && python -m apps.collector.main
api:       . .venv/bin/activate && uvicorn apps.api.main:app --port 8787
web:       cd apps/web && npm run dev
