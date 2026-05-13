"""指数分时数据端点,给市场页的 mini chart 用。

A 股指数:走 ak.stock_zh_a_minute(period=5) 拿当日 5 分钟线
港股指数:走 ak.stock_hk_index_daily_sina 拿近 N 日日线(港股分时接口不通)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import akshare as ak
import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/indices", tags=["indices"])


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
    df = await asyncio.to_thread(ak.stock_zh_a_minute, symbol=sina_code, period="5", adjust="")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days + 1)
    points: list[MinutePoint] = []
    for _, row in df.iterrows():
        day_str = str(row["day"]).replace(" ", "T") + "+00:00"
        ts = datetime.fromisoformat(day_str)
        if ts < cutoff:
            continue
        points.append(MinutePoint(
            ts=ts.isoformat(),
            close=float(row["close"]),
            volume=int(float(row["volume"])),
        ))
    # 取最近一个交易日(按日期分组,取最后一组)
    if days == 1 and points:
        last_date = points[-1].ts[:10]
        points = [p for p in points if p.ts.startswith(last_date)]
    return IndexMinuteResponse(symbol=symbol, name=name, granularity="5m", points=points)


async def _hk_index_daily(symbol: str, name: str, days: int) -> IndexMinuteResponse:
    label = _to_hk_label(symbol)
    df = await asyncio.to_thread(ak.stock_hk_index_daily_sina, symbol=label)
    # 取最后 N 天
    df_tail = df.tail(days)
    points: list[MinutePoint] = []
    for _, row in df_tail.iterrows():
        d = row["date"]
        # d 可能是 date / str / Timestamp
        if hasattr(d, "isoformat"):
            ts = datetime.combine(d if not hasattr(d, "date") else d.date(),
                                   datetime.min.time(), tzinfo=timezone.utc)
        else:
            ts = datetime.fromisoformat(f"{d}T00:00:00+00:00")
        points.append(MinutePoint(
            ts=ts.isoformat(),
            close=float(row["close"]),
            volume=int(float(row["volume"])),
        ))
    return IndexMinuteResponse(symbol=symbol, name=name, granularity="1d", points=points)
