from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo


def _bar(market, symbol, day_offset, close):
    ts = datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(days=day_offset)
    return Bar(
        market=market, symbol=symbol, ts=ts,
        open=Decimal("1"), high=Decimal("2"), low=Decimal("0.5"),
        close=Decimal(str(close)), volume=100, interval="1d",
    )


def test_insert_and_select(tmp_path):
    repo = BarRepo(str(tmp_path / "bars.duckdb"))
    repo.init()
    repo.insert_bars([_bar("ashare", "000858.SZ", 0, 100), _bar("ashare", "000858.SZ", 1, 101)])
    rows = repo.fetch_history("ashare", "000858.SZ",
                               start=datetime(2026, 5, 1, tzinfo=timezone.utc),
                               end=datetime(2026, 5, 2, tzinfo=timezone.utc))
    assert len(rows) == 2
    assert rows[0].close == Decimal("100")


def test_upsert_replaces_same_ts(tmp_path):
    repo = BarRepo(str(tmp_path / "bars.duckdb"))
    repo.init()
    repo.insert_bars([_bar("us", "AAPL", 0, 190)])
    repo.insert_bars([_bar("us", "AAPL", 0, 195)])
    rows = repo.fetch_history("us", "AAPL",
                               start=datetime(2026, 5, 1, tzinfo=timezone.utc),
                               end=datetime(2026, 5, 1, tzinfo=timezone.utc))
    assert len(rows) == 1
    assert rows[0].close == Decimal("195")


def test_fetch_empty_returns_empty_list(tmp_path):
    repo = BarRepo(str(tmp_path / "bars.duckdb"))
    repo.init()
    rows = repo.fetch_history("hk", "00700.HK",
                               start=datetime(2026, 5, 1, tzinfo=timezone.utc),
                               end=datetime(2026, 5, 2, tzinfo=timezone.utc))
    assert rows == []
