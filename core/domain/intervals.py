"""统一所有 interval 元数据 -- 单一事实源。

之前散在:
- apps/api/routes/cd_signals.py (_SUPPORTED_INTERVALS)
- apps/api/routes/symbols.py (_VALID_INTERVALS)
- apps/api/routes/watchlists.py (_INITIAL_SCAN_INTERVALS)
- core/services/signal_service.py (_LOOKBACK_BARS / _BARS_PER_DAY)
- 前端 3 处 hard-code

加新周期时只改这里, 不再追着 5 个文件改。
前端 apps/web/lib/intervals.ts 是镜像副本, 改这里时记得同步。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IntervalSpec:
    key: str
    label_cn: str
    is_kline: bool          # 是否暴露给 K 线 chart / bars 接口
    is_signal: bool         # 是否会被 CD 扫描
    lookback_bars: int      # 信号扫描回看根数(is_signal=False 时无意义)
    bars_per_day_ashare: int  # A 股一天的根数,用于"回看根数 → 日历天数"换算
    crypto_only: bool       # 历史字段, 已废弃: 4h tab 可见性由前端按 market 控制


_SPECS: list[IntervalSpec] = [
    # K 线分时(信号不扫)
    IntervalSpec("1m",  "分时",   False, False, 0,   240, False),  # 废弃: 分时图取代
    IntervalSpec("5m",  "5分",    True,  False, 0,   48,  False),
    # 信号 + K 线
    IntervalSpec("15m", "15分",   True,  True,  1000, 16, False),
    IntervalSpec("30m", "30分",   True,  True,  500,  8,  False),
    IntervalSpec("60m", "1小时",  True,  True,  400,  4,  False),
    IntervalSpec("4h",  "4小时",  True,  True,  500,  1,  False),
    # 日 / 周 / 月
    IntervalSpec("1d",  "日线",   True,  True,  500,  1,  False),
    IntervalSpec("1wk", "周线",   True,  False, 0,    1,  False),
    IntervalSpec("1mo", "月线",   True,  False, 0,    1,  False),
]

INTERVAL_CONFIG: dict[str, IntervalSpec] = {s.key: s for s in _SPECS}

KLINE_INTERVALS: frozenset[str] = frozenset(s.key for s in _SPECS if s.is_kline)
SIGNAL_INTERVALS: tuple[str, ...] = tuple(s.key for s in _SPECS if s.is_signal)
SIGNAL_INTERVALS_SET: frozenset[str] = frozenset(SIGNAL_INTERVALS)

LOOKBACK_BARS: dict[str, int] = {
    s.key: s.lookback_bars for s in _SPECS if s.is_signal
}
BARS_PER_DAY: dict[str, int] = {
    s.key: s.bars_per_day_ashare for s in _SPECS
}
