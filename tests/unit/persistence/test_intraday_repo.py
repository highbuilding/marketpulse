from datetime import datetime, timezone
from core.persistence.intraday_repo import IntradayLineRepo, IntradayPoint


def test_insert_and_fetch_day(tmp_path):
    repo = IntradayLineRepo(str(tmp_path / "intraday_ashare.duckdb"))
    ts = datetime(2026, 6, 1, 1, 31, tzinfo=timezone.utc)
    repo.insert_points([IntradayPoint(
        symbol="600519.SH", ts=ts, price=1700.0,
        cum_amount=1700000.0, cum_volume=1000, avg_price=1700.0)])
    rows = repo.fetch_day("600519.SH", ts.date())
    assert len(rows) == 1
    assert rows[0]["avg_price"] == 1700.0
    assert rows[0]["price"] == 1700.0


def test_upsert_overwrites_same_minute(tmp_path):
    repo = IntradayLineRepo(str(tmp_path / "intraday_ashare.duckdb"))
    ts = datetime(2026, 6, 1, 1, 31, tzinfo=timezone.utc)
    repo.insert_points([IntradayPoint("600519.SH", ts, 1700.0, 1700000.0, 1000, 1700.0)])
    repo.insert_points([IntradayPoint("600519.SH", ts, 1705.0, 1710000.0, 1005, 1701.5)])
    rows = repo.fetch_day("600519.SH", ts.date())
    assert len(rows) == 1 and rows[0]["price"] == 1705.0


def test_purge_before(tmp_path):
    repo = IntradayLineRepo(str(tmp_path / "intraday_ashare.duckdb"))
    old = datetime(2026, 1, 1, 1, 31, tzinfo=timezone.utc)
    new = datetime(2026, 6, 1, 1, 31, tzinfo=timezone.utc)
    repo.insert_points([
        IntradayPoint("X", old, 1.0, 1.0, 1, 1.0),
        IntradayPoint("X", new, 2.0, 2.0, 1, 2.0)])
    repo.purge_before(datetime(2026, 3, 1, tzinfo=timezone.utc))
    assert len(repo.fetch_day("X", new.date())) == 1
    assert repo.fetch_day("X", old.date()) == []
