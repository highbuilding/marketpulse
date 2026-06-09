from __future__ import annotations

from core.domain.core_symbols import core_symbols
from core.domain.markets import infer_market
from core.domain.models import Watchlist
from core.persistence.watchlist_repo import WatchlistRepo


_DEFAULT_NAME = "我的关注"


class SymbolNotCollectedError(ValueError):
    """自选标的必须在采集集(CORE)内 —— watchlist ⊆ 采集集。

    采集集是权威全集(bar_poller/reconcile/cron 采的); watchlist 是用户从中挑的子集。
    加非采集集标的会导致'有现价(tick含全集?)无K线(poller只采CORE)'的非法状态。
    """


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
        # watchlist ⊆ 采集集: 只能加 CORE 内标的(按其市场判定), 否则非法状态
        # (有现价无K线)。crypto 等无 CORE 概念的市场不校验(core_symbols 返回其全集)。
        symbol = symbol.strip().upper()
        mkt = infer_market(symbol)
        core = set(core_symbols(mkt))
        if core and symbol not in core:
            raise SymbolNotCollectedError(
                f"{symbol} 不在采集集内, 无法加入自选(自选只能从采集列表选)")
        await self.repo.add_symbol(wl_id, symbol)

    async def remove_symbol(self, wl_id: int, symbol: str) -> None:
        await self.repo.remove_symbol(wl_id, symbol)

    async def list_symbols(self, wl_id: int) -> list[str]:
        return await self.repo.list_symbols(wl_id)

    async def dynamic_universe(self) -> list[str]:
        return await self.repo.all_active_symbols()
