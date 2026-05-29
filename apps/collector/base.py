"""3 个 collector 进程共享的 lifespan helper.

负责:
- proxy / logging / faulthandler 一次性初始化
- ak_middleware (breakers/ratelimits/outlets) — A 股 / 美股 collector 用,crypto 进程不接 ak 仍可调用此初始化(无副作用)
- Redis 健康检查 + 加载共享依赖
- 各市场各自的 cron + 长任务在自己的 main.py 里 attach

每个 collector 进程的 main.py 自己起 FastAPI(只暴露 /health)给 honcho 探活.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog
from fastapi import FastAPI

log = structlog.get_logger(__name__)


@dataclass
class CollectorContext:
    process_name: str   # "collector_ashare" / "collector_us" / "collector_crypto"
    market: str         # "ashare" / "us" / "crypto"
    bar_repo_path: str  # data/bars_{market}.duckdb


def setup_proxy_and_logging(process_name: str) -> None:
    """所有 collector 共用的启动顺序: proxy 必须在 import adapter 前."""
    from dotenv import load_dotenv
    load_dotenv()

    from core.integrations.proxy_setup import setup_process_proxy
    setup_process_proxy()

    from core.integrations.logging_setup import setup_logging
    setup_logging(process_name=process_name)


def install_async_exception_handler() -> None:
    """兜住 asyncio.create_task 抛出的异常, 强制走 root logger 落 errors.log."""
    def _handler(loop, context):
        msg = context.get("exception") or context.get("message")
        log.error("asyncio.unhandled_exception",
                  message=context.get("message"),
                  exception_type=type(context.get("exception")).__name__
                      if context.get("exception") else None,
                  error=str(msg) if msg else None,
                  task=str(context.get("task")) if context.get("task") else None)
    asyncio.get_event_loop().set_exception_handler(_handler)


def health_app(role: str) -> FastAPI:
    """每个 collector 进程内嵌的最小 FastAPI, 仅暴露 /health 给 honcho 探活."""
    app = FastAPI(title=f"MarketPulse {role}")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "role": role}
    return app
