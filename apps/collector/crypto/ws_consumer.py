"""Binance combined kline WS 长连接消费 (P3).

40 路 stream (5 标的 × 8 周期) 复用 1 个 connection。

收到 kline 事件:
- k.x=true (收盘): DuckDB insert + Redis tail upsert + xadd bus:bars.updated (final=true)
- k.x=false (进行中): cache:bars:{m}:{s}:{iv}:current 单根 + xadd bus:bars.updated (final=false)

设计原则: 优雅降级不 fail-fast. 解析失败 / xadd 失败 / 单条 handle 异常都仅 warning,
不让循环退出。WS 断线指数退避 reconnect (1s → 60s)。

参考: docs/superpowers/specs/2026-05-29-crypto-sse-and-collector-split-design.md §9
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import structlog
import websockets

from core.cache import keys
from core.cache.redis_bars_cache import RedisBarsCache
from core.cache.redis_client import RedisCache
from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)

WS_BASE = "wss://stream.binance.com:9443/stream"
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "TRX-USDT")
INTERVALS_PROJECT = ("5m", "15m", "30m", "60m", "4h", "1d", "1wk", "1mo")
INTERVAL_PROJ_TO_BINANCE = {
    "5m": "5m", "15m": "15m", "30m": "30m", "60m": "1h", "4h": "4h",
    "1d": "1d", "1wk": "1w", "1mo": "1M",
}
INTERVAL_BINANCE_TO_PROJ = {v: k for k, v in INTERVAL_PROJ_TO_BINANCE.items()}

# 进行中 bar TTL = 2 × interval, 防止 WS 断开后 cache 残留太久
_TTL_MAP = {
    "5m": 600, "15m": 1800, "30m": 3600, "60m": 7200,
    "4h": 28800, "1d": 172800, "1wk": 1209600, "1mo": 5184000,
}


def _build_streams_url() -> str:
    parts: list[str] = []
    for sym in SYMBOLS:
        b_sym = sym.replace("-", "").lower()
        for proj_iv in INTERVALS_PROJECT:
            b_iv = INTERVAL_PROJ_TO_BINANCE[proj_iv]
            parts.append(f"{b_sym}@kline_{b_iv}")
    return f"{WS_BASE}?streams={'/'.join(parts)}"


def _binance_symbol_to_project(b_sym: str) -> str | None:
    """e.g. 'BTCUSDT' -> 'BTC-USDT'. 不匹配返 None。"""
    target = b_sym.upper()
    for s in SYMBOLS:
        if s.replace("-", "").upper() == target:
            return s
    return None


def _parse_kline_msg(stream_data: dict) -> tuple[Bar, bool] | None:
    """返回 (Bar, is_final). 解析失败返 None,日志由调用方决定。"""
    try:
        k = stream_data["k"]
        b_sym = k["s"]
        b_iv = k["i"]
        proj_iv = INTERVAL_BINANCE_TO_PROJ.get(b_iv)
        if proj_iv is None:
            return None
        sym = _binance_symbol_to_project(b_sym)
        if sym is None:
            return None
        # crypto 例外: bar.ts 用 openTime (k.t), 与币安 / TradingView K 线对齐.
        # 这与项目 SSoT 雷区 3 (其他市场 ts=close) 不同, crypto 24/7 无 session
        # 切桶, open 对齐更直观.
        ts = datetime.fromtimestamp(k["t"] / 1000, tz=timezone.utc)
        bar = Bar(
            market="crypto",
            symbol=sym,
            ts=ts,
            open=Decimal(str(k["o"])),
            high=Decimal(str(k["h"])),
            low=Decimal(str(k["l"])),
            close=Decimal(str(k["c"])),
            volume=int(float(k["v"])),
            interval=proj_iv,
            amount=float(k.get("q")) if k.get("q") is not None else None,
        )
        return bar, bool(k["x"])
    except Exception as e:  # noqa: BLE001
        log.warning("ws.parse_failed", error=str(e))
        return None


def _bar_to_event(bar: Bar, final: bool) -> dict:
    return {
        "market": bar.market,
        "symbol": bar.symbol,
        "interval": bar.interval,
        "ts": bar.ts.astimezone(timezone.utc).isoformat(),
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": int(bar.volume),
        "final": final,
    }


async def handle_message(
    msg: dict,
    *,
    repo: BarRepo,
    redis_bars: RedisBarsCache,
    redis_cache: RedisCache,
) -> None:
    """处理一条 combined-stream 消息。任何子动作失败仅 warning,不抛。"""
    stream_data = msg.get("data")
    if not stream_data or stream_data.get("e") != "kline":
        return
    parsed = _parse_kline_msg(stream_data)
    if parsed is None:
        return
    bar, final = parsed

    if final:
        # 收盘: 持久化到 DuckDB + 写 Redis tail
        try:
            repo.insert_bars([bar])
        except Exception as e:  # noqa: BLE001
            log.warning(
                "ws.duckdb_write_failed",
                symbol=bar.symbol, interval=bar.interval, error=str(e),
            )
        try:
            await redis_bars.upsert_tail("crypto", bar.symbol, bar.interval, [bar])
        except Exception as e:  # noqa: BLE001
            log.warning(
                "ws.tail_write_failed",
                symbol=bar.symbol, interval=bar.interval, error=str(e),
            )
        log.info(
            "ws.kline_closed",
            symbol=bar.symbol, interval=bar.interval,
            ts=bar.ts.isoformat(), close=float(bar.close),
        )
    else:
        # 进行中: 单根 current key, TTL 2x interval
        ttl = _TTL_MAP.get(bar.interval, 600)
        cur_key = keys.cache_bars_current("crypto", bar.symbol, bar.interval)
        try:
            await redis_cache.set_msgpack(cur_key, _bar_to_event(bar, False), ttl_s=ttl)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "ws.current_write_failed",
                key=cur_key, error=str(e),
            )

    # xadd 给 SSE 路由消费 (final/in-progress 都发)
    payload = _bar_to_event(bar, final)
    try:
        await redis_cache._r.xadd(  # noqa: SLF001
            keys.BUS_BARS_UPDATED,
            {"data": json.dumps(payload).encode()},
            maxlen=10000,
            approximate=True,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("ws.xadd_failed", error=str(e))


async def consume_loop(
    *,
    repo: BarRepo,
    redis_bars: RedisBarsCache,
    redis_cache: RedisCache,
) -> None:
    """长连消费循环。被 cancel 时干净退出,其他异常做指数退避 reconnect。"""
    url = _build_streams_url()
    log.info("ws.start", streams=len(SYMBOLS) * len(INTERVALS_PROJECT))
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(
                url, ping_interval=30, ping_timeout=10,
            ) as ws:
                log.info("ws.connected")
                backoff = 1.0
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        await handle_message(
                            msg,
                            repo=repo,
                            redis_bars=redis_bars,
                            redis_cache=redis_cache,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        log.warning("ws.handle_failed", error=str(e))
        except asyncio.CancelledError:
            log.info("ws.cancelled")
            return
        except Exception as e:  # noqa: BLE001
            log.warning("ws.connection_lost", error=str(e), retry_in=backoff)
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                log.info("ws.cancelled")
                return
            backoff = min(backoff * 2, 60)
