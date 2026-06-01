# 实时 K 线推送 + 分时图(A 股先行)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 A 股 K 线对齐 crypto 实时体验(进行中态实时跳 + 收线即 push 含聚合周期),并新增券商口径分时图(时分线),A 股先行验证。

**Architecture:** 三组件按"桶是否收线"分工:`quote_bar_ticker`(quote 驱动进行中态,推 final=false + 写 :current 不入库)、源头采集(bar_poller 收线入库 + 发 final=true + 触发聚合)、事件驱动聚合(收线大桶发 bus)。分时图为独立子系统:独立 DuckDB 库 + writer + 独立 bus channel + SSE 端点。

**Tech Stack:** Python (FastAPI / APScheduler / asyncio)、DuckDB、Redis Streams、Next.js (lightweight-charts / EventSource)、pytest + fakeredis。

**Spec:** `docs/superpowers/specs/2026-06-01-realtime-kline-and-intraday-line-design.md`

**范围:** 本计划覆盖 spec 第 5 章步骤 1-4(A 股)。步骤 5(美股复刻)、步骤 6(更正 CLAUDE.md)为后续计划。

---

## 文件结构

**新建:**
- `apps/collector/ashare/quote_bar_ticker.py` — A 股进行中态组件(quote 驱动所有被订阅周期当前桶)
- `core/persistence/intraday_repo.py` — `IntradayLineRepo`(独立分时库读写 + purge)
- `apps/collector/ashare/intraday_line_writer.py` — quote 驱动写分时点 + 发 bus
- `apps/api/routes/sse_intraday.py` — 分时 SSE 端点
- `apps/web/lib/use_intraday_line.ts` — 前端分时取数 hook
- `apps/web/components/IntradayLineChart.tsx` — 前端分时折线组件
- `tests/unit/collector/test_quote_bar_ticker.py`
- `tests/unit/persistence/test_intraday_repo.py`
- `tests/unit/collector/test_intraday_line_writer.py`
- `tests/unit/collector/test_aggregate_trigger.py`

**改造:**
- `core/cache/keys.py` — 加 `BUS_INTRADAY_UPDATED` + `cache_intraday_current`
- `core/domain/intervals.py` — 1m 标记废弃(is_kline=False)
- `core/domain/market_sessions.py` — `bucket_grid` 支持 5/15/30 分钟
- `apps/web/lib/intervals.ts` — 去 1m tab
- `core/scheduler/jobs.py` — `flush_quotes_to_duckdb` 砍 quote→1m
- `apps/collector/jobs/aggregate_derived.py` — 加发 bus + 事件驱动入口 `aggregate_and_publish`
- `apps/collector/ashare/bar_poller.py` — 砍 1m 轮询;收线发 final=true;触发聚合
- `apps/collector/ashare/main.py` — 接线 ticker / writer / purge cron;sweep 降频 2h
- `core/adapters/ashare.py` — `_fetch_snapshot_sina` 带累计成交额进 Quote
- `core/domain/models.py` — `Quote` 加 `amount` 字段

---

## 步骤 1 · 废弃 1m + 基础设施(keys / Quote / bucket_grid)

### Task 1: keys.py 加分时 bus channel + current key

**Files:**
- Modify: `core/cache/keys.py:23`(bus 常量区)、`:64`(cache 区)
- Test: `tests/unit/cache/test_keys.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/cache/test_keys.py 追加
from core.cache import keys

def test_bus_intraday_updated_constant():
    assert keys.BUS_INTRADAY_UPDATED == "bus:intraday.updated"

def test_cache_intraday_current_key():
    k = keys.cache_intraday_current("ashare", "600519.SH")
    assert k == "cache:intraday:ashare:600519.SH:current"
    keys.validate(k)  # 不抛
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/cache/test_keys.py -k intraday -v`
Expected: FAIL(AttributeError: BUS_INTRADAY_UPDATED / cache_intraday_current)

- [ ] **Step 3: 实现**

`core/cache/keys.py` bus 常量区(`:23` 后)加:
```python
BUS_INTRADAY_UPDATED = "bus:intraday.updated"
```
cache 区(`cache_bars_current` 后)加:
```python
def cache_intraday_current(market: str, symbol: str) -> str:
    """分时图当前点 cache。intraday_line_writer 写, SSE init 读。"""
    return f"cache:intraday:{market}:{symbol}:current"
```

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/cache/test_keys.py -k intraday -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add core/cache/keys.py tests/unit/cache/test_keys.py
git commit -m "feat: keys 加分时 bus channel + current key"
```

---

### Task 2: Quote 模型加 amount + sina 带累计成交额

**Files:**
- Modify: `core/domain/models.py`(Quote dataclass)、`core/adapters/ashare.py:151-168`(`_fetch_snapshot_sina`)
- Test: `tests/unit/adapters/test_ashare_quote_amount.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/adapters/test_ashare_quote_amount.py
from decimal import Decimal
from core.domain.models import Quote
from datetime import datetime, timezone

def test_quote_has_amount_field():
    q = Quote(market="ashare", symbol="600519.SH",
              ts=datetime.now(timezone.utc), price=Decimal("1700"),
              change_pct=1.0, volume=1000, source="sina", amount=1700000.0)
    assert q.amount == 1700000.0

def test_quote_amount_defaults_none():
    q = Quote(market="ashare", symbol="600519.SH",
              ts=datetime.now(timezone.utc), price=Decimal("1700"),
              change_pct=1.0, volume=1000, source="sina")
    assert q.amount is None
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/adapters/test_ashare_quote_amount.py -v`
Expected: FAIL(TypeError: unexpected keyword 'amount')

- [ ] **Step 3: 实现**

`core/domain/models.py` Quote 末尾字段加(在 `source: str` 后):
```python
    amount: float | None = None  # 当日累计成交额(元), sina parts[9]
```

`core/adapters/ashare.py` `_fetch_snapshot_sina` 解析处(`:154` 附近):
```python
                volume = int(float(parts[8])) if len(parts) > 8 else 0
                amount = float(parts[9]) if len(parts) > 9 and parts[9] else None
```
并在构造 Quote 处(`:160`)加 `amount=amount,`。

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/adapters/test_ashare_quote_amount.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add core/domain/models.py core/adapters/ashare.py tests/unit/adapters/test_ashare_quote_amount.py
git commit -m "feat: Quote 加 amount 字段, sina 带累计成交额"
```

---

### Task 3: 1m 周期标记废弃 + 砍 quote→1m 伪 bar

**Files:**
- Modify: `core/domain/intervals.py:31`、`apps/web/lib/intervals.ts`、`core/scheduler/jobs.py:82-108`
- Test: `tests/unit/domain/test_intervals_1m_deprecated.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/domain/test_intervals_1m_deprecated.py
from core.domain.intervals import INTERVAL_CONFIG, KLINE_INTERVALS

def test_1m_not_kline():
    assert "1m" not in KLINE_INTERVALS

def test_1m_spec_still_exists_but_hidden():
    # 1m spec 保留(历史数据兼容)但不暴露给 K 线
    assert INTERVAL_CONFIG["1m"].is_kline is False
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/domain/test_intervals_1m_deprecated.py -v`
Expected: FAIL(1m 仍 is_kline=True)

