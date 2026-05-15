"""CD 抄底/卖出指标 — 翻译自 docs/third_Indicator/CD.ftindex。

公式由富途自编指标导出, 基于 MACD 多重背离:
- DXDX(抄底, 红字): close 创新低 + DIF 不创新低 + D<0、M<0 (底背离)
- DBJGXC(卖出, 绿字): close 创新高 + DIF 不创新高 + D>0、M>0 (顶背离)

输入: 按时间正序的 Bar 列表(任意周期 — 60m / 4h / 1d 等)。
输出: 信号事件列表(只在触发那一根 K 线上有一个事件, 后续不重复)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
import pandas as pd

from core.domain.models import Bar

SignalType = Literal["buy", "sell"]


@dataclass(frozen=True, slots=True)
class CDSignal:
    bar_ts: datetime
    signal_type: SignalType
    price: float
    d_value: float  # MACD DIF 值, 调试/展示用


_MIN_BARS = 60  # 公式回看 N1+MM1+MM1 大约 30~50 根, 60 根为稳态下限


def compute_cd_signals(bars: list[Bar]) -> list[CDSignal]:
    """对一组按时间正序的 bar 计算 CD 信号。

    bar 数量不足 _MIN_BARS 时返回空列表(公式回看深度未稳定)。
    """
    if len(bars) < _MIN_BARS:
        return []
    df = _bars_to_df(bars)
    flags = _compute_cd_flags(df)

    signals: list[CDSignal] = []
    closes = df["close"].to_numpy(dtype=float)
    d_vals = flags["D"].to_numpy(dtype=float)
    buy_mask = flags["DXDX"].to_numpy(dtype=bool)
    sell_mask = flags["DBJGXC"].to_numpy(dtype=bool)
    for i, bar in enumerate(bars):
        if buy_mask[i]:
            signals.append(CDSignal(bar.ts, "buy", float(closes[i]), float(d_vals[i])))
        if sell_mask[i]:
            signals.append(CDSignal(bar.ts, "sell", float(closes[i]), float(d_vals[i])))
    return signals


def _bars_to_df(bars: list[Bar]) -> pd.DataFrame:
    return pd.DataFrame({
        "ts": [b.ts for b in bars],
        "close": [float(b.close) for b in bars],
    })


def _compute_cd_flags(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"].astype(float)

    # MACD: D = DIF, A = DEA, M = (DIF-DEA)*2
    d = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    a = d.ewm(span=9, adjust=False).mean()
    m = (d - a) * 2

    # N1: 距上一次 M 由正翻负的根数; MM1: 距上一次 M 由负翻正的根数
    flip_to_neg = (m.shift(1) >= 0) & (m < 0)
    flip_to_pos = (m.shift(1) <= 0) & (m > 0)
    n1 = _barslast(flip_to_neg)
    mm1 = _barslast(flip_to_pos)

    # ---------- 底背离 / 抄底 ----------
    cc1 = _llv_dynamic(c, n1 + 1)
    cc2 = _ref_dynamic(cc1, mm1 + 1)
    cc3 = _ref_dynamic(cc2, mm1 + 1)
    difl1 = _llv_dynamic(d, n1 + 1)
    difl2 = _ref_dynamic(difl1, mm1 + 1)
    difl3 = _ref_dynamic(difl2, mm1 + 1)

    aaa = (cc1 < cc2) & (difl1 > difl2) & (m.shift(1) < 0) & (d < 0)
    bbb = (
        (cc1 < cc3) & (difl1 < difl2) & (difl1 > difl3)
        & (m.shift(1) < 0) & (d < 0)
    )
    ccc = (aaa | bbb) & (d < 0)
    jjj = ccc.shift(1).fillna(False).astype(bool) & (d.shift(1).abs() >= d.abs() * 1.01)
    dxdx = ~jjj.shift(1).fillna(False).astype(bool) & jjj

    # ---------- 顶背离 / 卖出 ----------
    ch1 = _hhv_dynamic(c, mm1 + 1)
    ch2 = _ref_dynamic(ch1, n1 + 1)
    ch3 = _ref_dynamic(ch2, n1 + 1)
    difh1 = _hhv_dynamic(d, mm1 + 1)
    difh2 = _ref_dynamic(difh1, n1 + 1)
    difh3 = _ref_dynamic(difh2, n1 + 1)

    zjdbl = (ch1 > ch2) & (difh1 < difh2) & (m.shift(1) > 0) & (d > 0)
    gxdbl = (
        (ch1 > ch3) & (difh1 > difh2) & (difh1 < difh3)
        & (m.shift(1) > 0) & (d > 0)
    )
    dbbl = (zjdbl | gxdbl) & (d > 0)
    dbjg = dbbl.shift(1).fillna(False).astype(bool) & (d.shift(1) >= d * 1.01)
    dbjgxc = ~dbjg.shift(1).fillna(False).astype(bool) & dbjg

    return pd.DataFrame({
        "D": d,
        "DXDX": dxdx.fillna(False),
        "DBJGXC": dbjgxc.fillna(False),
    })


def _barslast(cond: pd.Series) -> pd.Series:
    """与通达信 BARSLAST 一致: 距离上次 cond=True 的根数, 自身为 True 返回 0。
    历史从未触发返回 len(cond)(用一个大数代替无穷, 让后续 REF 自然落空)。"""
    arr = cond.to_numpy(dtype=bool)
    out = np.full(len(arr), len(arr), dtype=np.int64)
    last = -1
    for i, v in enumerate(arr):
        if v:
            last = i
        if last >= 0:
            out[i] = i - last
    return pd.Series(out, index=cond.index)


def _ref_dynamic(s: pd.Series, n_series: pd.Series) -> pd.Series:
    """REF(x, n) — n 为序列(动态): out[i] = s[i - n[i]], 越界为 NaN。"""
    vals = s.to_numpy(dtype=float)
    ns = n_series.to_numpy()
    out = np.full(len(s), np.nan, dtype=float)
    for i in range(len(s)):
        n = ns[i]
        if not np.isfinite(n):
            continue
        j = i - int(n)
        if 0 <= j < len(s):
            out[i] = vals[j]
    return pd.Series(out, index=s.index)


def _llv_dynamic(s: pd.Series, window_series: pd.Series) -> pd.Series:
    return _rolling_dynamic(s, window_series, np.min)


def _hhv_dynamic(s: pd.Series, window_series: pd.Series) -> pd.Series:
    return _rolling_dynamic(s, window_series, np.max)


def _rolling_dynamic(s: pd.Series, window_series: pd.Series, reducer) -> pd.Series:
    vals = s.to_numpy(dtype=float)
    ws = window_series.to_numpy()
    out = np.full(len(s), np.nan, dtype=float)
    for i in range(len(s)):
        w = ws[i]
        if not np.isfinite(w):
            continue
        w = max(int(w), 1)
        lo = max(0, i - w + 1)
        out[i] = reducer(vals[lo:i + 1])
    return pd.Series(out, index=s.index)
