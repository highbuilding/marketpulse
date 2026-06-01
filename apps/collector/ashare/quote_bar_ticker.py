"""A 股 K 线进行中态组件 (quote 驱动).

每 10s 读最新 quote, 对每个被 SSE 订阅的 (symbol, interval), 维护当前未收线桶的
进行中 bar (high/low/close 随 quote 跳), 推 final=false + 写 :current, 不入库。
收线由源头采集 (bar_poller) 负责。对齐 crypto 的进行中态体验。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class BucketState:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


def update_bucket(state: BucketState | None, price: Decimal, *, volume: int) -> BucketState:
    """用新 quote 价更新进行中桶 OHLC。

    state=None 时新建桶 (open=high=low=close=price)。
    已有 state 时: open 保持, high=max, low=min, close=price, volume 取累计最新值。
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
