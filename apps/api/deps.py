from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from core.adapters.registry import AdapterRegistry, load_sources_config
from core.cache.quote_cache import QuoteCache
from core.notifications import EmailChannel, WeChatChannel
from core.persistence.duckdb_repo import BarRepo
from core.persistence.chip_repo import ChipRepo
from core.persistence.collector_symbol_repo import CollectorSymbolRepo
from core.persistence.fund_flow_repo import FundFlowRepo
from core.persistence.live_message_repo import LiveMessageRepo
from core.persistence.limit_pool_repo import LimitPoolRepo
from core.persistence.notification_repo import NotificationRepo
from core.persistence.position_repo import PositionRepo
from core.persistence.signal_repo import SignalRepo
from core.persistence.sqlite_repo import StateRepo
from core.persistence.sw_industry_repo import SwIndustryRepo
from core.persistence.symbol_directory_repo import SymbolDirectoryRepo
from core.persistence.theme_repo import ThemeRepo
from core.persistence.daily_review_repo import DailyReviewRepo
from core.persistence.watchlist_repo import WatchlistRepo
from core.positions.service import PositionService
from core.services.fund_flow_service import FundFlowService
from core.services.chip_service import ChipService
from core.services.ai_market_service import AIMarketService
from core.services.kline_service import KLineService
from core.services.live_message_service import LiveMessageService
from core.services.limit_pool_service import LimitPoolService
from core.services.market_query import MarketQueryService
from core.services.market_conclusion_service import MarketConclusionService
from core.services.sw_industry_service import SwIndustryService
from core.services.notification_service import NotificationService
from core.services.signal_service import SignalScanService
from core.services.symbol_directory_service import SymbolDirectoryService
from core.services.watchlist_service import WatchlistService
from core.services.volume_indicator_service import VolumeIndicatorService


_BASE = Path(__file__).resolve().parents[2]
_CONFIG = _BASE / "config" / "sources.yaml"
_DATA = Path(os.getenv("APP_DATA_DIR", str(_BASE / "data")))


@lru_cache(maxsize=1)
def get_registry() -> AdapterRegistry:
    return AdapterRegistry.from_config(load_sources_config(str(_CONFIG)))


@lru_cache(maxsize=1)
def get_quote_cache() -> QuoteCache:
    return QuoteCache(ttl_s=60)


# P1: collector 拆 ashare/us/crypto 3 进程后, 每个 collector 在自己的 lifespan
# 通过 set_bar_repo_override(...) 注入本市场专属 bar_repo (bars_{market}.duckdb).
# api 进程不调用 setter → 始终拿 None, 完全脱离 DuckDB.
_BAR_REPO_OVERRIDE: BarRepo | None = None


def set_bar_repo_override(repo: BarRepo | None) -> None:
    """collector 进程在 lifespan 早期调用, 注入本市场专属 BarRepo.
    api 进程绝不调用 → get_bar_repo() 始终 None.
    """
    global _BAR_REPO_OVERRIDE
    _BAR_REPO_OVERRIDE = repo
    # 关键: 清掉所有依赖 get_bar_repo 的下游缓存, 让下次调用看到新 repo
    get_bar_repo.cache_clear()
    get_kline_service.cache_clear()
    get_signal_scan_service.cache_clear()


@lru_cache(maxsize=1)
def get_bar_repo() -> BarRepo | None:
    """api 进程恒返 None (脱离 DuckDB, K 线读路径全走 RedisBarsCache).

    collector 进程在 lifespan 早期 set_bar_repo_override(BarRepo(...))
    注入本市场专属 RW repo.

    历史 read_only 路径保留向后兼容 (warmup / repair script 仍可显式构造 BarRepo).
    """
    return _BAR_REPO_OVERRIDE


# === 历史分页: api 转发到对应市场 collector 的只读接口 ===
# DuckDB 单写多读互斥 → api 绝不直连 DuckDB (会撞锁甚至踢掉 collector 的写)。
# 改为转发到 collector 进程内查询 (它本就持 RW repo, 同进程零锁冲突)。
_COLLECTOR_HOST = os.getenv("COLLECTOR_HOST", "127.0.0.1")
_COLLECTOR_PORTS: dict[str, int] = {
    "ashare": int(os.getenv("COLLECTOR_ASHARE_PORT", "8788")),
    "us": int(os.getenv("COLLECTOR_US_PORT", "8789")),
    "crypto": int(os.getenv("COLLECTOR_CRYPTO_PORT", "8790")),
}


def collector_base_url(market: str) -> str | None:
    port = _COLLECTOR_PORTS.get(market)
    if port is None:
        return None
    return f"http://{_COLLECTOR_HOST}:{port}"


