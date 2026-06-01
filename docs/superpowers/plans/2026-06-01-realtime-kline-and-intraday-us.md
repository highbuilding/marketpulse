# 美股实时 K 线 + 分时图(复刻 A 股)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 A 股已交付的"实时 K 线进行中态 + 券商口径分时图"复刻到美股,WS 换 `trades`(秒级逐笔)驱动分时(真 VWAP)+ 进行中态,收线 bar 走 REST SIP 权威源,1m 不再落库。

**Architecture:** Alpaca IEX WS 从 `bars`(1m) 换成 `trades`(逐笔)→ `TradeHub` 内存累加 cum_amount/cum_volume + ~1s 节流分发给:① `UsIntradayWriter`(仅 RTH,真 VWAP 均价线)② `UsBarTicker`(逐笔攒进行中桶推 `final=false`,桶滚动补发 provisional `final=true` 仅 bus 填 SIP 延迟洞,不入库)。`UsBarPoller` 用 REST SIP 周期轮询收线 5m/15m/30m 入库 + 发 `final=true` + 触发 `aggregate_and_publish`。DuckDB 只存 SIP 权威收线 bar(喂 CD 信号/量指标,零污染)。

**Tech Stack:** Python (FastAPI / APScheduler / asyncio / websockets)、DuckDB、Redis Streams、Alpaca SDK、Next.js (lightweight-charts / EventSource)、pytest + fakeredis。

**Spec:** `docs/superpowers/specs/2026-06-01-realtime-kline-and-intraday-us.md`

---

## 文件结构

**新建:**
- `core/domain/bucket_state.py` — 共享桶纯函数(`BucketState`/`update_bucket`/`current_bucket`/`seed_baseline`),从 ashare 提取
- `apps/collector/us/trade_hub.py` — `TradeAccumulator`(VWAP 累加 + RTH 重置)+ `TradeHub`(逐笔分发 + 1s 节流 loop)
- `apps/collector/us/intraday_line_writer.py` — `compute_us_point` + `UsIntradayWriter`(算 VWAP 分时点 + 发 bus)
- `apps/collector/us/bar_ticker.py` — `UsBarTicker`(推 final=false + 桶滚动 provisional)
- `apps/collector/us/bar_poller.py` — `UsBarPoller`(REST SIP 收线 + final=true + 触发聚合)
- `tests/unit/domain/test_bucket_state_shared.py`
- `tests/unit/collector/test_trade_accumulator.py`
- `tests/unit/collector/test_us_intraday_writer.py`
- `tests/unit/collector/test_us_bar_ticker.py`
- `tests/unit/collector/test_us_bar_poller.py`
- `tests/unit/domain/test_us_regular_session.py`

**改造:**
- `apps/collector/ashare/quote_bar_ticker.py` — 改 import 共享纯函数,删本地定义
- `apps/collector/us/ws_consumer.py` — 订阅 `bars`→`trades`,解析 trade,分发 TradeHub
- `apps/collector/us/main.py` — 接线 TradeHub/writer/ticker/poller/intraday repo/attach_intraday_route/purge/sweep 降频
- `core/domain/market_sessions.py` — 加 `is_us_regular_session`
- `apps/collector/base.py` — `attach_intraday_route` 加可选 `get_bar_repo`(带昨收 prev_close)
- `apps/web/lib/markets.ts` — 加 `isUsRegularSession`
- `apps/web/app/symbol/[code]/page.tsx` — 非 RTH 默认 K 线 + 分时 tab 盘前提示
- `apps/web/components/IntradayLineChart.tsx` — 昨收基准线 + 红绿染色 + IEX 量注脚
- `apps/web/lib/use_intraday_line.ts` — 响应带 `prev_close`
- `CLAUDE.md` — 删过时段落 + 补落地态

**复用(零改):** `core/persistence/intraday_repo.py`、`apps/api/routes/sse_intraday.py`、`apps/api/routes/symbols.py::intraday_line`、`apps/collector/jobs/aggregate_derived.py::aggregate_and_publish`、`core/cache/keys.py`、前端 `use_intraday_line` 基础逻辑。

---

## 步骤 1 · 共享桶纯函数提取(零行为变化)

### Task 1: 提取 BucketState/update_bucket/current_bucket/seed_baseline 到共享模块

**Files:**
- Create: `core/domain/bucket_state.py`
- Modify: `apps/collector/ashare/quote_bar_ticker.py:27-74`(删本地定义,改 import)
- Test: `tests/unit/domain/test_bucket_state_shared.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/domain/test_bucket_state_shared.py
from decimal import Decimal
from datetime import datetime, timezone
from core.domain.bucket_state import (
    BucketState, update_bucket, current_bucket, seed_baseline,
)


def test_update_bucket_new_sets_open():
    st = update_bucket(None, Decimal("100"), volume=5)
    assert st.open == st.high == st.low == st.close == Decimal("100")
    assert st.volume == 5


def test_update_bucket_tracks_high_low_close():
    st = update_bucket(None, Decimal("100"), volume=5)
    st = update_bucket(st, Decimal("105"), volume=8)
    st = update_bucket(st, Decimal("98"), volume=12)
    assert st.open == Decimal("100")
    assert st.high == Decimal("105")
    assert st.low == Decimal("98")
    assert st.close == Decimal("98")
    assert st.volume == 12


def test_current_bucket_us_rth_5m():
    # 09:30 ET = 14:30 UTC (EDT 夏令时, 6/1 是 EDT). 5m 桶 09:30-09:35
    now = datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc)
    ob = current_bucket("us", now, 5)
    assert ob is not None
    open_utc, close_utc = ob
    assert open_utc.hour == 14 and open_utc.minute == 30
    assert close_utc.hour == 14 and close_utc.minute == 35


def test_seed_baseline_from_smaller_bars():
    from core.domain.models import Bar
    bars = [
        Bar(market="us", symbol="X", ts=datetime(2026, 6, 1, 14, 35, tzinfo=timezone.utc),
            open=Decimal("10"), high=Decimal("12"), low=Decimal("9"), close=Decimal("11"),
            volume=100, interval="5m"),
        Bar(market="us", symbol="X", ts=datetime(2026, 6, 1, 14, 40, tzinfo=timezone.utc),
            open=Decimal("11"), high=Decimal("15"), low=Decimal("10"), close=Decimal("14"),
            volume=200, interval="5m"),
    ]
    st = seed_baseline(bars)
    assert st.open == Decimal("10")
    assert st.high == Decimal("15")
    assert st.low == Decimal("9")
    assert st.close == Decimal("14")
    assert st.volume == 300


def test_seed_baseline_empty_returns_none():
    assert seed_baseline([]) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/domain/test_bucket_state_shared.py -v`
Expected: FAIL(ModuleNotFoundError: core.domain.bucket_state)

- [ ] **Step 3: 创建共享模块**

```python
# core/domain/bucket_state.py
"""SSoT: 进行中 bar 桶状态纯函数。A 股 quote_bar_ticker + 美股 bar_ticker 共用。

无市场耦合: BucketState OHLC 攒法 / current_bucket 当前桶定位 / seed_baseline 基线补全。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from core.domain.market_sessions import bucket_grid


@dataclass
class BucketState:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


def update_bucket(state: BucketState | None, price: Decimal, *, volume: int) -> BucketState:
    """用新价更新进行中桶 OHLC。

    state=None 时新建 (open=high=low=close=price)。
    已有 state: open 保持, high=max, low=min, close=price, volume 取传入值(累计口径由调用方算)。
    """
    if state is None:
        return BucketState(open=price, high=price, low=price, close=price, volume=volume)
    return BucketState(
        open=state.open,
        high=max(state.high, price),
        low=min(state.low, price),
        close=price,
        volume=volume,
    )


def current_bucket(
    market: str, now: datetime, interval_min: int,
) -> tuple[datetime, datetime] | None:
    """返回 now 落在的桶 (open_utc, close_utc]; 不在任何桶(休市)返回 None。"""
    grid = bucket_grid(market, now.date(), interval_min)
    for open_utc, close_utc in grid:
        if open_utc <= now < close_utc:
            return (open_utc, close_utc)
    return None


def seed_baseline(bars: list) -> BucketState | None:
    """用更小周期已收线 bar 序列算出当前大桶到目前为止的 OHLC 基线。"""
    if not bars:
        return None
    return BucketState(
        open=bars[0].open,
        high=max(b.high for b in bars),
        low=min(b.low for b in bars),
        close=bars[-1].close,
        volume=sum(int(b.volume) for b in bars),
    )
```

