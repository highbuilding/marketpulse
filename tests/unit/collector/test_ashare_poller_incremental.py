"""A 股 bar_poller 增量 upsert + 进行中根过滤单测。"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from apps.collector.ashare.bar_poller import BarPoller
from core.domain.models import Bar
from decimal import Decimal


def _df(rows):
    """rows: list of (day_str, ohlcv)。day 为 BJT close 时刻字符串。"""
    return pd.DataFrame([
        {"day": d, "open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5, "volume": 100}
        for d in rows
    ])


def _stored_bar(ts, interval="5m"):
    return Bar(market="ashare", symbol="600519.SH", ts=ts,
               open=Decimal("1"), high=Decimal("2"), low=Decimal("1"),
               close=Decimal("1.5"), volume=100, interval=interval)


@pytest.mark.asyncio
async def test_poll_one_only_inserts_fresh(monkeypatch):
    """DB 已有较早根, 只 upsert 比 last_ts 更晚的新根。"""
    # 三根 5m, BJT 已收线(用过去日期确保 ts<=now)
    df = _df(["2026-06-01 09:35:00", "2026-06-01 09:40:00", "2026-06-01 09:45:00"])
    monkeypatch.setattr(
        "core.integrations.akshare.ak_call", AsyncMock(return_value=df))

    repo = MagicMock()
    # DB 现有最新 = 09:40 (BJT) → UTC 01:40。只有 09:45 是 fresh
    last = datetime(2026, 6, 1, 1, 40, tzinfo=timezone.utc)
    repo.fetch_history_paged.return_value = [_stored_bar(last)]
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    monkeypatch.setattr(
        "apps.collector.jobs.aggregate_derived.aggregate_and_publish", AsyncMock())

    poller = BarPoller(repo, redis)
    await poller._poll_one("600519.SH", "5m")

    repo.insert_bars.assert_called_once()
    inserted = repo.insert_bars.call_args[0][0]
    assert len(inserted) == 1
    assert inserted[0].ts == datetime(2026, 6, 1, 1, 45, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_poll_one_skips_when_all_stored(monkeypatch):
    """DB 已有最新根, 无新增 → 不写不发。"""
    df = _df(["2026-06-01 09:35:00", "2026-06-01 09:40:00"])
    monkeypatch.setattr(
        "core.integrations.akshare.ak_call", AsyncMock(return_value=df))
    repo = MagicMock()
    repo.fetch_history_paged.return_value = [
        _stored_bar(datetime(2026, 6, 1, 1, 40, tzinfo=timezone.utc))]
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    monkeypatch.setattr(
        "apps.collector.jobs.aggregate_derived.aggregate_and_publish", AsyncMock())

    poller = BarPoller(repo, redis)
    await poller._poll_one("600519.SH", "5m")

    repo.insert_bars.assert_not_called()
    assert redis._r.xadd.await_count == 0


@pytest.mark.asyncio
async def test_poll_one_empty_db_inserts_all_closed(monkeypatch):
    """DB 空 → 全部已收线根都 upsert。"""
    df = _df(["2026-06-01 09:35:00", "2026-06-01 09:40:00"])
    monkeypatch.setattr(
        "core.integrations.akshare.ak_call", AsyncMock(return_value=df))
    repo = MagicMock()
    repo.fetch_history_paged.return_value = []
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    monkeypatch.setattr(
        "apps.collector.jobs.aggregate_derived.aggregate_and_publish", AsyncMock())

    poller = BarPoller(repo, redis)
    await poller._poll_one("600519.SH", "5m")

    repo.insert_bars.assert_called_once()
    assert len(repo.insert_bars.call_args[0][0]) == 2
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["final"] is True
