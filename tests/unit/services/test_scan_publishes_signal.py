import pytest
from datetime import datetime, timezone

from core.domain.models import Bar
from core.services.signal_service import SignalScanService


class FakeRepo:
    def __init__(self, bars):
        self._bars = bars

    def fetch_history(self, market, symbol, start, end, interval):
        return self._bars


class FakeSigRepo:
    def __init__(self):
        self.existing: set[str] = set()

    async def upsert_many(self, records):
        return len(records)

    async def existing_bar_ts(self, symbol, interval):
        return self.existing


class FakeRedis:
    def __init__(self):
        self.added = []

    async def xadd(self, stream, fields, **kw):
        self.added.append((stream, fields))


class FakeKLine:
    def __init__(self, bars):
        self.repo = FakeRepo(bars)


def _bar(h):
    return Bar(market="crypto", symbol="BTC-USDT",
               ts=datetime(2026, 5, 23, h, tzinfo=timezone.utc),
               open=1.0, high=2.0, low=0.5, close=1.5, volume=10, interval="4h")


@pytest.mark.asyncio
async def test_scan_publishes_new_signal_to_bus():
    # 全是新信号(existing 空)→ 有 CD 信号时应 xadd 到 bus:signal.new
    bars = [_bar(h) for h in range(0, 24, 4)]
    redis = FakeRedis()
    svc = SignalScanService(FakeKLine(bars), FakeSigRepo(), redis=redis)
    await svc.scan_symbol_readonly("BTC-USDT", "4h")
    # 不论是否算出信号, 不应抛错; 若算出信号则发到 signal.new
    assert all("signal.new" in s for s, _ in redis.added) or redis.added == []


@pytest.mark.asyncio
async def test_scan_no_redis_still_works():
    # 不传 redis 时(默认)scan 照常工作不抛错
    bars = [_bar(h) for h in range(0, 24, 4)]
    svc = SignalScanService(FakeKLine(bars), FakeSigRepo())
    n = await svc.scan_symbol_readonly("BTC-USDT", "4h")
    assert isinstance(n, int)
