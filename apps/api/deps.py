from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from core.adapters.registry import AdapterRegistry, load_sources_config
from core.cache.quote_cache import QuoteCache
from core.persistence.duckdb_repo import BarRepo
from core.persistence.fund_flow_repo import FundFlowRepo
from core.persistence.sector_repo import SectorRepo
from core.persistence.signal_repo import SignalRepo
from core.persistence.sqlite_repo import StateRepo
from core.persistence.symbol_directory_repo import SymbolDirectoryRepo
from core.persistence.watchlist_repo import WatchlistRepo
from core.services.fund_flow_service import FundFlowService
from core.services.kline_service import KLineService
from core.services.sector_service import SectorService
from core.services.signal_service import SignalScanService
from core.services.symbol_directory_service import SymbolDirectoryService
from core.services.watchlist_service import WatchlistService


_BASE = Path(__file__).resolve().parents[2]
_CONFIG = _BASE / "config" / "sources.yaml"
_DATA = Path(os.getenv("APP_DATA_DIR", str(_BASE / "data")))


@lru_cache(maxsize=1)
def get_registry() -> AdapterRegistry:
    return AdapterRegistry.from_config(load_sources_config(str(_CONFIG)))


@lru_cache(maxsize=1)
def get_quote_cache() -> QuoteCache:
    return QuoteCache(ttl_s=60)


@lru_cache(maxsize=1)
def get_bar_repo() -> BarRepo:
    repo = BarRepo(str(_DATA / "bars.duckdb"))
    repo.init()
    return repo


@lru_cache(maxsize=1)
def get_state_repo() -> StateRepo:
    return StateRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_kline_service() -> KLineService:
    registry = get_registry()
    adapters = {m: registry.get(m) for m in registry.markets()}
    # 给 us adapter 注入 dir_repo, 启用 akshare 主源路径(akshare_code 缓存写入用)
    if "us" in adapters:
        adapters["us"].dir_repo = get_symbol_directory_repo()
    return KLineService(get_bar_repo(), adapters)


@lru_cache(maxsize=1)
def get_watchlist_repo() -> WatchlistRepo:
    return WatchlistRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_watchlist_service() -> WatchlistService:
    return WatchlistService(get_watchlist_repo())


@lru_cache(maxsize=1)
def get_sector_repo() -> SectorRepo:
    return SectorRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_sector_service() -> SectorService:
    return SectorService(get_sector_repo())


@lru_cache(maxsize=1)
def get_fund_flow_repo() -> FundFlowRepo:
    return FundFlowRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_fund_flow_service() -> FundFlowService:
    return FundFlowService(get_fund_flow_repo())


@lru_cache(maxsize=1)
def get_symbol_directory_repo() -> SymbolDirectoryRepo:
    return SymbolDirectoryRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_symbol_directory_service() -> SymbolDirectoryService:
    return SymbolDirectoryService(get_symbol_directory_repo())


@lru_cache(maxsize=1)
def get_signal_repo() -> SignalRepo:
    return SignalRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_signal_scan_service() -> SignalScanService:
    return SignalScanService(get_kline_service(), get_signal_repo())
