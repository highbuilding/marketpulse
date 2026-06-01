"""SSoT: 进行中 bar 桶状态纯函数。A 股 quote_bar_ticker + 美股 bar_ticker 共用。

无市场耦合: BucketState OHLC 攒法 / current_bucket 当前桶定位 / seed_baseline 基线补全。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from core.domain.market_sessions import bucket_grid


@dataclass
class BucketState:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


def update_bucket(state: BucketState | None, price: Decimal, *, volume: int) -> BucketState:
    """用新价更新进行中桶 OHLC。

    state=None 时新建 (open=high=low=close=price)。
    已有 state: open 保持, high=max, low=min, close=price, volume 取传入值(累计口径由调用方算)。
    """
    if state is None:
        return BucketState(open=price, high=price, low=price, close=price, volume=volume)
    return BucketState(
        open=state.open,
        high=max(state.high, price),
        low=min(state.low, price),
        close=price,
        volume=volume,
    )


def current_bucket(
    market: str, now: datetime, interval_min: int,
) -> tuple[datetime, datetime] | None:
    """返回 now 落在的桶 (open_utc, close_utc]; 不在任何桶(休市)返回 None。"""
    grid = bucket_grid(market, now.date(), interval_min)
    for open_utc, close_utc in grid:
        if open_utc <= now < close_utc:
            return (open_utc, close_utc)
    return None


def seed_baseline(bars: list) -> BucketState | None:
    """用更小周期已收线 bar 序列算出当前大桶到目前为止的 OHLC 基线。"""
    if not bars:
        return None
    return BucketState(
        open=bars[0].open,
        high=max(b.high for b in bars),
        low=min(b.low for b in bars),
        close=bars[-1].close,
        volume=sum(int(b.volume) for b in bars),
    )
