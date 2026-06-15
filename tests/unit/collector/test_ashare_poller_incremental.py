"""A 股 bar_poller 增量 upsert + 进行中根过滤单测。

拉取/解析/防御已收口到 adapter.fetch_intraday, 本测试 mock adapter 返回
list[Bar], 聚焦 poller 自身职责: 进行中根过滤 → 增量 → 入库 → 发 bus。
"""
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.collector.ashare.bar_poller import BarPoller
from core.domain.models import Bar


def _bar(ts, interval="5m", symbol="600519.SH"):
    return Bar(market="ashare", symbol=symbol, ts=ts,
               open=Decimal("1"), high=Decimal("2"), low=Decimal("1"),
               close=Decimal("1.5"), volume=100, interval=interval)


def _make_poller(repo, redis, fetched_bars):
    adapter = MagicMock()
    adapter.fetch_intraday = AsyncMock(return_value=fetched_bars)
    collector_symbols = MagicMock()
    collector_symbols.active_symbols = AsyncMock(return_value=["600519.SH"])
    redis.get_msgpack = AsyncMock(return_value=[])
    redis.set_msgpack = AsyncMock()
    return BarPoller(repo, redis, adapter, collector_symbols)


@pytest.mark.asyncio
async def test_poll_one_only_inserts_fresh(monkeypatch):
    """DB 已有较早根, 只 upsert 比 last_ts 更晚的新根。"""
    # 三根 5m 已收线(过去日期确保 ts<=now)
    b1 = _bar(datetime(2026, 6, 1, 1, 35, tzinfo=timezone.utc))
    b2 = _bar(datetime(2026, 6, 1, 1, 40, tzinfo=timezone.utc))
    b3 = _bar(datetime(2026, 6, 1, 1, 45, tzinfo=timezone.utc))

    repo = MagicMock()
    # DB 现有最新 = 01:40 → 只有 01:45 是 fresh
    repo.fetch_history_paged.return_value = [b2]
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    monkeypatch.setattr(
        "apps.collector.jobs.aggregate_derived.aggregate_and_publish", AsyncMock())

    poller = _make_poller(repo, redis, [b1, b2, b3])
    await poller._poll_one("600519.SH", "5m")

    repo.insert_bars.assert_called_once()
    inserted = repo.insert_bars.call_args[0][0]
    assert len(inserted) == 1
    assert inserted[0].ts == datetime(2026, 6, 1, 1, 45, tzinfo=timezone.utc)
    redis.set_msgpack.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_one_skips_when_all_stored(monkeypatch):
    """DB 已有最新根, 无新增 → 不写不发。"""
    b1 = _bar(datetime(2026, 6, 1, 1, 35, tzinfo=timezone.utc))
    b2 = _bar(datetime(2026, 6, 1, 1, 40, tzinfo=timezone.utc))
    repo = MagicMock()
    repo.fetch_history_paged.return_value = [b2]
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    monkeypatch.setattr(
        "apps.collector.jobs.aggregate_derived.aggregate_and_publish", AsyncMock())

    poller = _make_poller(repo, redis, [b1, b2])
    await poller._poll_one("600519.SH", "5m")

    repo.insert_bars.assert_not_called()
    assert redis._r.xadd.await_count == 0
    redis.set_msgpack.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_one_empty_db_inserts_all_closed(monkeypatch):
    """DB 空 → 全部已收线根都 upsert + 发 final=true bus。"""
    b1 = _bar(datetime(2026, 6, 1, 1, 35, tzinfo=timezone.utc))
    b2 = _bar(datetime(2026, 6, 1, 1, 40, tzinfo=timezone.utc))
    repo = MagicMock()
    repo.fetch_history_paged.return_value = []
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    monkeypatch.setattr(
        "apps.collector.jobs.aggregate_derived.aggregate_and_publish", AsyncMock())

    poller = _make_poller(repo, redis, [b1, b2])
    await poller._poll_one("600519.SH", "5m")

    repo.insert_bars.assert_called_once()
    assert len(repo.insert_bars.call_args[0][0]) == 2
    redis.set_msgpack.assert_awaited_once()
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["final"] is True


@pytest.mark.asyncio
async def test_poll_one_filters_in_progress_bar(monkeypatch):
    """ts>now 的进行中根交给 ticker, poller 不入库。"""
    closed = _bar(datetime(2026, 6, 1, 1, 35, tzinfo=timezone.utc))
    future = _bar(datetime(2099, 1, 1, tzinfo=timezone.utc))  # ts>now
    repo = MagicMock()
    repo.fetch_history_paged.return_value = []
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    monkeypatch.setattr(
        "apps.collector.jobs.aggregate_derived.aggregate_and_publish", AsyncMock())

    poller = _make_poller(repo, redis, [closed, future])
    await poller._poll_one("600519.SH", "5m")

    inserted = repo.insert_bars.call_args[0][0]
    assert all(b.ts <= datetime.now(timezone.utc) for b in inserted)
    assert future not in inserted


@pytest.mark.asyncio
async def test_poll_one_empty_fetch_noop(monkeypatch):
    """adapter 返回空 → 直接 return, 不查 DB 不写。"""
    repo = MagicMock()
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    poller = _make_poller(repo, redis, [])
    await poller._poll_one("600519.SH", "5m")
    repo.insert_bars.assert_not_called()
    repo.fetch_history_paged.assert_not_called()
