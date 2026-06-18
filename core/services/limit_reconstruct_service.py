"""从日线 OHLC 价格反推涨停生态历史 (limit_pool_daily)。

EM 涨停池接口只服务最近 ~3 周, 无法回填长历史; 但我们有 2019 至今的日线,
可据 收盘≈涨停价且封死=涨停 / 摸板未封=炸板 / 跌停 / 连续涨停=连板 反推。

边界(诚实声明):
- 只覆盖**已采集的标的**(非全市场), 故"市场涨停总数/晋级率"等宽度指标会偏低;
  每只股票的"昨板"信号准确, 适合 previous_limit_strong 的入场条件回测。
- ST(±5%)无法从日线判别, 按板块默认涨跌幅, ST 会漏标(可接受)。
- 封单/封板时间等盘中字段无法反推, 留空。

涨停判定 = `收盘/昨收-1 >= 涨幅-0.5%` 且 `收盘≈最高`(封死); 炸板 = 摸板但未封死。
"""
from __future__ import annotations

import pandas as pd

from core.domain.models import LimitPoolItem

_TOL = 0.005  # 涨跌幅判定容差(相对昨收)


def board_limit_pct(symbol: str) -> float:
    """各板涨跌幅限制。688 科创/30 创业=20%; 8/4/920 北交所=30%; 其余主板=10%。"""
    code = symbol.split(".")[0]
    if code.startswith("688") or code.startswith("30"):
        return 0.20
    if code.startswith(("8", "4", "920")):
        return 0.30
    return 0.10


def _num(v) -> float | None:
    try:
        f = float(v)
        return f if f == f else None  # NaN check
    except (TypeError, ValueError):
        return None


def reconstruct_from_bars(bars: pd.DataFrame, *, market: str = "ashare") -> list[LimitPoolItem]:
    """从长表日线 bars(列: symbol, ts, open, high, low, close, amount)反推涨停生态。

    返回 LimitPoolItem 列表(pool_type: limit_up / broken_limit / down_limit / previous)。
    """
    if bars is None or bars.empty:
        return []
    df = bars.copy()
    df["trade_date"] = (
        pd.to_datetime(df["ts"], utc=True).dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d")
    )
    has_amount = "amount" in df.columns
    items: list[LimitPoolItem] = []
    for symbol, g in df.groupby("symbol", sort=False):
        g = g.sort_values("trade_date")
        limit_pct = board_limit_pct(str(symbol))
        closes = g["close"].tolist()
        highs = g["high"].tolist()
        lows = g["low"].tolist()
        amounts = g["amount"].tolist() if has_amount else [None] * len(g)
        dates = g["trade_date"].tolist()
        prev_close: float | None = None
        prev_is_lu = False
        prev_ladder = 0
        for i in range(len(g)):
            c = _num(closes[i]); h = _num(highs[i]); lo = _num(lows[i])
            if c is None or h is None or lo is None or c <= 0:
                prev_close = c; prev_is_lu = False; prev_ladder = 0
                continue
            if prev_close is None or prev_close <= 0:
                prev_close = c; prev_is_lu = False; prev_ladder = 0
                continue
            ratio = c / prev_close - 1
            closed_at_high = (h - c) <= max(0.01, c * 0.002)
            closed_at_low = (c - lo) <= max(0.01, c * 0.002)
            touched_up = (h / prev_close - 1) >= limit_pct - _TOL
            is_lu = ratio >= limit_pct - _TOL and closed_at_high
            is_broken = touched_up and not is_lu
            is_dl = ratio <= -(limit_pct) + _TOL and closed_at_low
            ladder = prev_ladder + 1 if is_lu else 0
            change_pct = ratio * 100
            limit_price = round(prev_close * (1 + limit_pct), 2)
            amt = _num(amounts[i])
            common = dict(
                market=market, trade_date=dates[i], symbol=str(symbol),
                change_pct=change_pct, price=c, amount=amt, raw={"derived": True},
            )
            if is_lu:
                items.append(LimitPoolItem(pool_type="limit_up", limit_price=limit_price,
                                           ladder_count=ladder, **common))
            elif is_broken:
                items.append(LimitPoolItem(pool_type="broken_limit", limit_price=limit_price,
                                           **common))
            elif is_dl:
                items.append(LimitPoolItem(pool_type="down_limit", **common))
            # 昨板今表现: 昨日涨停 → 今日这行记为 previous(change_pct=今日, ladder=昨日连板)
            if prev_is_lu:
                items.append(LimitPoolItem(pool_type="previous", limit_price=limit_price,
                                           ladder_count=prev_ladder, **common))
            prev_close = c; prev_is_lu = is_lu; prev_ladder = ladder
    return items