- [ ] **Step 3: 实现**

`core/domain/intervals.py:31` 改:
```python
    IntervalSpec("1m",  "分时",   False, False, 0,   240, False),  # 废弃: 分时图取代
```

`apps/web/lib/intervals.ts` 删掉 `1m` 那一项(对齐后端 KLINE_INTERVALS)。

`core/scheduler/jobs.py` `flush_quotes_to_duckdb`(`:82-108`)整个函数体改为直接返回空(不再拍 1m 伪 bar):
```python
def flush_quotes_to_duckdb(
    market: str, cache: QuoteCache, repo: BarRepo,
) -> list[Bar]:
    """已废弃: 1m 伪 bar 由分时图取代。保留空壳避免调用方报错。"""
    return []
```

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/domain/test_intervals_1m_deprecated.py -v && cd apps/web && npx tsc --noEmit && cd ../..`
Expected: PASS + tsc 无错

- [ ] **Step 5: 提交**

```bash
git add core/domain/intervals.py apps/web/lib/intervals.ts core/scheduler/jobs.py tests/unit/domain/test_intervals_1m_deprecated.py
git commit -m "feat: 废弃 1m 周期, 砍 quote→1m 伪 bar"
```

---

### Task 4: bucket_grid 支持 5/15/30 分钟

**Files:**
- Modify: `core/domain/market_sessions.py:18`(Literal)
- Test: `tests/unit/domain/test_bucket_grid_small.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/domain/test_bucket_grid_small.py
from datetime import date
from core.domain.market_sessions import bucket_grid

def test_ashare_5m_buckets_count():
    # A 股 240 分钟 / 5 = 48 根
    grid = bucket_grid("ashare", date(2026, 6, 1), 5)
    assert len(grid) == 48

def test_ashare_5m_first_bucket_close_0935():
    grid = bucket_grid("ashare", date(2026, 6, 1), 5)
    open_utc, close_utc = grid[0]
    # 首根 09:30-09:35, close = 09:35 BJT = 01:35 UTC
    assert close_utc.hour == 1 and close_utc.minute == 35

def test_ashare_15m_buckets_count():
    assert len(bucket_grid("ashare", date(2026, 6, 1), 15)) == 16
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/domain/test_bucket_grid_small.py -v`
Expected: 可能 PASS(Literal 仅类型提示不强制运行期),但若有运行期校验则 FAIL。无论如何先跑

- [ ] **Step 3: 实现**

`core/domain/market_sessions.py:18` 扩展 Literal:
```python
IntradayMinutes = Literal[5, 15, 30, 60, 240]
```
(bucket_grid 算法本身按 interval_minutes 通用切桶, 无需改逻辑)

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/domain/test_bucket_grid_small.py -v`
Expected: PASS(48 / 16 根)

- [ ] **Step 5: 提交**

```bash
git add core/domain/market_sessions.py tests/unit/domain/test_bucket_grid_small.py
git commit -m "feat: bucket_grid 支持 5/15/30 分钟周期"
```

---

## 步骤 2 · 聚合发 bus + 事件驱动

### Task 5: aggregate_derived 加"聚合后发已收线桶 bus"入口

**Files:**
- Modify: `apps/collector/jobs/aggregate_derived.py`(新增 `aggregate_and_publish`)
- Test: `tests/unit/collector/test_aggregate_trigger.py`

聚合本身复用 `aggregate_derived_for_symbol`(返回 stats)。新增包装:聚合后回查每个目标周期最新一根 bar,若其 `ts(=close_ts) <= now` 即已收线,发 `bus:bars.updated`(final=true)。同一桶重复发无害(前端按 ts 覆盖)。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_aggregate_trigger.py
import json
import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from core.domain.models import Bar
from apps.collector.jobs.aggregate_derived import aggregate_and_publish

@pytest.mark.asyncio
async def test_publishes_closed_bucket_only():
    repo = MagicMock()
    # 15m 最新一根已收线(ts 在过去)
    closed = Bar(market="ashare", symbol="600519.SH",
                 ts=datetime(2026,6,1,2,0,tzinfo=timezone.utc),
                 open=Decimal("1"), high=Decimal("2"), low=Decimal("1"),
                 close=Decimal("2"), volume=10, interval="15m")
    repo.fetch_history_paged.return_value = [closed]
    redis = MagicMock()
    redis._r = MagicMock()
    redis._r.xadd = AsyncMock()
    # 只触发 15m
    await aggregate_and_publish(repo, redis, "ashare", "600519.SH",
                                targets=("15m",), now=datetime(2026,6,1,3,0,tzinfo=timezone.utc))
    assert redis._r.xadd.await_count == 1
    args, kwargs = redis._r.xadd.await_args
    payload = json.loads(args[1]["data"].decode())
    assert payload["interval"] == "15m" and payload["final"] is True

@pytest.mark.asyncio
async def test_skips_unclosed_bucket():
    repo = MagicMock()
    # 最新一根未收线(ts 在未来)
    unclosed = Bar(market="ashare", symbol="600519.SH",
                   ts=datetime(2026,6,1,4,0,tzinfo=timezone.utc),
                   open=Decimal("1"), high=Decimal("2"), low=Decimal("1"),
                   close=Decimal("2"), volume=10, interval="15m")
    repo.fetch_history_paged.return_value = [unclosed]
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    await aggregate_and_publish(repo, redis, "ashare", "600519.SH",
                                targets=("15m",), now=datetime(2026,6,1,3,0,tzinfo=timezone.utc))
    assert redis._r.xadd.await_count == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_aggregate_trigger.py -v`
Expected: FAIL(ImportError: aggregate_and_publish)

- [ ] **Step 3: 实现**

`apps/collector/jobs/aggregate_derived.py` 顶部加 `import json`、`from datetime import datetime, timezone`、`from core.cache import keys`。`aggregate_derived_for_symbol` 后加:

```python
_TARGET_SOURCE_WINDOW = {
    "15m": dict(window_15m=2), "30m": dict(window_30m=2),
    "60m": dict(window_60m=2), "4h": dict(window_4h=2),
    "1wk": dict(window_1wk=14), "1mo": dict(window_1mo=40),
}

