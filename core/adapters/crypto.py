from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import httpx
import structlog

from core.adapters.base import AdapterError, CircuitBreaker
from core.domain.models import Bar, HealthStatus, Quote

log = structlog.get_logger(__name__)

_CG_ID_MAP = {
    "BTC-USDT": "bitcoin",
    "ETH-USDT": "ethereum",
    "BNB-USDT": "binancecoin",
    "SOL-USDT": "solana",
    "XRP-USDT": "ripple",
    "ADA-USDT": "cardano",
    "DOGE-USDT": "dogecoin",
    "TON-USDT": "the-open-network",
    "TRX-USDT": "tron",
    "AVAX-USDT": "avalanche-2",
}


class CryptoAdapter:
    market = "crypto"
    name = "crypto"

    def __init__(self) -> None:
        self.ws_url = os.getenv("BINANCE_WS_URL", "wss://stream.binance.com:9443/ws")
        self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=60)
        self._ws_task: asyncio.Task | None = None
        self._ws_connected = False

    def _to_cg_id(self, symbol: str) -> str | None:
        return _CG_ID_MAP.get(symbol)

    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]:
        ids = [i for i in (self._to_cg_id(s) for s in symbols) if i]
        if not ids:
            return []
        async with httpx.AsyncClient(base_url="https://api.coingecko.com", timeout=10) as c:
            resp = await c.get("/api/v3/simple/price", params={
                "ids": ",".join(ids),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
            })
            if resp.status_code != 200:
                raise AdapterError(f"coingecko HTTP {resp.status_code}", source="crypto")
            data = resp.json()
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for sym in symbols:
            cg = self._to_cg_id(sym)
            if cg is None or cg not in data:
                continue
            d = data[cg]
            out.append(Quote(
                market="crypto",
                symbol=sym,
                ts=now,
                price=Decimal(str(d["usd"])),
                change_pct=float(d.get("usd_24h_change", 0) or 0),
                volume=int(d.get("usd_24h_vol", 0) or 0),
                source="coingecko",
            ))
        return out

    async def subscribe(self, symbols: list[str], on_bar: Callable[[Bar], None]) -> None:
        import websockets
        streams = "/".join(f"{s.replace('-', '').lower()}@kline_1m" for s in symbols)
        url = f"{self.ws_url}/{streams}"
        while True:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    self._ws_connected = True
                    async for msg in ws:
                        payload = json.loads(msg)
                        k = payload.get("k") or {}
                        if not k.get("x"):
                            continue
                        sym_raw = payload["s"].upper()
                        sym = f"{sym_raw[:-4]}-USDT" if sym_raw.endswith("USDT") else sym_raw
                        bar = Bar(
                            market="crypto", symbol=sym,
                            ts=datetime.fromtimestamp(k["t"] / 1000, tz=timezone.utc),
                            open=Decimal(k["o"]), high=Decimal(k["h"]),
                            low=Decimal(k["l"]), close=Decimal(k["c"]),
                            volume=int(float(k["v"])),
                            interval="1m",
                        )
                        on_bar(bar)
            except Exception as e:  # noqa: BLE001
                self._ws_connected = False
                log.warning("crypto.ws_disconnected", error=str(e))
                await asyncio.sleep(2)

    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        sym = symbol.replace("-", "").upper()
        async with httpx.AsyncClient(base_url="https://api.binance.com", timeout=15) as c:
            resp = await c.get("/api/v3/klines", params={
                "symbol": sym, "interval": "1d",
                "startTime": int(start.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000),
                "limit": 1000,
            })
        data = resp.json()
        out: list[Bar] = []
        for row in data:
            out.append(Bar(
                market="crypto", symbol=symbol,
                ts=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                open=Decimal(row[1]), high=Decimal(row[2]),
                low=Decimal(row[3]), close=Decimal(row[4]),
                volume=int(float(row[5])),
                interval="1d",
            ))
        return out

    async def health(self) -> HealthStatus:
        if not self.primary_cb.can_execute():
            return HealthStatus(name="crypto", state="degraded", detail="primary circuit open")
        return HealthStatus(name="crypto", state="ok")