@lru_cache(maxsize=1)
def get_collector_http_client():
    """转发到本机 collector 的 httpx client.

    trust_env=False: 忽略 HTTP_PROXY/HTTPS_PROXY env (项目走 7890 代理),
    localhost collector 调用绝不能经代理。
    """
    import httpx
    # read 超时 15s: 历史查询(limit≤2000)在 collector 满载时可能较慢; 配合
    # collector 侧 to_thread(不冻结事件循环)+ 慢查询日志, 给开盘极端场景留余量。
    # connect 2s 不变: 连不上要快速失败, 不拖前端。
    return httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=2.0), trust_env=False)


@lru_cache(maxsize=1)
def get_redis_bars_cache():  # -> RedisBarsCache
    from core.cache.redis_bars_cache import RedisBarsCache  # noqa: PLC0415
    return RedisBarsCache(get_redis_cache())


@lru_cache(maxsize=1)
def get_state_repo() -> StateRepo:
    return StateRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_kline_service() -> KLineService:
    registry = get_registry()
    adapters = {m: registry.get(m) for m in registry.markets()}
    return KLineService(get_bar_repo(), adapters, redis_bars=get_redis_bars_cache())


@lru_cache(maxsize=1)
def get_watchlist_repo() -> WatchlistRepo:
    return WatchlistRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_watchlist_service() -> WatchlistService:
    return WatchlistService(get_watchlist_repo(), get_collector_symbol_repo())


@lru_cache(maxsize=1)
def get_position_repo() -> PositionRepo:
    return PositionRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_position_service() -> PositionService:
    return PositionService(get_position_repo())


@lru_cache(maxsize=1)
def get_theme_repo() -> ThemeRepo:
    return ThemeRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_collector_symbol_repo() -> CollectorSymbolRepo:
    return CollectorSymbolRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_live_message_repo() -> LiveMessageRepo:
    return LiveMessageRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_limit_pool_repo() -> LimitPoolRepo:
    return LimitPoolRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_limit_pool_service() -> LimitPoolService:
    return LimitPoolService(get_limit_pool_repo())


@lru_cache(maxsize=1)
def get_live_message_service() -> LiveMessageService:
    return LiveMessageService(
        get_theme_repo(),
        get_watchlist_service(),
        get_fund_flow_repo(),
        get_bar_repo(),
        get_symbol_directory_repo(),
    )


@lru_cache(maxsize=1)
def get_fund_flow_repo() -> FundFlowRepo:
    return FundFlowRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_fund_flow_service() -> FundFlowService:
    return FundFlowService(get_fund_flow_repo())


@lru_cache(maxsize=1)
def get_chip_repo() -> ChipRepo:
    return ChipRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_chip_service() -> ChipService:
    return ChipService(get_chip_repo())


@lru_cache(maxsize=1)
def get_volume_indicator_service() -> VolumeIndicatorService:
    return VolumeIndicatorService()


@lru_cache(maxsize=1)
def get_market_query_service() -> MarketQueryService:
    return MarketQueryService()


@lru_cache(maxsize=1)
def get_market_conclusion_service() -> MarketConclusionService:
    return MarketConclusionService(
        get_live_message_repo(),
        get_theme_repo(),
        get_limit_pool_repo(),
        daily_review_repo=get_daily_review_repo(),
    )


@lru_cache(maxsize=1)
def get_sw_industry_repo() -> SwIndustryRepo:
    return SwIndustryRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_sw_industry_service() -> SwIndustryService:
    return SwIndustryService(get_sw_industry_repo())


@lru_cache(maxsize=1)
def get_daily_review_repo() -> DailyReviewRepo:
    return DailyReviewRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_ai_market_service() -> AIMarketService:
    return AIMarketService(
        registry=get_registry(),
        cache=get_quote_cache(),
        watchlist=get_watchlist_service(),
        directory=get_symbol_directory_service(),
        market_query=get_market_query_service(),
    )


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


@lru_cache(maxsize=1)
def get_notification_repo() -> NotificationRepo:
    return NotificationRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_notification_service() -> NotificationService:
    channels = {
        "email": EmailChannel(),
        "wechat": WeChatChannel(),
    }
    return NotificationService(
        notif_repo=get_notification_repo(),
        signal_repo=get_signal_repo(),
        channels=channels,
        directory_service=get_symbol_directory_service(),
    )


# === Redis cache (Plan 1 stage 1) ===
@lru_cache(maxsize=1)
def get_redis_cache():  # -> RedisCache
    from core.cache.redis_client import RedisCache, make_redis  # noqa: PLC0415
    url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    return RedisCache(make_redis(url))
