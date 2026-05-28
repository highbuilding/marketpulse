"""美股 3 大指数 collector job — Plan C (2026-05-28 设计)。

数据源: Alpaca + ETF 代理 (SPY/QQQ/DIA = SPX/NDX/DJI)
- yfinance 实测严重限频 (.info/.history 都 RateLimit)
- Alpaca 不收 ^GSPC/^DJI/^IXIC 指数 (整批 400)
- ETF 代理: Bloomberg/TradingView 在指数页显示的"Volume"实际就是 ETF 成交额

5m 序列: StockBarsRequest TimeFrame(5, Minute), feed='iex'
prev_close: StockBarsRequest TimeFrame(1, Day), 取倒数第 2 根 close
amount: Σ(close × volume) per 5m bucket
fund_inflow: 不显示 (美股无北向概念)

参考: docs/superpowers/specs/2026-05-28-market-index-extended-design.md §4.3
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.persistence.market_amount_baseline_repo import MarketAmountBaselineRepo

log = structlog.get_logger(__name__)

# 美股大盘指数, ETF 代理: SPY=S&P500, QQQ=NASDAQ100, DIA=DJI
US_INDEX_SYMBOLS = ["SPY", "QQQ", "DIA"]
_INDEX_NAME = {
    "SPY": "标普500 (SPY)",
    "QQQ": "纳指100 (QQQ)",
    "DIA": "道琼斯 (DIA)",
}

_ET_TZ = ZoneInfo("America/New_York")
_CACHE_TTL_S = 120  # 60s 写 + 120s TTL
_BASELINE_OFFSET_BUCKETS = 78  # 美股 9:30-16:00 ET = 6.5h × 12 buckets/h = 78


def _us_5m_offset_from_dt(dt_et: datetime) -> int | None:
    """美股 ET 时刻 → 5m offset (0..77)。9:30 ET = 0, 15:55 ET = 77。盘外返 None。"""
    t = dt_et.time()
    h, m = t.hour, t.minute
    minutes_from_open = (h - 9) * 60 + (m - 30)
    if 0 <= minutes_from_open < 390:  # 6.5h × 60 = 390min
        return minutes_from_open // 5
    if h == 16 and m == 0:
        return 77
    return None


def _build_alpaca_client():
    """惰性创建 Alpaca client, 复用环境变量"""
    from alpaca.data.historical import StockHistoricalDataClient
    key = os.environ.get("ALPACA_API_KEY")
    sec = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY 缺失")
    return StockHistoricalDataClient(api_key=key, secret_key=sec)


def _fetch_us_5m_and_prev_close_sync() -> dict[str, dict]:
    """批量拉 SPY/QQQ/DIA 当日 5m + 前日 daily, 返回:
    {symbol: {points: [...], prev_close: float, amount_usd: float}}
    任一失败该 symbol 缺字段, 不抛。
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    cli = _build_alpaca_client()
    out: dict[str, dict] = {s: {"points": [], "prev_close": None, "amount_usd": None}
                            for s in US_INDEX_SYMBOLS}

    # 5m: 当日 (实际 24h 内, IEX feed 15min 滞后, 减安全 20min)
    end = datetime.now(timezone.utc) - timedelta(minutes=20)
    start = end - timedelta(hours=24)
    try:
        req = StockBarsRequest(
            symbol_or_symbols=US_INDEX_SYMBOLS,
            timeframe=TimeFrame(5, TimeFrameUnit.Minute),
            start=start, end=end, feed="iex",
        )
        bars = cli.get_stock_bars(req)
        for sym in US_INDEX_SYMBOLS:
            rows = bars.data.get(sym, []) or []
            cum_amount_usd = 0.0
            points: list[dict] = []
            # 取数据中"最大日期"作为"最近交易日", 过滤之
            # 这样即使盘前/收盘后, 也能展示最近一个完整交易日的盘中曲线
            if not rows:
                out[sym]["amount_usd"] = None
                continue
            latest_date_et = max(
                (b.timestamp + timedelta(minutes=5)).astimezone(_ET_TZ).date() for b in rows
            )
            for b in rows:
                ts_close = b.timestamp + timedelta(minutes=5)
                ts_et = ts_close.astimezone(_ET_TZ)
                if ts_et.date() != latest_date_et:
                    continue
                points.append({
                    "ts": ts_close.isoformat(),
                    "close": float(b.close),
                    "volume": int(b.volume or 0),
                })
                cum_amount_usd += float(b.close) * float(b.volume or 0)
            out[sym]["points"] = points
            out[sym]["amount_usd"] = cum_amount_usd if cum_amount_usd > 0 else None
    except Exception as e:  # noqa: BLE001
        log.warning("us_index_minute.bars_failed", error=str(e))

    # prev_close: daily, 倒数第 2 根
    try:
        day_req = StockBarsRequest(
            symbol_or_symbols=US_INDEX_SYMBOLS,
            timeframe=TimeFrame(1, TimeFrameUnit.Day),
            start=datetime.now(timezone.utc) - timedelta(days=10),
            end=datetime.now(timezone.utc),
            feed="iex",
        )
        day_bars = cli.get_stock_bars(day_req)
        for sym in US_INDEX_SYMBOLS:
            rows = day_bars.data.get(sym, []) or []
            if len(rows) >= 2:
                out[sym]["prev_close"] = float(rows[-2].close)
            elif len(rows) == 1:
                out[sym]["prev_close"] = float(rows[-1].close)
    except Exception as e:  # noqa: BLE001
        log.warning("us_index_minute.prev_close_failed", error=str(e))

    return out