- [ ] **Step 4: 改 ashare 引用(删本地定义,保留 _INTERVAL_MIN/类不变)**

`apps/collector/ashare/quote_bar_ticker.py`:删除 `:27-74`(`BucketState` / `update_bucket` / `current_bucket` / `seed_baseline` 四段定义),改顶部 import:

```python
# 删除原 from dataclasses import dataclass (若仅此处用) 与 :19 的 bucket_grid import
from core.domain.bucket_state import (
    BucketState, update_bucket, current_bucket, seed_baseline,
)
from core.domain.market_calendar import is_trading_day
from core.domain.market_sessions import is_market_session_open
```

(`is_market_session_open` 仍需;`bucket_grid` 不再直接用可删。`_INTERVAL_MIN` / `TICK_INTERVAL_S` / `QuoteBarTicker` 类全部保留不动。)

- [ ] **Step 5: 运行确认通过 + A 股回归**

Run: `. .venv/bin/activate && pytest tests/unit/domain/test_bucket_state_shared.py tests/unit/collector/test_quote_bar_ticker.py tests/unit/collector/test_quote_bar_ticker_run.py -v && python -c "from apps.collector.ashare.main import app; print('ashare import ok')"`
Expected: 全 PASS + import ok(A 股行为零变化)

- [ ] **Step 6: 提交**

```bash
git add core/domain/bucket_state.py apps/collector/ashare/quote_bar_ticker.py tests/unit/domain/test_bucket_state_shared.py
git commit -m "refactor: 桶状态纯函数提取到 core/domain/bucket_state (A股美股共用)"
```

---

## 步骤 2 · is_us_regular_session(RTH 判定)

### Task 2: market_sessions 加 is_us_regular_session

**Files:**
- Modify: `core/domain/market_sessions.py`(末尾加函数)
- Test: `tests/unit/domain/test_us_regular_session.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/domain/test_us_regular_session.py
from datetime import datetime, timezone
from core.domain.market_sessions import is_us_regular_session


def test_rth_open_true():
    # 10:00 ET = 14:00 UTC (EDT). RTH 内
    assert is_us_regular_session(datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)) is True


def test_premarket_false():
    # 08:00 ET = 12:00 UTC. 盘前, 非 RTH
    assert is_us_regular_session(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)) is False


def test_afterhours_false():
    # 17:00 ET = 21:00 UTC. 盘后, 非 RTH
    assert is_us_regular_session(datetime(2026, 6, 1, 21, 0, tzinfo=timezone.utc)) is False


def test_exactly_open_true():
    # 09:30 ET = 13:30 UTC 开盘
    assert is_us_regular_session(datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc)) is True


def test_exactly_close_false():
    # 16:00 ET = 20:00 UTC 收盘(含端点判定: close 不算 RTH)
    assert is_us_regular_session(datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)) is False
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/domain/test_us_regular_session.py -v`
Expected: FAIL(ImportError: is_us_regular_session)

- [ ] **Step 3: 实现**

`core/domain/market_sessions.py` 末尾加:

```python
def is_us_regular_session(when: datetime | None = None) -> bool:
    """美股正常交易时段 RTH (09:30-16:00 ET, 不含 16:00 端点)。

    区别于 is_market_session_open('us'): 后者含盘前 04:00 + 盘后 20:00。
    分时图只画 RTH (券商口径); 周末/节假日由调用方 is_trading_day 门控。
    """
    tz = ZoneInfo(MARKET_TZ["us"])
    cur = (when or datetime.now(timezone.utc)).astimezone(tz).time()
    return time(9, 30) <= cur < time(16, 0)
```

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/domain/test_us_regular_session.py -v`
Expected: PASS(5 项)

- [ ] **Step 5: 提交**

```bash
git add core/domain/market_sessions.py tests/unit/domain/test_us_regular_session.py
git commit -m "feat: market_sessions 加 is_us_regular_session (RTH 判定)"
```

---

## 步骤 3 · TradeAccumulator(VWAP 累加 + RTH 重置)

### Task 3: TradeAccumulator 纯逻辑

**Files:**
- Create: `apps/collector/us/trade_hub.py`(先只放 `TradeAccumulator`)
- Test: `tests/unit/collector/test_trade_accumulator.py`

`TradeAccumulator` 维护单标的当日 RTH 累计成交额/量。跨 ET 日或首次进入 RTH 自动重置。VWAP = cum_amount/cum_volume。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_trade_accumulator.py
from datetime import datetime, timezone
from apps.collector.us.trade_hub import TradeAccumulator


def _utc(h, m):
    return datetime(2026, 6, 1, h, m, tzinfo=timezone.utc)


def test_accumulates_vwap():
    acc = TradeAccumulator()
    acc.add_trade(price=100.0, size=10, ts=_utc(14, 0))   # 10:00 ET RTH
    acc.add_trade(price=110.0, size=20, ts=_utc(14, 1))
    # cum_amount = 100*10 + 110*20 = 3200; cum_vol = 30; vwap = 106.666...
    assert acc.cum_volume == 30
    assert acc.cum_amount == 3200.0
    assert abs(acc.vwap() - 3200.0 / 30) < 1e-9
    assert acc.last_price == 110.0


def test_vwap_zero_volume_falls_back_to_last_price():
    acc = TradeAccumulator()
    assert acc.vwap() == 0.0  # 无成交时 0(调用方用 last_price 兜底)


def test_resets_on_new_et_day():
    acc = TradeAccumulator()
    acc.add_trade(price=100.0, size=10, ts=_utc(14, 0))      # 6/1 RTH
    acc.add_trade(price=200.0, size=5, ts=datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc))  # 6/2 RTH
    # 跨日重置, 只剩 6/2 那笔
    assert acc.cum_volume == 5
    assert acc.cum_amount == 1000.0
    assert acc.session_date.day == 2
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_trade_accumulator.py -v`
Expected: FAIL(ImportError)

- [ ] **Step 3: 实现**

```python
# apps/collector/us/trade_hub.py
"""美股逐笔成交实时中枢 (替代 1m bar 频道)。

Alpaca IEX WS `trades` 逐笔 → TradeAccumulator 累加当日 RTH VWAP +
TradeHub 维护进行中桶 + ~1s 节流分发给分时 writer / K 线 ticker。
1m 不落库。收线由 UsBarPoller (REST SIP) 负责。
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import structlog

log = structlog.get_logger(__name__)

_ET = ZoneInfo("America/New_York")
FLUSH_INTERVAL_S = 1.0
SUBS_REFRESH_TICKS = 5  # 每 5 个 flush tick (~5s) 刷新订阅


def _et_date(ts: datetime) -> date:
    return ts.astimezone(_ET).date()


class TradeAccumulator:
    """单标的当日 RTH 累计成交额/量, 跨 ET 日自动重置。VWAP = cum_amount/cum_volume。"""

    def __init__(self) -> None:
        self.session_date: date | None = None
        self.cum_amount: float = 0.0
        self.cum_volume: int = 0
        self.last_price: float = 0.0

    def _maybe_reset(self, ts: datetime) -> None:
        d = _et_date(ts)
        if self.session_date != d:
            self.session_date = d
            self.cum_amount = 0.0
            self.cum_volume = 0

    def add_trade(self, *, price: float, size: int, ts: datetime) -> None:
        self._maybe_reset(ts)
        self.cum_amount += price * size
        self.cum_volume += size
        self.last_price = price

    def vwap(self) -> float:
        return (self.cum_amount / self.cum_volume) if self.cum_volume else 0.0
```

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_trade_accumulator.py -v`
Expected: PASS(3 项)

- [ ] **Step 5: 提交**

```bash
git add apps/collector/us/trade_hub.py tests/unit/collector/test_trade_accumulator.py
git commit -m "feat: 美股 TradeAccumulator 逐笔 VWAP 累加 + 跨日重置"
```

---

## 步骤 4 · UsIntradayWriter(分时点 + 真 VWAP 均价线)

### Task 4: compute_us_point + UsIntradayWriter.flush

**Files:**
- Create: `apps/collector/us/intraday_line_writer.py`
- Test: `tests/unit/collector/test_us_intraday_writer.py`

writer 不自带 loop(由 TradeHub 节流驱动),只暴露 `flush(symbol, accum, now)`:仅 RTH 写分时点 + 发 bus。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_us_intraday_writer.py
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from apps.collector.us.intraday_line_writer import compute_us_point, UsIntradayWriter
from apps.collector.us.trade_hub import TradeAccumulator


def test_compute_us_point_vwap():
    p = compute_us_point(
        "AAPL", datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc),
        price=110.0, cum_amount=3200.0, cum_volume=30)
    assert p.avg_price == pytest.approx(3200.0 / 30)
    assert p.price == 110.0
    assert p.ts.second == 0  # 截断到分钟


def test_compute_us_point_zero_volume_avg_is_price():
    p = compute_us_point(
        "AAPL", datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc),
        price=110.0, cum_amount=0.0, cum_volume=0)
    assert p.avg_price == 110.0


@pytest.mark.asyncio
async def test_flush_writes_and_publishes_in_rth():
    repo = MagicMock()
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    redis.set_msgpack = AsyncMock()
    acc = TradeAccumulator()
    acc.add_trade(price=110.0, size=30, ts=datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc))
    w = UsIntradayWriter(repo, redis)
    await w.flush("AAPL", acc, now=datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc))  # 10:31 ET RTH
    assert repo.insert_points.call_count == 1
    assert redis._r.xadd.await_count == 1
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["symbol"] == "AAPL" and "avg_price" in payload


@pytest.mark.asyncio
async def test_flush_skips_outside_rth():
    repo = MagicMock()
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    acc = TradeAccumulator()
    acc.add_trade(price=110.0, size=30, ts=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc))
    w = UsIntradayWriter(repo, redis)
    await w.flush("AAPL", acc, now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc))  # 08:00 ET 盘前
    assert repo.insert_points.call_count == 0
    assert redis._r.xadd.await_count == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_us_intraday_writer.py -v`
