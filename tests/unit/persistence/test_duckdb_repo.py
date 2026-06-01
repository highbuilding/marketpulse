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
        amount=12345.6, turnover=1.23, outstanding_share=1_000_000,
    )


def test_insert_bars_drops_1m(tmp_path):
    """审计 B4: insert_bars 永久杜绝 1m 落库; 混合批次只入 5m+。"""
    repo = BarRepo(str(tmp_path / "bars.duckdb"))
    repo.init()

    def _mk(iv, off):
        ts = datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(minutes=off)
        return Bar(market="us", symbol="AAPL", ts=ts,
                   open=Decimal("1"), high=Decimal("2"), low=Decimal("1"),
                   close=Decimal("2"), volume=10, interval=iv)

    repo.insert_bars([_mk("1m", 0), _mk("5m", 5)])
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    end = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert len(repo.fetch_history("us", "AAPL", start=start, end=end, interval="1m")) == 0
    assert len(repo.fetch_history("us", "AAPL", start=start, end=end, interval="5m")) == 1


def test_insert_and_select(tmp_path):
    repo = BarRepo(str(tmp_path / "bars.duckdb"))
    repo.init()
    repo.insert_bars([_bar("ashare", "000858.SZ", 0, 100), _bar("ashare", "000858.SZ", 1, 101)])
    rows = repo.fetch_history("ashare", "000858.SZ",
                               start=datetime(2026, 5, 1, tzinfo=timezone.utc),
                               end=datetime(2026, 5, 2, tzinfo=timezone.utc))
    assert len(rows) == 2
    assert rows[0].close == Decimal("100")
    assert rows[0].amount == pytest.approx(12345.6)
    assert rows[0].turnover == pytest.approx(1.23)


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


def _seed_n(repo, market, symbol, n):
    repo.insert_bars([_bar(market, symbol, i, 100 + i) for i in range(n)])


def test_paged_latest_page_ascending(tmp_path):
    repo = BarRepo(str(tmp_path / "bars.duckdb"))
    repo.init()
    _seed_n(repo, "crypto", "BTC-USDT", 50)
    # before=None → 最新一页, limit=10 → 取最近 10 根, 升序
    page = repo.fetch_history_paged("crypto", "BTC-USDT", "1d", before=None, limit=10)
    assert len(page) == 10
    assert all(page[i].ts <= page[i + 1].ts for i in range(len(page) - 1))
    # 最新一根是 day_offset=49
    assert page[-1].ts == datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(days=49)
    assert page[0].ts == datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(days=40)


def test_paged_cursor_no_overlap(tmp_path):
    repo = BarRepo(str(tmp_path / "bars.duckdb"))
    repo.init()
    _seed_n(repo, "crypto", "BTC-USDT", 50)
    page1 = repo.fetch_history_paged("crypto", "BTC-USDT", "1d", before=None, limit=10)
    page2 = repo.fetch_history_paged("crypto", "BTC-USDT", "1d", before=page1[0].ts, limit=10)
    assert len(page2) == 10
    # page2 全部严格早于 page1 最老一根 (无重叠)
    assert page2[-1].ts < page1[0].ts
    assert all(page2[i].ts <= page2[i + 1].ts for i in range(len(page2) - 1))


def test_paged_floor_returns_partial(tmp_path):
    repo = BarRepo(str(tmp_path / "bars.duckdb"))
    repo.init()
    _seed_n(repo, "crypto", "BTC-USDT", 25)
    # 翻到底: 一直用 before 游标翻, 直到不足一页
    cursor = None
    total = 0
    pages = 0
    while True:
        page = repo.fetch_history_paged("crypto", "BTC-USDT", "1d", before=cursor, limit=10)
        if not page:
            break
        total += len(page)
        pages += 1
        cursor = page[0].ts
        if len(page) < 10:  # 不足一页 = 到顶
            break
    assert total == 25
    assert pages == 3  # 10 + 10 + 5


def test_paged_empty_symbol(tmp_path):
    repo = BarRepo(str(tmp_path / "bars.duckdb"))
    repo.init()
    page = repo.fetch_history_paged("crypto", "NOPE-USDT", "1d", before=None, limit=10)
    assert page == []
