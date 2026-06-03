import pytest
from datetime import datetime, timezone

from core.domain.models import Bar
from core.services.signal_service import SignalScanService


class FakeRepo:
    """假 bar_repo: fetch_history 返回预置 bar, 记录调用。"""
    def __init__(self, bars):
        self._bars = bars
        self.calls = []

    def fetch_history(self, market, symbol, start, end, interval):
        self.calls.append((market, symbol, interval))
        return self._bars


class FakeSigRepo:
    def __init__(self):
        self.upserted = []

    async def upsert_many(self, records):
        self.upserted.extend(records)
        return len(records)


class FakeKLine:
    def __init__(self, bars):
        self.repo = FakeRepo(bars)


def _bar(ts):
    return Bar(market="crypto", symbol="BTC-USDT", ts=ts,
               open=1.0, high=2.0, low=0.5, close=1.5, volume=10, interval="4h")


@pytest.mark.asyncio
async def test_scan_readonly_reads_repo_no_fetch():
    # 喂若干 4h bar, scan_symbol_readonly 应只读 repo, 不抛错, 返回 int
    bars = [_bar(datetime(2026, 5, 23, h, tzinfo=timezone.utc)) for h in range(0, 24, 4)]
    kl = FakeKLine(bars)
    svc = SignalScanService(kl, FakeSigRepo())
    n = await svc.scan_symbol_readonly("BTC-USDT", "4h")
    assert isinstance(n, int)
    # 证明走了只读 repo 路径(crypto 市场推断 + 4h interval)
    assert kl.repo.calls and kl.repo.calls[0][2] == "4h"


@pytest.mark.asyncio
async def test_scan_readonly_empty_bars_returns_zero():
    kl = FakeKLine([])
    svc = SignalScanService(kl, FakeSigRepo())
    n = await svc.scan_symbol_readonly("BTC-USDT", "4h")
    assert n == 0


@pytest.mark.asyncio
async def test_scan_readonly_none_repo_returns_zero():
    class NoRepoKLine:
        repo = None
    svc = SignalScanService(NoRepoKLine(), FakeSigRepo())
    n = await svc.scan_symbol_readonly("BTC-USDT", "4h")
    assert n == 0