Expected: FAIL(ImportError)

- [ ] **Step 3: 实现**

```python
# apps/collector/us/intraday_line_writer.py
"""美股分时图写入器。逐笔 VWAP 累加 (TradeHub 驱动), 仅 RTH 写分时点。

均价线 = 当日累计成交额/累计成交量 (真 VWAP, Σp×s / Σs)。
独立分时库 intraday_us.duckdb (物理隔离, 雷区 6)。成交量为 IEX 口径。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from core.cache import keys
from core.domain.market_sessions import is_us_regular_session
from core.persistence.intraday_repo import IntradayLineRepo, IntradayPoint

log = structlog.get_logger(__name__)


def compute_us_point(symbol: str, ts: datetime, *, price: float,
                     cum_amount: float, cum_volume: int) -> IntradayPoint:
    """算分时点。均价 = 累计成交额/累计成交量; 量为 0 退化为现价。ts 截断到分钟。"""
    avg = (cum_amount / cum_volume) if cum_volume else price
    minute_ts = ts.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return IntradayPoint(symbol=symbol, ts=minute_ts, price=price,
                         cum_amount=cum_amount, cum_volume=cum_volume, avg_price=avg)


class UsIntradayWriter:
    """美股分时 writer。无 loop, 由 TradeHub 节流调 flush。"""

    def __init__(self, repo: IntradayLineRepo, redis):
        self._repo = repo
        self._redis = redis

    async def flush(self, symbol: str, accum, *, now: datetime) -> None:
        if not is_us_regular_session(now):
            return  # 仅 RTH 画分时
        if accum.cum_volume <= 0 and accum.last_price <= 0:
            return
        p = compute_us_point(symbol, now, price=accum.last_price,
                             cum_amount=accum.cum_amount, cum_volume=accum.cum_volume)
        try:
            self._repo.insert_points([p])
        except Exception as e:  # noqa: BLE001
            log.warning("us_intraday.write_failed", symbol=symbol, error=str(e))
            return
        payload = {"market": "us", "symbol": symbol, "ts": p.ts.isoformat(),
                   "price": p.price, "avg_price": p.avg_price}
        try:
            await self._redis._r.xadd(  # noqa: SLF001
                keys.BUS_INTRADAY_UPDATED,
                {"data": json.dumps(payload).encode()},
                maxlen=20000, approximate=True)
            await self._redis.set_msgpack(
                keys.cache_intraday_current("us", symbol), payload, ttl=120)
        except Exception as e:  # noqa: BLE001
            log.warning("us_intraday.publish_failed", symbol=symbol, error=str(e))
```

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_us_intraday_writer.py -v`
Expected: PASS(4 项)

- [ ] **Step 5: 提交**

```bash
git add apps/collector/us/intraday_line_writer.py tests/unit/collector/test_us_intraday_writer.py
git commit -m "feat: 美股 UsIntradayWriter 真VWAP分时点 (仅RTH) + 发bus"
```

---

## 步骤 5 · UsBarTicker(进行中态 final=false + 桶滚动 provisional)

### Task 5: UsBarTicker.publish_current(进行中桶推 final=false)

**Files:**
- Create: `apps/collector/us/bar_ticker.py`
- Test: `tests/unit/collector/test_us_bar_ticker.py`

ticker 不自带 loop。`publish_current(symbol, interval, tracker, now)` 推 final=false + 写 :current;`publish_provisional(symbol, interval, tracker)` 推 final=true 到 bus(不入库不写 :current)。tracker 由 TradeHub 维护。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_us_bar_ticker.py
import json
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from core.domain.bucket_state import BucketState
from apps.collector.us.bar_ticker import UsBarTicker, BucketTracker


def _tracker():
    return BucketTracker(
        open_ts=datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc),
        close_ts=datetime(2026, 6, 1, 14, 35, tzinfo=timezone.utc),
        state=BucketState(open=Decimal("100"), high=Decimal("105"),
                          low=Decimal("99"), close=Decimal("104"), volume=500),
    )


@pytest.mark.asyncio
async def test_publish_current_final_false_and_writes_current():
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    redis.set_msgpack = AsyncMock()
    t = UsBarTicker(redis)
    await t.publish_current("AAPL", "5m", _tracker())
    assert redis._r.xadd.await_count == 1
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["final"] is False
    assert payload["interval"] == "5m" and payload["symbol"] == "AAPL"
    assert payload["ts"].startswith("2026-06-01T14:35")  # close_ts
    assert redis.set_msgpack.await_count == 1  # 写 :current


@pytest.mark.asyncio
async def test_publish_provisional_final_true_bus_only():
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    redis.set_msgpack = AsyncMock()
    t = UsBarTicker(redis)
    await t.publish_provisional("AAPL", "5m", _tracker())
    assert redis._r.xadd.await_count == 1
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["final"] is True
    # provisional 不写 :current (DuckDB 由 poller 写)
    assert redis.set_msgpack.await_count == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_us_bar_ticker.py -v`
Expected: FAIL(ImportError)

- [ ] **Step 3: 实现**

