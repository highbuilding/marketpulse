from datetime import datetime, timedelta, timezone

import pytest

from core.domain.models import IndicatorSignal
from core.persistence.signal_repo import SignalRepo
from core.persistence.sqlite_repo import StateRepo


def _sig(symbol="600519.SH", interval="1d", signal_type="buy",
         bar_ts=None, detected_at=None, price=100.0):
    bar_ts = bar_ts or datetime(2026, 5, 13, tzinfo=timezone.utc)
    return IndicatorSignal(
        symbol=symbol, interval=interval, indicator="CD",
        signal_type=signal_type, bar_ts=bar_ts,
        detected_at=detected_at or bar_ts + timedelta(minutes=5),
        price=price, d_value=-30.0,
    )


@pytest.fixture
async def repo(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return SignalRepo(str(tmp_path / "state.db"))


@pytest.mark.asyncio
async def test_upsert_many_inserts_then_unique_dedups(repo):
    s = _sig()
    n1 = await repo.upsert_many([s])
    assert n1 == 1
    n2 = await repo.upsert_many([s])  # 同 (symbol,interval,indicator,type,bar_ts) — 应被忽略
    assert n2 == 0


@pytest.mark.asyncio
async def test_upsert_many_distinguishes_signal_types(repo):
    bar_ts = datetime(2026, 5, 13, tzinfo=timezone.utc)
    n = await repo.upsert_many([
        _sig(signal_type="buy", bar_ts=bar_ts),
        _sig(signal_type="sell", bar_ts=bar_ts),  # 不同 type, 应入两条
    ])
    assert n == 2


@pytest.mark.asyncio
async def test_list_recent_filters_by_interval_and_unack(repo):
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    await repo.upsert_many([
        _sig(interval="60m", bar_ts=base),
        _sig(interval="4h", bar_ts=base),
        _sig(interval="1d", bar_ts=base),
    ])
    sigs = await repo.list_recent(intervals=["1d", "4h"])
    assert len(sigs) == 2
    assert {s.interval for s in sigs} == {"1d", "4h"}


@pytest.mark.asyncio
async def test_acknowledge_marks_row(repo):
    await repo.upsert_many([_sig()])
    sigs = await repo.list_recent()
    assert sigs[0].acknowledged is False
    await repo.acknowledge(sigs[0].id)
    assert (await repo.count_unacknowledged()) == 0


@pytest.mark.asyncio
async def test_latest_per_symbol_returns_one_per_pair(repo):
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    await repo.upsert_many([
        _sig(symbol="A", interval="1d", bar_ts=base),
        _sig(symbol="A", interval="1d", bar_ts=base + timedelta(days=2)),  # 更新
        _sig(symbol="A", interval="4h", bar_ts=base),
        _sig(symbol="B", interval="1d", bar_ts=base + timedelta(days=1)),
    ])
    latest = await repo.latest_per_symbol(["A", "B"], ["1d", "4h"])
    assert set(latest.keys()) == {("A", "1d"), ("A", "4h"), ("B", "1d")}
    # ("A","1d") 应是更新的那条
    assert latest[("A", "1d")].bar_ts == base + timedelta(days=2)


@pytest.mark.asyncio
async def test_list_by_symbol_orders_recent_first(repo):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await repo.upsert_many([
        _sig(bar_ts=base + timedelta(days=i)) for i in range(5)
    ])
    sigs = await repo.list_by_symbol("600519.SH")
    assert len(sigs) == 5
    assert sigs[0].bar_ts > sigs[-1].bar_ts