async def aggregate_and_publish(
    repo, redis, market: str, symbol: str,
    *, targets: tuple[str, ...], now: datetime | None = None,
) -> None:
    """事件驱动: 聚合 targets, 对已收线(ts<=now)的最新桶发 bus(final=true)."""
    now = now or datetime.now(timezone.utc)
    kw: dict = {}
    for t in targets:
        kw.update(_TARGET_SOURCE_WINDOW.get(t, {}))
    try:
        await aggregate_derived_for_symbol(repo, market, symbol, **kw)
    except Exception as e:  # noqa: BLE001
        log.warning("derived.publish_agg_failed", symbol=symbol, error=str(e))
        return
    for t in targets:
        try:
            latest = repo.fetch_history_paged(market, symbol, t, before=None, limit=1)
            if not latest:
                continue
            bar = latest[-1]
            if bar.ts > now:  # 未收线, 交给 ticker
                continue
            payload = {
                "market": market, "symbol": symbol, "interval": t,
                "ts": bar.ts.isoformat(), "open": float(bar.open),
                "high": float(bar.high), "low": float(bar.low),
                "close": float(bar.close), "volume": int(bar.volume),
                "final": True,
            }
            await redis._r.xadd(  # noqa: SLF001
                keys.BUS_BARS_UPDATED,
                {"data": json.dumps(payload).encode()},
                maxlen=10000, approximate=True,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("derived.publish_failed", symbol=symbol, target=t, error=str(e))
```

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_aggregate_trigger.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/collector/jobs/aggregate_derived.py tests/unit/collector/test_aggregate_trigger.py
git commit -m "feat: 聚合后对已收线桶发 bus (事件驱动入口 aggregate_and_publish)"
```

---

### Task 6: bar_poller 砍 1m + 收线发 final=true + 触发聚合

**Files:**
- Modify: `apps/collector/ashare/bar_poller.py:36`(INTERVAL_TO_PERIOD)、`:66-141`(`_poll_one`)
- Test: `tests/unit/collector/test_bar_poller_closed.py`

核心改动:① 砍 1m;② `_poll_one` 入库**全部已收线根**,但**只发已收线根的 final=true**(最后一根若未收线不发,交 ticker);③ 5m 收线后调 `aggregate_and_publish` 触发大周期。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_bar_poller_closed.py
from apps.collector.ashare.bar_poller import INTERVAL_TO_PERIOD

def test_1m_removed_from_poller():
    assert "1m" not in INTERVAL_TO_PERIOD

def test_poller_periods_are_5_15_30():
    assert set(INTERVAL_TO_PERIOD) == {"5m", "15m", "30m"}
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_bar_poller_closed.py -v`
Expected: FAIL(1m 仍在)

- [ ] **Step 3: 实现**

`bar_poller.py:36` 改:
```python
INTERVAL_TO_PERIOD = {"5m": "5", "15m": "15", "30m": "30"}
```

`_poll_one`(`:125-141`)的"发布最新 bar"段替换为:**只发已收线根**。判定:用 `market_sessions.bucket_grid` 当前时刻所在桶的 close_ts;`bars[-1].ts > now` 即未收线,跳过发布(但已 upsert 入库,无妨——其实进行中根不该入库,见下)。简化口径:**poller 入库除最后一根外的所有根,最后一根只在其 ts<=now 时才入库+发**:

```python
        from datetime import datetime as _dt
        now = _dt.now(timezone.utc)
        # 进行中根(ts>now)交给 ticker, poller 不入库不发
        closed_bars = [b for b in bars if b.ts <= now]
        if not closed_bars:
            return
        try:
            self._repo.insert_bars(closed_bars)
        except Exception as e:  # noqa: BLE001
            log.warning("bar_poller.db_write_failed",
                        symbol=symbol, interval=interval, error=str(e))
        latest = closed_bars[-1]
        payload = {
            "market": "ashare", "symbol": latest.symbol,
            "interval": latest.interval, "ts": latest.ts.isoformat(),
            "open": float(latest.open), "high": float(latest.high),
            "low": float(latest.low), "close": float(latest.close),
            "volume": int(latest.volume), "final": True,
        }
        try:
            await self._redis._r.xadd(  # noqa: SLF001
                keys.BUS_BARS_UPDATED,
                {"data": json.dumps(payload).encode()},
                maxlen=10000, approximate=True,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("bar_poller.xadd_failed", error=str(e))
        # 5m 收线触发大周期聚合 + 发 bus
        if interval == "5m":
            from apps.collector.jobs.aggregate_derived import aggregate_and_publish
            await aggregate_and_publish(
                self._repo, self._redis, "ashare", symbol,
                targets=("15m", "30m", "60m", "4h"), now=now,
            )
```

(删掉原 `:118-141` 旧入库+发布段)

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_bar_poller_closed.py -v && python -c "from apps.collector.ashare.bar_poller import run_bar_poller; print('import ok')"`
Expected: PASS + import ok

- [ ] **Step 5: 提交**

```bash
git add apps/collector/ashare/bar_poller.py tests/unit/collector/test_bar_poller_closed.py
git commit -m "feat: bar_poller 砍 1m, 只入库/发已收线根, 5m 收线触发聚合"
```

---

## 步骤 3 · A 股进行中态 quote_bar_ticker

### Task 7: quote_bar_ticker 桶状态纯函数(OHLC 攒法)

**Files:**
- Create: `apps/collector/ashare/quote_bar_ticker.py`(先只放纯函数 `update_bucket`)
- Test: `tests/unit/collector/test_quote_bar_ticker.py`

先做最易测的纯函数:给定"当前桶状态 + 新 quote 价",返回更新后的 OHLC。状态用 dataclass。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_quote_bar_ticker.py
from decimal import Decimal
from apps.collector.ashare.quote_bar_ticker import BucketState, update_bucket

def test_new_bucket_sets_open():
    st = update_bucket(None, Decimal("100"), volume=5)
    assert st.open == st.high == st.low == st.close == Decimal("100")
    assert st.volume == 5

def test_update_tracks_high_low_close():
    st = update_bucket(None, Decimal("100"), volume=5)
    st = update_bucket(st, Decimal("105"), volume=8)
    st = update_bucket(st, Decimal("98"), volume=12)
    assert st.open == Decimal("100")
    assert st.high == Decimal("105")
    assert st.low == Decimal("98")
    assert st.close == Decimal("98")
    assert st.volume == 12  # 累计 volume 取最新

def test_baseline_seeds_ohlc():
    # 用更小周期 bar 算出的基线初始化(重启/中途订阅)
    base = BucketState(open=Decimal("90"), high=Decimal("110"),
                       low=Decimal("88"), close=Decimal("95"), volume=100)
    st = update_bucket(base, Decimal("112"), volume=120)
    assert st.open == Decimal("90")   # open 保持基线
    assert st.high == Decimal("112")  # 新高
    assert st.low == Decimal("88")
    assert st.close == Decimal("112")
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_quote_bar_ticker.py -v`
Expected: FAIL(ImportError)

- [ ] **Step 3: 实现**

```python
# apps/collector/ashare/quote_bar_ticker.py
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal

@dataclass
class BucketState:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

def update_bucket(state: BucketState | None, price: Decimal, *, volume: int) -> BucketState:
    """用新 quote 价更新进行中桶 OHLC。state=None 时新建(open=price)。volume 取累计最新值。"""
    if state is None:
        return BucketState(open=price, high=price, low=price, close=price, volume=volume)
    return BucketState(
        open=state.open,
        high=max(state.high, price),
        low=min(state.low, price),
        close=price,
        volume=volume,
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_quote_bar_ticker.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/collector/ashare/quote_bar_ticker.py tests/unit/collector/test_quote_bar_ticker.py
git commit -m "feat: quote_bar_ticker 桶状态 OHLC 攒法纯函数"
```

---

### Task 8: 当前桶定位 + 基线补全纯函数

**Files:**
- Modify: `apps/collector/ashare/quote_bar_ticker.py`(加 `current_bucket` / `seed_baseline`)
- Test: `tests/unit/collector/test_quote_bar_ticker.py`(追加)

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/unit/collector/test_quote_bar_ticker.py
from datetime import datetime, timezone, date
from apps.collector.ashare.quote_bar_ticker import current_bucket

def test_current_bucket_finds_open_close():
    # 13:50 BJT = 05:50 UTC, 5m 桶应为 13:50-13:55
    now = datetime(2026, 6, 1, 5, 50, 30, tzinfo=timezone.utc)
    ob = current_bucket("ashare", now, 5)
    assert ob is not None
    open_utc, close_utc = ob
    assert open_utc.hour == 5 and open_utc.minute == 50
    assert close_utc.hour == 5 and close_utc.minute == 55

def test_current_bucket_none_outside_session():
    # 03:00 UTC = 11:00 BJT 上午盘内? 11:00 在 09:30-11:30, 是开市
    # 用真正休市时刻 07:00 UTC = 15:00 收盘后边界
    now = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)  # 16:00 BJT 已收盘
    assert current_bucket("ashare", now, 5) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_quote_bar_ticker.py -k current_bucket -v`
Expected: FAIL(ImportError current_bucket)

- [ ] **Step 3: 实现**

`quote_bar_ticker.py` 加(顶部 import `from datetime import datetime`; `from core.domain.market_sessions import bucket_grid`):

```python
def current_bucket(market: str, now: datetime, interval_min: int) -> tuple[datetime, datetime] | None:
    """返回 now 落在的桶 (open_utc, close_utc]; 不在任何桶(休市)返回 None。"""
    grid = bucket_grid(market, now.date(), interval_min)
    for open_utc, close_utc in grid:
        if open_utc <= now < close_utc:
            return (open_utc, close_utc)
    return None
```

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_quote_bar_ticker.py -k current_bucket -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/collector/ashare/quote_bar_ticker.py tests/unit/collector/test_quote_bar_ticker.py
git commit -m "feat: quote_bar_ticker 当前桶定位"
```

---

### Task 9: ticker 主循环(扫订阅 → 读 quote → 推 final=false + 写 :current)

**Files:**
- Modify: `apps/collector/ashare/quote_bar_ticker.py`(加 `QuoteBarTicker` 类 + `run_quote_bar_ticker`)
- Test: `tests/unit/collector/test_quote_bar_ticker_run.py`

INTERVAL→分钟映射:`{"5m":5,"15m":15,"30m":30,"60m":60,"4h":240}`(1d 进行中态本期不做,日线收盘才出)。订阅扫描复用 `state:subscribe:ashare:{symbol}:{interval}` pattern(同 bar_poller,parts[4]=interval)。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_quote_bar_ticker_run.py
import json, pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from apps.collector.ashare.quote_bar_ticker import QuoteBarTicker

@pytest.mark.asyncio
async def test_tick_once_publishes_final_false(monkeypatch):
    redis = MagicMock()
    redis._r = MagicMock()
    redis._r.xadd = AsyncMock()
    redis.set_msgpack = AsyncMock()
    # quote cache 命中
    redis.get_msgpack = AsyncMock(return_value={
        "price": "100.5", "volume": 1000, "amount": 100500.0,
    })
    t = QuoteBarTicker(redis)
    now = datetime(2026, 6, 1, 5, 50, 30, tzinfo=timezone.utc)  # 13:50:30 BJT 开市
    await t.tick_once("600519.SH", "5m", now=now)
    # 应发 final=false
    assert redis._r.xadd.await_count == 1
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["final"] is False
    assert payload["interval"] == "5m"
    assert payload["symbol"] == "600519.SH"
    # 写了 :current
    assert redis.set_msgpack.await_count == 1

@pytest.mark.asyncio
async def test_tick_once_skips_when_no_quote():
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    redis.get_msgpack = AsyncMock(return_value=None)
    t = QuoteBarTicker(redis)
    now = datetime(2026, 6, 1, 5, 50, 30, tzinfo=timezone.utc)
    await t.tick_once("600519.SH", "5m", now=now)
    assert redis._r.xadd.await_count == 0
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_quote_bar_ticker_run.py -v`
Expected: FAIL(QuoteBarTicker 无 tick_once)

- [ ] **Step 3: 实现**

`quote_bar_ticker.py` 加(import `json`、`asyncio`、`timezone`、`from core.cache import keys`、`from core.domain.market_calendar import is_trading_day`、`from core.domain.market_sessions import is_market_session_open`):

```python
_INTERVAL_MIN = {"5m": 5, "15m": 15, "30m": 30, "60m": 60, "4h": 240}
TICK_INTERVAL_S = 10

class QuoteBarTicker:
    def __init__(self, redis):
        self._redis = redis
        self._buckets: dict[str, tuple[datetime, BucketState]] = {}  # key=sym:iv -> (open_ts, state)
        self._stopped = False

    async def tick_once(self, symbol: str, interval: str, *, now: datetime) -> None:
        mins = _INTERVAL_MIN.get(interval)
        if mins is None:
            return
        ob = current_bucket("ashare", now, mins)
        if ob is None:
            return  # 休市
        open_ts, close_ts = ob
        try:
            q = await self._redis.get_msgpack(keys.cache_quote("ashare", symbol))
        except Exception:
            q = None
        if not q:
            return
        price = Decimal(str(q.get("price")))
        volume = int(q.get("volume") or 0)
        tk = f"{symbol}:{interval}"
        prev = self._buckets.get(tk)
        # 新桶: 丢弃旧状态从头攒(基线补全在 Task 10 接入, 这里先 None)
        state = None if (prev is None or prev[0] != open_ts) else prev[1]
        state = update_bucket(state, price, volume=volume)
        self._buckets[tk] = (open_ts, state)
        payload = {
            "market": "ashare", "symbol": symbol, "interval": interval,
            "ts": close_ts.isoformat(),
            "open": float(state.open), "high": float(state.high),
            "low": float(state.low), "close": float(state.close),
            "volume": int(state.volume), "final": False,
        }
        try:
            await self._redis._r.xadd(  # noqa: SLF001
                keys.BUS_BARS_UPDATED,
                {"data": json.dumps(payload).encode()},
                maxlen=10000, approximate=True,
            )
            await self._redis.set_msgpack(
                keys.cache_bars_current("ashare", symbol, interval), payload, ttl=mins * 120,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("ticker.publish_failed", symbol=symbol, interval=interval, error=str(e))
```

(文件顶部加 `import structlog` + `log = structlog.get_logger(__name__)`)

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_quote_bar_ticker_run.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/collector/ashare/quote_bar_ticker.py tests/unit/collector/test_quote_bar_ticker_run.py
git commit -m "feat: quote_bar_ticker 主循环 tick_once 推 final=false + 写 :current"
```

---

### Task 10: ticker 扫订阅循环 + 基线补全 + 接线 main

**Files:**
- Modify: `apps/collector/ashare/quote_bar_ticker.py`(加 `_scan` / `run` / `seed_baseline`)、`apps/collector/ashare/main.py:188`(接线)
- Test: `tests/unit/collector/test_quote_bar_ticker_run.py`(追加基线测试)

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/unit/collector/test_quote_bar_ticker_run.py
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import MagicMock
from core.domain.models import Bar
from apps.collector.ashare.quote_bar_ticker import seed_baseline

def test_seed_baseline_from_smaller_bars():
    # 60m 当前桶 13:00-14:00, 用已收线 5m bar 算基线
    bars = [
        Bar(market="ashare", symbol="X", ts=datetime(2026,6,1,5,5,tzinfo=timezone.utc),
            open=Decimal("10"), high=Decimal("12"), low=Decimal("9"), close=Decimal("11"),
            volume=100, interval="5m"),
        Bar(market="ashare", symbol="X", ts=datetime(2026,6,1,5,10,tzinfo=timezone.utc),
            open=Decimal("11"), high=Decimal("15"), low=Decimal("10"), close=Decimal("14"),
            volume=200, interval="5m"),
    ]
    st = seed_baseline(bars)
    assert st.open == Decimal("10")   # 第一根 open
    assert st.high == Decimal("15")   # 最高
    assert st.low == Decimal("9")     # 最低
    assert st.close == Decimal("14")  # 最后一根 close
    assert st.volume == 300           # 累加

def test_seed_baseline_empty_returns_none():
    assert seed_baseline([]) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_quote_bar_ticker_run.py -k seed_baseline -v`
Expected: FAIL(ImportError seed_baseline)

- [ ] **Step 3: 实现**

`quote_bar_ticker.py` 加:

```python
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

加扫订阅循环(复用 bar_poller 的 scan 口径):

```python
    async def _scan_subscribed(self) -> set[tuple[str, str]]:
        active: set[tuple[str, str]] = set()
        try:
            cursor = 0
            while True:
                cursor, found = await self._redis._r.scan(  # noqa: SLF001
                    cursor, match="state:subscribe:ashare:*", count=200)
                for k in found:
                    parts = (k.decode() if isinstance(k, bytes) else k).split(":")
                    if len(parts) >= 5 and parts[4] in _INTERVAL_MIN:
                        active.add((parts[3], parts[4]))
                if cursor == 0:
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("ticker.scan_failed", error=str(e))
        return active

    async def run(self) -> None:
        log.info("quote_bar_ticker.started")
        while not self._stopped:
            try:
                if is_trading_day("ashare") and is_market_session_open("ashare"):
                    now = datetime.now(timezone.utc)
                    for symbol, interval in await self._scan_subscribed():
                        await self.tick_once(symbol, interval, now=now)
            except Exception as e:  # noqa: BLE001
                log.warning("ticker.loop_error", error=str(e))
            await asyncio.sleep(TICK_INTERVAL_S)

async def run_quote_bar_ticker(redis) -> None:
    await QuoteBarTicker(redis).run()
```

**接入 seed_baseline(关键 wiring,补 Task 9 留的 None 占位)**:`QuoteBarTicker.__init__` 增加 `repo` 参数(`def __init__(self, redis, repo=None)`,存 `self._repo=repo`)。`tick_once` 里新建桶时(`state is None` 分支前),用更小周期已收线 bar 算基线:

```python
        # 新桶: 用更小周期 bar 补 OHLC 基线(避免重启/中途订阅 open 漂移)
        if prev is None or prev[0] != open_ts:
            base = None
            if self._repo is not None and mins > 5:
                src_iv, src_min = ("5m", 5)  # 大周期统一从已收线 5m 补基线
                try:
                    src_bars = self._repo.fetch_history_paged(
                        "ashare", symbol, src_iv, before=close_ts, limit=mins // src_min)
                    src_bars = [b for b in src_bars if open_ts < b.ts <= close_ts]
                    base = seed_baseline(src_bars)
                except Exception:  # noqa: BLE001
                    base = None
            state = base
        else:
            state = prev[1]
        state = update_bucket(state, price, volume=volume)
```

(替换 Task 9 里 `state = None if (prev is None ...) else prev[1]` 那两行)

`run_quote_bar_ticker` 改签名带 repo:`async def run_quote_bar_ticker(redis, repo): await QuoteBarTicker(redis, repo).run()`。

`apps/collector/ashare/main.py` 在 `_poller_task` 创建后(`:188` 附近)加:
```python
    from apps.collector.ashare.quote_bar_ticker import run_quote_bar_ticker
    _ticker_task = asyncio.create_task(
        run_quote_bar_ticker(redis_cache, bar_repo), name="ashare.quote_bar_ticker",
    )
```
并在 finally 段 `_poller_task.cancel()` 后加 `_ticker_task.cancel()` + await(同 poller 模式)。

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_quote_bar_ticker_run.py -v && python -c "from apps.collector.ashare.main import app; print('import ok')"`
Expected: PASS + import ok

- [ ] **Step 5: 提交**

```bash
git add apps/collector/ashare/quote_bar_ticker.py apps/collector/ashare/main.py tests/unit/collector/test_quote_bar_ticker_run.py
git commit -m "feat: quote_bar_ticker 扫订阅循环 + 基线补全 + 接线 ashare main"
```

---

## 步骤 4 · A 股分时图(线二)

### Task 11: IntradayLineRepo(独立分时库 + purge)

**Files:**
- Create: `core/persistence/intraday_repo.py`
- Test: `tests/unit/persistence/test_intraday_repo.py`

独立 DuckDB 文件 `data/intraday_{market}.duckdb`,沿用 BarRepo 连接/建表/read_only 模式(物理隔离规避雷区 6 锁竞争)。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/persistence/test_intraday_repo.py
from datetime import datetime, timezone, timedelta
from core.persistence.intraday_repo import IntradayLineRepo, IntradayPoint

def test_insert_and_fetch_day(tmp_path):
    repo = IntradayLineRepo(str(tmp_path / "intraday_ashare.duckdb"))
    ts = datetime(2026, 6, 1, 1, 31, tzinfo=timezone.utc)
    repo.insert_points([IntradayPoint(
        symbol="600519.SH", ts=ts, price=1700.0,
        cum_amount=1700000.0, cum_volume=1000, avg_price=1700.0)])
    rows = repo.fetch_day("600519.SH", ts.date())
    assert len(rows) == 1
    assert rows[0]["avg_price"] == 1700.0

def test_upsert_overwrites_same_minute(tmp_path):
    repo = IntradayLineRepo(str(tmp_path / "intraday_ashare.duckdb"))
    ts = datetime(2026, 6, 1, 1, 31, tzinfo=timezone.utc)
    repo.insert_points([IntradayPoint("600519.SH", ts, 1700.0, 1700000.0, 1000, 1700.0)])
    repo.insert_points([IntradayPoint("600519.SH", ts, 1705.0, 1710000.0, 1005, 1701.5)])
    rows = repo.fetch_day("600519.SH", ts.date())
    assert len(rows) == 1 and rows[0]["price"] == 1705.0

def test_purge_before(tmp_path):
    repo = IntradayLineRepo(str(tmp_path / "intraday_ashare.duckdb"))
    old = datetime(2026, 1, 1, 1, 31, tzinfo=timezone.utc)
    new = datetime(2026, 6, 1, 1, 31, tzinfo=timezone.utc)
    repo.insert_points([
        IntradayPoint("X", old, 1.0, 1.0, 1, 1.0),
        IntradayPoint("X", new, 2.0, 2.0, 1, 2.0)])
    repo.purge_before(datetime(2026, 3, 1, tzinfo=timezone.utc))
    rows = repo.fetch_day("X", new.date())
    assert len(rows) == 1
    assert repo.fetch_day("X", old.date()) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/persistence/test_intraday_repo.py -v`
Expected: FAIL(ImportError)

- [ ] **Step 3: 实现**

```python
# core/persistence/intraday_repo.py
from __future__ import annotations
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import duckdb

@dataclass
class IntradayPoint:
    symbol: str
    ts: datetime
    price: float
    cum_amount: float
    cum_volume: int
    avg_price: float

class IntradayLineRepo:
    """A 股/美股当日分时点存储, 独立 DuckDB 文件(物理隔离, 雷区 6)。"""
    def __init__(self, db_path: str, *, read_only: bool = False) -> None:
        self.db_path = db_path
        self.read_only = read_only
        self._lock = threading.Lock()
        if not read_only:
            self._ensure_schema()

    @contextmanager
    def _conn(self):
        con = duckdb.connect(self.db_path, read_only=self.read_only)
        try:
            yield con
        finally:
            con.close()

    def _ensure_schema(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS intraday_lines (
                    symbol VARCHAR, ts TIMESTAMP, price DOUBLE,
                    cum_amount DOUBLE, cum_volume BIGINT, avg_price DOUBLE,
                    PRIMARY KEY (symbol, ts)
                )
            """)

    def insert_points(self, points: list[IntradayPoint]) -> None:
        if not points:
            return
        rows = [(p.symbol, p.ts.astimezone(timezone.utc).replace(tzinfo=None),
                 p.price, p.cum_amount, p.cum_volume, p.avg_price) for p in points]
        with self._lock, self._conn() as c:
            c.executemany("""
                INSERT INTO intraday_lines (symbol, ts, price, cum_amount, cum_volume, avg_price)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (symbol, ts) DO UPDATE SET
                    price=excluded.price, cum_amount=excluded.cum_amount,
                    cum_volume=excluded.cum_volume, avg_price=excluded.avg_price
            """, rows)

    def fetch_day(self, symbol: str, day: date) -> list[dict]:
        with self._lock, self._conn() as c:
            rs = c.execute("""
                SELECT ts, price, cum_amount, cum_volume, avg_price
                FROM intraday_lines
                WHERE symbol = ? AND CAST(ts AS DATE) = ?
                ORDER BY ts ASC
            """, (symbol, day)).fetchall()
        return [{"ts": r[0].replace(tzinfo=timezone.utc).isoformat(),
                 "price": r[1], "cum_amount": r[2],
                 "cum_volume": r[3], "avg_price": r[4]} for r in rs]

    def purge_before(self, cutoff: datetime) -> int:
        cut = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM intraday_lines WHERE ts < ?", (cut,))
        return 0
```

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/persistence/test_intraday_repo.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add core/persistence/intraday_repo.py tests/unit/persistence/test_intraday_repo.py
git commit -m "feat: IntradayLineRepo 独立分时库 + purge_before"
```

---

### Task 12: intraday_line_writer(quote 驱动写分时点 + 发 bus)

**Files:**
- Create: `apps/collector/ashare/intraday_line_writer.py`
- Test: `tests/unit/collector/test_intraday_line_writer.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_intraday_line_writer.py
import json, pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from apps.collector.ashare.intraday_line_writer import IntradayLineWriter, compute_point

def test_compute_point_avg_price():
    p = compute_point("600519.SH", datetime(2026,6,1,1,31,tzinfo=timezone.utc),
                      price=1705.0, cum_amount=1710000.0, cum_volume=1005)
    assert p.avg_price == pytest.approx(1710000.0 / 1005)

def test_compute_point_zero_volume_avg_falls_back_to_price():
    p = compute_point("X", datetime(2026,6,1,1,31,tzinfo=timezone.utc),
                      price=10.0, cum_amount=0.0, cum_volume=0)
    assert p.avg_price == 10.0

@pytest.mark.asyncio
async def test_write_once_inserts_and_publishes():
    repo = MagicMock()
    redis = MagicMock(); redis._r = MagicMock(); redis._r.xadd = AsyncMock()
    redis.get_msgpack = AsyncMock(return_value={
        "price": "1705", "volume": 1005, "amount": 1710000.0})
    w = IntradayLineWriter(repo, redis)
    now = datetime(2026, 6, 1, 5, 50, 30, tzinfo=timezone.utc)  # 13:50 BJT 开市
    await w.write_once("600519.SH", now=now)
    assert repo.insert_points.call_count == 1
    assert redis._r.xadd.await_count == 1
    payload = json.loads(redis._r.xadd.await_args[0][1]["data"].decode())
    assert payload["symbol"] == "600519.SH" and "avg_price" in payload
```

- [ ] **Step 2: 运行确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_intraday_line_writer.py -v`
Expected: FAIL(ImportError)

- [ ] **Step 3: 实现**

```python
# apps/collector/ashare/intraday_line_writer.py
from __future__ import annotations
import asyncio, json
from datetime import datetime, timezone
import structlog
from core.cache import keys
from core.domain.market_calendar import is_trading_day
from core.domain.market_sessions import is_market_session_open
from core.persistence.intraday_repo import IntradayLineRepo, IntradayPoint

log = structlog.get_logger(__name__)
WRITE_INTERVAL_S = 10

def compute_point(symbol: str, ts: datetime, *, price: float,
                  cum_amount: float, cum_volume: int) -> IntradayPoint:
    avg = (cum_amount / cum_volume) if cum_volume else price
    minute_ts = ts.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return IntradayPoint(symbol=symbol, ts=minute_ts, price=price,
                         cum_amount=cum_amount, cum_volume=cum_volume, avg_price=avg)

class IntradayLineWriter:
    def __init__(self, repo: IntradayLineRepo, redis):
        self._repo = repo
        self._redis = redis
        self._stopped = False

    async def write_once(self, symbol: str, *, now: datetime) -> None:
        if not is_market_session_open("ashare", now):
            return
        try:
            q = await self._redis.get_msgpack(keys.cache_quote("ashare", symbol))
        except Exception:
            q = None
        if not q:
            return
        price = float(q.get("price"))
        cum_volume = int(q.get("volume") or 0)
        cum_amount = float(q.get("amount") or 0.0)
        p = compute_point(symbol, now, price=price, cum_amount=cum_amount, cum_volume=cum_volume)
        try:
            self._repo.insert_points([p])
        except Exception as e:  # noqa: BLE001
            log.warning("intraday.write_failed", symbol=symbol, error=str(e))
            return
        payload = {"market": "ashare", "symbol": symbol, "ts": p.ts.isoformat(),
                   "price": p.price, "avg_price": p.avg_price}
        try:
            await self._redis._r.xadd(  # noqa: SLF001
                keys.BUS_INTRADAY_UPDATED,
                {"data": json.dumps(payload).encode()},
                maxlen=20000, approximate=True)
            await self._redis.set_msgpack(
                keys.cache_intraday_current("ashare", symbol), payload, ttl=120)
        except Exception as e:  # noqa: BLE001
            log.warning("intraday.publish_failed", symbol=symbol, error=str(e))

    async def _scan_subscribed(self) -> set[str]:
        active: set[str] = set()
        try:
            cursor = 0
            while True:
                cursor, found = await self._redis._r.scan(  # noqa: SLF001
                    cursor, match="state:subscribe:ashare:*", count=200)
                for k in found:
                    parts = (k.decode() if isinstance(k, bytes) else k).split(":")
                    if len(parts) >= 4:
                        active.add(parts[3])
                if cursor == 0:
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("intraday.scan_failed", error=str(e))
        return active

    async def run(self) -> None:
        log.info("intraday_line_writer.started")
        while not self._stopped:
            try:
                if is_trading_day("ashare") and is_market_session_open("ashare"):
                    now = datetime.now(timezone.utc)
                    for symbol in await self._scan_subscribed():
                        await self.write_once(symbol, now=now)
            except Exception as e:  # noqa: BLE001
                log.warning("intraday.loop_error", error=str(e))
            await asyncio.sleep(WRITE_INTERVAL_S)

async def run_intraday_line_writer(repo: IntradayLineRepo, redis) -> None:
    await IntradayLineWriter(repo, redis).run()
```

- [ ] **Step 4: 运行确认通过**

Run: `. .venv/bin/activate && pytest tests/unit/collector/test_intraday_line_writer.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/collector/ashare/intraday_line_writer.py tests/unit/collector/test_intraday_line_writer.py
git commit -m "feat: intraday_line_writer quote 驱动写分时点 + 发 bus"
```

---

### Task 13: 分时 SSE 端点 + collector 内嵌只读路由 + api 转发

**Files:**
- Create: `apps/api/routes/sse_intraday.py`
- Modify: `apps/collector/base.py`(加 `attach_intraday_route`)、`apps/api/main.py`(注册 sse_intraday router)、`apps/api/routes/symbols.py`(加 `/intraday-line` 转发)、`apps/collector/ashare/main.py`(挂 attach_intraday_route)
- Test: `tests/integration/test_sse_intraday.py`(标 integration, 默认不跑)

- [ ] **Step 1: 写 SSE 端点(复刻 sse_bars.py 结构)**

`apps/api/routes/sse_intraday.py`:订阅 `keys.BUS_INTRADAY_UPDATED`,init 读 `cache_intraday_current`,按 symbol 过滤推 `point` 事件。结构同 `sse_bars.py::_stream_gen`,但 channel 换成 BUS_INTRADAY_UPDATED、过滤只按 symbol(无 interval)、事件名 `point`。

```python
# apps/api/routes/sse_intraday.py (核心 generator, 其余 boilerplate 同 sse_bars.py)
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
import asyncio, json
from datetime import datetime, timezone
import structlog
from apps.api.deps import get_redis_cache
from core.cache import keys
from core.domain.markets import infer_market

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/sse", tags=["sse"])
PING_INTERVAL_S = 30

def _sse_event(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()

async def _gen(symbol: str, redis_cache):
    market = infer_market(symbol)
    server_ts = datetime.now(timezone.utc).isoformat()
    yield _sse_event("connected", {"symbol": symbol, "server_ts": server_ts})
    try:
        cur = await redis_cache.get_msgpack(keys.cache_intraday_current(market, symbol))
    except Exception:
        cur = None
    if cur:
        yield _sse_event("init", {"point": cur, "symbol": symbol})
    try:
        c0 = await redis_cache._r.xread(streams={keys.BUS_INTRADAY_UPDATED: "$"}, count=1, block=0)
    except Exception:
        c0 = None
    last_id = "$"
    if c0:
        for _s, msgs in c0:
            if msgs:
                last_id = msgs[-1][0]
    last_ping = datetime.now(timezone.utc)
    while True:
        try:
            entries = await redis_cache._r.xread(
                streams={keys.BUS_INTRADAY_UPDATED: last_id},
                count=20, block=PING_INTERVAL_S * 1000)
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("sse_intraday.read_failed", error=str(e))
            await asyncio.sleep(1); continue
        now = datetime.now(timezone.utc)
        if entries:
            for _s, msgs in entries:
                for msg_id, fields in msgs:
                    last_id = msg_id
                    try:
                        raw = fields.get(b"data") or fields.get("data")
                        if raw is None:
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", "replace")
                        payload = json.loads(raw)
                        if payload.get("symbol") == symbol:
                            yield _sse_event("point", payload); last_ping = now
                    except asyncio.CancelledError:
                        return
                    except Exception as e:
                        log.warning("sse_intraday.parse_failed", error=str(e))
        if (now - last_ping).total_seconds() >= PING_INTERVAL_S:
            yield _sse_event("ping", {"server_ts": now.isoformat()}); last_ping = now

@router.get("/intraday/{symbol}")
async def sse_intraday(symbol: str, redis_cache=Depends(get_redis_cache)):
    return StreamingResponse(_gen(symbol, redis_cache), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})
```

`apps/api/main.py` 注册:`from apps.api.routes import sse_intraday` + `app.include_router(sse_intraday.router)`。

- [ ] **Step 2: collector 内嵌只读路由 `attach_intraday_route`**

`apps/collector/base.py` 仿 `attach_bars_history_route` 加(module 级挂载):
```python
def attach_intraday_route(app, get_intraday_repo, market: str) -> None:
    @app.get("/internal/intraday-line")
    async def intraday_line(symbol: str, date: str | None = None):  # noqa: ANN202
        from datetime import datetime, timezone
        repo = get_intraday_repo()
        if repo is None:
            return {"symbol": symbol, "points": [], "meta": {"stale": True, "reason": "repo_not_ready"}}
        day = (datetime.fromisoformat(date).date() if date
               else datetime.now(timezone.utc).date())
        try:
            pts = repo.fetch_day(symbol, day)
        except Exception as e:  # noqa: BLE001
            return {"symbol": symbol, "points": [], "meta": {"stale": True, "reason": str(e)}}
        return {"symbol": symbol, "points": pts, "meta": {"stale": False}}
```

- [ ] **Step 3: api 转发 `/intraday-line`**

`apps/api/routes/symbols.py` 仿 `bars_history`(`:241`)加路由 `GET /{symbol}/intraday-line`,httpx 转发到 collector `/internal/intraday-line`(端口按 market:ashare=8788),`trust_env=False`,collector 不可达 → `stale=true`。

- [ ] **Step 4: ashare main 接线 writer + intraday repo + attach_intraday_route**

`apps/collector/ashare/main.py`:
- 创建 `intraday_repo = IntradayLineRepo("data/intraday_ashare.duckdb")`
- module 级(app 定义后)调 `attach_intraday_route(app, lambda: intraday_repo, "ashare")`(参考 attach_bars_history_route 的 override 模式)
- lifespan 内 `_intraday_task = asyncio.create_task(run_intraday_line_writer(intraday_repo, redis_cache))` + finally cancel

- [ ] **Step 5: 运行验证 + 提交**

Run: `. .venv/bin/activate && python -c "from apps.api.main import app; from apps.collector.ashare.main import app as a; print('OK')" && cd apps/web && npx tsc --noEmit && cd ../..`
Expected: OK + tsc 无错

```bash
git add apps/api/routes/sse_intraday.py apps/api/main.py apps/collector/base.py apps/api/routes/symbols.py apps/collector/ashare/main.py
git commit -m "feat: 分时 SSE 端点 + collector 内嵌只读路由 + api 转发"
```

---

### Task 14: 分时 purge cron + sweep 降频 2h

**Files:**
- Modify: `apps/collector/ashare/main.py`(加 purge cron + sweep interval 30→120min)
- Test: 手动验证(cron 注册)

- [ ] **Step 1: 实现**

`apps/collector/ashare/main.py`:
- sweep_derived 的 `add_job` interval `minutes=30` 改 `minutes=120`(降频兜底)
- 加每日 purge cron:
```python
    async def _purge_intraday():
        from datetime import datetime, timezone, timedelta
        try:
            n = intraday_repo.purge_before(datetime.now(timezone.utc) - timedelta(days=90))
            log.info("intraday.purged", before_days=90)
        except Exception as e:  # noqa: BLE001
            log.warning("intraday.purge_failed", error=str(e))
    sched.add_job(_purge_intraday, "cron", hour=2, minute=30,
                  id="ashare:intraday_purge", max_instances=1, coalesce=True)
```

- [ ] **Step 2: 验证 import + cron 注册**

Run: `. .venv/bin/activate && python -c "from apps.collector.ashare.main import app; print('OK')"`
Expected: OK

- [ ] **Step 3: 提交**

```bash
git add apps/collector/ashare/main.py
git commit -m "feat: 分时 90 天 purge cron + sweep 降频 2h 兜底"
```

---

### Task 15: 前端分时折线组件 + EventSource hook

**Files:**
- Create: `apps/web/lib/use_intraday_line.ts`、`apps/web/components/IntradayLineChart.tsx`
- Test: `cd apps/web && npx tsc --noEmit`

- [ ] **Step 1: hook(首屏拉当日 + SSE 订阅)**

`apps/web/lib/use_intraday_line.ts`:
- `useIntradayLine(symbol)`:SWR 拉 `/api/symbols/{symbol}/intraday-line`(首屏当日全量),返回 `points[]`(ts/price/avg_price)
- EventSource 订阅 `/api/sse/intraday/{symbol}`,`point` 事件 append/更新最右点(按 ts 去重)
- 复刻现有 `use_kline_stream.ts` 的 EventSource 生命周期(connect/cleanup/重连)

- [ ] **Step 2: 折线组件**

`apps/web/components/IntradayLineChart.tsx`:
- 用 lightweight-charts 的 `addLineSeries`(非蜡烛):price 一条线 + avg_price 一条黄线
- 横轴当日 09:30-15:00;数据来自 `useIntradayLine`
- 复用项目现有图表容器/样式约定(参考 `KLineChart`)

- [ ] **Step 3: 验证**

Run: `cd apps/web && npx tsc --noEmit && cd ../..`
Expected: 无类型错误

- [ ] **Step 4: 提交**

```bash
git add apps/web/lib/use_intraday_line.ts apps/web/components/IntradayLineChart.tsx
git commit -m "feat: 前端分时折线组件 + EventSource hook"
```

---

## 收尾验证(全部 Task 完成后)

- [ ] **后端 import 测试**

Run: `. .venv/bin/activate && python -c "from apps.api.main import app; from apps.collector.crypto.main import app as c; from apps.collector.us.main import app as u; from apps.collector.ashare.main import app as a; print('OK')"`

- [ ] **前端类型检查**

Run: `cd apps/web && npx tsc --noEmit && cd ../..`

- [ ] **全套单测**

Run: `. .venv/bin/activate && pytest -m "not integration" -q`

- [ ] **重启 3 collector + api 冒烟**(雷区 2 模板)

按 CLAUDE.md 雷区 2 的 pkill + nohup 重启模板,然后:
Run: `curl -s -m 3 http://localhost:8787/api/health | grep -o '"status":"[^"]*"'` + `for p in 8788 8789 8790; do curl -s -m 3 http://127.0.0.1:$p/health; done`

- [ ] **Playwright 证据式验证**(memory feedback_playwright_evidence_testing)

A 股盘中:驱动真实 Chrome 打开个股详情,拦 `/api/sse/bars` 与 `/api/sse/intraday` 网络流,确认:① K 线进行中桶 final=false 在跳;② 分时折线 + 均价线在更新。

---

## 后续计划(本计划不含)

- **步骤 5**:美股复刻(ws_consumer 改 REST 源头直取 5m/15m/30m;美股 ticker;美股分时)。
- **步骤 6**:更正 CLAUDE.md "美股/A股无实时推送"过时段落 + 补本设计落地态。