```python
# apps/collector/us/bar_ticker.py
"""美股 K 线进行中态 (trades 驱动)。

逐笔攒进行中桶, 推 final=false + 写 :current (不入库)。
桶滚动时对刚收桶补发 provisional final=true 仅到 bus (填 REST SIP ~20min 延迟洞,
不写 DuckDB —— DuckDB 只存 SIP 权威收线 bar)。tracker 由 TradeHub 维护。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import structlog

from core.cache import keys
from core.domain.bucket_state import BucketState

log = structlog.get_logger(__name__)

# 进行中态支持的周期 → 分钟
INTERVAL_MIN = {"5m": 5, "15m": 15, "30m": 30, "60m": 60, "4h": 240}


@dataclass
class BucketTracker:
    open_ts: datetime
    close_ts: datetime
    state: BucketState


def _payload(symbol: str, interval: str, tr: BucketTracker, *, final: bool) -> dict:
    return {
        "market": "us", "symbol": symbol, "interval": interval,
        "ts": tr.close_ts.isoformat(),
        "open": float(tr.state.open), "high": float(tr.state.high),
        "low": float(tr.state.low), "close": float(tr.state.close),
        "volume": int(tr.state.volume), "final": final,
    }


class UsBarTicker:
    def __init__(self, redis):
        self._redis = redis

    async def publish_current(self, symbol: str, interval: str, tr: BucketTracker) -> None:
        """进行中桶: final=false + 写 :current。"""
        mins = INTERVAL_MIN.get(interval, 5)
        payload = _payload(symbol, interval, tr, final=False)
        try:
            await self._redis._r.xadd(  # noqa: SLF001
                keys.BUS_BARS_UPDATED,
                {"data": json.dumps(payload).encode()},
                maxlen=10000, approximate=True)
            await self._redis.set_msgpack(
                keys.cache_bars_current("us", symbol, interval), payload, ttl=mins * 120)
        except Exception as e:  # noqa: BLE001
            log.warning("us_ticker.publish_failed",
                        symbol=symbol, interval=interval, error=str(e))

    async def publish_provisional(self, symbol: str, interval: str, tr: BucketTracker) -> None:
        """桶滚动: 刚收桶补发 final=true 仅到 bus (填 SIP 洞, 不入库不写 :current)。"""
        payload = _payload(symbol, interval, tr, final=True)
        try:
            await self._redis._r.xadd(  # noqa: SLF001
                keys.BUS_BARS_UPDATED,
                {"data": json.dumps(payload).encode()},
                maxlen=10000, approximate=True)
        except Exception as e:  # noqa: BLE001
            log.warning("us_ticker.provisional_failed",
                        symbol=symbol, interval=interval, error=str(e))
```

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_us_bar_ticker.py -v`
Expected: PASS(2 项)

- [ ] **Step 5: 提交**

```bash
git add apps/collector/us/bar_ticker.py tests/unit/collector/test_us_bar_ticker.py
git commit -m "feat: 美股 UsBarTicker 进行中态final=false + 桶滚动provisional(bus only)"
```

---

## 步骤 6 · TradeHub 主循环(逐笔分发 + 桶维护 + 节流 flush)

### Task 6: TradeHub.on_trade(桶维护 + 滚动检测)

**Files:**
- Modify: `apps/collector/us/trade_hub.py`(加 `TradeHub` 类)
- Test: `tests/unit/collector/test_trade_hub_buckets.py`

`on_trade` 同步(纯内存):更新累加器 + 各被订阅周期当前桶;桶滚动时把刚收桶塞 `_just_closed`。`seed_baseline` 用 bar_repo 已存 5m 补大周期基线。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_trade_hub_buckets.py
from datetime import datetime, timezone
from unittest.mock import MagicMock
from apps.collector.us.trade_hub import TradeHub


def _utc(h, m, s=0):
    return datetime(2026, 6, 1, h, m, s, tzinfo=timezone.utc)


def test_on_trade_builds_current_bucket():
    hub = TradeHub(redis=MagicMock(), repo=MagicMock(), writer=MagicMock(), ticker=MagicMock())
    hub._subs = {"AAPL": {"5m"}}
    hub.on_trade("AAPL", price=100.0, size=10, ts=_utc(14, 31))  # 10:31 ET, 桶 14:30-14:35
    tr = hub._buckets[("AAPL", "5m")]
    assert tr.open_ts == _utc(14, 30)
    assert float(tr.state.close) == 100.0
    assert tr.state.volume == 10
    assert "AAPL" in hub._dirty


def test_on_trade_accumulates_volume_same_bucket():
    hub = TradeHub(redis=MagicMock(), repo=MagicMock(), writer=MagicMock(), ticker=MagicMock())
    hub._subs = {"AAPL": {"5m"}}
    hub.on_trade("AAPL", price=100.0, size=10, ts=_utc(14, 31))
    hub.on_trade("AAPL", price=105.0, size=20, ts=_utc(14, 32))
    tr = hub._buckets[("AAPL", "5m")]
    assert tr.state.volume == 30  # 累加
    assert float(tr.state.high) == 105.0
    assert float(tr.state.close) == 105.0


def test_on_trade_roll_marks_just_closed():
    hub = TradeHub(redis=MagicMock(), repo=MagicMock(), writer=MagicMock(), ticker=MagicMock())
    hub._subs = {"AAPL": {"5m"}}
    hub.on_trade("AAPL", price=100.0, size=10, ts=_utc(14, 31))   # 桶 14:30-14:35
    hub.on_trade("AAPL", price=106.0, size=5, ts=_utc(14, 36))    # 滚到 14:35-14:40
    # 14:30-14:35 桶应进 just_closed
    assert ("AAPL", "5m") in hub._just_closed
    closed = hub._just_closed[("AAPL", "5m")]
    assert closed.open_ts == _utc(14, 30)
    # 新桶已建
    assert hub._buckets[("AAPL", "5m")].open_ts == _utc(14, 35)
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_trade_hub_buckets.py -v`
Expected: FAIL(TradeHub 无此构造/方法)

- [ ] **Step 3: 实现(在 trade_hub.py 追加 TradeHub 类)**

```python
# apps/collector/us/trade_hub.py 追加 (顶部补 import)
from decimal import Decimal

from core.domain.bucket_state import BucketState, current_bucket, seed_baseline, update_bucket
from apps.collector.us.bar_ticker import INTERVAL_MIN, BucketTracker


class TradeHub:
    """逐笔中枢: 累加器 + 进行中桶维护 + ~1s 节流分发。"""

    def __init__(self, redis, repo, writer, ticker):
        self._redis = redis
        self._repo = repo          # bar_repo (RW, 同进程查已收 5m 补基线)
        self._writer = writer      # UsIntradayWriter
        self._ticker = ticker      # UsBarTicker
        self._accums: dict[str, TradeAccumulator] = {}
        self._buckets: dict[tuple[str, str], BucketTracker] = {}
        self._just_closed: dict[tuple[str, str], BucketTracker] = {}
        self._subs: dict[str, set[str]] = {}   # symbol -> {interval} (订阅)
        self._dirty: set[str] = set()
        self._stopped = False

    def on_trade(self, symbol: str, *, price: float, size: int, ts: datetime) -> None:
        """逐笔处理 (同步纯内存)。累加 + 更新各订阅周期当前桶 + 滚动检测。"""
        acc = self._accums.get(symbol)
        if acc is None:
            acc = self._accums[symbol] = TradeAccumulator()
        acc.add_trade(price=price, size=size, ts=ts)

        price_dec = Decimal(str(price))
        for interval in self._subs.get(symbol, set()):
            mins = INTERVAL_MIN.get(interval)
            if mins is None:
                continue
            ob = current_bucket("us", ts, mins)
            if ob is None:
                continue
            open_ts, close_ts = ob
            key = (symbol, interval)
            tr = self._buckets.get(key)
            if tr is None or tr.open_ts != open_ts:
                if tr is not None:
                    self._just_closed[key] = tr   # 旧桶滚动 → provisional 待发
                base = self._seed(symbol, interval, mins, open_ts, close_ts)
                base_vol = base.volume if base else 0
                state = update_bucket(base, price_dec, volume=base_vol + size)
                tr = BucketTracker(open_ts=open_ts, close_ts=close_ts, state=state)
            else:
                state = update_bucket(tr.state, price_dec, volume=tr.state.volume + size)
                tr.state = state
            self._buckets[key] = tr
        self._dirty.add(symbol)

    def _seed(self, symbol, interval, mins, open_ts, close_ts):
        """大周期当前桶用已收线 5m bar 补基线 (重启/中途订阅防 open 漂移)。"""
        if self._repo is None or mins <= 5:
            return None
        try:
            src = self._repo.fetch_history_paged("us", symbol, "5m", before=close_ts, limit=mins // 5)
            src = [b for b in src if open_ts < b.ts <= close_ts]
            return seed_baseline(src)
        except Exception:  # noqa: BLE001
            return None
```

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_trade_hub_buckets.py -v`
Expected: PASS(3 项)

- [ ] **Step 5: 提交**

```bash
git add apps/collector/us/trade_hub.py tests/unit/collector/test_trade_hub_buckets.py
git commit -m "feat: 美股 TradeHub on_trade 逐笔攒进行中桶 + 滚动检测"
```

---

### Task 7: TradeHub flush loop + 订阅扫描

**Files:**
- Modify: `apps/collector/us/trade_hub.py`(加 `_scan_subs` / `_flush` / `run`)
- Test: `tests/unit/collector/test_trade_hub_flush.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_trade_hub_flush.py
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from apps.collector.us.trade_hub import TradeHub
from apps.collector.us.bar_ticker import BucketTracker
from core.domain.bucket_state import BucketState
from decimal import Decimal


