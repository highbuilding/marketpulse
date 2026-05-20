from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

import pandas as pd
import structlog
import yfinance as yf

from core.adapters.base import AdapterError, CircuitBreaker
from core.domain.models import Bar, HealthStatus, Quote

log = structlog.get_logger(__name__)


def _to_yfinance_ticker(symbol: str) -> str:
    """Class share 字符转换: BRK.B → BRK-B(yfinance 格式)。
    业务层永远见 BRK.B, adapter 进出口转换。
    """
    return symbol.replace(".", "-")


class USAdapter:
    market = "us"
    name = "us"

    def __init__(self) -> None:
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.secret = os.getenv("ALPACA_SECRET_KEY")
        self.has_primary = bool(self.api_key and self.secret)
        # primary: Alpaca, 中等阈值
        self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=300)
        # backup: yfinance, 更激进 - 429 / 网络失败 2 次熔断 30 分钟
        self.backup_cb = CircuitBreaker(fail_threshold=2, reset_after_s=1800)

    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]:
        if self.has_primary and self.primary_cb.can_execute():
            try:
                quotes = await asyncio.to_thread(self._fetch_snapshot_alpaca, symbols)
                self.primary_cb.record_success()
                return quotes
            except Exception as e:
                self.primary_cb.record_failure()
                log.warning("us.alpaca_failed", error=str(e))
        # yfinance backup, 熔断保护
        if not self.backup_cb.can_execute():
            log.debug("us.yfinance_circuit_open_skip_snapshot")
            return []
        try:
            quotes = await asyncio.to_thread(self._fetch_snapshot_yfinance, symbols)
            if quotes:
                self.backup_cb.record_success()
            else:
                # 整批 0 quote 视作失败(可能全部 429)
                self.backup_cb.record_failure()
            return quotes
        except Exception as e:
            self.backup_cb.record_failure()
            raise AdapterError(f"both primary and backup failed: {e}", source="us") from e

    def _fetch_snapshot_alpaca(self, symbols: list[str]) -> list[Quote]:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest
        # Alpaca 拒绝 ^GSPC 这种 Yahoo 指数 ticker(整批 400),先过滤
        eligible = [s for s in symbols if not s.startswith("^")]
        if not eligible:
            return []
        client = StockHistoricalDataClient(self.api_key, self.secret)
        req = StockLatestQuoteRequest(symbol_or_symbols=eligible)
        resp = client.get_stock_latest_quote(req)
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for sym in eligible:
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

    async def fetch_intraday(self, symbol: str, freq: str = "5") -> list[Bar]:
        """freq: '1'/'5'/'15'/'30'/'60' min。
        yfinance 限制: 1m=7d, 5m/15m/30m/60m=60d。prepost=True 拿盘前盘后。
        """
        interval_map = {"1": "1m", "5": "5m", "15": "15m",
                        "30": "30m", "60": "60m"}
        if freq not in interval_map:
            raise ValueError(f"unsupported freq: {freq}")
        yf_interval = interval_map[freq]
        period = "7d" if freq == "1" else "60d"
        yf_symbol = _to_yfinance_ticker(symbol)
        df = await asyncio.to_thread(
            yf.download, yf_symbol,
            period=period, interval=yf_interval,
            prepost=True, progress=False, auto_adjust=False,
        )
        out: list[Bar] = []
        for idx, row in df.iterrows():
            # yfinance intraday 通常返回 ET 时区的 index
            if idx.tzinfo is None:
                ts_utc = (
                    idx.tz_localize("America/New_York")
                    .tz_convert("UTC")
                    .to_pydatetime()
                )
            else:
                ts_utc = idx.tz_convert("UTC").to_pydatetime()
            # 跳过 OHLC 任何字段 NaN 的行(yfinance 在 prepost 时段偶发)
            if any(pd.isna(row[c]) for c in ("Open", "High", "Low", "Close")):
                continue
            vol = int(row["Volume"]) if not pd.isna(row["Volume"]) else 0
            out.append(Bar(
                market="us", symbol=symbol, ts=ts_utc,
                open=Decimal(str(float(row["Open"]))),
                high=Decimal(str(float(row["High"]))),
                low=Decimal(str(float(row["Low"]))),
                close=Decimal(str(float(row["Close"]))),
                volume=vol, interval=f"{freq}m",
            ))
        return out

    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        """1d 历史。
        路径: Alpaca IEX 主源 → yfinance 备(熔断保护)。
        """
        if self.has_primary:
            try:
                return await asyncio.to_thread(
                    self._fetch_history_alpaca, symbol, start, end,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("us.alpaca_history_failed",
                            symbol=symbol, error=str(e))

        if not self.backup_cb.can_execute():
            log.warning("us.yfinance_circuit_open_skip_history", symbol=symbol)
            raise AdapterError(
                f"alpaca unavailable and yfinance circuit open for {symbol}",
                source="us",
            )
        try:
            bars = await self._fetch_history_yfinance(symbol, start, end)
            self.backup_cb.record_success()
            return bars
        except Exception as e:
            self.backup_cb.record_failure()
            raise AdapterError(
                f"both alpaca and yfinance failed for {symbol}: {e}",
                source="us",
            ) from e

    def _fetch_history_alpaca(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        """Alpaca IEX 拿 1d。
        Alpaca 1d timestamp 已是 ET 自然交易日 00:00 → UTC, 满足雷区 3 对称。
        """
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = StockHistoricalDataClient(self.api_key, self.secret)
        yf_symbol = _to_yfinance_ticker(symbol)
        # IEX 实时数据有 15 min 延迟, end 留 20 min 余量
        now = datetime.now(timezone.utc)
        end_safe = min(end, now - timedelta(minutes=20))
        req = StockBarsRequest(
            symbol_or_symbols=yf_symbol,
            timeframe=TimeFrame.Day,
            start=start, end=end_safe, feed="iex",
        )
        resp = client.get_stock_bars(req)
        raw_bars = resp.data.get(yf_symbol, [])
        out: list[Bar] = []
        for b in raw_bars:
            out.append(Bar(
                market="us", symbol=symbol, ts=b.timestamp,
                open=Decimal(str(float(b.open))),
                high=Decimal(str(float(b.high))),
                low=Decimal(str(float(b.low))),
                close=Decimal(str(float(b.close))),
                volume=int(b.volume) if b.volume else 0,
                interval="1d",
            ))
        return out

    async def _fetch_history_yfinance(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        """1d 历史。ts 与 A 股雷区 3 对称: normalize 为该市场本地交易日 00:00 → UTC。
        美股本地 = America/New_York(自动跟夏/冬令时)。
        """
        yf_symbol = _to_yfinance_ticker(symbol)
        df = await asyncio.to_thread(
            yf.download, yf_symbol,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False, auto_adjust=False,
        )
        out: list[Bar] = []
        for idx, row in df.iterrows():
            local_ts = (
                idx.tz_localize("America/New_York") if idx.tzinfo is None
                else idx.tz_convert("America/New_York")
            )
            # normalize() 把 wall-clock 设到 00:00, 对应该市场自然交易日开始
            ts_utc = local_ts.normalize().tz_convert("UTC").to_pydatetime()
            # 跳过 OHLC 任何字段 NaN 的行(与 fetch_intraday 一致)
            if any(pd.isna(row[c]) for c in ("Open", "High", "Low", "Close")):
                continue
            out.append(Bar(
                market="us", symbol=symbol, ts=ts_utc,
                open=Decimal(str(float(row["Open"]))),
                high=Decimal(str(float(row["High"]))),
                low=Decimal(str(float(row["Low"]))),
                close=Decimal(str(float(row["Close"]))),
                volume=int(row["Volume"]) if not pd.isna(row["Volume"]) else 0,
                interval="1d",
            ))
        return out

    async def verify_ticker(self, symbol: str) -> tuple[bool, str | None]:
        """轻量校验 + 拿 long name。供 directory 懒加载用。
        返回 (是否有效, 公司名 | None)。
        """
        yf_symbol = _to_yfinance_ticker(symbol)

        def _fetch() -> tuple[bool, str | None]:
            ticker = yf.Ticker(yf_symbol)
            last_price = getattr(ticker.fast_info, "last_price", None)
            if last_price is None:
                return False, None
            long_name = ticker.info.get("longName") if isinstance(ticker.info, dict) else None
            return True, long_name

        try:
            return await asyncio.to_thread(_fetch)
        except Exception:  # noqa: BLE001
            return False, None

    async def health(self) -> HealthStatus:
        if not self.has_primary:
            return HealthStatus(name="us", state="disabled", detail="missing ALPACA_API_KEY")
        if not self.primary_cb.can_execute():
            return HealthStatus(name="us", state="degraded", detail="alpaca circuit open")
        return HealthStatus(name="us", state="ok")
