from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import structlog

from core.domain.markets import infer_market
from core.domain.models import ChipSummary
from core.integrations.akshare import ak_call
from core.persistence.chip_repo import ChipRepo

log = structlog.get_logger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")


def _code(symbol: str) -> str:
    return symbol.split(".")[0]


def _num(v) -> float | None:
    if v is None or pd.isna(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date(v) -> datetime:
    d = pd.to_datetime(v).date()
    return datetime.combine(d, datetime.min.time(), tzinfo=_CN_TZ).astimezone(timezone.utc)


class ChipService:
    def __init__(self, repo: ChipRepo) -> None:
        self.repo = repo

    async def get_summary(self, symbol: str, *, days: int = 90) -> list[ChipSummary]:
        if infer_market(symbol) != "ashare":
            return []

        cached = await self.repo.list_recent(symbol, limit=days)
        # 东方财富该接口仅近 90 个交易日; 有缓存先返回, 避免详情页频繁触发 ak 调用。
        if cached:
            return cached[-days:]

        rows = await self.pull_summary(symbol)
        if rows:
            return rows[-days:]
        return []

    async def get_summary_cache_only(
        self, symbol: str, *, days: int = 90,
    ) -> list[ChipSummary]:
        """只读 DuckDB cache, 不触发 ak_call。

        api 路由必须用这个方法; collector 的 chip:preload cron 用 get_summary 走全量
        (cache miss 时拉 ak)。Plan 3 Hotfix B 引入。
        """
        if infer_market(symbol) != "ashare":
            return []
        cached = await self.repo.list_recent(symbol, limit=days)
        if not cached:
            return []
        return cached[-days:]

    async def preload_watchlist_chip_summary(
        self, watchlist, *, days: int = 90,
    ) -> int:
        """日终全量预热: 把 watchlist 中所有 A 股标的的筹码摘要一次性算完写库。

        返回成功 preload 的标的数。单条失败不影响后续。
        """
        syms = await watchlist.dynamic_universe()
        ashare = [s for s in syms if s.endswith(".SH") or s.endswith(".SZ")]
        ok_count = 0
        for s in ashare:
            try:
                await self.get_summary(s, days=days)
                ok_count += 1
            except Exception as e:  # noqa: BLE001
                log.warning("chip.preload_failed", symbol=s, error=str(e))
        log.info("chip.preload_done", total=len(ashare), ok=ok_count)
        return ok_count

    async def pull_summary(self, symbol: str) -> list[ChipSummary]:
        if infer_market(symbol) != "ashare":
            return []

        df = await ak_call(
            "stock_cyq_em",
            symbol=_code(symbol),
            adjust="qfq",
            caller=f"chip.summary:{symbol}",
        )
        rows = self._parse(symbol, df)
        await self.repo.upsert_many(rows)
        log.info("chip.summary_fetched", symbol=symbol, rows=len(rows))
        return rows

    @staticmethod
    def _parse(symbol: str, df: pd.DataFrame) -> list[ChipSummary]:
        if df is None or df.empty:
            return []

        out: list[ChipSummary] = []
        for _, row in df.iterrows():
            try:
                trade_date = _date(row.get("日期"))
            except Exception:  # noqa: BLE001
                continue
            out.append(ChipSummary(
                symbol=symbol,
                trade_date=trade_date,
                profit_ratio=_num(row.get("获利比例")),
                avg_cost=_num(row.get("平均成本")),
                cost_90_low=_num(row.get("90成本-低")),
                cost_90_high=_num(row.get("90成本-高")),
                concentration_90=_num(row.get("90集中度")),
                cost_70_low=_num(row.get("70成本-低")),
                cost_70_high=_num(row.get("70成本-高")),
                concentration_70=_num(row.get("70集中度")),
            ))
        out.sort(key=lambda r: r.trade_date)
        return out
