"""指标信号扫描服务: 拉 bar -> 算 CD -> 入库(UNIQUE 幂等)。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import structlog

from core.cache import keys
from core.domain.intervals import BARS_PER_DAY, LOOKBACK_BARS
from core.domain.markets import infer_market
from core.domain.models import IndicatorSignal
from core.indicators.cd import compute_cd_signals
from core.persistence.signal_repo import SignalRepo
from core.services.kline_service import Interval, KLineService

log = structlog.get_logger(__name__)


class SignalScanService:
    def __init__(
        self, kline: KLineService, repo: SignalRepo, *, redis=None,
        live_message_repo=None, live_message_service=None,
    ) -> None:
        self.kline = kline
        self.repo = repo
        self.redis = redis  # raw redis(AsyncRedis); 非空时新信号发 bus:signal.new
        self.live_message_repo = live_message_repo  # 非空时 rescan_clean 同步清派生消息
        self.live_message_service = live_message_service  # 非空时 rescan_clean 重建消息(带名字)

    async def scan_symbol_readonly(self, symbol: str, interval: Interval) -> int:
        """事件驱动: 只读已存 bar 算信号, 不 fetch/aggregate/persist。

        与 scan_symbol 区别: 直接读 repo.fetch_history(绕开 get_bars 的
        _INTRADAY_AGG 现聚合分支), bar 口径完全信任上游采集(crypto=open,
        A股/美股=close)。根除 60m/4h close/open 偏移 + 双写覆盖。
        """
        if getattr(self.kline, "repo", None) is None:
            return 0
        market = infer_market(symbol)
        end = datetime.now(timezone.utc)
        lookback = LOOKBACK_BARS.get(interval, 200)
        days = max(lookback // BARS_PER_DAY.get(interval, 1) * 2, 30)
        start = end - timedelta(days=days)
        bars = self.kline.repo.fetch_history(
            market, symbol, start, end, interval=interval, closed_only=True,
        )
        if not bars:
            return 0
        cd_signals = compute_cd_signals(bars)
        detected_at = datetime.now(timezone.utc)
        # 发布前 diff: 已存 bar_ts 集合, 取差集 = 本次真新增(用于发 bus, upsert 仍幂等全量)
        existing: set[str] = set()
        if self.redis is not None and hasattr(self.repo, "existing_bar_ts"):
            try:
                existing = await self.repo.existing_bar_ts(symbol, interval)
            except Exception:  # noqa: BLE001
                existing = set()
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
            log.info("signal.scan_readonly_new", symbol=symbol,
                     interval=interval, new=n)
        # fire-and-forget: 对真新增信号发 bus:signal.new(前端 SSE 消费)
        if self.redis is not None:
            fresh = [s for s in cd_signals if s.bar_ts.isoformat() not in existing]
            for s in fresh:
                payload = {
                    "market": market, "symbol": symbol, "interval": interval,
                    "signal_type": s.signal_type, "bar_ts": s.bar_ts.isoformat(),
                    "price": float(s.price) if s.price is not None else None,
                    "detected_at": detected_at.isoformat(),
                }
                try:
                    await self.redis.xadd(
                        keys.BUS_SIGNAL_NEW,
                        {"data": json.dumps(payload).encode()},
                        maxlen=10000, approximate=True,
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("signal.publish_failed", symbol=symbol,
                                interval=interval, error=str(e))
        return n
        return n

    async def rescan_clean(self, symbol: str, interval: Interval) -> tuple[int, int]:
        """冲刷重扫: 删除该标的该周期已存 CD 信号, 基于当前 (已冲刷的) 干净 bar
        重新计算并写入。返回 (删除数, 写入数)。

        用于历史 CD 信号有问题 (基于错误 K 线算出 / 时间戳错) 时, 删旧重生成准确信号。
        只读已入库收线 bar (closed_only), bar_ts 即信号真实 K 线时刻, 时间不会错。
        """
        if getattr(self.kline, "repo", None) is None:
            return 0, 0
        market = infer_market(symbol)
        end = datetime.now(timezone.utc)
        lookback = LOOKBACK_BARS.get(interval, 200)
        days = max(lookback // BARS_PER_DAY.get(interval, 1) * 2, 30)
        start = end - timedelta(days=days)
        bars = self.kline.repo.fetch_history(
            market, symbol, start, end, interval=interval, closed_only=True,
        )
        deleted = await self.repo.delete_for(symbol, interval, indicator="CD")
        # 同步清派生消息: 信号表重扫后, 清掉由旧信号派生的 live_message, 保证两表一致
        if self.live_message_repo is not None:
            try:
                m = await self.live_message_repo.delete_signal_messages(market, symbol, interval)
                if m:
                    log.info("signal.rescan_clean_messages", symbol=symbol, interval=interval, deleted_messages=m)
            except Exception as e:  # noqa: BLE001
                log.warning("signal.rescan_clean_messages_failed", symbol=symbol, interval=interval, error=str(e))
        if not bars:
            log.info("signal.rescan_clean", symbol=symbol, interval=interval,
                     deleted=deleted, written=0, note="no_bars")
            return deleted, 0
        cd_signals = compute_cd_signals(bars)
        detected_at = datetime.now(timezone.utc)
        records = [
            IndicatorSignal(
                symbol=symbol, interval=interval, indicator="CD",
                signal_type=s.signal_type, bar_ts=s.bar_ts,
                detected_at=detected_at, price=s.price, d_value=s.d_value,
            )
            for s in cd_signals
        ]
        written = await self.repo.upsert_many(records)
        # 重建派生消息 (带名字): 用与 live 链路同一 handle_signal_new 转换, 保证
        # CD信号页(indicator_signals) 与 实盘消息页(live_messages) 内容完全一致。
        rebuilt = 0
        if self.live_message_service is not None and self.live_message_repo is not None:
            try:
                msgs = []
                for s in cd_signals:
                    payload = {
                        "market": market, "symbol": symbol, "interval": interval,
                        "signal_type": s.signal_type, "bar_ts": s.bar_ts.isoformat(),
                        "price": float(s.price) if s.price is not None else None,
                        "detected_at": detected_at.isoformat(),
                    }
                    msgs.extend(await self.live_message_service.handle_signal_new(payload))
                if msgs:
                    rebuilt = await self.live_message_repo.insert_many(msgs)
            except Exception as e:  # noqa: BLE001
                log.warning("signal.rescan_rebuild_messages_failed",
                            symbol=symbol, interval=interval, error=str(e))
        log.info("signal.rescan_clean", symbol=symbol, interval=interval,
                 deleted=deleted, written=written, rebuilt_messages=rebuilt)
        return deleted, written

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

    async def scan_many(
        self, symbols: list[str], interval: Interval,
        *, market_filter: str | None = None,
    ) -> int:
        if market_filter:
            symbols = [s for s in symbols if infer_market(s) == market_filter]
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
        return await self.kline.fetch_fresh_bars(
            symbol, interval=interval, start=start, end=end,
        )
