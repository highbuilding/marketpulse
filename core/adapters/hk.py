from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import akshare as ak
import requests
import structlog
import yfinance as yf

from core.adapters.base import AdapterError, CircuitBreaker
from core.domain.models import Bar, HealthStatus, Quote

log = structlog.get_logger(__name__)

_SINA_BASE = "https://hq.sinajs.cn/list="


def _to_hk_code(symbol: str) -> str:
    return symbol.split(".")[0].zfill(5)


def _to_yf_ticker(symbol: str) -> str:
    code = _to_hk_code(symbol).lstrip("0") or "0"
    return f"{code.zfill(4)}.HK"


def _to_sina_code(symbol: str) -> str:
    raw = symbol.split(".")[0]
    if raw.upper() in {"HSI", "HSCEI"}:
        return f"hk{raw.upper()}"
    return f"hk{raw.zfill(5)}"


class HKAdapter:
    market = "hk"
    name = "hk"

    def __init__(self) -> None:
        self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=300)
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.proxies = {}
        self._session.headers.update({
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        })

    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]:
        if self.primary_cb.can_execute():
            try:
                quotes = await asyncio.to_thread(self._fetch_snapshot_sina, symbols)
                self.primary_cb.record_success()
                return quotes
            except Exception as e:
                self.primary_cb.record_failure()
                log.warning("hk.primary_failed", error=str(e))
        try:
            return await asyncio.to_thread(self._fetch_snapshot_yfinance, symbols)
        except Exception as e:
            raise AdapterError(f"both primary and backup failed: {e}", source="hk") from e

    def _fetch_snapshot_sina(self, symbols: list[str]) -> list[Quote]:
        codes = ",".join(_to_sina_code(s) for s in symbols)
        r = self._session.get(_SINA_BASE + codes, timeout=5)
        r.encoding = "gbk"
        r.raise_for_status()
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        # hk00700: en,name,open,prev_close,high,low,now,change,change_pct,bid,ask,...
        for line in r.text.splitlines():
            if 'hq_str_hk' not in line or '="' not in line:
                continue
            sina_code = line.split('hq_str_')[1].split('=')[0]  # e.g. "hk00700" or "hkHSI"
            payload = line.split('="', 1)[1].rstrip('";\n')
            parts = payload.split(",")
            if len(parts) < 9:
                continue
            raw = sina_code[2:]
            symbol = f"{raw.upper()}.HK" if raw.upper() in {"HSI", "HSCEI"} else f"{raw}.HK"
            try:
                prev_close = float(parts[3])
                price = float(parts[6])
                change_pct = float(parts[8])
                volume = int(float(parts[12])) if len(parts) > 12 else 0
            except (ValueError, IndexError):
                continue
            if price == 0:
                continue
            out.append(Quote(
                market="hk",
                symbol=symbol,
                ts=now,
                price=Decimal(f"{price:.4f}"),
                change_pct=change_pct,
                volume=volume,
                source="sina",
            ))
        return out

    def _fetch_snapshot_akshare(self, wanted: set[str]) -> list[Quote]:
        df = ak.stock_hk_spot_em()
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for _, row in df.iterrows():
            code = str(row["代码"]).zfill(5)
            if code not in wanted:
                continue
            out.append(Quote(
                market="hk",
                symbol=f"{code}.HK",
                ts=now,
                price=Decimal(str(row["最新价"])),
                change_pct=float(row["涨跌幅"]),
                volume=int(row["成交量"]),
                source="akshare",
            ))
        return out

    def _fetch_snapshot_yfinance(self, symbols: list[str]) -> list[Quote]:
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for s in symbols:
            try:
                info = yf.Ticker(_to_yf_ticker(s)).fast_info
                price = Decimal(str(info.last_price))
                prev = float(info.previous_close or 0) or 1
                change_pct = (float(info.last_price) - prev) / prev * 100
                out.append(Quote(
                    market="hk",
                    symbol=s,
                    ts=now,
                    price=price,
                    change_pct=change_pct,
                    volume=int(info.last_volume or 0),
                    source="yfinance",
                ))
            except Exception as e:  # noqa: BLE001
                log.warning("hk.yfinance_symbol_failed", symbol=s, error=str(e))
        return out

    async def subscribe(self, symbols: list[str], on_bar: Callable[[Bar], None]) -> None:
        raise NotImplementedError("use scheduler polling for hk")

    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        df = await asyncio.to_thread(
            yf.download,
            _to_yf_ticker(symbol),
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
        )
        out: list[Bar] = []
        for idx, row in df.iterrows():
            ts = datetime.fromtimestamp(idx.timestamp(), tz=timezone.utc)
            out.append(Bar(
                market="hk",
                symbol=symbol,
                ts=ts,
                open=Decimal(str(float(row["Open"]))),
                high=Decimal(str(float(row["High"]))),
                low=Decimal(str(float(row["Low"]))),
                close=Decimal(str(float(row["Close"]))),
                volume=int(row["Volume"]),
                interval="1d",
            ))
        return out

    async def health(self) -> HealthStatus:
        if not self.primary_cb.can_execute():
            return HealthStatus(name="hk", state="degraded", detail="primary circuit open")
        try:
            r = await asyncio.to_thread(self._session.get, _SINA_BASE + "hkHSI", timeout=3)
            r.raise_for_status()
            return HealthStatus(name="hk", state="ok")
        except Exception as e:
            return HealthStatus(name="hk", state="down", detail=str(e))
