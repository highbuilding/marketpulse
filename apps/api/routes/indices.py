"""指数分时数据端点,给市场页的 mini chart 用。

A 股指数:走 ak.stock_zh_a_minute(period=5) 拿当日 5 分钟线
港股指数:走 ak.stock_hk_index_daily_sina 拿近 N 日日线(港股分时接口不通)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.integrations.akshare import ak_call

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/indices", tags=["indices"])

_CN_TZ = ZoneInfo("Asia/Shanghai")
_HK_TZ = ZoneInfo("Asia/Hong_Kong")


class MinutePoint(BaseModel):
    ts: str
    close: float
    volume: int


class IndexMinuteResponse(BaseModel):
    symbol: str
    name: str
    granularity: str  # "5m" 或 "1d"
    points: list[MinutePoint]


_INDEX_NAME = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "000300.SH": "沪深300",
    "399006.SZ": "创业板指",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "000688.SH": "科创50",
    "000016.SH": "上证50",
    "HSI.HK": "恒生指数",
    "HSTECH.HK": "恒生科技指数",
    "HSCEI.HK": "恒生中国企业指数",
}


def _to_sina_a(symbol: str) -> str:
    """000001.SH → sh000001."""
    code, mkt = symbol.split(".")
    return f"{mkt.lower()}{code}"


def _to_hk_label(symbol: str) -> str:
    """HSI.HK → HSI."""
    return symbol.split(".")[0]


@router.get("/{symbol}/minute", response_model=IndexMinuteResponse)
async def index_minute(symbol: str, days: int = Query(1, ge=1, le=30)) -> IndexMinuteResponse:
    if symbol not in _INDEX_NAME:
        raise HTTPException(404, f"unknown index: {symbol}")
    name = _INDEX_NAME[symbol]

    if symbol.endswith(".HK"):
        return await _hk_index_daily(symbol, name, days=30)
    return await _ashare_index_5min(symbol, name, days=days)


async def _ashare_index_5min(symbol: str, name: str, days: int) -> IndexMinuteResponse:
    sina_code = _to_sina_a(symbol)
    period = "1" if days == 1 else "5"
    df = await ak_call(
        "stock_zh_a_minute", symbol=sina_code, period=period, adjust="",
        caller=f"indices.ashare_5min:{symbol}",
    )
    cutoff_utc = datetime.now(timezone.utc) - timedelta(days=days + 1)
    points: list[MinutePoint] = []
    for _, row in df.iterrows():
        naive = datetime.fromisoformat(str(row["day"]).replace(" ", "T"))
        ts = naive.replace(tzinfo=_CN_TZ).astimezone(timezone.utc)
        if ts < cutoff_utc:
            continue
        points.append(MinutePoint(
            ts=ts.isoformat(),
            close=float(row["close"]),
            volume=int(float(row["volume"])),
        ))
    if days == 1 and points:
        # 取最近一个交易日(按北京时间日期)
        last_date_cn = points[-1].ts  # iso UTC
        last_date_cn_obj = datetime.fromisoformat(last_date_cn).astimezone(_CN_TZ).date()
        points = [
            p for p in points
            if datetime.fromisoformat(p.ts).astimezone(_CN_TZ).date() == last_date_cn_obj
        ]
    granularity = f"{period}m"
    return IndexMinuteResponse(symbol=symbol, name=name, granularity=granularity, points=points)


async def _hk_index_daily(symbol: str, name: str, days: int) -> IndexMinuteResponse:
    label = _to_hk_label(symbol)
    df = await ak_call(
        "stock_hk_index_daily_sina", symbol=label,
        caller=f"indices.hk_daily:{symbol}",
    )
    df_tail = df.tail(days)
    points: list[MinutePoint] = []
    for _, row in df_tail.iterrows():
        d = row["date"]
        if hasattr(d, "date"):  # pandas Timestamp
            d_obj = d.date()
        elif hasattr(d, "isoformat") and not isinstance(d, str):  # date
            d_obj = d
        else:
            d_obj = datetime.fromisoformat(str(d)).date()
        # 港股日期 → 当地午夜 → UTC
        ts = datetime.combine(d_obj, datetime.min.time(), tzinfo=_HK_TZ).astimezone(timezone.utc)
        points.append(MinutePoint(
            ts=ts.isoformat(),
            close=float(row["close"]),
            volume=int(float(row["volume"])),
        ))
    return IndexMinuteResponse(symbol=symbol, name=name, granularity="1d", points=points)
