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

    async def get_position(self, position_id: int) -> Position | None:
        return await self.repo.get_by_id(position_id)

    async def create_position(self, position: Position) -> int:
        self.ensure_supported_market(position.market)
        return await self.repo.create(position)

    async def update_position(self, position: Position) -> None:
        self.ensure_supported_market(position.market)
        await self.repo.update(position)

    async def close_position(
        self, position_id: int, *, close_price: float | None = None,
        profit_amount: float | None = None, profit_pct: float | None = None,
    ) -> None:
        await self.repo.close(
            position_id, close_price=close_price,
            profit_amount=profit_amount, profit_pct=profit_pct,
        )

    async def delete_position(self, position_id: int) -> None:
        await self.repo.delete(position_id)
