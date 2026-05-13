from __future__ import annotations

import asyncio

import akshare as ak
import structlog

from core.domain.models import Sector
from core.persistence.sector_repo import SectorRepo

log = structlog.get_logger(__name__)


def _to_symbol(sina_code: str) -> str:
    """sh600660 → 600660.SH, sz000012 → 000012.SZ, bj920001 → 920001.BJ"""
    if len(sina_code) < 3:
        return sina_code
    mkt = sina_code[:2].upper()
    code = sina_code[2:]
    return f"{code}.{mkt}" if mkt in {"SH", "SZ", "BJ"} else sina_code


class SectorService:
    def __init__(self, repo: SectorRepo) -> None:
        self.repo = repo

    async def refresh_sector(self, label: str, display_name: str) -> int:
        df = await asyncio.to_thread(ak.stock_sector_detail, sector=label)
        symbols = [_to_symbol(str(c)) for c in df["代码"].tolist()]
        await self.repo.upsert_sector(display_name, "sina", symbols)
        return len(symbols)

    async def refresh_all_sina(self) -> int:
        spot = await asyncio.to_thread(ak.stock_sector_spot, indicator="新浪行业")
        total = 0
        for _, row in spot.iterrows():
            label = str(row["label"])
            name = str(row["板块"])
            try:
                total += await self.refresh_sector(label, name)
            except Exception as e:  # noqa: BLE001
                log.warning("sector.refresh_failed", label=label, name=name, error=str(e))
        return total

    async def list_sectors(self) -> list[Sector]:
        return await self.repo.list_sectors()

    async def list_constituents(self, sector_name: str) -> list[str]:
        return await self.repo.list_constituents(sector_name)

    async def sectors_of(self, symbol: str) -> list[str]:
        return await self.repo.sectors_of_symbol(symbol)
