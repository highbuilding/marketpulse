from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import akshare as ak
import structlog

from core.domain.models import FundFlowSnapshot
from core.persistence.fund_flow_repo import FundFlowRepo

log = structlog.get_logger(__name__)


def _split_symbol(symbol: str) -> tuple[str, str]:
    code, mkt = symbol.split(".")
    return code, mkt.lower()


def _parse_ts(s: str) -> datetime:
    if " " in s:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(s + "T00:00:00+00:00")


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


class FundFlowService:
    def __init__(self, repo: FundFlowRepo) -> None:
        self.repo = repo

    async def pull_symbol_flow(self, symbol: str) -> int:
        code, mkt = _split_symbol(symbol)
        df = await asyncio.to_thread(
            ak.stock_individual_fund_flow, stock=code, market=mkt,
        )
        snapshots: list[FundFlowSnapshot] = []
        for _, row in df.iterrows():
            try:
                ts = _parse_ts(str(row["日期"]))
                snapshots.append(FundFlowSnapshot(
                    subject=symbol, kind="symbol", ts=ts,
                    main_net=_num(row.get("主力净流入-净额")),
                    super_large_net=_num(row.get("超大单净流入-净额")),
                    large_net=_num(row.get("大单净流入-净额")),
                    medium_net=_num(row.get("中单净流入-净额")),
                    small_net=_num(row.get("小单净流入-净额")),
                ))
            except (KeyError, ValueError, TypeError) as e:
                log.warning("symbol_flow.parse_failed", symbol=symbol, error=str(e))
        await self.repo.save_symbol_flows(snapshots)
        return len(snapshots)

    async def query_symbol(self, symbol: str, start: datetime, end: datetime) -> list[FundFlowSnapshot]:
        return await self.repo.query_symbol_flow(symbol, start, end)

    async def pull_north_flow(self) -> None:
        df = await asyncio.to_thread(
            ak.stock_hsgt_north_net_flow_in_em, symbol="北上",
        )
        row = df.iloc[-1]
        ts = _parse_ts(str(row["日期"]))
        total = _num(row.get("当日资金流入")) or 0.0
        await self.repo.save_north_flow(FundFlowSnapshot(
            subject="north", kind="north", ts=ts,
            hgt_net=total * 0.6,
            sgt_net=total * 0.4,
        ))

    async def query_north(self, start: datetime, end: datetime) -> list[FundFlowSnapshot]:
        return await self.repo.query_north_flow(start, end)
