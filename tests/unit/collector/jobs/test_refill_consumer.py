import pytest

from apps.collector.jobs.refill_consumer import handle_refill_message


async def test_handle_refill_message_calls_refill_fn():
    called = []

    async def fake_refill(market, symbol, interval, days):
        called.append((market, symbol, interval, days))

    msg = {"market": "ashare", "symbol": "600519.SH", "interval": "1d", "days": 365}
    await handle_refill_message(msg, refill_fn=fake_refill)
    assert called == [("ashare", "600519.SH", "1d", 365)]


async def test_handle_refill_message_swallows_handler_errors():
    async def fake_refill(*args, **kwargs):
        raise RuntimeError("boom")

    msg = {"market": "ashare", "symbol": "X.SH", "interval": "1d", "days": 30}
    # 不应抛
    await handle_refill_message(msg, refill_fn=fake_refill)


async def test_handle_refill_message_skips_malformed():
    async def fake_refill(*args, **kwargs):
        raise AssertionError("should not be called")

    await handle_refill_message({}, refill_fn=fake_refill)
    await handle_refill_message({"market": "ashare"}, refill_fn=fake_refill)
