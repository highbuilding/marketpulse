"""验证 SignalScanService 链路: 拉 600519 1d, 跑 CD, 列出信号。"""
import os
for k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY'):
    os.environ.pop(k, None)

import requests
_orig_get = requests.get
def _no_proxy_get(url, **kw):
    s = requests.Session(); s.trust_env = False; s.proxies = {}
    return s.get(url, **kw)
requests.get = _no_proxy_get

import asyncio
from datetime import datetime, timedelta, timezone

from core.adapters.ashare import AShareAdapter
from core.indicators.cd import compute_cd_signals


async def main():
    a = AShareAdapter()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=400)
    bars = await a.fetch_history("600519.SH", start, end)
    print(f"adapter -> {len(bars)} bars")
    print(f"  first ts={bars[0].ts.date()} close={float(bars[0].close):.2f}")
    print(f"  last  ts={bars[-1].ts.date()} close={float(bars[-1].close):.2f}")

    sigs = compute_cd_signals(bars)
    print(f"\n=> {len(sigs)} signals")
    for s in sigs:
        print(f"  {s.signal_type:4s} {s.bar_ts.date()} price={s.price:.2f}")


asyncio.run(main())
