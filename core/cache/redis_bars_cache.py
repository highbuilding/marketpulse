"""Redis bars 缓存 — api 进程只读路径,绕开 DuckDB 跨进程锁。

设计:
- collector 在 BarRepo.insert_bars 之后调 upsert_tail() 写一份 tail (最近 N 根) 到 Redis
- api 进程的 KLineService.get_bars_cache_only() 直接读 Redis, 不再碰 DuckDB
- key: cache:bars:{market}:{symbol}:{interval}:tail (keys.py 已预留)
- value: msgpack list[dict], 每根 dict 用 isoformat ts + float 数字 (avoid Decimal)

为什么 tail 而非 full:
- 详情页 K 线最多需要近 6 年(daily) / 近 1 月(intraday) → 5 年 daily 1500 根, intraday 几百根
- Redis 存全量历史不划算;但 tail = 近 200 根足够覆盖 95% 的前端展示窗口
- cache miss 时 api 路由发 bus:bars.refill_request, collector refill 后重写 cache

雷区: BarRepo.insert_bars 用 ON CONFLICT DO UPDATE, 同一根 ts 写多次都是同一根
→ Redis tail 也按 ts 去重 (内部用 dict[ts] = bar 合并)。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §2.1
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.domain.models import Bar, Market

log = structlog.get_logger(__name__)

# tail 长度: daily 覆盖 ~6 年交易日 (1500), intraday 覆盖近 30 个交易日 (300+)
# 取 2000 上限统一处理, intraday 实际不会到这么多
_TAIL_MAX = 2000
# Redis tail 不主动 expire — 每次 collector 写入都覆盖, 由 LRU 兜底
# (Redis 内存压力下被 evict 也无害, api 走 bus:refill 兜底)
_TAIL_TTL_S = 7 * 24 * 3600


def _bar_to_dict(b: Bar) -> dict[str, Any]:
    return {
        "ts": b.ts.astimezone(timezone.utc).isoformat(),
        "open": float(b.open),
        "high": float(b.high),
        "low": float(b.low),
        "close": float(b.close),
        "volume": int(b.volume),
        "interval": b.interval,
        "amount": b.amount,
        "turnover": b.turnover,
        "outstanding_share": b.outstanding_share,
    }


def _dict_to_bar(d: dict[str, Any], market: Market, symbol: str) -> Bar:
    return Bar(
        market=market,
        symbol=symbol,
        ts=datetime.fromisoformat(d["ts"]),
        open=Decimal(str(d["open"])),
        high=Decimal(str(d["high"])),
        low=Decimal(str(d["low"])),
        close=Decimal(str(d["close"])),
        volume=int(d["volume"]),
        interval=d.get("interval", "1d"),
        amount=d.get("amount"),
        turnover=d.get("turnover"),
        outstanding_share=d.get("outstanding_share"),
    )


class RedisBarsCache:
    """K 线 Redis tail 读写。collector 写 + api 读 都走这里。"""

    def __init__(self, cache: RedisCache) -> None:
        self._cache = cache

    async def upsert_tail(
        self, market: str, symbol: str, interval: str, bars: list[Bar],
    ) -> None:
        """把 bars 合并进 Redis tail。失败仅 warning, 不抛(优雅降级)。"""
        if not bars:
            return
        key = keys.cache_bars_tail(market, symbol, interval)
        try:
            existing = await self._cache.get_msgpack(key) or []
        except Exception as e:  # noqa: BLE001
            log.warning("redis_bars.read_failed", key=key, error=str(e))
            existing = []
        # 合并: ts 去重 (后写覆盖, 同 BarRepo ON CONFLICT DO UPDATE 语义)
        by_ts: dict[str, dict] = {d["ts"]: d for d in existing}
        for b in bars:
            d = _bar_to_dict(b)
            by_ts[d["ts"]] = d
        # 排序 + tail 截断
        merged = sorted(by_ts.values(), key=lambda d: d["ts"])
        if len(merged) > _TAIL_MAX:
            merged = merged[-_TAIL_MAX:]
        try:
            await self._cache.set_msgpack(key, merged, ttl_s=_TAIL_TTL_S)
        except Exception as e:  # noqa: BLE001
            log.warning("redis_bars.write_failed", key=key, error=str(e))

    async def get_tail(
        self, market: Market, symbol: str, interval: str,
        start: datetime, end: datetime,
    ) -> list[Bar]:
        """读 tail 并按 [start, end] 过滤。空返 []。"""
        key = keys.cache_bars_tail(market, symbol, interval)
        try:
            payload = await self._cache.get_msgpack(key)
        except Exception as e:  # noqa: BLE001
            log.warning("redis_bars.read_failed", key=key, error=str(e))
            return []
        if not payload:
            return []
        out: list[Bar] = []
        for d in payload:
            try:
                bar = _dict_to_bar(d, market, symbol)
            except Exception:  # noqa: BLE001
                continue
            if start <= bar.ts <= end:
                out.append(bar)
        return out