@pytest.mark.asyncio
async def test_flush_calls_writer_and_ticker_then_clears():
    writer = MagicMock(); writer.flush = AsyncMock()
    ticker = MagicMock(); ticker.publish_current = AsyncMock(); ticker.publish_provisional = AsyncMock()
    hub = TradeHub(redis=MagicMock(), repo=MagicMock(), writer=writer, ticker=ticker)
    hub._subs = {"AAPL": {"5m"}}
    hub.on_trade("AAPL", price=100.0, size=10,
                 ts=datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc))
    await hub._flush(now=datetime(2026, 6, 1, 14, 31, tzinfo=timezone.utc))
    writer.flush.assert_awaited_once()                 # 分时
    ticker.publish_current.assert_awaited_once()       # 进行中态
    assert hub._dirty == set()                         # 清 dirty


@pytest.mark.asyncio
async def test_flush_emits_provisional_for_just_closed():
    writer = MagicMock(); writer.flush = AsyncMock()
    ticker = MagicMock(); ticker.publish_current = AsyncMock(); ticker.publish_provisional = AsyncMock()
    hub = TradeHub(redis=MagicMock(), repo=MagicMock(), writer=writer, ticker=ticker)
    hub._just_closed[("AAPL", "5m")] = BucketTracker(
        open_ts=datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc),
        close_ts=datetime(2026, 6, 1, 14, 35, tzinfo=timezone.utc),
        state=BucketState(open=Decimal("1"), high=Decimal("2"),
                          low=Decimal("1"), close=Decimal("2"), volume=1))
    await hub._flush(now=datetime(2026, 6, 1, 14, 36, tzinfo=timezone.utc))
    ticker.publish_provisional.assert_awaited_once()
    assert hub._just_closed == {}
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_trade_hub_flush.py -v`
Expected: FAIL(TradeHub 无 _flush)

- [ ] **Step 3: 实现(trade_hub.py 追加;顶部补 import)**

```python
# 顶部补: from core.domain.market_calendar import is_trading_day
#         from core.domain.market_sessions import is_market_session_open

    async def _scan_subs(self) -> None:
        """刷新订阅: state:subscribe:us:{symbol}:{interval}。"""
        subs: dict[str, set[str]] = {}
        try:
            cursor = 0
            while True:
                cursor, found = await self._redis._r.scan(  # noqa: SLF001
                    cursor, match="state:subscribe:us:*", count=200)
                for k in found:
                    kk = k.decode() if isinstance(k, bytes) else k
                    parts = kk.split(":")
                    if len(parts) >= 5 and parts[4] in INTERVAL_MIN:
                        subs.setdefault(parts[3], set()).add(parts[4])
                if cursor == 0:
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("us_hub.scan_failed", error=str(e))
        self._subs = subs

    async def _flush(self, *, now: datetime) -> None:
        # 1. 桶滚动 provisional (填 SIP 洞)
        for (symbol, interval), tr in list(self._just_closed.items()):
            await self._ticker.publish_provisional(symbol, interval, tr)
        self._just_closed.clear()
        # 2. dirty 标的: 分时 + 进行中态
        dirty = list(self._dirty)
        self._dirty.clear()
        for symbol in dirty:
            acc = self._accums.get(symbol)
            if acc is not None:
                try:
                    await self._writer.flush(symbol, acc, now=now)
                except Exception as e:  # noqa: BLE001
                    log.warning("us_hub.writer_failed", symbol=symbol, error=str(e))
            for interval in self._subs.get(symbol, set()):
                tr = self._buckets.get((symbol, interval))
                if tr is not None:
                    try:
                        await self._ticker.publish_current(symbol, interval, tr)
                    except Exception as e:  # noqa: BLE001
                        log.warning("us_hub.ticker_failed",
                                    symbol=symbol, interval=interval, error=str(e))

    async def run(self) -> None:
        log.info("us_trade_hub.started")
        tick = 0
        while not self._stopped:
            try:
                if tick % SUBS_REFRESH_TICKS == 0:
                    await self._scan_subs()
                tick += 1
                if is_trading_day("us") and is_market_session_open("us"):
                    await self._flush(now=datetime.now(timezone.utc))
            except Exception as e:  # noqa: BLE001
                log.warning("us_hub.loop_error", error=str(e))
            await asyncio.sleep(FLUSH_INTERVAL_S)


async def run_trade_hub(hub: TradeHub) -> None:
    await hub.run()
```

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_trade_hub_flush.py tests/unit/collector/test_trade_hub_buckets.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/collector/us/trade_hub.py tests/unit/collector/test_trade_hub_flush.py
git commit -m "feat: 美股 TradeHub 节流flush loop + 订阅扫描 + provisional发射"
```

---

## 步骤 7 · ws_consumer 换 trades 频道

### Task 8: ws_consumer 订阅/解析 trades → 分发 TradeHub

**Files:**
- Modify: `apps/collector/us/ws_consumer.py`(`_parse_bar`→`_parse_trade`,`handle_bar` 删,订阅改 trades,consume 分发 hub)
- Test: `tests/unit/collector/test_ws_us_parse_trade.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_ws_us_parse_trade.py
from datetime import timezone
from apps.collector.us.ws_consumer import _parse_trade


def test_parse_trade_extracts_price_size_ts():
    msg = {"T": "t", "S": "AAPL", "p": 150.25, "s": 100,
           "t": "2026-06-01T14:30:00.123Z"}
    tr = _parse_trade(msg)
    assert tr is not None
    symbol, price, size, ts = tr
    assert symbol == "AAPL"
    assert price == 150.25
    assert size == 100
    assert ts.tzinfo == timezone.utc and ts.hour == 14 and ts.minute == 30


def test_parse_trade_missing_symbol_returns_none():
    assert _parse_trade({"T": "t", "p": 1.0, "s": 1, "t": "2026-06-01T14:30:00Z"}) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_ws_us_parse_trade.py -v`
Expected: FAIL(ImportError: _parse_trade)

- [ ] **Step 3: 实现**

`apps/collector/us/ws_consumer.py` 改造:

① 模块 docstring `:1-15` 改为说明 trades(可保留大意)。删除 `_parse_bar`(`:67-96`)、`_bar_to_event`(`:103-115`)、`handle_bar`(`:118-148`)。删除不再需要的 import:`Bar`、`BarRepo`、`RedisBarsCache`、`Decimal`、`keys`、`json` 中仅 handle_bar 用到的(保留 json 给认证)。`timedelta` 不再需要。

② 加解析:

```python
def _parse_trade(item: dict) -> tuple[str, float, int, datetime] | None:
    """Alpaca trade 消息 → (symbol, price, size, ts_utc)。

    格式: {"T":"t","S":"AAPL","p":150.25,"s":100,"t":"2026-06-01T14:30:00.123Z"}
    """
    try:
        symbol = item.get("S")
        if not symbol:
            return None
        ts = datetime.fromisoformat(item["t"].replace("Z", "+00:00")).astimezone(timezone.utc)
        return (symbol, float(item["p"]), int(float(item["s"])), ts)
    except Exception as e:  # noqa: BLE001
        log.warning("ws_us.parse_trade_failed", error=str(e), item=str(item)[:200])
        return None
```

③ `consume_loop` 签名改为接收 hub:

```python
async def consume_loop(*, hub) -> None:
    """Alpaca WS trades 长连消费。逐笔 → hub.on_trade。被 cancel 干净退出。"""
    api_key = os.getenv("ALPACA_API_KEY", "")
    api_secret = os.getenv("ALPACA_SECRET_KEY", "")
    if not api_key or not api_secret:
        log.warning("ws_us.no_alpaca_keys", note="WS consumer 无法启动")
        return
    symbols = _load_symbols()
    log.info("ws_us.start", symbols=len(symbols))
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(_WS_URL, ping_interval=30, ping_timeout=10) as ws:
                await ws.send(json.dumps({"action": "auth", "key": api_key, "secret": api_secret}))
                auth_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if isinstance(auth_resp, list):
                    for item in auth_resp:
                        if item.get("T") == "error":
                            log.error("ws_us.auth_failed", msg=item.get("msg", ""))
                            return
                log.info("ws_us.authenticated")
                await ws.send(json.dumps({"action": "subscribe", "trades": symbols}))
                sub_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if isinstance(sub_resp, list):
                    for item in sub_resp:
                        if item.get("T") == "subscription":
                            log.info("ws_us.subscribed", count=len(item.get("trades", [])))
                log.info("ws_us.connected", symbols=len(symbols))
                backoff = 1.0
                async for raw in ws:
                    try:
                        msgs = json.loads(raw)
                        if not isinstance(msgs, list):
                            continue
                        for item in msgs:
                            if item.get("T") == "t":
                                tr = _parse_trade(item)
                                if tr:
                                    hub.on_trade(tr[0], price=tr[1], size=tr[2], ts=tr[3])
                            elif item.get("T") == "error":
                                log.warning("ws_us.stream_error",
                                            code=item.get("code"), msg=item.get("msg"))
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        log.warning("ws_us.handle_failed", error=str(e))
        except asyncio.CancelledError:
            log.info("ws_us.cancelled")
            return
        except Exception as e:  # noqa: BLE001
            log.warning("ws_us.connection_lost", error=str(e), retry_in=backoff)
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                log.info("ws_us.cancelled")
                return
            backoff = min(backoff * 2, 60)
```

