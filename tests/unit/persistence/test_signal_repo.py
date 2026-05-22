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


# ---------- count_by_symbol_interval (通知汇总用) ----------

@pytest.mark.asyncio
async def test_count_by_symbol_interval_filters_by_bar_ts_not_detected_at(repo):
    """关键回归: 历史 bar 在今天才入库时, 不应被算作"今日新信号"。

    场景: 新加 watchlist 的 symbol, 第一次扫描把全部历史信号入库, 所以
    detected_at=今天但 bar_ts=数月前。汇总要按 bar_ts 过滤, 不应计入今日。
    """
    today = datetime(2026, 5, 21, tzinfo=timezone.utc)
    detected_today = today.replace(hour=10)
    # 历史 bar (跨多个周期), detected_at 都是今天
    await repo.upsert_many([
        _sig(symbol="GLD", interval="1d", signal_type="sell",
             bar_ts=datetime(2024, 7, 19, tzinfo=timezone.utc),
             detected_at=detected_today),
        _sig(symbol="GLD", interval="1d", signal_type="sell",
             bar_ts=datetime(2025, 11, 7, tzinfo=timezone.utc),
             detected_at=detected_today),
        # 真正今日的 bar
        _sig(symbol="GLD", interval="1d", signal_type="sell",
             bar_ts=today,
             detected_at=detected_today),
    ])
    counts = await repo.count_by_symbol_interval(["GLD"], today)
    assert counts == {("GLD", "1d", "sell"): 1}, "只应数今日 bar, 历史 bar 必须排除"


@pytest.mark.asyncio
async def test_count_by_symbol_interval_groups_intervals(repo):
    today = datetime(2026, 5, 21, tzinfo=timezone.utc)
    await repo.upsert_many([
        _sig(symbol="X", interval="1d", signal_type="buy", bar_ts=today),
        _sig(symbol="X", interval="60m", signal_type="buy",
             bar_ts=today + timedelta(hours=2)),
        _sig(symbol="X", interval="60m", signal_type="buy",
             bar_ts=today + timedelta(hours=3)),
        _sig(symbol="X", interval="60m", signal_type="sell",
             bar_ts=today + timedelta(hours=4)),
    ])
    counts = await repo.count_by_symbol_interval(["X"], today)
    assert counts == {
        ("X", "1d", "buy"): 1,
        ("X", "60m", "buy"): 2,
        ("X", "60m", "sell"): 1,
    }


@pytest.mark.asyncio
async def test_count_by_symbol_interval_filters_symbols(repo):
    today = datetime(2026, 5, 21, tzinfo=timezone.utc)
    await repo.upsert_many([
        _sig(symbol="A", interval="1d", bar_ts=today),
        _sig(symbol="B", interval="1d", bar_ts=today),
    ])
    # 只查 A
    counts = await repo.count_by_symbol_interval(["A"], today)
    assert ("A", "1d", "buy") in counts
    assert ("B", "1d", "buy") not in counts


@pytest.mark.asyncio
async def test_count_by_symbol_interval_empty_symbols_returns_empty(repo):
    today = datetime(2026, 5, 21, tzinfo=timezone.utc)
    await repo.upsert_many([_sig(bar_ts=today)])
    counts = await repo.count_by_symbol_interval([], today)
    assert counts == {}


# ---------- latest_signals_today (HTML 模板用, 同时拿 count + price) ----------

@pytest.mark.asyncio
async def test_latest_signals_today_returns_count_and_latest_price(repo):
    today = datetime(2026, 5, 21, tzinfo=timezone.utc)
    await repo.upsert_many([
        _sig(symbol="QQQ", interval="60m", signal_type="buy",
             bar_ts=today + timedelta(hours=10), price=497.10),
        _sig(symbol="QQQ", interval="60m", signal_type="buy",
             bar_ts=today + timedelta(hours=14), price=498.50),  # 最新
        _sig(symbol="QQQ", interval="60m", signal_type="buy",
             bar_ts=today + timedelta(hours=12), price=497.80),
    ])
    cells = await repo.latest_signals_today(["QQQ"], today)
    assert ("QQQ", "60m", "buy") in cells
    cell = cells[("QQQ", "60m", "buy")]
    assert cell.count == 3
    assert cell.latest_price == 498.50
    assert cell.latest_bar_ts == today + timedelta(hours=14)


@pytest.mark.asyncio
async def test_latest_signals_today_excludes_history(repo):
    """历史 bar 在 today_start 之前的不计入。"""
    today = datetime(2026, 5, 21, tzinfo=timezone.utc)
    await repo.upsert_many([
        _sig(symbol="X", interval="1d", signal_type="sell",
             bar_ts=datetime(2025, 1, 1, tzinfo=timezone.utc), price=10),
        _sig(symbol="X", interval="1d", signal_type="sell",
             bar_ts=today, price=20),
    ])
    cells = await repo.latest_signals_today(["X"], today)
    assert cells[("X", "1d", "sell")].count == 1
    assert cells[("X", "1d", "sell")].latest_price == 20


@pytest.mark.asyncio
async def test_latest_signals_today_empty_inputs(repo):
    today = datetime(2026, 5, 21, tzinfo=timezone.utc)
    assert await repo.latest_signals_today([], today) == {}


@pytest.mark.asyncio
async def test_latest_signals_today_groups_by_interval_and_type(repo):
    today = datetime(2026, 5, 21, tzinfo=timezone.utc)
    await repo.upsert_many([
        _sig(symbol="A", interval="1d", signal_type="buy",
             bar_ts=today, price=100),
        _sig(symbol="A", interval="60m", signal_type="buy",
             bar_ts=today + timedelta(hours=1), price=101),
        _sig(symbol="A", interval="60m", signal_type="sell",
             bar_ts=today + timedelta(hours=2), price=102),
    ])
    cells = await repo.latest_signals_today(["A"], today)
    assert set(cells.keys()) == {
        ("A", "1d", "buy"),
        ("A", "60m", "buy"),
        ("A", "60m", "sell"),
    }
    assert cells[("A", "1d", "buy")].latest_price == 100
    assert cells[("A", "60m", "buy")].latest_price == 101
    assert cells[("A", "60m", "sell")].latest_price == 102


@pytest.mark.asyncio
async def test_latest_signals_today_collects_trigger_times(repo):
    """trigger_times 应包含当日所有触发 bar_ts, 按时间升序。"""
    today = datetime(2026, 5, 21, tzinfo=timezone.utc)
    bars = [
        today + timedelta(hours=10),
        today + timedelta(hours=14),
        today + timedelta(hours=12),  # 故意不按顺序插入
    ]
    await repo.upsert_many([
        _sig(symbol="QQQ", interval="60m", signal_type="buy",
             bar_ts=ts, price=100 + i)
        for i, ts in enumerate(bars)
    ])
    cells = await repo.latest_signals_today(["QQQ"], today)
    cell = cells[("QQQ", "60m", "buy")]
    assert cell.count == 3
    # 应为升序
    assert list(cell.trigger_times) == sorted(bars)
