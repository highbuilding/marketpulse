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
        exc = context.get("exception")
        # websockets 16.0 自身 bug: 异常关闭路径的 connection_lost 回调里调用了
        # 不存在的 ClientConnection.recv_messages → AttributeError。纯噪音,
        # 不影响主循环重连(ws_consumer 的活性看门狗 + 外层退避已处理)。降级 debug,
        # 避免污染 errors.log。
        is_ws_noise = (
            isinstance(exc, AttributeError)
            and "recv_messages" in str(msg)
        )
        log_fn = log.debug if is_ws_noise else log.error
        log_fn("asyncio.unhandled_exception",
               message=context.get("message"),
               exception_type=type(exc).__name__ if exc else None,
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


def attach_bars_history_route(app: FastAPI, get_repo, market: str) -> None:
    """在 collector 自己的 FastAPI 上挂只读历史分页接口 (内部, 仅 127.0.0.1 可达).

    **必须在 module 级 (app 定义后) 调用**, 不能在 lifespan 内挂 ——
    Starlette 在 lifespan 启动后才加的路由不会被路由表识别 (实测 404)。
    repo 通过 get_repo() 在**请求时惰性解析** (collector lifespan 早期已
    set_bar_repo_override, 请求到来时必然就绪)。

    关键: collector 进程本就持有 RW bar_repo, 在同一进程内用同一连接查询
    => 零 DuckDB 锁冲突。api 进程通过 httpx 转发到此, 自己绝不碰 DuckDB
    (DuckDB 单写多读互斥: api 直连 read_only 会撞锁甚至踢掉 collector 的写)。

    派生周期 (60m/4h/1wk/1mo) 在 init_data 时预生成, 这里只管查询。

    游标分页 (币安/TradingView 反向翻页口径):
      GET /internal/bars/history?symbol=&interval=&before=&limit=
      before 空 = 最新一页; 返回严格早于 before 的最近 limit 根, 升序。
    """
    from datetime import datetime

    @app.get("/internal/bars/history")
    async def bars_history(  # noqa: ANN202
        symbol: str,
        interval: str = "1d",
        before: str | None = None,
        limit: int = 500,
    ) -> dict:
        limit = max(1, min(limit, 2000))
        before_dt: datetime | None = None
        if before:
            try:
                before_dt = datetime.fromisoformat(before)
            except ValueError:
                return {"symbol": symbol, "interval": interval, "bars": [],
                        "meta": {"stale": True, "reason": "bad_before_cursor"}}
        repo = get_repo()
        if repo is None:
            return {"symbol": symbol, "interval": interval, "bars": [],
                    "meta": {"stale": True, "reason": "repo_not_ready"}}
        try:
            bars = repo.fetch_history_paged(
                market, symbol, interval, before=before_dt, limit=limit,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("collector.bars_history_failed",
                        market=market, symbol=symbol, interval=interval,
                        error=str(e))
            return {"symbol": symbol, "interval": interval, "bars": [],
                    "meta": {"stale": True, "reason": "repo_error"}}
        return {
            "symbol": symbol, "interval": interval,
            "bars": [{
                "ts": b.ts.isoformat(),
                "open": float(b.open), "high": float(b.high),
                "low": float(b.low), "close": float(b.close),
                "volume": b.volume, "amount": b.amount,
                "turnover": b.turnover, "outstanding_share": b.outstanding_share,
            } for b in bars],
            "meta": {"stale": False, "count": len(bars)},
        }


def attach_intraday_route(
    app: FastAPI,
    get_intraday_repo,
    market: str,
    *,
    get_bar_repo=None,
) -> None:
    """在 collector 自己的 FastAPI 上挂分时只读接口 (内部, 仅 127.0.0.1 可达).

    **必须在 module 级 (app 定义后) 调用**, 不能在 lifespan 内挂 ——
    Starlette 在 lifespan 启动后才加的路由不会被路由表识别 (实测 404)。
    repo 通过 get_intraday_repo() 在**请求时惰性解析** (lifespan 已注入 repo)。

    关键: collector 进程本就持有 RW intraday_repo, 在同一进程内用同一连接查询
    => 零 DuckDB 锁冲突。api 进程通过 httpx 转发到此, 自己绝不碰 DuckDB。

    GET /internal/intraday-line?symbol=&date=
    date 空 = 当日 (UTC); 返回升序分时点列表。

    get_bar_repo (可选): 提供时, 响应顶层带 prev_close (该标的最近一根 1d bar
    的 close), 用作美股分时图的昨收基准线。A 股不传此参数, prev_close=None。
    """
    @app.get("/internal/intraday-line")
    async def _intraday_line(symbol: str, date: str | None = None) -> dict:  # noqa: ANN202
        from datetime import datetime, timezone
        repo = get_intraday_repo()
        if repo is None:
            return {"symbol": symbol, "points": [], "prev_close": None,
                    "meta": {"stale": True, "reason": "repo_not_ready"}}
        try:
            day = (datetime.fromisoformat(date).date() if date
                   else datetime.now(timezone.utc).date())
            pts = repo.fetch_day(symbol, day)
        except Exception as e:  # noqa: BLE001
            return {"symbol": symbol, "points": [], "prev_close": None,
                    "meta": {"stale": True, "reason": str(e)}}
        prev_close = None
        if get_bar_repo is not None:
            try:
                br = get_bar_repo()
                daily = br.fetch_history_paged(market, symbol, "1d", before=None, limit=1)
                if daily:
                    prev_close = float(daily[-1].close)
            except Exception:  # noqa: BLE001
                prev_close = None
        return {"symbol": symbol, "points": pts, "prev_close": prev_close,
                "meta": {"stale": False}}
