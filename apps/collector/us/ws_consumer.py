"""Alpaca bar WebSocket 长连消费 (与 crypto ws_consumer 对等).

Alpaca Streaming API (IEX free tier) 原生只推送 1m bar。
收到 1m bar 事件:
- DuckDB insert + Redis tail upsert
- xadd bus:bars.updated (SSE 总线)

5m/15m/30m/60m 由 KLineService.aggregate_intraday 从 1m 聚合派生,
或走 REST 轮询补充 (bar_poller)。

标的集合: 从 US_SEEDS + watchlist 动态读取。
设计原则: 优雅降级不 fail-fast。WS 断线指数退避 reconnect (1s → 60s)。

参考: apps/collector/crypto/ws_consumer.py (对等实现)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import structlog
import websockets

from core.cache import keys
from core.cache.redis_bars_cache import RedisBarsCache
from core.cache.redis_client import RedisCache
from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo

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
# bar 解析
# ---------------------------------------------------------------------------

def _parse_bar(item: dict) -> Bar | None:
    """Alpaca 1m bar 消息 → Bar.

    Alpaca 消息格式:
      {"T":"b", "S":"AAPL", "o":150.0, "h":151.0, "l":149.0, "c":150.5,
       "v":1000, "t":"2024-06-01T09:30:00Z"}

    ts 语义: Alpaca bar.timestamp 是 bar START。
    按雷区 3, 美股 intraday bar.ts = close 时刻 → ts + 1min。
    """
    try:
        symbol = item.get("S")
        if not symbol:
            return None
        ts_str = item.get("t", "").replace("Z", "+00:00")
        ts_start = datetime.fromisoformat(ts_str)
        return Bar(
            market="us",
            symbol=symbol,
            ts=ts_start + timedelta(minutes=1),  # START → CLOSE (雷区 3)
            open=Decimal(str(item["o"])),
            high=Decimal(str(item["h"])),
            low=Decimal(str(item["l"])),
            close=Decimal(str(item["c"])),
            volume=int(float(item.get("v", 0))),
            interval="1m",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("ws_us.parse_failed", error=str(e), item=str(item)[:200])
        return None


# ---------------------------------------------------------------------------
# 三写
# ---------------------------------------------------------------------------

def _bar_to_event(bar: Bar) -> dict:
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
        "final": True,
    }


async def handle_bar(
    bar: Bar,
    *,
    repo: BarRepo,
    redis_bars: RedisBarsCache,
    redis_cache: RedisCache,
) -> None:
    """三写: DuckDB + Redis tail + Streams. 任何失败仅 warning."""
    # 1. DuckDB
    try:
        repo.insert_bars([bar])
    except Exception as e:  # noqa: BLE001
        log.warning("ws_us.duckdb_write_failed",
                    symbol=bar.symbol, error=str(e))
    # 2. Redis tail
    try:
        await redis_bars.upsert_tail("us", bar.symbol, bar.interval, [bar])
    except Exception as e:  # noqa: BLE001
        log.warning("ws_us.tail_write_failed",
                    symbol=bar.symbol, error=str(e))
    # 3. SSE 总线
    payload = _bar_to_event(bar)
    try:
        await redis_cache._r.xadd(  # noqa: SLF001
            keys.BUS_BARS_UPDATED,
            {"data": json.dumps(payload).encode()},
            maxlen=10000,
            approximate=True,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("ws_us.xadd_failed", error=str(e))


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------

async def consume_loop(
    *,
    repo: BarRepo,
    redis_bars: RedisBarsCache,
    redis_cache: RedisCache,
) -> None:
    """Alpaca WS 长连消费循环。被 cancel 干净退出, 其他异常指数退避 reconnect."""
    api_key = os.getenv("ALPACA_API_KEY", "")
    api_secret = os.getenv("ALPACA_SECRET_KEY", "")
    if not api_key or not api_secret:
        log.warning("ws_us.no_alpaca_keys", note="WS consumer 无法启动")
        return

    symbols = _load_symbols()
    log.info("ws_us.start", symbols=len(symbols))

    backoff = 1.0
    while True:
        try:
            async with websockets.connect(
                _WS_URL, ping_interval=30, ping_timeout=10,
            ) as ws:
                # 认证
                await ws.send(json.dumps({
                    "action": "auth", "key": api_key, "secret": api_secret,
                }))
                auth_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if isinstance(auth_resp, list):
                    for item in auth_resp:
                        if item.get("T") == "error":
                            log.error("ws_us.auth_failed", msg=item.get("msg", ""))
                            return  # 认证失败不重试
                log.info("ws_us.authenticated")

                # 订阅
                await ws.send(json.dumps({
                    "action": "subscribe", "bars": symbols,
                }))
                sub_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if isinstance(sub_resp, list):
                    for item in sub_resp:
                        if item.get("T") == "subscription":
                            log.info("ws_us.subscribed",
                                     count=len(item.get("bars", [])))

                log.info("ws_us.connected", symbols=len(symbols))
                backoff = 1.0

                # 消费
                async for raw in ws:
                    try:
                        msgs = json.loads(raw)
                        if not isinstance(msgs, list):
                            continue
                        for item in msgs:
                            if item.get("T") == "b":
                                bar = _parse_bar(item)
                                if bar:
                                    await handle_bar(
                                        bar, repo=repo, redis_bars=redis_bars,
                                        redis_cache=redis_cache,
                                    )
                            elif item.get("T") == "error":
                                log.warning("ws_us.stream_error",
                                            code=item.get("code"),
                                            msg=item.get("msg"))
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        log.warning("ws_us.handle_failed", error=str(e))

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
