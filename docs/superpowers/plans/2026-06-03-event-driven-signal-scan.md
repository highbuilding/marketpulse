# 事件驱动 CD 信号扫描 实施计划

> **For agentic workers:** 本计划把 CD 信号 scan 从 cron+自拉自聚合 改为 bus:bars.updated 事件驱动 + 只读已存 bar。Steps 用 checkbox 跟踪。设计见 `docs/superpowers/specs/2026-06-03-event-driven-signal-scan-design.md`。

**Goal:** scan 改为纯下游消费者:只读 DuckDB 已存确定 bar → compute_cd_signals → upsert SQLite,由 bus:bars.updated 事件触发,根除 60m/4h close/open 偏移。

**Architecture:** 新增 `scan_symbol_readonly`(用 `repo.fetch_history` 直读,绕开 get_bars 的 _INTRADAY_AGG 聚合分支)+ `SignalScanConsumer`(订阅 bus:bars.updated,final=true+信号周期才扫)。三 collector 接线 consumer、移除 cd:* cron。历史数据用一次性脚本删旧写新。

**Tech Stack:** Python/asyncio、Redis Streams(consumer group)、DuckDB(只读 fetch_history)、SQLite(信号 upsert)。

---

## 文件结构

**新增:**
- `core/services/signal_service.py`(改):`scan_symbol_readonly(symbol, interval)` 方法
- `apps/collector/jobs/signal_scan_consumer.py`:`run_signal_scan_consumer` 消费循环 + `handle_bar_event`
- `apps/rescan_all_signals.py`:历史修正一次性脚本
- `tests/unit/services/test_scan_readonly.py`、`tests/unit/collector/test_signal_scan_consumer.py`

**改动:**
- `apps/collector/{ashare,us,crypto}/main.py`:启动接线 consumer
- `core/scheduler/scheduler.py`:移除 cd:* cron

---

## Task 1: scan_symbol_readonly(只读扫描)

**Files:**
- Modify: `core/services/signal_service.py`
- Test: `tests/unit/services/test_scan_readonly.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/services/test_scan_readonly.py
import pytest
from datetime import datetime, timezone
from core.services.signal_service import SignalScanService

class FakeRepo:
    def __init__(self, bars): self._bars = bars; self.upserted = []
    def fetch_history(self, market, symbol, start, end, interval): return self._bars
class FakeSigRepo:
    async def upsert_many(self, records): return len(records)

@pytest.mark.asyncio
async def test_scan_readonly_uses_repo_not_aggregate(monkeypatch):
    # 喂 3 根 4h bar, 不应触发任何 fetch/aggregate
    from core.domain.models import Bar
    bars = [Bar(market="crypto", symbol="BTC-USDT", ts=datetime(2026,5,23,16,tzinfo=timezone.utc),
                open=1, high=2, low=0.5, close=1.5, volume=10, interval="4h")]
    class KL:
        def __init__(self): self.repo = FakeRepo(bars)
    svc = SignalScanService(KL(), FakeSigRepo())
    n = await svc.scan_symbol_readonly("BTC-USDT", "4h")
    assert isinstance(n, int)  # 不抛错, 走只读路径
```

