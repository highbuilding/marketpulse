"""A 股收盘结算 daily_settlement 单测。

锁住核心行为(根因 2026-06-08: 拆分架构下 1d 无稳态写入路径, settlement 补上):
- 1d 覆盖今日 → 就位 (settle_one True) + 触发 resample
- 1d 未定稿(最新仍是旧根)→ 未就位 (False), 留待下一轮重试 (条件等待)
- run_settlement_round: 部分就位, 未就位标的下轮继续
"""
import pytest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from apps.collector.ashare import daily_settlement as ds


def _bar(ts):
    return SimpleNamespace(ts=ts)


def test_bar_covers_today():
    today = datetime(2026, 6, 8).date()
    # 6-8 这根: ts=UTC 6-7 16:00 → BJT 6-8
    assert ds._bar_covers_today(datetime(2026, 6, 7, 16, 0, tzinfo=timezone.utc), today)
    # 6-4 这根: ts=UTC 6-3 16:00 → BJT 6-4, 不覆盖今日
    assert not ds._bar_covers_today(datetime(2026, 6, 3, 16, 0, tzinfo=timezone.utc), today)
    # naive ts 也兼容(当 UTC)
    assert ds._bar_covers_today(datetime(2026, 6, 7, 16, 0), today)


@pytest.mark.asyncio
async def test_settle_one_today_ready(monkeypatch):
    """拉到今日这根 → 就位 + 触发 resample。"""
    today = datetime(2026, 6, 8).date()
    kline = MagicMock()

    async def fake_fetch(symbol, *, interval, start, end):
        return [_bar(datetime(2026, 6, 7, 16, 0, tzinfo=timezone.utc))]
    kline.fetch_fresh_bars = fake_fetch

    agg_called = {}

    async def fake_agg(repo, market, symbol, **kw):
        agg_called["sym"] = symbol
        return {}
    monkeypatch.setattr(ds, "aggregate_derived_for_symbol", fake_agg)

    ok = await ds.settle_one(kline, MagicMock(), "ashare", "600519.SH", today)
    assert ok is True
    assert agg_called["sym"] == "600519.SH"  # resample 被触发


@pytest.mark.asyncio
async def test_settle_one_not_finalized(monkeypatch):
    """sina 未定稿(最新仍是旧根)→ 未就位, 不 resample。"""
    today = datetime(2026, 6, 8).date()
    kline = MagicMock()

    async def fake_fetch(symbol, *, interval, start, end):
        return [_bar(datetime(2026, 6, 4, 16, 0, tzinfo=timezone.utc))]  # 旧根
    kline.fetch_fresh_bars = fake_fetch

    agg_called = {"n": 0}

    async def fake_agg(repo, market, symbol, **kw):
        agg_called["n"] += 1
        return
    monkeypatch.setattr(ds, "aggregate_derived_for_symbol", fake_agg)

    ok = await ds.settle_one(kline, MagicMock(), "ashare", "600519.SH", today)
    assert ok is False
    assert agg_called["n"] == 0  # 未定稿不触发 resample


@pytest.mark.asyncio
async def test_settle_one_fetch_fails_returns_false(monkeypatch):
    """fetch 抛错 → 优雅降级返 False(留待下一轮), 不冒泡。"""
    today = datetime(2026, 6, 8).date()
    kline = MagicMock()

    async def fake_fetch(symbol, *, interval, start, end):
        raise RuntimeError("SSLError: UNEXPECTED_EOF")
    kline.fetch_fresh_bars = fake_fetch

    ok = await ds.settle_one(kline, MagicMock(), "ashare", "X", today)
    assert ok is False


@pytest.mark.asyncio
async def test_settle_one_finalizes_redis_current(monkeypatch):
    """1d 就位 + 提供 redis_cache → 用真实收盘根 (final=true) 定稿 current。

    雷区3: current 的 ts 必须复用定稿 1d 根自身 ts, 否则与 REST 1d 根 ts 不撞,
    前端 final 保护不触发 + 多出一根。
    """
    from decimal import Decimal
    today = datetime(2026, 6, 8).date()
    close_ts = datetime(2026, 6, 7, 16, 0, tzinfo=timezone.utc)
    kline = MagicMock()

    async def fake_fetch(symbol, *, interval, start, end):
        if interval == "1d":
            return [SimpleNamespace(
                ts=close_ts, open=Decimal("4090"), high=Decimal("4100"),
                low=Decimal("4080"), close=Decimal("4091.89"), volume=123,
            )]
        return []  # 5m 补齐: 空, 不影响
    kline.fetch_fresh_bars = fake_fetch

    async def fake_agg(repo, market, symbol, **kw):
        return {}
    monkeypatch.setattr(ds, "aggregate_derived_for_symbol", fake_agg)

    captured = {}

    class FakeRedis:
        _r = MagicMock()
        async def set_msgpack(self, key, payload, ttl_s=None):
            captured["key"] = key
            captured["payload"] = payload
            captured["ttl"] = ttl_s

    # upsert_tail 走 RedisBarsCache(redis), stub 掉避免真连
    async def fake_upsert_tail(self, market, symbol, interval, bars):
        captured["upsert"] = (market, symbol, interval, len(bars))
    monkeypatch.setattr(ds.RedisBarsCache, "upsert_tail", fake_upsert_tail)

    fake_redis = FakeRedis()

    async def fake_xadd(*a, **k):
        return b"1-0"
    fake_redis._r.xadd = fake_xadd

    ok = await ds.settle_one(kline, MagicMock(), "ashare", "000001.SH", today,
                             redis_cache=fake_redis)
    assert ok is True
    assert captured["payload"]["final"] is True
    assert captured["payload"]["close"] == 4091.89
    # ts 复用定稿根 ts (雷区3)
    assert captured["payload"]["ts"] == close_ts.isoformat()
    assert captured["ttl"] == 86400


@pytest.mark.asyncio
async def test_settle_one_no_redis_skips_finalize(monkeypatch):
    """redis_cache=None → 不定稿 current (向后兼容, 旧调用不传 redis)。"""
    today = datetime(2026, 6, 8).date()
    kline = MagicMock()

    async def fake_fetch(symbol, *, interval, start, end):
        if interval == "1d":
            return [_bar(datetime(2026, 6, 7, 16, 0, tzinfo=timezone.utc))]
        return []
    kline.fetch_fresh_bars = fake_fetch

    async def fake_agg(repo, market, symbol, **kw):
        return {}
    monkeypatch.setattr(ds, "aggregate_derived_for_symbol", fake_agg)

    called = {"finalize": 0}

    async def fake_finalize(*a, **k):
        called["finalize"] += 1
    monkeypatch.setattr(ds, "_finalize_daily_current", fake_finalize)

    ok = await ds.settle_one(kline, MagicMock(), "ashare", "X", today)
    assert ok is True
    assert called["finalize"] == 0


@pytest.mark.asyncio
async def test_run_settlement_round_partial(monkeypatch):
    """一轮结算: A 就位 / B 未定稿 → 返回 {A}。"""
    today = datetime(2026, 6, 8).date()
    monkeypatch.setattr(ds, "_THROTTLE_S", 0.0)

    async def fake_settle(kline, repo, market, sym, today_, **_):
        return sym == "A"
    monkeypatch.setattr(ds, "settle_one", fake_settle)

    settled = await ds.run_settlement_round(MagicMock(), MagicMock(), ["A", "B"], today)
    assert settled == {"A"}
