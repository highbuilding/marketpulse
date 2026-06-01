"""Alpaca trades WebSocket 长连消费 (与 crypto ws_consumer 对等).

Alpaca Streaming API trades 频道推送逐笔成交。
收到 trade 事件后分发到 TradeHub, 由 TradeHub 聚合 1m bar 并驱动后续写库/推送。

旧 bars(1m) 频道已废弃: 1m bar 不再由 WS 直接落库,改由 TradeHub 聚合。

标的集合: 从 US_SEEDS + watchlist 动态读取。
设计原则: 优雅降级不 fail-fast。WS 断线指数退避 reconnect (1s → 60s)。

参考: apps/collector/crypto/ws_consumer.py (对等实现)
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone

import structlog
import websockets

log = structlog.get_logger(__name__)


_WS_URL = os.getenv("ALPACA_WS_URL", "wss://stream.data.alpaca.markets/v2/iex")

# 默认标的 (启动时与 US_SEEDS + watchlist 合并)
_DEFAULT_SYMBOLS = (
    "SPY", "QQQ", "DIA", "IWM",
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    "BRK.B", "JPM", "V", "UNH", "MA", "HD", "PG", "LLY",
    "CRM", "COST", "ABBV", "WMT", "KO", "PEP", "BAC", "AMD",
    "ADBE", "NFLX", "CSCO", "MRK", "CVX", "XOM",
)


# ---------------------------------------------------------------------------
# symbol 管理
# ---------------------------------------------------------------------------

def _load_symbols() -> list[str]:
    """启动时的标的集合: US_SEEDS + 硬编码核心."""
    try:
        from core.services._us_seeds import US_SEEDS
        seeds = {s for s, _, _ in US_SEEDS}
    except Exception:  # noqa: BLE001
        seeds = set()
    seeds.update(_DEFAULT_SYMBOLS)
    return sorted(seeds)


# ---------------------------------------------------------------------------
# 动态订阅辅助
# ---------------------------------------------------------------------------

def _subscription_deltas(desired: set[str], subscribed: set[str]) -> tuple[set[str], set[str]]:
    """返回 (要新订阅, 要退订)。"""
    return (desired - subscribed, subscribed - desired)


async def _desired_trade_symbols(redis, cap: int = 30) -> set[str]:
    """读订阅登记表 state:subscribe:us:* 收集'正在看'的标的, 上限 cap (免费 IEX trades 限 ~30)。"""
    syms: set[str] = set()
    try:
        cursor = 0
        while True:
            cursor, found = await redis._r.scan(  # noqa: SLF001
                cursor, match="state:subscribe:us:*", count=200)
            for k in found:
                kk = k.decode() if isinstance(k, bytes) else k
                parts = kk.split(":")
                if len(parts) >= 4:
                    syms.add(parts[3])
            if cursor == 0:
                break
    except Exception as e:  # noqa: BLE001
        log.warning("ws_us.desired_scan_failed", error=str(e))
    return set(sorted(syms)[:cap])


# ---------------------------------------------------------------------------
# trade 解析
# ---------------------------------------------------------------------------

def _parse_trade(item: dict) -> tuple[str, float, int, datetime] | None:
    """Alpaca trade 消息 → (symbol, price, size, ts_utc)。

    格式: {"T":"t","S":"AAPL","p":150.25,"s":100,"t":"2026-06-01T14:30:00.123Z"}
    """
    try:
        symbol = item.get("S")
        if not symbol:
            return None
        ts = datetime.fromisoformat(item["t"].replace("Z", "+00:00")).astimezone(timezone.utc)
        return (symbol, float(item["p"]), int(float(item["s"])), ts)
    except Exception as e:  # noqa: BLE001
        log.warning("ws_us.parse_trade_failed", error=str(e), item=str(item)[:200])
        return None


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

async def consume_loop(*, hub, redis) -> None:
    """Alpaca WS trades 长连消费。逐笔 → hub.on_trade。被 cancel 干净退出。

    动态订阅: 每 5s 扫 state:subscribe:us:* 取"正在看"的标的 (上限 30,
    免费 IEX trades 限制),增量 subscribe/unsubscribe,避免 405 symbol limit。
    """
    api_key = os.getenv("ALPACA_API_KEY", "")
    api_secret = os.getenv("ALPACA_SECRET_KEY", "")
    if not api_key or not api_secret:
        log.warning("ws_us.no_alpaca_keys", note="WS consumer 无法启动")
        return
    log.info("ws_us.start", mode="dynamic_subscribe")
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(_WS_URL, ping_interval=30, ping_timeout=10) as ws:
                await ws.send(json.dumps({"action": "auth", "key": api_key, "secret": api_secret}))
                auth_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if isinstance(auth_resp, list):
                    for item in auth_resp:
                        if item.get("T") == "error":
                            log.error("ws_us.auth_failed", msg=item.get("msg", ""))
                            return
                log.info("ws_us.authenticated")

                subscribed: set[str] = set()

                async def _manage_subs():
                    nonlocal subscribed
                    while True:
                        desired = await _desired_trade_symbols(redis, cap=30)
                        to_add, to_remove = _subscription_deltas(desired, subscribed)
                        if to_add:
                            await ws.send(json.dumps(
                                {"action": "subscribe", "trades": sorted(to_add)}))
                            log.info("ws_us.subscribed", count=len(to_add))
                        if to_remove:
                            await ws.send(json.dumps(
                                {"action": "unsubscribe", "trades": sorted(to_remove)}))
                            log.info("ws_us.unsubscribed", count=len(to_remove))
                        subscribed = desired
                        await asyncio.sleep(5)

                _mgr_task = asyncio.create_task(_manage_subs())
                log.info("ws_us.connected", symbols="dynamic")
                backoff = 1.0
                try:
                    async for raw in ws:
                        try:
                            msgs = json.loads(raw)
                            if not isinstance(msgs, list):
                                continue
                            for item in msgs:
                                if item.get("T") == "t":
                                    tr = _parse_trade(item)
                                    if tr:
                                        hub.on_trade(tr[0], price=tr[1], size=tr[2], ts=tr[3])
                                elif item.get("T") == "error":
                                    log.warning("ws_us.stream_error",
                                                code=item.get("code"), msg=item.get("msg"))
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:  # noqa: BLE001
                            log.warning("ws_us.handle_failed", error=str(e))
                finally:
                    _mgr_task.cancel()
                    try:
                        await _mgr_task
                    except (asyncio.CancelledError, Exception):
                        pass
        except asyncio.CancelledError:
            log.info("ws_us.cancelled")
            return
        except Exception as e:  # noqa: BLE001
            log.warning("ws_us.connection_lost", error=str(e), retry_in=backoff)
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                log.info("ws_us.cancelled")
                return
            backoff = min(backoff * 2, 60)
