"""手动验证 ET 16:30 时点 Alpaca SIP 当日 daily 是否定稿。

用法:
  ET 16:30 后(BJT 04:30 / 05:30 因夏令时)运行
  python -m apps.verify_us_daily_at_1630

预期输出:
  - 当日 daily.close 与 5m bars 中 16:00 ET 那根的 close 一致
  - 与盘后任何价格无关
  - 16:30 拉到的 close 与 20:30 再拉一次拿到的 close 完全相同(终态)
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv(".env")


def main() -> None:
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    c = StockHistoricalDataClient(
        os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"),
    )
    now = datetime.now(timezone.utc)
    et_now = now.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
    print(f"now UTC = {now.isoformat()}")
    print(f"now ET  = {et_now.isoformat()}")

    today_et = et_now.date()
    start = datetime.combine(today_et, datetime.min.time(),
                              tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))
    end = start + timedelta(days=1)

    end_safe = min(end.astimezone(timezone.utc), now - timedelta(minutes=20))
    print(f"end_safe = {end_safe.isoformat()} (= now - 20min, free tier 余量)")

    daily_req = StockBarsRequest(
        symbol_or_symbols="QQQ", timeframe=TimeFrame.Day,
        start=start.astimezone(timezone.utc), end=end_safe,
        feed="sip", adjustment="all",
    )
    daily = c.get_stock_bars(daily_req).data.get("QQQ", [])
    print(f"\n=== {today_et} QQQ daily ===")
    if not daily:
        print(" 没拿到! 可能 ET 还没到 16:00 RTH 收盘")
        return
    for b in daily:
        print(f"  ts={b.timestamp} O={b.open} H={b.high} L={b.low} C={b.close} V={b.volume:,}")

    # 5m bars 找 16:00 ET 那根 close (= UTC 20:00)
    intra_req = StockBarsRequest(
        symbol_or_symbols="QQQ",
        timeframe=TimeFrame(5, TimeFrameUnit.Minute),
        start=start.astimezone(timezone.utc),
        end=end_safe,
        feed="sip", adjustment="all",
    )
    intra = c.get_stock_bars(intra_req).data.get("QQQ", [])
    rth_close_bar = None
    for b in intra:
        if b.timestamp.hour == 20 and b.timestamp.minute == 0:
            rth_close_bar = b
            break
    if rth_close_bar:
        print(f"\nRTH 16:00 ET 5m bar close = {rth_close_bar.close}")
        match = float(rth_close_bar.close) == float(daily[0].close)
        print(f"daily.close == RTH 16:00 close ? {match}")
    else:
        print("\nRTH 16:00 ET 那根 5m 还没拉到 (现在没到 RTH 收盘?)")


if __name__ == "__main__":
    main()
