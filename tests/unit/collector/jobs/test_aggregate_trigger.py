import json
import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from core.domain.models import Bar
from apps.collector.jobs.aggregate_derived import aggregate_and_publish


def _bar(interval, ts):
    return Bar(market="ashare", symbol="600519.SH", ts=ts,
               open=Decimal("1"), high=Decimal("2"), low=Decimal("1"),
               close=Decimal("2"), volume=10, interval=interval)


@pytest.mark.asyncio
async def test_publishes_closed_bucket_only(monkeypatch):
    repo = MagicMock()
    repo.fetch_history_paged.return_value = [
        _bar("15m", datetime(2026, 6, 1, 2, 0, tzinfo=timezone.utc))]  # 已收线(过去)
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    # mock 掉真实聚合,避免依赖 DuckDB
    async def fake_agg(*a, **k): return {}
    monkeypatch.setattr("apps.collector.jobs.aggregate_derived.aggregate_derived_for_symbol", fake_agg)
    await aggregate_and_publish(repo, redis, "ashare", "600519.SH",
                                targets=("15m",), now=datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc))
    assert redis._r.xadd.await_count == 1
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["interval"] == "15m" and payload["final"] is True


@pytest.mark.asyncio
async def test_skips_unclosed_bucket(monkeypatch):
    repo = MagicMock()
    repo.fetch_history_paged.return_value = [
        _bar("15m", datetime(2026, 6, 1, 4, 0, tzinfo=timezone.utc))]  # 未收线(未来)
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    async def fake_agg(*a, **k): return {}
    monkeypatch.setattr("apps.collector.jobs.aggregate_derived.aggregate_derived_for_symbol", fake_agg)
    await aggregate_and_publish(repo, redis, "ashare", "600519.SH",
                                targets=("15m",), now=datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc))
    assert redis._r.xadd.await_count == 0
