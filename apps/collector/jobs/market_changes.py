"""A 股盘口异动 + 板块异动采集 job (collector ashare, leader-gated)。

雷区: stock_changes_em / stock_board_change_em 是东财 EM bulk 接口。
- changes 每类型返回聚焦列表 (几百行), 每轮 ~8 类型, 比 stock_zh_a_spot_em(5000) 轻。
- board_change 较重 (~1000 板块), 低频刷。
任何失败只 warning, 不影响主行情采集 (规范 5)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.domain.market_calendar import is_trading_day
from core.services.market_changes_service import CHANGE_CATEGORY, MarketChangesService

log = structlog.get_logger(__name__)
_BJT = ZoneInfo("Asia/Shanghai")
_CHANGES_TTL_S = 180
_BOARD_TTL_S = 300


def _in_window(now: datetime, start_hm: int, end_hm: int) -> bool:
    """now 是否在 [start, end] BJT 分钟窗口内 (HHMM 用 时*60+分 表示)。"""
    hm = now.hour * 60 + now.minute
    return start_hm <= hm <= end_hm


async def refresh_ashare_changes(
    service: MarketChangesService, cache: RedisCache,
) -> None:
    """拉个股盘口异动 → 落库 → 写 Redis 快照 (最近 200 条 + 按类型计数)。"""
    now = datetime.now(_BJT)
    # 竞价异动从 09:15 起, 尾盘封板到 15:00; 窗口外不打 EM。
    if not is_trading_day("ashare", now) or not _in_window(now, 9 * 60 + 15, 15 * 60 + 2):
        return
    trade_date = now.date().isoformat()
    try:
        result = await service.pull_changes(trade_date)
        items = await service.list_changes("ashare", trade_date, limit=200)
        counts = await service.count_by_type("ashare", trade_date)
        payload = {
            "changes": [
                {
                    "time": it.change_time,
                    "symbol": it.symbol,
                    "name": it.name,
                    "change_type": it.change_type,
                    "category": CHANGE_CATEGORY.get(it.change_type),
                    "price": it.price,
                    "change_pct": it.change_pct,
                }
                for it in items
            ],
            "counts": counts,
            "meta": {"fresh_at": datetime.now(timezone.utc).isoformat(),
                     "trade_date": trade_date},
        }
        await cache.set_msgpack(keys.cache_market_changes("ashare"), payload,
                               ttl_s=_CHANGES_TTL_S)
        log.info("market_changes.refresh_done", trade_date=trade_date,
                 pulled=sum(result.values()), cached=len(items))
    except Exception as e:  # noqa: BLE001
        log.warning("market_changes.refresh_failed", trade_date=trade_date, error=str(e))


async def refresh_ashare_board_changes(
    service: MarketChangesService, cache: RedisCache,
) -> None:
    """拉板块异动 → 落库 → 写 Redis 快照 (Top 50 按异动次数)。"""
    now = datetime.now(_BJT)
    if not is_trading_day("ashare", now) or not _in_window(now, 9 * 60 + 30, 15 * 60 + 2):
        return
    trade_date = now.date().isoformat()
    try:
        n = await service.pull_board_changes(trade_date)
        boards = await service.list_board_changes("ashare", trade_date, limit=50)
        payload = {
            "boards": [
                {
                    "board_name": b.board_name,
                    "change_pct": b.change_pct,
                    "main_net_inflow": b.main_net_inflow,
                    "change_total": b.change_total,
                    "top_symbol": b.top_symbol,
                    "top_name": b.top_name,
                    "top_direction": b.top_direction,
                }
                for b in boards
            ],
            "meta": {"fresh_at": datetime.now(timezone.utc).isoformat(),
                     "trade_date": trade_date},
        }
        await cache.set_msgpack(keys.cache_market_board_changes("ashare"), payload,
                               ttl_s=_BOARD_TTL_S)
        log.info("market_changes.board_done", trade_date=trade_date, pulled=n,
                 cached=len(boards))
    except Exception as e:  # noqa: BLE001
        log.warning("market_changes.board_refresh_failed", trade_date=trade_date,
                    error=str(e))
