"""全市场行情/板块查询。

A 股实时榜单、全市场快照、行业/概念板块统一走 AKShare 封装接口，
调用入口必须经 `core.integrations.akshare.ak_call`，避免业务层散点直接请求。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd
import structlog

from core.integrations.akshare import ak_call

log = structlog.get_logger(__name__)


SORT_DIR = Literal["asc", "desc"]


@dataclass(frozen=True, slots=True)
class RankRow:
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: int
    amount: float  # 成交额


@dataclass(frozen=True, slots=True)
class SectorRow:
    code: str
    name: str
    change_pct: float
    avg_price: float
    company_count: int
    leader_name: str
    leader_change_pct: float
    leader_symbol: str | None = None


class MarketQueryService:
    async def top_ashare(self, direction: SORT_DIR = "desc", limit: int = 10) -> list[RankRow]:
        rows = await self.all_ashare()
        return sorted(rows, key=lambda row: row.change_pct, reverse=(direction == "desc"))[:limit]

    async def all_ashare(self, limit: int = 5000) -> list[RankRow]:
        df = await ak_call(
            "stock_zh_a_spot_em",
            caller=f"market_query.all_ashare:limit={limit}",
            ak_timeout_s=8,
        )
        rows = self._parse_spot_em_rows(df)
        return sorted(rows[:limit], key=lambda row: row.change_pct, reverse=True)

    async def sectors_ashare(self) -> list[SectorRow]:
        rows: list[SectorRow] = []
        try:
            industry_df = await ak_call(
                "stock_board_industry_name_em",
                caller="market_query.sectors_ashare:industry",
                ak_timeout_s=8,
            )
            rows.extend(self._parse_sector_name_rows(industry_df, kind="industry"))
        except Exception as e:  # noqa: BLE001
            log.warning("market_query.sectors_ashare.industry_failed", error=str(e))
        try:
            concept_df = await ak_call(
                "stock_board_concept_name_em",
                caller="market_query.sectors_ashare:concept",
                ak_timeout_s=8,
            )
            rows.extend(self._parse_sector_name_rows(concept_df, kind="concept"))
        except Exception as e:  # noqa: BLE001
            log.warning("market_query.sectors_ashare.concept_failed", error=str(e))
        if not rows:
            raise RuntimeError("AKShare 行业/概念板块接口均获取失败")
        return sorted(rows, key=lambda s: s.change_pct, reverse=True)

    async def sector_constituents_ashare(self, sector_code: str, limit: int = 8) -> list[RankRow]:
        kind, name = _split_sector_code(sector_code)
        func_name = (
            "stock_board_concept_cons_em"
            if kind == "concept"
            else "stock_board_industry_cons_em"
        )
        df = await ak_call(
            func_name,
            symbol=name,
            caller=f"market_query.sector_constituents:{sector_code}:limit={limit}",
            ak_timeout_s=6,
        )
        return sorted(
            self._parse_spot_em_rows(df),
            key=lambda row: row.change_pct,
            reverse=True,
        )[:limit]

    async def top_hk(self, direction: SORT_DIR = "desc", limit: int = 10) -> list[RankRow]:
        df = await ak_call(
            "stock_hk_spot_em",
            caller=f"market_query.top_hk:{direction}:limit={limit}",
            ak_timeout_s=8,
        )
        rows = self._parse_hk_spot_rows(df)
        return sorted(rows, key=lambda row: row.change_pct, reverse=(direction == "desc"))[:limit]

    @staticmethod
    def _parse_spot_em_rows(df: pd.DataFrame) -> list[RankRow]:
        out: list[RankRow] = []
        for _, row in df.iterrows():
            try:
                code = _text(row, "代码")
                price = _float(row, "最新价")
                change_pct = _float(row, "涨跌幅")
                if code is None or price is None or change_pct is None:
                    continue
                out.append(RankRow(
                    symbol=_code_to_symbol(code),
                    name=_text(row, "名称") or code,
                    price=price,
                    change_pct=change_pct,
                    volume=int(_float(row, "成交量") or 0),
                    amount=_float(row, "成交额") or 0.0,
                ))
            except (KeyError, ValueError, TypeError) as e:  # noqa: PERF203
                log.warning("market_query.spot_row_parse_failed", error=str(e))
        return out

    @staticmethod
    def _parse_sector_name_rows(df: pd.DataFrame, *, kind: Literal["industry", "concept"]) -> list[SectorRow]:
        out: list[SectorRow] = []
        for _, row in df.iterrows():
            try:
                name = _text(row, "板块名称")
                if not name:
                    continue
                up_count = int(_float(row, "上涨家数") or 0)
                down_count = int(_float(row, "下跌家数") or 0)
                out.append(SectorRow(
                    code=f"{kind}:{name}",
                    name=name,
                    company_count=up_count + down_count,
                    avg_price=_float(row, "最新价") or 0.0,
                    change_pct=_float(row, "涨跌幅") or 0.0,
                    leader_change_pct=_float(row, "领涨股票-涨跌幅") or 0.0,
                    leader_name=_text(row, "领涨股票") or "",
                    leader_symbol=None,
                ))
            except (KeyError, ValueError, TypeError) as e:  # noqa: PERF203
                log.warning("market_query.sector_row_parse_failed", kind=kind, error=str(e))
        return out

    @staticmethod
    def _parse_hk_spot_rows(df: pd.DataFrame) -> list[RankRow]:
        out: list[RankRow] = []
        for _, row in df.iterrows():
            try:
                code = _text(row, "代码")
                price = _float(row, "最新价")
                change_pct = _float(row, "涨跌幅")
                if code is None or price is None or change_pct is None:
                    continue
                out.append(RankRow(
                    symbol=f"{code.zfill(5)}.HK",
                    name=_text(row, "名称") or code,
                    price=price,
                    change_pct=change_pct,
                    volume=int(_float(row, "成交量") or 0),
                    amount=_float(row, "成交额") or 0.0,
                ))
            except (KeyError, ValueError, TypeError) as e:  # noqa: PERF203
                log.warning("market_query.hk_spot_row_parse_failed", error=str(e))
        return out


def _text(row, key: str) -> str | None:
    value = row.get(key)
    if value is None or pd.isna(value) or str(value) == "-":
        return None
    return str(value).strip()


def _float(row, key: str) -> float | None:
    value = row.get(key)
    if value is None or pd.isna(value) or str(value) == "-":
        return None
    return float(value)


def _code_to_symbol(code: str) -> str:
    code = str(code).strip().zfill(6)
    if code.startswith(("4", "8", "9")):
        return f"{code}.BJ"
    if code.startswith(("6", "5", "11", "13", "68")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _split_sector_code(sector_code: str) -> tuple[Literal["industry", "concept"], str]:
    if ":" not in sector_code:
        return "industry", sector_code
    kind, name = sector_code.split(":", 1)
    if kind == "concept":
        return "concept", name
    return "industry", name
