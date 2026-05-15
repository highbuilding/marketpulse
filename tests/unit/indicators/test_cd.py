from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from core.domain.models import Bar
from core.indicators.cd import CDSignal, compute_cd_signals

FIXTURE = Path(__file__).parent / "fixtures" / "600519_daily.csv"


def _load_bars(symbol: str = "600519.SH") -> list[Bar]:
    df = pd.read_csv(FIXTURE, parse_dates=["date"])
    return [
        Bar(
            market="ashare", symbol=symbol,
            ts=row["date"].to_pydatetime().replace(tzinfo=timezone.utc),
            open=Decimal(str(row["open"])), high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])), close=Decimal(str(row["close"])),
            volume=int(row["volume"]), interval="1d",
        )
        for _, row in df.iterrows()
    ]


# 由 POC 跑出, 后续需对照富途客户端逐一核对。
# 不匹配时调整公式 + 同步这里。
EXPECTED_BUYS_600519 = [
    "2023-04-17", "2023-04-25", "2023-05-22",
    "2024-01-19", "2024-01-24",
    "2024-07-31", "2024-08-05", "2024-09-24",
    "2025-02-07", "2025-02-12",
]
EXPECTED_SELLS_600519 = [
    "2024-03-18",
]


def test_compute_cd_signals_600519_daily_matches_expected():
    bars = _load_bars()
    signals = compute_cd_signals(bars)

    buys = [s.bar_ts.date().isoformat() for s in signals if s.signal_type == "buy"]
    sells = [s.bar_ts.date().isoformat() for s in signals if s.signal_type == "sell"]

    assert buys == EXPECTED_BUYS_600519, (
        f"抄底信号不匹配\n  期望: {EXPECTED_BUYS_600519}\n  实际: {buys}"
    )
    assert sells == EXPECTED_SELLS_600519, (
        f"卖出信号不匹配\n  期望: {EXPECTED_SELLS_600519}\n  实际: {sells}"
    )


def test_compute_cd_signals_returns_empty_when_bars_too_few():
    bars = _load_bars()[:20]
    assert compute_cd_signals(bars) == []


def test_compute_cd_signals_price_matches_bar_close():
    bars = _load_bars()
    by_ts = {b.ts: b for b in bars}
    for sig in compute_cd_signals(bars):
        assert sig.price == pytest.approx(float(by_ts[sig.bar_ts].close), rel=1e-9)


def test_cd_signal_dataclass_is_frozen():
    sig = CDSignal(
        bar_ts=datetime(2024, 9, 24, tzinfo=timezone.utc),
        signal_type="buy", price=1297.16, d_value=-34.058,
    )
    with pytest.raises((AttributeError, Exception)):
        sig.price = 0.0  # type: ignore[misc]
