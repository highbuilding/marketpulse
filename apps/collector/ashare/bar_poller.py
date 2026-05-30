"""A 股按需 K 线轮询 (SSE 订阅驱动).

SSE 订阅 → Redis state:subscribe:ashare:{symbol}:{interval} → bar_poller 检测
→ 启动对应标的的 10s 定时轮询 → DuckDB + Redis + SSE 总线

仅交易时段轮询。SSE 断开 → Redis key 过期 → 轮询自动停止。

支持 1m/5m/15m/30m 周期 (sina stock_zh_a_minute 原生提供).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.domain.market_calendar import is_trading_day
from core.domain.models import Bar as BarModel
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)

# 轮询间隔 (秒)
POLL_INTERVAL_S = 10

# 订阅扫描间隔 (秒)
SCAN_INTERVAL_S = 30

# 支持从 sina 拉的周期 → ak_call period 参数
INTERVAL_TO_PERIOD = {"1m": "1", "5m": "5", "15m": "15", "30m": "30"}

# 大盘默认标的 (始终轮询, 不受 SSE 订阅影响)
_DEFAULT_SYMBOLS = (
    "000001.SH", "399001.SZ", "000300.SH", "399006.SZ",
    "000905.SH", "000852.SH", "000688.SH", "000016.SH",
)
_DEFAULT_INTERVALS = ("5m", "15m", "30m")


class BarPoller:
    """管理 A 股标的的按需轮询任务。"""

    def __init__(
        self,
        repo: BarRepo,
        redis_cache: RedisCache,
    ) -> None:
        self._repo = repo
        self._redis = redis_cache
        self._tasks: dict[str, asyncio.Task] = {}  # key = "symbol:interval"
        self._stopped = False

    def _task_key(self, symbol: str, interval: str) -> str:
        return f"{symbol}:{interval}"

    # ------------------------------------------------------------------
    # 单标的轮询
    # ------------------------------------------------------------------

    async def _poll_one(self, symbol: str, interval: str) -> None:
        """轮询单标的单周期. 拉全量 → upsert → 检测新 bar → pub."""
        period = INTERVAL_TO_PERIOD.get(interval)
        if period is None:
            return

        try:
            from core.integrations.akshare import ak_call

            df = await ak_call(
                "stock_zh_a_minute",
                symbol=symbol, period=period, adjust="qfq",
                caller=f"bar_poller:{symbol}:{interval}",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("bar_poller.fetch_failed",
                        symbol=symbol, interval=interval, error=str(e))
            return

        if df is None or df.empty:
            return

        # 解析 bars
        _CN_TZ = ZoneInfo("Asia/Shanghai")

        bars = []
        for _, row in df.iterrows():
            if pd.isna(row.get("open")) or pd.isna(row.get("close")):
                continue
            try:
                naive = datetime.fromisoformat(str(row["day"]).replace(" ", "T"))
                ts = naive.replace(tzinfo=_CN_TZ).astimezone(timezone.utc)
            except Exception:  # noqa: BLE001
                continue
            try:
                bars.append(BarModel(
                    market="ashare",
                    symbol=symbol,
                    ts=ts,
                    open=Decimal(str(row["open"])),
                    high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])),
                    close=Decimal(str(row["close"])),
                    volume=int(float(row["volume"])),
                    interval=interval,
                ))
            except Exception:  # noqa: BLE001
                continue

        if not bars:
            return

        # DuckDB upsert
        try:
            self._repo.insert_bars(bars)
        except Exception as e:  # noqa: BLE001
            log.warning("bar_poller.db_write_failed",
                        symbol=symbol, interval=interval, error=str(e))

        # 发布最新 bar 到 SSE 总线
        latest = bars[-1]
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

    async def _poll_loop(self, symbol: str, interval: str) -> None:
        """单标的轮询循环. 被 cancel 时干净退出."""
        key = self._task_key(symbol, interval)
        log.info("bar_poller.poll_start", symbol=symbol, interval=interval)
        while not self._stopped:
            try:
                if is_trading_day("ashare"):
                    await self._poll_one(symbol, interval)
                await asyncio.sleep(POLL_INTERVAL_S)
            except asyncio.CancelledError:
                log.info("bar_poller.poll_stop", symbol=symbol, interval=interval)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("bar_poller.poll_error",
                            symbol=symbol, interval=interval, error=str(e))
                await asyncio.sleep(POLL_INTERVAL_S)

    # ------------------------------------------------------------------
    # 订阅扫描 + 任务管理
    # ------------------------------------------------------------------

    async def _scan_subscriptions(self) -> set[str]:
        """扫描 Redis 活跃订阅 → 返回需要轮询的 task_key 集合."""
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

    async def _sync_tasks(self, active: set[str]) -> None:
        """同步轮询任务: 启动新的, 停止过期的."""
        # 启动新任务
        for tk in active:
            if tk not in self._tasks or self._tasks[tk].done():
                symbol, interval = tk.split(":", 1)
                self._tasks[tk] = asyncio.create_task(
                    self._poll_loop(symbol, interval),
                    name=f"bar_poll:{tk}",
                )

        # 停止过期任务 (排除默认大盘)
        for tk, task in list(self._tasks.items()):
            if tk not in active and not self._is_default(tk):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                del self._tasks[tk]

    def _is_default(self, task_key: str) -> bool:
        symbol, interval = task_key.split(":", 1)
        return symbol in _DEFAULT_SYMBOLS and interval in _DEFAULT_INTERVALS

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """主调度循环: 定期扫描订阅, 同步任务."""
        log.info("bar_poller.start",
                 default_symbols=list(_DEFAULT_SYMBOLS),
                 default_intervals=list(_DEFAULT_INTERVALS),
                 poll_interval_s=POLL_INTERVAL_S)
        while not self._stopped:
            try:
                active = await self._scan_subscriptions()
                await self._sync_tasks(active)
                await asyncio.sleep(SCAN_INTERVAL_S)
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                log.warning("bar_poller.scan_error", error=str(e))
                await asyncio.sleep(SCAN_INTERVAL_S)

    async def shutdown(self) -> None:
        """停止所有轮询任务."""
        self._stopped = True
        for tk, task in list(self._tasks.items()):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        log.info("bar_poller.shutdown")


# ------------------------------------------------------------------
# 独立入口 (collector lifespan 调用)
# ------------------------------------------------------------------

async def run_bar_poller(repo: BarRepo, redis_cache: RedisCache) -> None:
    """collector lifespan 中作为 asyncio task 启动."""
    poller = BarPoller(repo, redis_cache)
    try:
        await poller.run()
    except asyncio.CancelledError:
        await poller.shutdown()


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------
