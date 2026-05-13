from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import akshare as ak
import requests
import structlog

from core.adapters.base import AdapterError, CircuitBreaker
from core.domain.models import Bar, HealthStatus, Quote

log = structlog.get_logger(__name__)


_SINA_BASE = "https://hq.sinajs.cn/list="


def _normalize_symbol(code: str) -> str:
    if "." in code:
        return code
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _denormalize(symbol: str) -> str:
    return symbol.split(".")[0]


def _to_sina_code(symbol: str) -> str:
    if "." not in symbol:
        return _to_sina_code(_normalize_symbol(symbol))
    code, mkt = symbol.split(".")
    return f"{mkt.lower()}{code}"


class AShareAdapter:
    market = "ashare"
    name = "ashare"

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
                log.warning("ashare.primary_failed", error=str(e))
        wanted = {_denormalize(s) for s in symbols}
        try:
            return await asyncio.to_thread(self._fetch_snapshot_mootdx, wanted)
        except Exception as e:
            raise AdapterError(f"both primary and backup failed: {e}", source="ashare") from e

    def _fetch_snapshot_sina(self, symbols: list[str]) -> list[Quote]:
        codes = ",".join(_to_sina_code(s) for s in symbols)
        url = _SINA_BASE + codes
        r = self._session.get(url, timeout=5)
        r.encoding = "gbk"
        r.raise_for_status()
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for line in r.text.splitlines():
            # var hq_str_sh600519="贵州茅台,open,prev_close,now,high,low,...";
            if 'hq_str_' not in line or '="' not in line:
                continue
            sina_code = line.split('hq_str_')[1].split('=')[0]  # e.g. "sh600519"
            payload = line.split('="', 1)[1].rstrip('";\n')
            parts = payload.split(",")
            if len(parts) < 6:
                continue
            symbol = f"{sina_code[2:]}.{sina_code[:2].upper()}"
            try:
                prev_close = float(parts[2])
                price = float(parts[3])
                volume = int(float(parts[8])) if len(parts) > 8 else 0
            except (ValueError, IndexError):
                continue
            if price == 0:
                continue
            change_pct = (price - prev_close) / prev_close * 100 if prev_close else 0.0
            out.append(Quote(
                market="ashare",
                symbol=symbol,
                ts=now,
                price=Decimal(f"{price:.4f}"),
                change_pct=change_pct,
                volume=volume,
                source="sina",
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
            r = await asyncio.to_thread(self._session.get, _SINA_BASE + "sh000001", timeout=3)
            r.raise_for_status()
            return HealthStatus(name="ashare", state="ok")
        except Exception as e:
            return HealthStatus(name="ashare", state="down", detail=str(e))