async def _fetch_us_data() -> dict[str, dict]:
    try:
        return await asyncio.to_thread(_fetch_us_5m_and_prev_close_sync)
    except Exception as e:  # noqa: BLE001
        log.warning("us_index_minute.fetch_failed", error=str(e))
        return {s: {"points": [], "prev_close": None, "amount_usd": None}
                for s in US_INDEX_SYMBOLS}


async def refresh_one_us_index(
    symbol: str, *, cache: RedisCache, data: dict, market_extras: dict,
) -> None:
    """一个 ETF 写 cache, 与 A 股一致 schema。"""
    payload = {
        "symbol": symbol,
        "granularity": "5m",
        "prev_close": data.get("prev_close"),
        "points": data.get("points", []),
        "market_extras": market_extras,
        "meta": {"fresh_at": datetime.now(timezone.utc).isoformat(),
                 "stale": False, "source": "alpaca-iex"},
    }
    await cache.set_msgpack(keys.cache_index_minute(symbol, days=1), payload, ttl_s=_CACHE_TTL_S)
    log.info("us_index_minute.cached", symbol=symbol,
             points=len(data.get("points", [])), prev_close=data.get("prev_close"))


async def refresh_all_us_indices(
    cache: RedisCache,
    baseline_repo: MarketAmountBaselineRepo | None = None,
) -> None:
    """循环刷 SPY/QQQ/DIA。

    非美股交易日跳过, 交易日 ET 9:00-17:00 内跑 (盘前盘后给 buffer)。
    """
    from core.domain.market_calendar import is_trading_day

    now_et = datetime.now(_ET_TZ)
    if not is_trading_day("us", now_et):
        # 非交易日 (周末/独立日等) 也允许刷一次, 避免 cache 过期变 stale
        # 拉到的是上一交易日数据, 用户看到"昨日收盘"也比 stale 好
        log.debug("us_index_minute.non_trading_day_still_refresh", date=str(now_et.date()))

    data_map = await _fetch_us_data()

    # 用 SPY 代表市场总成交额
    spy_amount_usd = data_map.get("SPY", {}).get("amount_usd")
    amount_yiyuan = spy_amount_usd / 1e8 if spy_amount_usd else None  # 1 亿美元单位

    # amount_ratio: Relative Volume 10D
    amount_ratio: float | None = None
    if baseline_repo is not None and spy_amount_usd is not None:
        offset = _us_5m_offset_from_dt(now_et)
        if offset is not None:
            today_str = now_et.date().isoformat()
            try:
                avg = await baseline_repo.query_avg_n_days_at_offset(
                    "us", today_str, offset, n_days=10)
                if avg and avg > 0:
                    amount_ratio = spy_amount_usd / avg - 1
            except Exception as e:  # noqa: BLE001
                log.warning("us_index_minute.amount_ratio_failed", error=str(e))

    market_extras = {
        "fund_inflow": None,         # 美股无北向概念
        "fund_inflow_label": None,
        "amount": amount_yiyuan,
        "amount_unit": "亿美元" if amount_yiyuan is not None else None,
        "amount_ratio": amount_ratio,
    }
    log.info("us_index_minute.market_extras", **market_extras)

    for symbol in US_INDEX_SYMBOLS:
        await refresh_one_us_index(
            symbol, cache=cache,
            data=data_map.get(symbol, {"points": [], "prev_close": None, "amount_usd": None}),
            market_extras=market_extras,
        )
