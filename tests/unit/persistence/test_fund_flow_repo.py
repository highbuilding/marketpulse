from datetime import datetime, timedelta, timezone

import pytest

from core.domain.models import FundFlowSnapshot
from core.persistence.fund_flow_repo import FundFlowRepo
from core.persistence.sqlite_repo import StateRepo


def _snap_symbol(sym, ts, main):
    return FundFlowSnapshot(
        subject=sym, kind="symbol", ts=ts, main_net=main,
        super_large_net=main * 0.5, large_net=main * 0.3,
        medium_net=main * 0.1, small_net=main * 0.1,
    )


@pytest.fixture
async def repo(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return FundFlowRepo(str(tmp_path / "state.db"))


@pytest.mark.asyncio
async def test_save_and_query_symbol_flow(repo):
    base = datetime(2026, 5, 13, 9, 30, tzinfo=timezone.utc)
    await repo.save_symbol_flows([
        _snap_symbol("600519.SH", base, 1e7),
        _snap_symbol("600519.SH", base + timedelta(minutes=30), 1.2e7),
    ])
    rows = await repo.query_symbol_flow("600519.SH",
                                         start=base - timedelta(hours=1),
                                         end=base + timedelta(hours=2))
    assert len(rows) == 2
    assert rows[0].main_net == pytest.approx(1e7)


@pytest.mark.asyncio
async def test_latest_symbol_flows_returns_latest_rows_in_window(repo):
    base = datetime(2026, 5, 13, 9, 30, tzinfo=timezone.utc)
    await repo.save_symbol_flows([
        _snap_symbol("600519.SH", base, 1e7),
        _snap_symbol("600519.SH", base + timedelta(minutes=30), 1.2e7),
        _snap_symbol("000858.SZ", base + timedelta(minutes=10), -2e7),
        _snap_symbol("300750.SZ", base - timedelta(days=1), 9e7),
    ])

    rows = await repo.latest_symbol_flows(
        ["600519.SH", "000858.SZ", "300750.SZ"],
        start=base - timedelta(minutes=1),
        end=base + timedelta(hours=1),
    )

    assert set(rows) == {"600519.SH", "000858.SZ"}
    assert rows["600519.SH"].main_net == pytest.approx(1.2e7)
    assert rows["000858.SZ"].main_net == pytest.approx(-2e7)


@pytest.mark.asyncio
async def test_save_north_flow(repo):
    ts = datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc)
    await repo.save_north_flow(FundFlowSnapshot(
        subject="north", kind="north", ts=ts,
        hgt_net=5e8, sgt_net=3e8,
    ))
    flows = await repo.query_north_flow(
        start=ts - timedelta(hours=1), end=ts + timedelta(hours=1),
    )
    assert len(flows) == 1
    assert flows[0].hgt_net == pytest.approx(5e8)


@pytest.mark.asyncio
async def test_save_sector_flow(repo):
    ts = datetime(2026, 5, 13, 10, 5, tzinfo=timezone.utc)
    await repo.save_sector_flows([
        FundFlowSnapshot(subject="玻璃行业", kind="sector", ts=ts,
                         main_net=2e7, pct_change=3.5),
    ])
    rows = await repo.query_sector_flow("玻璃行业",
                                         start=ts - timedelta(hours=1),
                                         end=ts + timedelta(hours=1))
    assert len(rows) == 1
    assert rows[0].pct_change == pytest.approx(3.5)


@pytest.mark.asyncio
async def test_purge_old(repo):
    old = datetime.now(timezone.utc) - timedelta(days=40)
    fresh = datetime.now(timezone.utc) - timedelta(days=5)
    await repo.save_symbol_flows([_snap_symbol("X", old, 1e6),
                                    _snap_symbol("X", fresh, 2e6)])
    deleted = await repo.purge_old_symbol(days=30)
    assert deleted == 1
    rows = await repo.query_symbol_flow("X",
                                         start=old - timedelta(days=1),
                                         end=datetime.now(timezone.utc))
    assert len(rows) == 1
    assert rows[0].ts >= fresh - timedelta(seconds=1)
