from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from core.domain.models import Bar

_MARKET_TZ = {
    "ashare": ZoneInfo("Asia/Shanghai"),
    "hk": ZoneInfo("Asia/Hong_Kong"),
    "us": ZoneInfo("America/New_York"),
    "crypto": ZoneInfo("UTC"),
}


@dataclass(frozen=True, slots=True)
class VolumeIndicatorRow:
    ts: datetime
    volume: int
    amount: float | None
    turnover: float | None
    vol_ma5: float | None
    vol_ma20: float | None
    amount_ma20: float | None
    volume_ratio: float | None
    single_bar_volume_ratio: float | None
    obv: float
    is_volume_breakout: bool
    is_shrink_pullback: bool


class VolumeIndicatorService:
    def compute(self, bars: list[Bar]) -> list[VolumeIndicatorRow]:
        if not bars:
            return []
        out: list[VolumeIndicatorRow] = []
        obv = 0.0
        for i, bar in enumerate(bars):
            if i == 0:
                obv = float(bar.volume)
            else:
                prev = bars[i - 1]
                if bar.close > prev.close:
                    obv += float(bar.volume)
                elif bar.close < prev.close:
                    obv -= float(bar.volume)

            vol_ma5 = _avg([float(b.volume) for b in bars[max(0, i - 4):i + 1]], 5)
            vol_ma20 = _avg([float(b.volume) for b in bars[max(0, i - 19):i + 1]], 20)
            amount_ma20 = _avg(
                [b.amount for b in bars[max(0, i - 19):i + 1] if b.amount is not None],
                20,
            )
            prev20_avg = _avg([float(b.volume) for b in bars[max(0, i - 20):i]], 20)
            single_bar_volume_ratio = (
                float(bar.volume) / prev20_avg
                if prev20_avg is not None and prev20_avg > 0 else None
            )
            volume_ratio = _volume_speed_ratio(bars, i)
            prev_close = bars[i - 1].close if i > 0 else bar.open
            pct = (float(bar.close - prev_close) / float(prev_close) * 100) if prev_close else 0.0

            out.append(VolumeIndicatorRow(
                ts=bar.ts,
                volume=bar.volume,
                amount=bar.amount,
                turnover=bar.turnover,
                vol_ma5=vol_ma5,
                vol_ma20=vol_ma20,
                amount_ma20=amount_ma20,
                volume_ratio=volume_ratio,
                single_bar_volume_ratio=single_bar_volume_ratio,
                obv=obv,
                is_volume_breakout=bool(
                    single_bar_volume_ratio is not None
                    and single_bar_volume_ratio >= 1.5
                    and pct > 0
                ),
                is_shrink_pullback=bool(
                    single_bar_volume_ratio is not None
                    and single_bar_volume_ratio <= 0.7
                    and pct < 0
                ),
            ))
        return out


def _avg(values: list[float | None], expected: int) -> float | None:
    vals = [float(v) for v in values if v is not None]
    if len(vals) < expected:
        return None
    return sum(vals[-expected:]) / expected


def _volume_speed_ratio(bars: list[Bar], index: int) -> float | None:
    """券商常见量比口径。

    - 日线:当前日成交量 / 前 5 个交易日平均成交量。
    - 分钟线:当日截至当前累计成交量 / 过去 5 个交易日同进度平均累计成交量。
    """
    bar = bars[index]
    if bar.interval == "1d":
        prev5 = [float(b.volume) for b in bars[max(0, index - 5):index]]
        avg_prev5 = _avg(prev5, 5)
        return float(bar.volume) / avg_prev5 if avg_prev5 and avg_prev5 > 0 else None

    tz = _MARKET_TZ.get(bar.market, ZoneInfo("UTC"))
    current_date = bar.ts.astimezone(tz).date()
    current_day_indices = [
        j for j in range(0, index + 1)
        if bars[j].ts.astimezone(tz).date() == current_date
    ]
    elapsed = len(current_day_indices)
    if elapsed <= 0:
        return None
    current_cumulative = sum(float(bars[j].volume) for j in current_day_indices)

    previous_dates: list[date] = []
    for b in reversed(bars[:index]):
        d = b.ts.astimezone(tz).date()
        if d == current_date or d in previous_dates:
            continue
        previous_dates.append(d)
        if len(previous_dates) >= 5:
            break
    if len(previous_dates) < 5:
        return None

    comparable: list[float] = []
    for d in previous_dates:
        day_bars = [b for b in bars[:index] if b.ts.astimezone(tz).date() == d]
        if len(day_bars) >= elapsed:
            comparable.append(sum(float(b.volume) for b in day_bars[:elapsed]))
    avg_comparable = _avg(comparable, 5)
    return current_cumulative / avg_comparable if avg_comparable and avg_comparable > 0 else None
