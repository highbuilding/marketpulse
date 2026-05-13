from __future__ import annotations

from core.domain.models import Watchlist
from core.persistence.watchlist_repo import WatchlistRepo


_DEFAULT_NAME = "我的关注"


class WatchlistService:
    def __init__(self, repo: WatchlistRepo) -> None:
        self.repo = repo

    async def bootstrap_default(self) -> None:
        existing = await self.repo.list_watchlists(include_archived=True)
        if any(w.name == _DEFAULT_NAME for w in existing):
            return
        await self.repo.create_watchlist(_DEFAULT_NAME)

    async def list_all(self, include_archived: bool = False) -> list[Watchlist]:
        return await self.repo.list_watchlists(include_archived=include_archived)

    async def create(self, name: str) -> int:
        return await self.repo.create_watchlist(name)

    async def rename(self, wl_id: int, new_name: str) -> None:
        await self.repo.rename_watchlist(wl_id, new_name)

    async def archive(self, wl_id: int) -> None:
        await self.repo.archive_watchlist(wl_id)

    async def add_symbol(self, wl_id: int, symbol: str) -> None:
        await self.repo.add_symbol(wl_id, symbol)

    async def remove_symbol(self, wl_id: int, symbol: str) -> None:
        await self.repo.remove_symbol(wl_id, symbol)

    async def list_symbols(self, wl_id: int) -> list[str]:
        return await self.repo.list_symbols(wl_id)

    async def dynamic_universe(self) -> list[str]:
        return await self.repo.all_active_symbols()
