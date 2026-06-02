import asyncio
import pytest
from apps.api.sse_hub import Subscriber, StreamHub, bars_key, intraday_key


def test_key_fns():
    assert bars_key({"symbol": "AAPL", "interval": "5m"}) == ("AAPL", "5m")
    assert intraday_key({"symbol": "AAPL"}) == "AAPL"


@pytest.mark.asyncio
async def test_subscriber_offer_drop_oldest_when_full():
    sub = Subscriber(maxsize=2)
    sub.offer({"n": 1}); sub.offer({"n": 2}); sub.offer({"n": 3})
    a = await sub.get(); b = await sub.get()
    assert [a["n"], b["n"]] == [2, 3]


def test_register_dispatch_only_to_matching_key():
    hub = StreamHub(redis=None, channel="c", key_fn=bars_key)
    sub_a = hub.register([("AAPL", "5m")])
    sub_b = hub.register([("MSFT", "5m")])
    n = hub.dispatch({"symbol": "AAPL", "interval": "5m", "final": False})
    assert n == 1
    assert sub_a._q.qsize() == 1 and sub_b._q.qsize() == 0


def test_register_multi_key_one_subscriber_batch():
    hub = StreamHub(redis=None, channel="c", key_fn=bars_key)
    sub = hub.register([("AAPL", "5m"), ("MSFT", "5m")])
    hub.dispatch({"symbol": "AAPL", "interval": "5m"})
    hub.dispatch({"symbol": "MSFT", "interval": "5m"})
    assert sub._q.qsize() == 2


def test_unregister_stops_delivery_and_cleans_key():
    hub = StreamHub(redis=None, channel="c", key_fn=bars_key)
    sub = hub.register([("AAPL", "5m")])
    hub.unregister([("AAPL", "5m")], sub)
    assert hub.dispatch({"symbol": "AAPL", "interval": "5m"}) == 0
    assert ("AAPL", "5m") not in hub._registry


def test_dispatch_bad_payload_no_crash():
    hub = StreamHub(redis=None, channel="c", key_fn=bars_key)
    assert hub.dispatch({}) == 0
