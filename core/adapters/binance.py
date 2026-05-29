"""Binance Spot REST + WS adapter.

REST: 历史回填 (klines 分页) + latest snapshot (ticker 24hr)
WS:   增量推送, 见 apps/collector/crypto/ws_consumer.py (P3)

interval 映射 (项目 → Binance):
    5m / 15m / 30m → 5m / 15m / 30m
    60m → 1h
    4h → 4h
    1d → 1d
    1wk → 1w
    1mo → 1M

symbol 映射: BTC-USDT (项目) ↔ BTCUSDT (Binance)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import structlog

from core.adapters.base import AdapterError
from core.domain.models import Bar, HealthStatus, Quote

log = structlog.get_logger(__name__)

REST_BASE = "https://api.binance.com"

INTERVAL_MAP = {
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "60m": "1h",
    "4h": "4h",
    "1d": "1d",
    "1wk": "1w",
    "1mo": "1M",
}


def _to_binance(symbol: str) -> str:
    """BTC-USDT → BTCUSDT"""
    return symbol.replace("-", "").upper()


def _from_binance(b_symbol: str) -> str:
    """BTCUSDT → BTC-USDT (启发式: 末尾稳定币 token, 前面是 base)."""
    for stable in ("USDT", "USDC", "BUSD", "FDUSD"):
        if b_symbol.endswith(stable):
            return f"{b_symbol[: -len(stable)]}-{stable}"
    return b_symbol


class BinanceAdapter:
    """Binance Spot 适配器,实现 MarketAdapter Protocol。"""

    market = "crypto"
    name = "binance"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=REST_BASE, timeout=10.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]:
        """24hr ticker batch 拿 latest price + change_pct + 成交量."""
        if not symbols:
            return []
        b_syms = [_to_binance(s) for s in symbols]
        params = {"symbols": '["' + '","'.join(b_syms) + '"]'}
        try:
            r = await self._client.get("/api/v3/ticker/24hr", params=params)
            r.raise_for_status()
        except Exception as e:
            log.warning("binance.snapshot_failed", symbols=symbols, error=str(e))
            return []
        out: list[Quote] = []
        now = datetime.now(timezone.utc)
        rows = r.json()
        # 单 symbol 时 binance 可能直接返回 dict
        if isinstance(rows, dict):
            rows = [rows]
        for d in rows:
            try:
                sym = _from_binance(d["symbol"])
                out.append(
                    Quote(
                        market="crypto",
                        symbol=sym,
                        ts=now,
                        price=Decimal(d["lastPrice"]),
                        change_pct=float(d["priceChangePercent"]),
                        volume=int(float(d["volume"])),
                        source="binance",
                    )
                )
            except Exception as e:  # noqa: BLE001
                log.warning("binance.snapshot_parse_failed", row=d, error=str(e))
        return out

    async def fetch_history(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[Bar]:
        """1d 历史. 分页内部处理."""
        return await self._fetch_klines_paged(symbol, "1d", start, end)

    async def fetch_intraday(self, symbol: str, freq: str = "5") -> list[Bar]:
        """freq: '5' / '15' / '30' / '60' (= 1h) / '240' (= 4h)。默认拉 30 天。"""
        interval_map = {
            "5": "5m",
            "15": "15m",
            "30": "30m",
            "60": "60m",
            "240": "4h",
        }
        proj_iv = interval_map.get(freq)
        if proj_iv is None:
            raise AdapterError(f"unsupported freq: {freq}", source="binance")
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=30)
        return await self._fetch_klines_paged(symbol, proj_iv, start, end)

    async def fetch_klines(
        self,
        symbol: str,
        project_interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """显式 interval 历史拉取 (给 backfill 用, 不限于 fetch_intraday 的 30 天)."""
        return await self._fetch_klines_paged(symbol, project_interval, start, end)

    async def _fetch_klines_paged(
        self,
        symbol: str,
        project_interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """反向分页拉取 [start, end] 区间的 klines.

        坑: 当同时传 startTime + endTime 时, Binance 返回的是从 startTime
        起最多 1000 根 (即正向, 不是反向),且 endTime 不起截断作用. 所以
        反向回溯必须只传 endTime, 让 Binance 自然返回 endTime 之前的最近
        1000 根, 直到回溯到 start 之前.
        """
        b_iv = INTERVAL_MAP.get(project_interval)
        if b_iv is None:
            raise AdapterError(
                f"unsupported interval: {project_interval}", source="binance"
            )
        b_sym = _to_binance(symbol)

        out: list[Bar] = []
        cursor_end = int(end.timestamp() * 1000)
        cursor_start = int(start.timestamp() * 1000)

        while cursor_end > cursor_start:
            params: dict = {
                "symbol": b_sym,
                "interval": b_iv,
                "endTime": cursor_end,
                "limit": 1000,
            }
            try:
                r = await self._client.get("/api/v3/klines", params=params)
                r.raise_for_status()
            except Exception as e:
                log.warning(
                    "binance.klines_failed",
                    symbol=symbol,
                    interval=project_interval,
                    error=str(e),
                )
                break
            rows = r.json()
            if not rows:
                break
            page = [
                self._parse_kline(symbol, project_interval, row) for row in rows
            ]
            # 只保留 >= cursor_start 的(最早页可能跨界, 截掉过早的)
            page = [b for b in page if int(b.ts.timestamp() * 1000) >= cursor_start]
            out = page + out  # 早段在前
            earliest_open_ms = rows[0][0]
            if earliest_open_ms <= cursor_start:
                break
            # 翻页: 把 cursor_end 设为本页最早一根的 openTime - 1ms
            cursor_end = earliest_open_ms - 1
            await asyncio.sleep(0.1)  # 限流缓冲
            if len(rows) < 1000:
                break

        # 去重排序
        by_ts: dict[datetime, Bar] = {b.ts: b for b in out}
        return sorted(by_ts.values(), key=lambda b: b.ts)

    @staticmethod
    def _parse_kline(symbol: str, project_interval: str, row: list) -> Bar:
        # row = [openTime, open, high, low, close, volume, closeTime,
        #        quoteVolume, trades, takerBaseVol, takerQuoteVol, ignore]
        # closeTime 是 ms, Binance closeTime = openTime + interval - 1ms
        # ts 用 closeTime + 1 (close 时刻边界, 符合项目 intraday close 语义)
        close_ts_ms = row[6] + 1
        ts = datetime.fromtimestamp(close_ts_ms / 1000, tz=timezone.utc)
        return Bar(
            market="crypto",
            symbol=symbol,
            ts=ts,
            open=Decimal(row[1]),
            high=Decimal(row[2]),
            low=Decimal(row[3]),
            close=Decimal(row[4]),
            volume=int(float(row[5])),
            interval=project_interval,
        )

    async def verify_ticker(self, symbol: str) -> tuple[bool, str | None]:
        try:
            r = await self._client.get(
                "/api/v3/exchangeInfo",
                params={"symbol": _to_binance(symbol)},
            )
            r.raise_for_status()
            d = r.json()
            return bool(d.get("symbols")), symbol
        except Exception:
            return False, None

    async def subscribe(self, symbols: list[str], on_bar) -> None:  # noqa: ARG002
        """WS 订阅由 P3 的 binance_ws_consumer 实现, adapter 接口保留兼容。"""
        return None

    async def health(self) -> HealthStatus:
        try:
            r = await self._client.get("/api/v3/ping", timeout=3.0)
            r.raise_for_status()
            return HealthStatus(name="binance", state="ok")
        except Exception as e:  # noqa: BLE001
            return HealthStatus(name="binance", state="degraded", detail=str(e))
