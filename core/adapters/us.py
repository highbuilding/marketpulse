from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import structlog
import yfinance as yf

from core.adapters.base import AdapterError, CircuitBreaker
from core.domain.models import Bar, HealthStatus, Quote

log = structlog.get_logger(__name__)


class USAdapter:
    market = "us"
    name = "us"

    def __init__(self) -> None:
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.secret = os.getenv("ALPACA_SECRET_KEY")
        self.has_primary = bool(self.api_key and self.secret)
        self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=300)

    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]:
        if self.has_primary and self.primary_cb.can_execute():
            try:
                quotes = await asyncio.to_thread(self._fetch_snapshot_alpaca, symbols)
                self.primary_cb.record_success()
                return quotes
            except Exception as e:
                self.primary_cb.record_failure()
                log.warning("us.alpaca_failed", error=str(e))
        try:
            return await asyncio.to_thread(self._fetch_snapshot_yfinance, symbols)
        except Exception as e:
            raise AdapterError(f"both primary and backup failed: {e}", source="us") from e

    def _fetch_snapshot_alpaca(self, symbols: list[str]) -> list[Quote]:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest
        client = StockHistoricalDataClient(self.api_key, self.secret)
        req = StockLatestQuoteRequest(symbol_or_symbols=symbols)
        resp = client.get_stock_latest_quote(req)
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for sym in symbols:
            q = resp.get(sym)
            if q is None:
                continue
            mid = (float(q.ask_price) + float(q.bid_price)) / 2
            out.append(Quote(
                market="us",
                symbol=sym,
                ts=q.timestamp or now,
                price=Decimal(f"{mid:.4f}"),
                change_pct=0.0,
                volume=0,
                source="alpaca",
            ))
        return out

    def _fetch_snapshot_yfinance(self, symbols: list[str]) -> list[Quote]:
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for s in symbols:
            try:
                info = yf.Ticker(s).fast_info
                last = float(info.last_price)
                prev = float(info.previous_close or 0) or 1
                out.append(Quote(
                    market="us",
                    symbol=s,
                    ts=now,
                    price=Decimal(f"{last:.4f}"),
                    change_pct=(last - prev) / prev * 100,
                    volume=int(info.last_volume or 0),
                    source="yfinance",
                ))
            except Exception as e:  # noqa: BLE001
                log.warning("us.yfinance_symbol_failed", symbol=s, error=str(e))
        return out

    async def subscribe(self, symbols: list[str], on_bar: Callable[[Bar], None]) -> None:
        raise NotImplementedError("use scheduler polling for us in V1")

    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        df = await asyncio.to_thread(
            yf.download, symbol,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
        )
        out: list[Bar] = []
        for idx, row in df.iterrows():
            out.append(Bar(
                market="us", symbol=symbol,
                ts=datetime.fromtimestamp(idx.timestamp(), tz=timezone.utc),
                open=Decimal(str(float(row["Open"]))),
                high=Decimal(str(float(row["High"]))),
                low=Decimal(str(float(row["Low"]))),
                close=Decimal(str(float(row["Close"]))),
                volume=int(row["Volume"]),
                interval="1d",
            ))
        return out

    async def health(self) -> HealthStatus:
        if not self.has_primary:
            return HealthStatus(name="us", state="disabled", detail="missing ALPACA_API_KEY")
        if not self.primary_cb.can_execute():
            return HealthStatus(name="us", state="degraded", detail="alpaca circuit open")
        return HealthStatus(name="us", state="ok")
