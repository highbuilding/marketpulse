from __future__ import annotations

from core.domain.models import Position
from core.persistence.position_repo import PositionRepo


SUPPORTED_MARKETS = {"ashare"}


class PositionService:
    def __init__(self, repo: PositionRepo) -> None:
        self.repo = repo

    def ensure_supported_market(self, market: str) -> None:
        if market not in SUPPORTED_MARKETS:
            raise ValueError(f"positions unsupported for market: {market}")

    async def list_positions(
        self, market: str, *, include_closed: bool = False,
    ) -> list[Position]:
        self.ensure_supported_market(market)
        return await self.repo.list_by_market(market, include_closed=include_closed)

    async def get_position(self, market: str, symbol: str) -> Position | None:
        self.ensure_supported_market(market)
        return await self.repo.get(market, symbol)

    async def upsert_position(self, position: Position) -> int:
        self.ensure_supported_market(position.market)
        return await self.repo.upsert(position)

    async def close_position(self, market: str, symbol: str) -> None:
        self.ensure_supported_market(market)
        await self.repo.close(market, symbol)