- [ ] **Step 2: 跑测试确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/services/test_scan_readonly.py -v`
Expected: FAIL（`scan_symbol_readonly` 不存在 / AttributeError）

- [ ] **Step 3: 实现 scan_symbol_readonly**

在 `SignalScanService` 加方法(读 repo 已存 bar,绕开 get_bars 聚合):
```python
async def scan_symbol_readonly(self, symbol: str, interval: Interval) -> int:
    """事件驱动: 只读已存 bar 算信号, 不 fetch/aggregate/persist。"""
    from datetime import datetime, timedelta, timezone
    from core.domain.intervals import BARS_PER_DAY, LOOKBACK_BARS
    from core.domain.markets import infer_market
    market = infer_market(symbol)
    end = datetime.now(timezone.utc)
    lookback = LOOKBACK_BARS.get(interval, 200)
    days = max(lookback // BARS_PER_DAY.get(interval, 1) * 2, 30)
    start = end - timedelta(days=days)
    if self.kline.repo is None:
        return 0
    bars = self.kline.repo.fetch_history(market, symbol, start, end, interval=interval)
    if not bars:
        return 0
    cd_signals = compute_cd_signals(bars)
    detected_at = datetime.now(timezone.utc)
    records = [
        IndicatorSignal(
            symbol=symbol, interval=interval, indicator="CD",
            signal_type=s.signal_type, bar_ts=s.bar_ts,
            detected_at=detected_at, price=s.price, d_value=s.d_value,
        )
        for s in cd_signals
    ]
    n = await self.repo.upsert_many(records)
    if n > 0:
        log.info("signal.scan_readonly_new", symbol=symbol, interval=interval, new=n)
    return n
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/services/test_scan_readonly.py -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add core/services/signal_service.py tests/unit/services/test_scan_readonly.py
git commit -m "feat(signal): scan_symbol_readonly 只读已存 bar 算信号(不 fetch/aggregate)"
```

---

## Task 2: SignalScanConsumer(订阅 bus:bars.updated)

**Files:**
- Create: `apps/collector/jobs/signal_scan_consumer.py`
- Test: `tests/unit/collector/test_signal_scan_consumer.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_signal_scan_consumer.py
import pytest
from apps.collector.jobs.signal_scan_consumer import should_scan

def test_should_scan_filters():
    # final=true + 信号周期 → 扫
    assert should_scan({"final": True, "interval": "4h"}) is True
    # 进行中态 → 不扫
    assert should_scan({"final": False, "interval": "4h"}) is False
    # 非信号周期 → 不扫
    assert should_scan({"final": True, "interval": "1m"}) is False
    assert should_scan({"final": True, "interval": "1wk"}) is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/collector/test_signal_scan_consumer.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 consumer**

```python
# apps/collector/jobs/signal_scan_consumer.py
"""订阅 bus:bars.updated, final=true + 信号周期 → scan_symbol_readonly。
scan 作为下游, 不拉数据/不聚合, 只读已存 bar。
"""
from __future__ import annotations
import json
import structlog
from redis.asyncio import Redis as AsyncRedis
from core.cache import keys
from core.domain.intervals import SIGNAL_INTERVALS_SET

log = structlog.get_logger(__name__)
_GROUP = "signal_scan"


def should_scan(payload: dict) -> bool:
    return bool(payload.get("final")) and payload.get("interval") in SIGNAL_INTERVALS_SET


async def _ensure_group(redis: AsyncRedis, stream: str) -> None:
    try:
        await redis.xgroup_create(stream, _GROUP, id="$", mkstream=True)
    except Exception as e:  # noqa: BLE001
        if "BUSYGROUP" not in str(e):
            raise


async def run_signal_scan_consumer(
    redis: AsyncRedis, *, consumer_id: str, scan_fn, market: str | None = None,
) -> None:
    """长循环消费 bus:bars.updated。scan_fn(symbol, interval) -> awaitable int。
    market 非空时只处理该市场事件(各 collector 各扫自己)。"""
    stream = keys.BUS_BARS_UPDATED
    await _ensure_group(redis, stream)
    log.info("signal_scan_consumer.start", consumer=consumer_id, market=market)
    while True:
        try:
            entries = await redis.xreadgroup(
                _GROUP, consumer_id, {stream: ">"}, count=50, block=5000)
        except Exception as e:  # noqa: BLE001
            log.warning("signal_scan_consumer.read_failed", error=str(e))
            continue
        for _stream, msgs in entries or []:
            for msg_id, fields in msgs:
                try:
                    payload = json.loads(fields[b"data"])
                    if (market is None or payload.get("market") == market) and should_scan(payload):
                        await scan_fn(payload["symbol"], payload["interval"])
                except Exception as e:  # noqa: BLE001
                    log.warning("signal_scan_consumer.handle_failed", error=str(e))
                finally:
                    await redis.xack(stream, _GROUP, msg_id)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/collector/test_signal_scan_consumer.py -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add apps/collector/jobs/signal_scan_consumer.py tests/unit/collector/test_signal_scan_consumer.py
git commit -m "feat(collector): SignalScanConsumer 订阅 bus:bars.updated 事件驱动扫描"
```

---

## Task 3: 三 collector 接线 + 移除 cron

**Files:**
- Modify: `apps/collector/ashare/main.py`、`apps/collector/us/main.py`、`apps/collector/crypto/main.py`
- Modify: `core/scheduler/scheduler.py`

- [ ] **Step 1: 各 collector 启动接线 consumer**

每个 collector main 的 lifespan 内,起 consumer task(以 ashare 为例,us/crypto 同样,market 改对应值):
```python
from apps.collector.jobs.signal_scan_consumer import run_signal_scan_consumer
from apps.api.deps import get_signal_scan_service
_scan_svc = get_signal_scan_service()
_scan_consumer_task = asyncio.create_task(
    run_signal_scan_consumer(
        _redis_for_mw, consumer_id=f"scan-ashare-{os.getpid()}",
        scan_fn=_scan_svc.scan_symbol_readonly, market="ashare"),
    name="ashare.signal_scan_consumer",
)
```
（us 用 `market="us"`、consumer_id `scan-us-`;crypto 用 `market="crypto"`、`scan-crypto-`。）

- [ ] **Step 2: 移除 scheduler.py 的 cd:* cron**

`core/scheduler/scheduler.py::attach_signal_jobs` 里删除 `cd:15m / cd:30m / cd:60m:* / cd:4h:* / cd:1d` 全部 `sched.add_job(...)`。保留函数壳(若被 import)但不注册 job;或整体不调用 attach_signal_jobs。

- [ ] **Step 3: import 测试 + 重启冒烟**

```bash
. .venv/bin/activate && python -c "from apps.collector.ashare.main import app as a; from apps.collector.us.main import app as u; from apps.collector.crypto.main import app as c; print('OK')"
# 按 CLAUDE.md 雷区2 重启 3 collector + api, 验证 health + signal_scan_consumer.start 日志
```
Expected: import OK;重启后 `grep signal_scan_consumer.start /tmp/collector_*.log` 三市场各一条

- [ ] **Step 4: commit**

```bash
git add apps/collector/ashare/main.py apps/collector/us/main.py apps/collector/crypto/main.py core/scheduler/scheduler.py
git commit -m "feat(collector): 三市场接线 SignalScanConsumer + 移除 cd:* cron"
```

---

## Task 4: 历史数据修正(一次性)

**Files:**
- Create: `apps/rescan_all_signals.py`

- [ ] **Step 1: 备份信号库**

```bash
cp data/state.db data/state.db.bak-$(date +%s)
```

- [ ] **Step 2: 写修正脚本**

```python
# apps/rescan_all_signals.py
"""一次性: 删旧信号 + 用现有 DuckDB bar 重算写新(scan_symbol_readonly 同口径)。
先备份 data/state.db。crypto 60m/4h 若被聚合污染, 先重跑 backfill 再跑本脚本。
"""
from __future__ import annotations
import asyncio, sqlite3
import duckdb
from datetime import datetime, timezone, timedelta
from core.domain.models import Bar, IndicatorSignal
from core.domain.intervals import LOOKBACK_BARS, BARS_PER_DAY
from core.indicators.cd import compute_cd_signals
from core.domain.core_symbols import CORE_SYMBOLS
from core.persistence.signal_repo import SignalRepo

IVS = ["15m", "30m", "60m", "4h", "1d"]
BARS_DB = {"ashare": "data/bars_ashare.duckdb", "us": "data/bars_us.duckdb",
           "crypto": "data/bars_crypto.duckdb"}


def load_bars(market, symbol, interval):
    con = duckdb.connect(BARS_DB[market], read_only=True)
    rows = con.execute(
        "SELECT ts,open,high,low,close,volume FROM bars WHERE symbol=? AND interval=? ORDER BY ts",
        [symbol, interval]).fetchall()
    con.close()
    return [Bar(market=market, symbol=symbol,
                ts=r[0].replace(tzinfo=timezone.utc) if r[0].tzinfo is None else r[0],
                open=r[1], high=r[2], low=r[3], close=r[4], volume=int(r[5] or 0),
                interval=interval) for r in rows]


async def main():
    repo = SignalRepo("data/state.db")
    raw = sqlite3.connect("data/state.db")
    for market in ("ashare", "us", "crypto"):
        for sym in CORE_SYMBOLS[market]:
            for iv in IVS:
                bars = load_bars(market, sym, iv)
                if not bars:
                    continue
                cds = compute_cd_signals(bars)
                # 删旧 (symbol, interval), 再写新 — 清除偏移残留
                raw.execute("DELETE FROM indicator_signals WHERE symbol=? AND interval=?", [sym, iv])
                raw.commit()
                det = datetime.now(timezone.utc)
                recs = [IndicatorSignal(symbol=sym, interval=iv, indicator="CD",
                        signal_type=s.signal_type, bar_ts=s.bar_ts, detected_at=det,
                        price=s.price, d_value=s.d_value) for s in cds]
                n = await repo.upsert_many(recs)
                print(f"{market} {sym} {iv}: bars={len(bars)} 重写={n}")
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: (crypto 60m/4h 被污染则先重 backfill)**

```bash
# crypto 60m/4h 若 scan 曾用聚合 bar 覆盖, 重跑原生直取覆盖回来
. .venv/bin/activate && python -c "
import asyncio
from apps.collector.crypto.backfill import backfill_one
from core.adapters.binance import BinanceAdapter
# (实施时按 backfill 模块实际入口调; 或直接重启 crypto collector 触发 daily backfill)
print('如需: 重启 crypto collector 触发 backfill 即可')
"
```

- [ ] **Step 4: 跑修正脚本**

Run: `. .venv/bin/activate && python -m apps.rescan_all_signals`
Expected: 每行打印 `市场 标的 周期: bars=N 重写=M`,末尾 `done`

- [ ] **Step 5: commit**

```bash
git add apps/rescan_all_signals.py
git commit -m "feat: 历史 CD 信号修正脚本(删旧写新, 统一 ts 口径)"
```

---

## Task 5: 验证

**Files:** 无改动(只跑验证)

- [ ] **Step 1: 重跑三市场核对**

Run: `. .venv/bin/activate && python -m apps.verify_all_signals 2>&1 | tail -20`
Expected: "系统偏移"(close/open +interval)归零;仅剩可解释的极少数(数据被后期修正的孤立项)

- [ ] **Step 2: 事件驱动端到端冒烟**

```bash
# 等一根 crypto bar 收线(或手动 xadd 一条 final=true 事件), 验证信号入库
docker exec marketpulse-redis-dev redis-cli XADD bus:bars.updated '*' data '{"market":"crypto","symbol":"BTC-USDT","interval":"4h","ts":"2026-06-03T00:00:00+00:00","final":true}'
sleep 3
grep "signal.scan_readonly_new\|signal_scan_consumer" /tmp/collector_crypto.log | tail -5
```
Expected: consumer 处理事件,无异常

- [ ] **Step 3: 全套单测**

Run: `pytest -m "not integration" -q`
Expected: 通过(新增 2 个测试文件 + 原有不回归)
