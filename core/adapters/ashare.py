from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import akshare as ak
import structlog

from core.adapters.base import AdapterError, CircuitBreaker
from core.domain.models import Bar, HealthStatus, Quote

log = structlog.get_logger(__name__)


def _normalize_symbol(code: str) -> str:
    if "." in code:
        return code
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _denormalize(symbol: str) -> str:
    return symbol.split(".")[0]


class AShareAdapter:
    market = "ashare"
    name = "ashare"

    def __init__(self) -> None:
        self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=300)

    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]:
        wanted = {_denormalize(s) for s in symbols}
        if self.primary_cb.can_execute():
            try:
                quotes = await asyncio.to_thread(self._fetch_snapshot_akshare, wanted)
                self.primary_cb.record_success()
                return quotes
            except Exception as e:
                self.primary_cb.record_failure()
                log.warning("ashare.primary_failed", error=str(e))
        try:
            return await asyncio.to_thread(self._fetch_snapshot_mootdx, wanted)
        except Exception as e:
            raise AdapterError(f"both primary and backup failed: {e}", source="ashare") from e

    def _fetch_snapshot_akshare(self, wanted: set[str]) -> list[Quote]:
        df = ak.stock_zh_a_spot_em()
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for _, row in df.iterrows():
            code = str(row["代码"])
            if code not in wanted:
                continue
            price = Decimal(str(row["最新价"]))
            out.append(Quote(
                market="ashare",
                symbol=_normalize_symbol(code),
                ts=now,
                price=price,
                change_pct=float(row["涨跌幅"]),
                volume=int(row["成交量"]),
                source="akshare",
            ))
        return out

    def _fetch_snapshot_mootdx(self, wanted: set[str]) -> list[Quote]:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market="std")
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for code in wanted:
            try:
                df = client.quotes(symbol=[code])
                if df is None or df.empty:
                    continue
                row = df.iloc[0]
                out.append(Quote(
                    market="ashare",
                    symbol=_normalize_symbol(code),
                    ts=now,
                    price=Decimal(str(row.get("price", 0))),
                    change_pct=float(row.get("rate", 0.0)),
                    volume=int(row.get("vol", 0)),
                    source="mootdx",
                ))
            except Exception as e:  # noqa: BLE001
                log.warning("ashare.mootdx_symbol_failed", symbol=code, error=str(e))
        return out

    async def subscribe(self, symbols: list[str], on_bar: Callable[[Bar], None]) -> None:
        raise NotImplementedError("use scheduler polling for ashare")

    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        code = _denormalize(symbol)
        df = await asyncio.to_thread(
            ak.stock_zh_a_hist,
            symbol=code,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        out: list[Bar] = []
        for _, row in df.iterrows():
            ts = datetime.combine(row["日期"], datetime.min.time(), tzinfo=timezone.utc)
            out.append(Bar(
                market="ashare",
                symbol=symbol,
                ts=ts,
                open=Decimal(str(row["开盘"])),
                high=Decimal(str(row["最高"])),
                low=Decimal(str(row["最低"])),
                close=Decimal(str(row["收盘"])),
                volume=int(row["成交量"]),
                interval="1d",
            ))
        return out

    async def health(self) -> HealthStatus:
        if not self.primary_cb.can_execute():
            return HealthStatus(name="ashare", state="degraded", detail="primary circuit open")
        try:
            await asyncio.to_thread(ak.stock_zh_index_spot_em, symbol="沪深重要指数")
            return HealthStatus(name="ashare", state="ok")
        except Exception as e:
            return HealthStatus(name="ashare", state="down", detail=str(e))