(顶部 import 保留 `asyncio`/`json`/`os`/`structlog`/`websockets`/`datetime,timezone`。)

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_ws_us_parse_trade.py -v && python -c "from apps.collector.us.ws_consumer import consume_loop, _parse_trade; print('ok')"`
Expected: PASS + ok

- [ ] **Step 5: 提交**

```bash
git add apps/collector/us/ws_consumer.py tests/unit/collector/test_ws_us_parse_trade.py
git commit -m "feat: 美股 ws_consumer 换 trades 频道, 逐笔分发 TradeHub (1m停止落库)"
```

---

## 步骤 8 · UsBarPoller(REST SIP 收线源)

### Task 9: UsBarPoller 周期拉收线 5m/15m/30m + final=true + 触发聚合

**Files:**
- Create: `apps/collector/us/bar_poller.py`
- Test: `tests/unit/collector/test_us_bar_poller.py`

每 ~60s 对被订阅美股标的调 `fetch_intraday`(5m/15m/30m),新出现的已收线根 upsert + 发 final=true;5m 收线触发 `aggregate_and_publish(60m/4h)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_us_bar_poller.py
import json
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from core.domain.models import Bar
from apps.collector.us.bar_poller import UsBarPoller


def _bar(ts, interval="5m"):
    return Bar(market="us", symbol="AAPL", ts=ts, open=Decimal("1"), high=Decimal("2"),
               low=Decimal("1"), close=Decimal("2"), volume=10, interval=interval)


@pytest.mark.asyncio
async def test_poll_upserts_new_closed_and_publishes(monkeypatch):
    repo = MagicMock()
    repo.fetch_history_paged.return_value = []  # 库里没有 → 全是新根
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    adapter = MagicMock()
    closed = _bar(datetime(2026, 6, 1, 14, 35, tzinfo=timezone.utc))
    adapter.fetch_intraday = AsyncMock(return_value=[closed])
    poller = UsBarPoller(repo, redis, adapter)
    # 只跑 5m, mock 掉聚合
    agg = AsyncMock()
    monkeypatch.setattr("apps.collector.us.bar_poller.aggregate_and_publish", agg)
    await poller.poll_one("AAPL", "5m")
    repo.insert_bars.assert_called_once()
    assert redis._r.xadd.await_count == 1
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["final"] is True and payload["interval"] == "5m"
    agg.assert_awaited_once()  # 5m 收线触发聚合


