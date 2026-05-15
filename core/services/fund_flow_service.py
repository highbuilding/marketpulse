from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import structlog

from core.domain.models import FundFlowSnapshot
from core.integrations.akshare import ak_call
from core.persistence.fund_flow_repo import FundFlowRepo

log = structlog.get_logger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")


def _split_symbol(symbol: str) -> tuple[str, str]:
    code, mkt = symbol.split(".")
    return code, mkt.lower()


def _parse_ts(s: str) -> datetime:
    """sina/akshare 返回北京时间,转 UTC 存库。"""
    if " " in s:
        naive = datetime.fromisoformat(s)
    else:
        naive = datetime.fromisoformat(s + "T00:00:00")
    return naive.replace(tzinfo=_CN_TZ).astimezone(timezone.utc)


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
        df = await ak_call(
            "stock_individual_fund_flow", stock=code, market=mkt,
            caller=f"fund_flow.pull_symbol:{symbol}",
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
        df = await ak_call(
            "stock_hsgt_hist_em", symbol="北向资金",
            caller="fund_flow.pull_north",
        )
        row = df.iloc[-1]
        ts = _parse_ts(str(row["日期"]))
        # 优先 当日成交净买额,后备 当日资金流入
        total = _num(row.get("当日成交净买额")) or _num(row.get("当日资金流入")) or 0.0
        await self.repo.save_north_flow(FundFlowSnapshot(
            subject="north", kind="north", ts=ts,
            hgt_net=total * 0.6,
            sgt_net=total * 0.4,
        ))

    async def query_north(self, start: datetime, end: datetime) -> list[FundFlowSnapshot]:
        return await self.repo.query_north_flow(start, end)
