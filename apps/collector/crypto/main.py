"""crypto collector 进程入口 (stub).

P2 接 BinanceAdapter REST + backfill cron.
P3 接 binance_ws_consumer.

参考: docs/superpowers/specs/2026-05-29-crypto-sse-and-collector-split-design.md
"""
from __future__ import annotations

from apps.collector.base import setup_proxy_and_logging
setup_proxy_and_logging("collector_crypto")

import os
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI

from apps.collector.base import health_app, install_async_exception_handler

log = structlog.get_logger(__name__)
_BASE = Path(__file__).resolve().parents[3]
_DATA = _BASE / "data"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("collector_crypto.boot")
    install_async_exception_handler()

    # 注入本市场 BarRepo (P2 / P3 用)
    from apps.api.deps import set_bar_repo_override
    from core.persistence.duckdb_repo import BarRepo
    bar_repo = BarRepo(str(_DATA / "bars_crypto.duckdb"))
    bar_repo.init()
    set_bar_repo_override(bar_repo)

    log.info("collector_crypto.started", note="P2 will attach BinanceAdapter")
    try:
        yield
    finally:
        log.info("collector_crypto.shutdown")


app = health_app("collector_crypto")
app.router.lifespan_context = lifespan


def main() -> None:
    port = int(os.getenv("COLLECTOR_CRYPTO_PORT", "8790"))
    uvicorn.run("apps.collector.crypto.main:app", host="127.0.0.1", port=port, log_config=None)


if __name__ == "__main__":
    main()
