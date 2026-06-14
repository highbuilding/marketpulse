"""A 股按需 K 线轮询 (SSE 订阅驱动).

SSE 订阅 → Redis state:subscribe:ashare:{symbol}:{interval} → bar_poller 检测
→ 启动对应标的的 10s 定时轮询 → DuckDB + Redis + SSE 总线

仅交易时段轮询。SSE 断开 → Redis key 过期 → 轮询自动停止。

支持 1m/5m/15m/30m 周期 (sina stock_zh_a_minute 原生提供).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from datetime import datetime, timezone

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.domain.market_calendar import is_trading_day
from core.domain.market_sessions import is_market_session_open
from core.domain.runtime_env import tiered_int
from core.persistence.collector_symbol_repo import CollectorSymbolRepo
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)

# 轮询间隔 (秒): 按 APP_ENV 分层。test(本地, CORE 30 标的)=30s;
# prod(线上 ~300 标的)=90s。卡 sina 服务端限流(雷区: 5m 每 5min 才收线, 30s 轮询
# 不影响及时性, 但能大幅降请求量避免触发 sina 软限流→坏数据 IndexError→熔断连锁)。
# 配合 _poll_loop 每轮 ±25% 抖动 sleep 持续削峰(防 N 个 loop 节拍漂移重聚成瞬时峰值)。
# POLL_INTERVAL_S 环境变量可显式覆盖。
POLL_INTERVAL_S = tiered_int("POLL_INTERVAL_S", test=30, prod=90)

# 调度扫描间隔 (秒): 300 个标的按 5 秒 slot 分摊到 5 分钟窗口。
SCAN_INTERVAL_S = 5
WINDOW_S = 300
MAX_CONCURRENCY = tiered_int("BAR_POLLER_MAX_CONCURRENCY", test=3, prod=3)

# 支持从 sina 拉的周期 → ak_call period 参数
INTERVAL_TO_PERIOD = {"5m": "5", "15m": "15", "30m": "30"}

# 大盘默认标的 (始终轮询, 不受 SSE 订阅影响)
_DEFAULT_SYMBOLS = (
    "000001.SH", "399001.SZ", "000300.SH", "399006.SZ",
    "000905.SH", "000852.SH", "000688.SH", "000016.SH",
)
_DEFAULT_INTERVALS = ("5m", "15m", "30m")


def _slot_for_symbol(symbol: str) -> int:
    bucket = int(hashlib.sha1(symbol.encode("utf-8")).hexdigest()[:8], 16) % (WINDOW_S // SCAN_INTERVAL_S)
    return bucket * SCAN_INTERVAL_S


class BarPoller:
    """管理 A 股标的的按需轮询任务。"""

    def __init__(
        self,
        repo: BarRepo,
        redis_cache: RedisCache,
        adapter,
        collector_symbols: CollectorSymbolRepo,
    ) -> None:
        self._repo = repo
        self._redis = redis_cache
        self._adapter = adapter
        self._collector_symbols = collector_symbols
        self._last_polled_window: dict[str, int] = {}
        self._sem = asyncio.Semaphore(MAX_CONCURRENCY)
        self._stopped = False

    def _task_key(self, symbol: str, interval: str) -> str:
        return f"{symbol}:{interval}"

    # ------------------------------------------------------------------
    # 单标的轮询
    # ------------------------------------------------------------------

    async def _poll_one(self, symbol: str, interval: str) -> None:
        """轮询单标的单周期. 拉全量 → upsert → 检测新 bar → pub.

        拉取+解析+防御(NaN 兜底 + 盘前凑数 bar 过滤)统一收口到
        AshareAdapter.fetch_intraday(SSoT 规范 1, 雷区 stock_zh_a_minute 歧义)。
        本方法只负责: 进行中根过滤 → 增量 → 入库 → 发 bus → 触发聚合。
        """
        freq = INTERVAL_TO_PERIOD.get(interval)
        if freq is None:
            return

        try:
            bars = await self._adapter.fetch_intraday(symbol, freq)
        except Exception as e:  # noqa: BLE001
            log.warning("bar_poller.fetch_failed",
                        symbol=symbol, interval=interval, error=str(e))
            return

        if not bars:
            return

        # 进行中根(ts>now)交给 ticker, poller 只处理已收线根
        now = datetime.now(timezone.utc)
        closed_bars = [b for b in bars if b.ts <= now]
        if not closed_bars:
            return

        # 增量: 只写比 DB 现有最新根更晚的 bar, 砍写放大
        # (stock_zh_a_minute 每次返回全部历史 ~1200+ 根, 全量 upsert 写放大严重)。
        try:
            existing = self._repo.fetch_history_paged(
                "ashare", symbol, interval, before=None, limit=1)
            last_ts = existing[-1].ts if existing else None
        except Exception:  # noqa: BLE001
            last_ts = None
        fresh = [b for b in closed_bars if last_ts is None or b.ts > last_ts]
        if not fresh:
            return

        # DuckDB upsert(仅新增已收线根)
        try:
            self._repo.insert_bars(fresh)
        except Exception as e:  # noqa: BLE001
            log.warning("bar_poller.db_write_failed",
                        symbol=symbol, interval=interval, error=str(e))

        # 发布最新已收线 bar 到 SSE 总线(final=true)
        latest = fresh[-1]
        payload = {
            "market": "ashare", "symbol": latest.symbol,
            "interval": latest.interval, "ts": latest.ts.isoformat(),
            "open": float(latest.open), "high": float(latest.high),
            "low": float(latest.low), "close": float(latest.close),
            "volume": int(latest.volume), "final": True,
        }
        try:
            await self._redis._r.xadd(  # noqa: SLF001
                keys.BUS_BARS_UPDATED,
                {"data": json.dumps(payload).encode()},
                maxlen=10000, approximate=True,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("bar_poller.xadd_failed", error=str(e))

        # 5m 收线触发大周期事件驱动聚合 + 发 bus
        # 审计 B5: 15m/30m 走源头直取(本 poller 已直取),不再聚合,只聚合 60m/4h(无直取源)
        if interval == "5m":
            from apps.collector.jobs.aggregate_derived import aggregate_and_publish
            await aggregate_and_publish(
                self._repo, self._redis, "ashare", symbol,
                targets=("15m", "30m", "60m", "4h"), now=now,
            )

    # ------------------------------------------------------------------
    # 订阅扫描 + 任务管理
    # ------------------------------------------------------------------

    async def _scan_subscriptions(self) -> set[str]:
        """采集集 = collector_symbols。仅 5m 直取,15m/30m/60m/4h 由 5m 聚合。"""
        symbols = await self._collector_symbols.active_symbols("ashare", capability="5m")
        return {self._task_key(symbol, "5m") for symbol in symbols}

    async def _scan_subscriptions_legacy(self) -> set[str]:
        """[废弃] 旧的订阅驱动扫描, 保留备查。"""
        active: set[str] = set()

        # 大盘默认始终轮询
        for sym in _DEFAULT_SYMBOLS:
            for iv in _DEFAULT_INTERVALS:
                active.add(self._task_key(sym, iv))

        # 扫描 SSE 订阅
        try:
            # Redis keys 扫描 (小量, 安全)
            cursor = 0
            pattern = "state:subscribe:*"
            while True:
                cursor, found = await self._redis._r.scan(  # noqa: SLF001
                    cursor, match=pattern, count=100,
                )
                for k in found:
                    k_str = k.decode() if isinstance(k, bytes) else k
                    # state:subscribe:{market}:{symbol}:{interval}
                    parts = k_str.split(":")
                    if len(parts) >= 5 and parts[2] == "ashare":
                        symbol = parts[3]
                        interval = parts[4]
                        active.add(self._task_key(symbol, interval))
                if cursor == 0:
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("bar_poller.scan_failed", error=str(e))

        return active

    def _due_tasks(self, active: set[str], *, now_s: int) -> list[str]:
        phase = now_s % WINDOW_S
        window_id = now_s // WINDOW_S
        due: list[str] = []
        for tk in sorted(active):
            symbol, _interval = tk.split(":", 1)
            slot = _slot_for_symbol(symbol)
            if not (slot <= phase < slot + SCAN_INTERVAL_S):
                continue
            if self._last_polled_window.get(tk) == window_id:
                continue
            self._last_polled_window[tk] = window_id
            due.append(tk)
        return due

    async def _poll_due(self, task_key: str) -> None:
        symbol, interval = task_key.split(":", 1)
        async with self._sem:
            await self._poll_one(symbol, interval)

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """主调度循环: 定期扫描订阅, 同步任务."""
        log.info("bar_poller.start",
                 mode="collector_symbols_slot_scheduler",
                 scan_interval_s=SCAN_INTERVAL_S,
                 window_s=WINDOW_S,
                 max_concurrency=MAX_CONCURRENCY)
        while not self._stopped:
            try:
                active = await self._scan_subscriptions()
                if is_trading_day("ashare") and is_market_session_open("ashare"):
                    due = self._due_tasks(active, now_s=int(time.time()))
                    if due:
                        log.debug("bar_poller.due", count=len(due), active=len(active))
                        await asyncio.gather(*(self._poll_due(tk) for tk in due))
                await asyncio.sleep(SCAN_INTERVAL_S * random.uniform(0.85, 1.15))
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                log.warning("bar_poller.scan_error", error=str(e))
                await asyncio.sleep(SCAN_INTERVAL_S)

    async def shutdown(self) -> None:
        """停止所有轮询任务."""
        self._stopped = True
        self._last_polled_window.clear()
        log.info("bar_poller.shutdown")


# ------------------------------------------------------------------
# 独立入口 (collector lifespan 调用)
# ------------------------------------------------------------------

async def run_bar_poller(
    repo: BarRepo,
    redis_cache: RedisCache,
    adapter,
    collector_symbols: CollectorSymbolRepo,
    *,
    startup_delay_s: int = 0,
) -> None:
    """collector lifespan 中作为 asyncio task 启动。

    startup_delay_s: 冷启动让路给 startup_reconcile —— reconcile 与 poller 都拉
    同一个 fetch_intraday('5')/同一 sina 接口, 并发会 double sina 压力触发限频(456)。
    延迟启动 poller, 让 reconcile 先把 5m 历史拉全, poller 再接管 live 收线轮询。
    """
    poller = BarPoller(repo, redis_cache, adapter, collector_symbols)
    try:
        if startup_delay_s > 0:
            log.info("bar_poller.startup_delay", seconds=startup_delay_s)
            await asyncio.sleep(startup_delay_s)
        await poller.run()
    except asyncio.CancelledError:
        await poller.shutdown()


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------
