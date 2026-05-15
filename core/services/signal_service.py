"""指标信号扫描服务: 拉 bar -> 算 CD -> 入库(UNIQUE 幂等)。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog

from core.domain.intervals import BARS_PER_DAY, LOOKBACK_BARS
from core.domain.models import IndicatorSignal
from core.indicators.cd import compute_cd_signals
from core.persistence.signal_repo import SignalRepo
from core.services.kline_service import Interval, KLineService

log = structlog.get_logger(__name__)


class SignalScanService:
    def __init__(self, kline: KLineService, repo: SignalRepo) -> None:
        self.kline = kline
        self.repo = repo

    async def scan_symbol(self, symbol: str, interval: Interval) -> int:
        """对单个 symbol/interval 扫一次 CD 信号, 入库, 返回新增条数。"""
        bars = await self._fetch_bars(symbol, interval)
        log.debug("signal.scan_fetched", symbol=symbol, interval=interval,
                  bars=len(bars))
        if not bars:
            return 0
        cd_signals = compute_cd_signals(bars)
        log.debug("signal.scan_computed", symbol=symbol, interval=interval,
                  bars=len(bars), signals=len(cd_signals))
        detected_at = datetime.now(timezone.utc)
        records = [
            IndicatorSignal(
                symbol=symbol, interval=interval, indicator="CD",
                signal_type=s.signal_type, bar_ts=s.bar_ts,
                detected_at=detected_at, price=s.price, d_value=s.d_value,
            )
            for s in cd_signals
        ]
        n = await self.repo.upsert_many(records)
        if n > 0:
            log.info("signal.scan_new", symbol=symbol, interval=interval, new=n,
                     total=len(records))
        return n

    async def scan_many(self, symbols: list[str], interval: Interval) -> int:
        total = 0
        for sym in symbols:
            try:
                total += await self.scan_symbol(sym, interval)
            except Exception as e:  # noqa: BLE001
                log.warning("signal.scan_failed",
                            symbol=sym, interval=interval, error=str(e))
        return total

    async def _fetch_bars(self, symbol: str, interval: Interval):
        end = datetime.now(timezone.utc)
        lookback = LOOKBACK_BARS.get(interval, 200)
        # 转成"日历天数", 给周末/节假日留一倍 buffer
        days = max(lookback // BARS_PER_DAY.get(interval, 1) * 2, 30)
        start = end - timedelta(days=days)
        return await self.kline.get_bars(
            symbol, interval=interval, start=start, end=end,
        )
