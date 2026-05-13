"""全市场行情/板块查询(独立于 Adapter 的 universe 快照,用于 dashboard)。

数据走 sina vip.stock.finance.sina.com.cn 与 akshare sina 行业通道。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Literal

import akshare as ak
import requests
import structlog

from core.services._locks import mini_racer_lock

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
    name: str
    change_pct: float
    avg_price: float
    company_count: int
    leader_name: str
    leader_change_pct: float


def _make_session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.proxies = {}
    s.headers.update({
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    })
    return s


_A_RANK_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
_HK_RANK_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHKStockData"
)


class MarketQueryService:
    def __init__(self) -> None:
        self._session = _make_session()

    async def top_ashare(self, direction: SORT_DIR = "desc", limit: int = 10) -> list[RankRow]:
        return await asyncio.to_thread(self._top_ashare_sync, direction, limit)

    def _top_ashare_sync(self, direction: SORT_DIR, limit: int) -> list[RankRow]:
        r = self._session.get(_A_RANK_URL, params={
            "page": 1, "num": limit,
            "sort": "changepercent",
            "asc": 0 if direction == "desc" else 1,
            "node": "hs_a", "_s_r_a": "page",
        }, timeout=5)
        r.raise_for_status()
        data = json.loads(r.text)
        out: list[RankRow] = []
        for row in data:
            try:
                out.append(RankRow(
                    symbol=_sina_to_symbol(row["symbol"]),
                    name=row["name"],
                    price=float(row["trade"]),
                    change_pct=float(row["changepercent"]),
                    volume=int(row["volume"]),
                    amount=float(row["amount"]),
                ))
            except (KeyError, ValueError, TypeError) as e:  # noqa: PERF203
                log.warning("top_ashare.parse_row_failed", error=str(e))
        return out

    async def top_hk(self, direction: SORT_DIR = "desc", limit: int = 10) -> list[RankRow]:
        return await asyncio.to_thread(self._top_hk_sync, direction, limit)

    def _top_hk_sync(self, direction: SORT_DIR, limit: int) -> list[RankRow]:
        r = self._session.get(_HK_RANK_URL, params={
            "page": 1, "num": limit,
            "sort": "changepercent",
            "asc": 0 if direction == "desc" else 1,
            "node": "qbgg_hk",
        }, timeout=5)
        r.raise_for_status()
        data = json.loads(r.text)
        out: list[RankRow] = []
        for row in data:
            try:
                out.append(RankRow(
                    symbol=f"{row['symbol'].zfill(5)}.HK",
                    name=row["name"],
                    price=float(row["lasttrade"]),
                    change_pct=float(row["changepercent"]),
                    volume=int(row["volume"]) if row.get("volume") else 0,
                    amount=float(row["amount"]) if row.get("amount") else 0.0,
                ))
            except (KeyError, ValueError, TypeError) as e:
                log.warning("top_hk.parse_row_failed", error=str(e))
        return out

    async def sectors_ashare(self) -> list[SectorRow]:
        async with mini_racer_lock:
            return await asyncio.to_thread(self._sectors_ashare_sync)

    def _sectors_ashare_sync(self) -> list[SectorRow]:
        df = ak.stock_sector_spot(indicator="新浪行业")
        out: list[SectorRow] = []
        for _, row in df.iterrows():
            try:
                out.append(SectorRow(
                    name=str(row["板块"]),
                    change_pct=float(row["涨跌幅"]),
                    avg_price=float(row["平均价格"]),
                    company_count=int(row["公司家数"]),
                    leader_name=str(row["股票名称"]),
                    leader_change_pct=float(row["个股-涨跌幅"]),
                ))
            except (KeyError, ValueError, TypeError) as e:
                log.warning("sectors_ashare.parse_row_failed", error=str(e))
        return out


def _sina_to_symbol(sina_code: str) -> str:
    """sh600519 → 600519.SH; sz000858 → 000858.SZ; bj920469 → 920469.BJ."""
    if len(sina_code) < 3:
        return sina_code
    mkt = sina_code[:2].upper()
    code = sina_code[2:]
    if mkt in {"SH", "SZ", "BJ"}:
        return f"{code}.{mkt}"
    return sina_code