@pytest.mark.asyncio
async def test_poll_skips_already_stored(monkeypatch):
    stored = _bar(datetime(2026, 6, 1, 14, 35, tzinfo=timezone.utc))
    repo = MagicMock()
    repo.fetch_history_paged.return_value = [stored]  # 已存在
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    adapter = MagicMock()
    adapter.fetch_intraday = AsyncMock(return_value=[stored])
    poller = UsBarPoller(repo, redis, adapter)
    monkeypatch.setattr("apps.collector.us.bar_poller.aggregate_and_publish", AsyncMock())
    await poller.poll_one("AAPL", "5m")
    repo.insert_bars.assert_not_called()
    assert redis._r.xadd.await_count == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_us_bar_poller.py -v`
Expected: FAIL(ImportError)

- [ ] **Step 3: 实现**

```python
# apps/collector/us/bar_poller.py
"""美股收线源 (REST SIP)。周期拉 5m/15m/30m 已收线根 → 入库 + 发 final=true。

成交量权威 (SIP 全市场), 喂 CD 信号/量指标。延迟 ~15-20min (免费层),
最近窗的实时跳由 TradeHub(IEX trades) 的 provisional 兜底。
5m 收线触发 aggregate_and_publish(60m/4h)。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog

from core.cache import keys
from core.domain.market_calendar import is_trading_day
from core.domain.market_sessions import is_market_session_open
from apps.collector.jobs.aggregate_derived import aggregate_and_publish
from apps.collector.us.bar_ticker import INTERVAL_MIN

log = structlog.get_logger(__name__)

POLL_INTERVAL_S = 60
_POLL_INTERVALS = ("5m", "15m", "30m")
_FREQ = {"5m": "5", "15m": "15", "30m": "30"}


class UsBarPoller:
    def __init__(self, repo, redis, adapter):
        self._repo = repo
        self._redis = redis
        self._adapter = adapter
        self._stopped = False

    async def poll_one(self, symbol: str, interval: str) -> None:
        try:
            bars = await self._adapter.fetch_intraday(symbol, _FREQ[interval])
        except Exception as e:  # noqa: BLE001
            log.warning("us_poller.fetch_failed", symbol=symbol, interval=interval, error=str(e))
            return
        if not bars:
            return
        # 已存在的最新 ts → 只入库严格更新的根
        try:
            existing = self._repo.fetch_history_paged("us", symbol, interval, before=None, limit=1)
            last_ts = existing[-1].ts if existing else None
        except Exception:  # noqa: BLE001
            last_ts = None
        fresh = [b for b in bars if last_ts is None or b.ts > last_ts]
        if not fresh:
            return
        try:
            self._repo.insert_bars(fresh)
        except Exception as e:  # noqa: BLE001
            log.warning("us_poller.db_write_failed", symbol=symbol, error=str(e))
        latest = fresh[-1]
        payload = {
            "market": "us", "symbol": symbol, "interval": interval,
            "ts": latest.ts.isoformat(), "open": float(latest.open),
            "high": float(latest.high), "low": float(latest.low),
            "close": float(latest.close), "volume": int(latest.volume), "final": True,
        }
        try:
            await self._redis._r.xadd(  # noqa: SLF001
                keys.BUS_BARS_UPDATED,
                {"data": json.dumps(payload).encode()}, maxlen=10000, approximate=True)
        except Exception as e:  # noqa: BLE001
            log.warning("us_poller.xadd_failed", error=str(e))
        if interval == "5m":
            await aggregate_and_publish(
                self._repo, self._redis, "us", symbol,
                targets=("60m", "4h"), now=datetime.now(timezone.utc))

    async def _scan_symbols(self) -> set[str]:
        active: set[str] = set()
        try:
            cursor = 0
            while True:
                cursor, found = await self._redis._r.scan(  # noqa: SLF001
                    cursor, match="state:subscribe:us:*", count=200)
                for k in found:
                    kk = k.decode() if isinstance(k, bytes) else k
                    parts = kk.split(":")
                    if len(parts) >= 4:
                        active.add(parts[3])
                if cursor == 0:
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("us_poller.scan_failed", error=str(e))
        return active

    async def run(self) -> None:
        log.info("us_bar_poller.started")
        while not self._stopped:
            try:
                if is_trading_day("us") and is_market_session_open("us"):
                    for symbol in await self._scan_symbols():
                        for interval in _POLL_INTERVALS:
                            await self.poll_one(symbol, interval)
            except Exception as e:  # noqa: BLE001
                log.warning("us_poller.loop_error", error=str(e))
            await asyncio.sleep(POLL_INTERVAL_S)


async def run_us_bar_poller(repo, redis, adapter) -> None:
    await UsBarPoller(repo, redis, adapter).run()
```

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_us_bar_poller.py -v`
Expected: PASS(2 项)

- [ ] **Step 5: 提交**

```bash
git add apps/collector/us/bar_poller.py tests/unit/collector/test_us_bar_poller.py
git commit -m "feat: 美股 UsBarPoller REST SIP 收线入库 + final=true + 触发聚合"
```

---

## 步骤 9 · attach_intraday_route 带昨收 + 接线 us/main

### Task 10: attach_intraday_route 加可选 get_bar_repo(返回 prev_close)

**Files:**
- Modify: `apps/collector/base.py`(`attach_intraday_route` 加可选参数)
- Test: `tests/unit/collector/test_attach_intraday_prev_close.py`

> 注:`attach_intraday_route` 当前签名 `(app, get_intraday_repo, market)`。加可选 `get_bar_repo=None`;提供时响应顶层带 `prev_close`(最近一根 1d close)。A 股调用不传 → 行为不变。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_attach_intraday_prev_close.py
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.domain.models import Bar
from apps.collector.base import attach_intraday_route


def test_response_includes_prev_close_when_bar_repo_given():
    app = FastAPI()
    intraday_repo = MagicMock()
    intraday_repo.fetch_day.return_value = [
        {"ts": "2026-06-01T14:31:00+00:00", "price": 100.0,
         "cum_amount": 1000.0, "cum_volume": 10, "avg_price": 100.0}]
    bar_repo = MagicMock()
    bar_repo.fetch_history_paged.return_value = [
        Bar(market="us", symbol="AAPL", ts=datetime(2026, 5, 30, tzinfo=timezone.utc),
            open=Decimal("1"), high=Decimal("2"), low=Decimal("1"), close=Decimal("99.5"),
            volume=1, interval="1d")]
    attach_intraday_route(app, lambda: intraday_repo, "us", get_bar_repo=lambda: bar_repo)
    c = TestClient(app)
    r = c.get("/internal/intraday-line", params={"symbol": "AAPL"})
    assert r.status_code == 200
    body = r.json()
    assert body["prev_close"] == 99.5
    assert len(body["points"]) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_attach_intraday_prev_close.py -v`
Expected: FAIL(TypeError: 不接受 get_bar_repo,或无 prev_close 字段)

- [ ] **Step 3: 实现**

`apps/collector/base.py` 找到 `attach_intraday_route`(A 股 Task 13 已建),改签名加 `get_bar_repo=None`,在返回体加 prev_close:

```python
def attach_intraday_route(app, get_intraday_repo, market: str, *, get_bar_repo=None) -> None:
    @app.get("/internal/intraday-line")
    async def intraday_line(symbol: str, date: str | None = None):  # noqa: ANN202
        from datetime import datetime, timezone
        repo = get_intraday_repo()
        if repo is None:
            return {"symbol": symbol, "points": [], "prev_close": None,
                    "meta": {"stale": True, "reason": "repo_not_ready"}}
        day = (datetime.fromisoformat(date).date() if date
               else datetime.now(timezone.utc).date())
        try:
            pts = repo.fetch_day(symbol, day)
        except Exception as e:  # noqa: BLE001
            return {"symbol": symbol, "points": [], "prev_close": None,
                    "meta": {"stale": True, "reason": str(e)}}
        prev_close = None
        if get_bar_repo is not None:
            try:
                br = get_bar_repo()
                daily = br.fetch_history_paged(market, symbol, "1d", before=None, limit=1)
                if daily:
                    prev_close = float(daily[-1].close)
            except Exception:  # noqa: BLE001
                prev_close = None
        return {"symbol": symbol, "points": pts, "prev_close": prev_close,
                "meta": {"stale": False}}
```

(若现有实现签名/返回体不同,以"加 `get_bar_repo` 可选参 + 返回 `prev_close`"为准,保持 A 股不传时旧字段不变。)

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_attach_intraday_prev_close.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/collector/base.py tests/unit/collector/test_attach_intraday_prev_close.py
git commit -m "feat: attach_intraday_route 可选带昨收 prev_close (美股分时基准线)"
```

---

### Task 11: us/main 接线 hub/writer/ticker/poller/intraday repo/purge/sweep 降频

**Files:**
- Modify: `apps/collector/us/main.py`
- Test: import 冒烟

- [ ] **Step 1: 改 ws_consumer 接线 + 新组件(lifespan 内)**

`apps/collector/us/main.py` lifespan 内,把 `:161-167` 的 ws 接线段替换为完整实时链路。在 `bar_repo` / `redis_cache` / `redis_bars` / `registry` 已就绪后(`:159 sched.start()` 后)加:

```python
    # === 美股实时链路: trades WS → TradeHub → writer/ticker; REST SIP poller 收线 ===
    from core.persistence.intraday_repo import IntradayLineRepo
    from apps.collector.us.intraday_line_writer import UsIntradayWriter
    from apps.collector.us.bar_ticker import UsBarTicker
    from apps.collector.us.trade_hub import TradeHub, run_trade_hub
    from apps.collector.us.bar_poller import run_us_bar_poller
    from apps.collector.us.ws_consumer import consume_loop as ws_consume_loop

    intraday_repo = IntradayLineRepo(str(_DATA / "intraday_us.duckdb"))
    set_us_intraday_repo_override(intraday_repo)  # module 级路由惰性解析 (见 Step 3)

    _us_writer = UsIntradayWriter(intraday_repo, redis_cache)
    _us_ticker = UsBarTicker(redis_cache)
    _hub = TradeHub(redis=redis_cache, repo=bar_repo, writer=_us_writer, ticker=_us_ticker)

    _ws_task = asyncio.create_task(ws_consume_loop(hub=_hub), name="us.ws_consumer")
    _hub_task = asyncio.create_task(run_trade_hub(_hub), name="us.trade_hub")
    us_adapter = registry.get("us")
    _poller_task = asyncio.create_task(
        run_us_bar_poller(bar_repo, redis_cache, us_adapter), name="us.bar_poller")
    log.info("us_realtime.bootstrapped")

    # === 分时 90 天 purge cron ===
    async def _purge_intraday():
        from datetime import datetime, timezone, timedelta
        try:
            intraday_repo.purge_before(datetime.now(timezone.utc) - timedelta(days=90))
            log.info("us_intraday.purged", before_days=90)
        except Exception as e:  # noqa: BLE001
            log.warning("us_intraday.purge_failed", error=str(e))
    sched.add_job(_purge_intraday, "cron", hour=7, minute=30,
                  id="us:intraday_purge", max_instances=1, coalesce=True)
```

> `registry.get("us")` 取美股 adapter。若 `AdapterRegistry` 取实例的方法名不同(查 `core/adapters/registry.py`),用对应方法(如 `registry.adapter("us")` / `registry._adapters["us"]`),保证拿到含 `fetch_intraday` 的 `USAdapter`。

② sweep 降频:`:184` `minutes=30` 改 `minutes=120`。

③ finally 段(`:202-212` 附近)在 `_ws_task.cancel()` 后补 cancel 新任务:

```python
        for _t in (_hub_task, _poller_task):
            _t.cancel()
            try:
                await _t
            except (asyncio.CancelledError, Exception):
                pass
```

- [ ] **Step 2: module 级挂 attach_intraday_route + intraday repo override**

`apps/collector/us/main.py` 底部(`:227` `attach_bars_history_route` 后)加:

```python
# 分时只读接口 (module 级挂载, 惰性解析 repo —— lifespan set override)
from apps.collector.base import attach_intraday_route  # noqa: E402

_us_intraday_repo = None  # lifespan 内 set_us_intraday_repo_override 注入

def set_us_intraday_repo_override(repo) -> None:
    global _us_intraday_repo
    _us_intraday_repo = repo

attach_intraday_route(app, lambda: _us_intraday_repo, "us", get_bar_repo=get_bar_repo)
```

(`get_bar_repo` 已在 `:226` import。`set_us_intraday_repo_override` 需在 lifespan 用前定义 —— 放 module 级,lifespan 内 `from apps.collector.us.main import set_us_intraday_repo_override` 或直接同模块引用。)

- [ ] **Step 3: 验证 import + 路由**

Run: `. .venv/bin/activate && python -c "from apps.collector.us.main import app; print('us import ok')" && python -c "from apps.api.main import app as a; from apps.collector.ashare.main import app as s; from apps.collector.crypto.main import app as c; print('all import ok')"`
Expected: us import ok + all import ok

- [ ] **Step 4: 提交**

```bash
git add apps/collector/us/main.py
git commit -m "feat: us/main 接线 TradeHub/writer/ticker/SIP poller + 分时repo/purge + sweep降频2h"
```

---

## 步骤 10 · 前端:非 RTH 默认 K 线 + 昨收基准线

### Task 12: markets.ts 加 isUsRegularSession + page 非 RTH 默认 K 线

**Files:**
- Modify: `apps/web/lib/markets.ts`、`apps/web/app/symbol/[code]/page.tsx`
- Test: `cd apps/web && npx tsc --noEmit`

- [ ] **Step 1: markets.ts 加 isUsRegularSession**

`apps/web/lib/markets.ts` 加(ET 09:30-16:00 判定,用 `Intl` 取 ET 时分,镜像后端):

```typescript
// 美股 RTH 判定 (09:30-16:00 ET)。分时图仅 RTH; 非 RTH 默认 K 线。
export function isUsRegularSession(now: Date = new Date()): boolean {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', hour12: false,
    hour: '2-digit', minute: '2-digit', weekday: 'short',
  }).formatToParts(now)
  const wd = parts.find((p) => p.type === 'weekday')?.value
  if (wd === 'Sat' || wd === 'Sun') return false
  const hh = Number(parts.find((p) => p.type === 'hour')?.value ?? '0')
  const mm = Number(parts.find((p) => p.type === 'minute')?.value ?? '0')
  const mins = hh * 60 + mm
  return mins >= 9 * 60 + 30 && mins < 16 * 60
}
```

- [ ] **Step 2: page.tsx 非 RTH 默认 K 线 + 分时 tab 盘前提示**

`apps/web/app/symbol/[code]/page.tsx`:
- import 加 `isUsRegularSession`(`@/lib/markets` 已 import inferMarket,补这个)。
- 初始 viewMode 改为按市场+时段:美股非 RTH 默认 `'kline'`,其余默认 `'intraday'`:

```typescript
  const supportsIntraday = effectiveMarket === 'ashare' || effectiveMarket === 'us'
  const usPremarket = effectiveMarket === 'us' && !isUsRegularSession()
  const [viewMode, setViewMode] = useState<'intraday' | 'kline'>(
    usPremarket ? 'kline' : 'intraday',
  )
```

- 分时 tab 按钮点击:美股非 RTH 时不切到空分时,改提示。最小实现:tab 上加 `title` 提示 + 点击仍切但组件显示提示(见 Task 13 的 IntradayLineChart 空态)。保留 tab 渲染逻辑不变,只调整初始默认。

- [ ] **Step 3: 验证**

Run: `cd apps/web && npx tsc --noEmit && cd ../..`
Expected: 无类型错误

- [ ] **Step 4: 提交**

```bash
git add apps/web/lib/markets.ts apps/web/app/symbol/[code]/page.tsx
git commit -m "feat: 美股非RTH详情页默认K线 + isUsRegularSession"
```

---

### Task 13: IntradayLineChart 昨收基准线 + 红绿染色 + use_intraday_line 带 prev_close

**Files:**
- Modify: `apps/web/lib/use_intraday_line.ts`、`apps/web/components/IntradayLineChart.tsx`
- Test: `cd apps/web && npx tsc --noEmit`

- [ ] **Step 1: use_intraday_line 暴露 prev_close**

`apps/web/lib/use_intraday_line.ts`:SWR 首屏响应体已含 `prev_close`(Task 10 后端加),hook 返回值加 `prevClose: number | null`(从首屏 response 读,SSE point 事件不带、保持首屏值)。类型与返回对象补 `prevClose`。

- [ ] **Step 2: IntradayLineChart 加昨收线 + 染色**

`apps/web/components/IntradayLineChart.tsx`:
- props/hook 取 `prevClose`。
- 加第三条 series(昨收基准线,灰色虚线):

```typescript
    // 昨收基准线 (灰色虚线)
    const prevSeries = chart.addLineSeries({
      color: '#6b7280',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
    })
    prevCloseSeriesRef.current = prevSeries
```

- 价格线颜色按末价相对昨收红绿(高于昨收 `#ef4444` 红 / 低于 `#22c55e` 绿,A 股口径;无 prevClose 时退回白 `#f5f5f5`)。在数据 effect 里 `priceSeries.applyOptions({ color })`。
- 昨收线数据:覆盖当日时间轴两端的水平线(取 points 首尾 ts,value=prevClose):

```typescript
    if (prevClose != null && points.length > 0) {
      const t0 = toChartTime(points[0].ts) as LineData['time']
      const t1 = toChartTime(points[points.length - 1].ts) as LineData['time']
      prevCloseSeriesRef.current?.setData([
        { time: t0, value: prevClose }, { time: t1, value: prevClose },
      ])
    }
```

- 底部加 IEX 量注脚:`<div className="text-xs text-neutral-600 mt-1">成交量为 IEX 口径(免费层),仅供参考</div>`(仅 `inferMarket(symbol)==='us'` 时显示;组件可接 `market` prop 或内部 infer)。

- [ ] **Step 3: 验证**

Run: `cd apps/web && npx tsc --noEmit && cd ../..`
Expected: 无类型错误

- [ ] **Step 4: 提交**

```bash
git add apps/web/lib/use_intraday_line.ts apps/web/components/IntradayLineChart.tsx
git commit -m "feat: 分时图昨收基准线 + 红绿染色 + 美股IEX量注脚"
```

---

## 步骤 11 · 更正 CLAUDE.md + 收尾验证

### Task 14: 更正 CLAUDE.md 过时段落

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 改"当前活跃约束"段**

`CLAUDE.md` 把"美股/A股目前无实时推送"、"只有 crypto 接了 SSE 实时尾部"等过时表述更正为:
- A 股:quote 驱动进行中态 + 分时图已落地。
- 美股:`trades` 逐笔驱动进行中态 + 分时图(真 VWAP);收线走 REST SIP 权威(~20min 延迟),TradeHub provisional 填洞;1m 不再落库。
- 补"美股成交量分两源:实时/分时 IEX(偏小),收线/信号 SIP(权威)"。

- [ ] **Step 2: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: 更正 CLAUDE.md 美股/A股实时推送落地态"
```

---

### Task 15: 收尾验证(全 Task 完成后)

- [ ] **后端 import 测试**

Run: `. .venv/bin/activate && python -c "from apps.api.main import app; from apps.collector.crypto.main import app as c; from apps.collector.us.main import app as u; from apps.collector.ashare.main import app as a; print('OK')"`
Expected: OK

- [ ] **前端类型检查**

Run: `cd apps/web && npx tsc --noEmit && cd ../..`
Expected: 无错

- [ ] **全套单测**

Run: `. .venv/bin/activate && pytest -m "not integration" -q`
Expected: 全绿

- [ ] **不变量守护 grep**

Run: `grep -n "insert_bars" apps/collector/us/trade_hub.py apps/collector/us/bar_ticker.py apps/collector/us/intraday_line_writer.py`
Expected: 无输出(进行中态/分时绝不写 DuckDB;只有 bar_poller 写)

Run: `grep -rn "ak_call" apps/api/`
Expected: 仅注释(api 0 ak_call 不变)

- [ ] **重启 us collector + api 冒烟**(雷区 2 模板)

按 CLAUDE.md 雷区 2 pkill + nohup 重启 `apps.collector.us.main` + api(3 进程隔离,只重启 us + api;ashare/crypto 不动)。然后:
Run: `curl -s -m 3 http://localhost:8787/api/health | grep -o '"status":"[^"]*"'` + `curl -s -m 3 http://127.0.0.1:8789/health`
Expected: ok

- [ ] **Playwright 证据式验证**(memory `feedback_playwright_evidence_testing`)

美股盘中(RTH)驱动真实 Chrome 打开美股个股详情(如 AAPL),拦 `/api/sse/bars` 与 `/api/sse/intraday/AAPL` 网络流,确认:① K 线进行中桶 `final=false` 在跳 + 桶滚动 provisional final=true;② 分时折线 + 均价线 + 昨收线在更新。盘前打开默认落 K 线。

---

## 后续/备注

- 存量孤儿 1m bar(`bars_us.duckdb` interval='1m')可选一次性 `DELETE FROM bars WHERE interval='1m'`,非必须(改造后不再新增)。
- `kline_service::_get_one_minute_bars`(55s 内存缓存)若 grep 确认前端无 1m 请求,可另起小 PR 标废弃,本计划不强制。
- `AdapterRegistry` 取 us adapter 的确切方法名以 `core/adapters/registry.py` 为准(Task 11 Step 1 注)。
