import pytest

from apps.collector.jobs.signal_sweep_worker import sweep_symbols_for_market


class FakeScan:
    def __init__(self):
        self.calls = []

    async def scan_symbol_readonly(self, sym, iv):
        self.calls.append((sym, iv))
        return 0


@pytest.mark.asyncio
async def test_sweep_calls_scan_for_all_signal_intervals():
    scan = FakeScan()
    await sweep_symbols_for_market(scan, ["BTC-USDT"], market="crypto")
    ivs = {iv for _, iv in scan.calls}
    assert ivs == {"15m", "30m", "60m", "4h", "1d"}


@pytest.mark.asyncio
async def test_sweep_covers_all_symbols():
    scan = FakeScan()
    await sweep_symbols_for_market(scan, ["BTC-USDT", "ETH-USDT"], market="crypto")
    syms = {sym for sym, _ in scan.calls}
    assert syms == {"BTC-USDT", "ETH-USDT"}


@pytest.mark.asyncio
async def test_sweep_continues_on_failure():
    class FlakyScan:
        def __init__(self):
            self.calls = []

        async def scan_symbol_readonly(self, sym, iv):
            self.calls.append((sym, iv))
            if iv == "15m":
                raise RuntimeError("boom")
            return 0

    scan = FlakyScan()
    # 单个失败不应中断整体
    await sweep_symbols_for_market(scan, ["BTC-USDT"], market="crypto")
    assert len(scan.calls) == 5  # 5 个周期都尝试了
