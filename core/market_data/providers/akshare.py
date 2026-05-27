from __future__ import annotations

from core.services.market_query import MarketQueryService, RankRow, SORT_DIR, SectorRow


class AKShareMarketDataProvider:
    """A 股盘面数据提供层。

    这里只做 AKShare 数据获取与原始结构返回，不做市场强弱判断。
    """

    def __init__(self, query: MarketQueryService) -> None:
        self.query = query

    async def fetch_all_stocks(self, *, limit: int = 5000) -> list[RankRow]:
        return await self.query.all_ashare(limit=limit)

    async def fetch_top_stocks(
        self,
        *,
        direction: SORT_DIR,
        limit: int = 10,
    ) -> list[RankRow]:
        return await self.query.top_ashare(direction=direction, limit=limit)

    async def fetch_sectors(self) -> list[SectorRow]:
        return await self.query.sectors_ashare()

    async def fetch_sector_constituents(
        self,
        sector_code: str,
        *,
        limit: int = 8,
    ) -> list[RankRow]:
        return await self.query.sector_constituents_ashare(sector_code, limit=limit)
