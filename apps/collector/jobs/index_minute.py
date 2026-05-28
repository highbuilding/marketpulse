"""8 大 A 股指数 5min 序列预拉取 — Plan 2 Stage 4 引入。

替代 apps/api/routes/indices.py 的"路由内 ak_call",前端读 cache 不打 ak。
交易时段每 30s 一次, 非交易时段每 5min 一次 (cron 设置在 attach 函数里)。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §3.2
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.integrations.akshare import ak_call

log = structlog.get_logger(__name__)

INDEX_SYMBOLS = [
    "000001.SH", "399001.SZ", "000300.SH", "399006.SZ",
    "000905.SH", "000852.SH", "000688.SH", "000016.SH",
]

_CN_TZ = ZoneInfo("Asia/Shanghai")
_CACHE_TTL_S = 90  # 30s 写 + 90s TTL = 充足覆盖


def _to_sina_a(symbol: str) -> str:
    code, mkt = symbol.split(".")
    return f"{mkt.lower()}{code}"


async def refresh_one_index(symbol: str, *, cache: RedisCache) -> None:
    """拉一个指数当日 5m 数据, 写 cache。单条失败仅 warning, 不抛。"""
    sina_code = _to_sina_a(symbol)
    try:
        df = await ak_call(
            "stock_zh_a_minute", symbol=sina_code, period="5", adjust="",
            caller=f"index_minute.refresh:{symbol}",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("index_minute.fetch_failed", symbol=symbol, error=str(e))
        return

    cutoff_utc = datetime.now(timezone.utc) - timedelta(days=2)
    points = []
    for _, row in df.iterrows():
        try:
            naive = datetime.fromisoformat(str(row["day"]).replace(" ", "T"))
            ts = naive.replace(tzinfo=_CN_TZ).astimezone(timezone.utc)
            if ts < cutoff_utc:
                continue
            points.append({
                "ts": ts.isoformat(),
                "close": float(row["close"]),
                "volume": int(float(row["volume"])),
            })
        except Exception:  # noqa: BLE001
            continue
    # 取最近一个交易日 (按北京日期)
    if points:
        last_date_cn = datetime.fromisoformat(points[-1]["ts"]).astimezone(_CN_TZ).date()
        points = [p for p in points
                  if datetime.fromisoformat(p["ts"]).astimezone(_CN_TZ).date() == last_date_cn]
    payload = {
        "symbol": symbol,
        "granularity": "5m",
        "points": points,
        "meta": {"fresh_at": datetime.now(timezone.utc).isoformat(),
                 "stale": False, "source": "sina"},
    }
    await cache.set_msgpack(keys.cache_index_minute(symbol, days=1), payload, ttl_s=_CACHE_TTL_S)
    log.info("index_minute.cached", symbol=symbol, points=len(points))


async def refresh_all_indices(cache: RedisCache) -> None:
    """循环刷新 8 个指数。单条失败不影响后续。

    非 A 股交易日(周末/法定节假日)直接跳过, 交易日只在 BJT 09:00-16:00 内跑。
    避免无意义 sina 调用。
    """
    from core.domain.market_calendar import is_trading_day

    now_bjt = datetime.now(_CN_TZ)
    if not is_trading_day("ashare", now_bjt):
        log.debug("index_minute.skip_non_trading_day", date=str(now_bjt.date()))
        return
    if not (9 <= now_bjt.hour < 16):
        log.debug("index_minute.skip_off_hours", hour=now_bjt.hour)
        return
    for symbol in INDEX_SYMBOLS:
        await refresh_one_index(symbol, cache=cache)
