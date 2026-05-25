from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain.models import Bar
from core.indicators.cd import CDSignal
from core.services.signal_service import SignalScanService


def _bar(i: int) -> Bar:
    return Bar(
        market="ashare", symbol="600519.SH",
        ts=datetime(2026, 5, 1, tzinfo=timezone.utc),
        open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
        close=Decimal("100"), volume=1000, interval="1d",
    )


@pytest.mark.asyncio
async def test_scan_symbol_writes_signals_through_repo(monkeypatch):
    kline = MagicMock()
    kline.fetch_fresh_bars = AsyncMock(return_value=[_bar(i) for i in range(100)])
    repo = MagicMock()
    repo.upsert_many = AsyncMock(return_value=2)

    fake_signals = [
        CDSignal(bar_ts=datetime(2024, 9, 24, tzinfo=timezone.utc),
                  signal_type="buy", price=1297.16, d_value=-34.0),
        CDSignal(bar_ts=datetime(2024, 3, 18, tzinfo=timezone.utc),
                  signal_type="sell", price=1601.94, d_value=13.0),
    ]
    monkeypatch.setattr(
        "core.services.signal_service.compute_cd_signals",
        lambda bars: fake_signals,
    )

    svc = SignalScanService(kline, repo)
    n = await svc.scan_symbol("600519.SH", "1d")

    assert n == 2
    repo.upsert_many.assert_awaited_once()
    written = repo.upsert_many.call_args.args[0]
    assert {w.signal_type for w in written} == {"buy", "sell"}
    assert all(w.indicator == "CD" for w in written)
    assert all(w.interval == "1d" for w in written)


@pytest.mark.asyncio
async def test_scan_symbol_empty_bars_skips(monkeypatch):
    kline = MagicMock()
    kline.fetch_fresh_bars = AsyncMock(return_value=[])
    repo = MagicMock()
    repo.upsert_many = AsyncMock(return_value=0)
    svc = SignalScanService(kline, repo)
    n = await svc.scan_symbol("X", "1d")
    assert n == 0
    repo.upsert_many.assert_not_called()


@pytest.mark.asyncio
async def test_scan_many_continues_after_per_symbol_error(monkeypatch):
    kline = MagicMock()
    kline.fetch_fresh_bars = AsyncMock(side_effect=[
        Exception("network"),                  # 第一个 symbol 失败
        [_bar(i) for i in range(100)],         # 第二个成功
    ])
    repo = MagicMock()
    repo.upsert_many = AsyncMock(return_value=1)
    monkeypatch.setattr(
        "core.services.signal_service.compute_cd_signals",
        lambda bars: [CDSignal(
            bar_ts=datetime(2024, 9, 24, tzinfo=timezone.utc),
            signal_type="buy", price=100.0, d_value=-1.0,
        )],
    )
    svc = SignalScanService(kline, repo)
    n = await svc.scan_many(["BAD", "GOOD"], "1d")
    assert n == 1  # 仅 GOOD 入库
