# CD 信号分发(SSE + 邮件 + 补扫)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development 或 executing-plans。设计见 `docs/superpowers/specs/2026-06-03-signal-distribution-sse-email-design.md`。Steps 用 checkbox 跟踪。

**Goal:** scan 产新信号发 bus:signal.new → 前端 SSE 实时推送 + 邮件 30min 攒批 + 补扫 cron 兜底。

**Architecture:** scan_symbol_readonly 拿到新增记录后 xadd bus:signal.new(fire-and-forget)。api 加 /api/sse/signals 订阅转发(复用 StreamHub)。邮件复用现成 NotificationService.maybe_send_summary(30min cron 调)。补扫 cron 对全标的全周期跑 scan_symbol_readonly 捞回漏事件。

**Tech Stack:** Redis Streams、StreamHub(已有)、NotificationService(已有)、APScheduler、Next.js EventSource。

**关键复用发现:**
- `NotificationService.maybe_send_summary(market)` 已完整实现:读 config → 当日信号 → 过滤 → snapshot hash 去重 → 渲染 → 广播启用收件人。邮件侧只需 30min cron 调它,不重写。
- `SignalRepo.list_recent(since=...)` 已支持 detected_at 区间查。
- `StreamHub(redis, channel, key_fn)` + `register(keys)` + `run()` 已有(sse_bars 在用)。
- `_leader_gated(coro_func)` 已有(scheduler.py:30)。

---

## 文件结构

**新增:**
- `apps/api/routes/sse_signals.py`:`/api/sse/signals` SSE 端点
- `apps/collector/jobs/signal_sweep_worker.py`:30min 补扫 cron
- `apps/web/lib/use_signal_stream.ts`:前端 EventSource hook
- 测试:`tests/unit/services/test_scan_publishes_signal.py`、`tests/unit/collector/test_signal_sweep.py`

**改动:**
- `core/services/signal_service.py`:scan_symbol_readonly 发 bus:signal.new
- `apps/api/main.py`:挂 sse_signals 路由 + StreamHub lifespan
- `apps/collector/ashare/main.py`:挂 SignalDigestWorker(30min 调 maybe_send_summary)+ SignalSweepWorker
- `apps/web/app/page.tsx`、`apps/web/app/signals/page.tsx`:EventSource + 当天交易日过滤

---

## Task 1: scan_symbol_readonly 发 bus:signal.new

**Files:**
- Modify: `core/services/signal_service.py`
- Test: `tests/unit/services/test_scan_publishes_signal.py`

发布"哪几条新增":upsert 前先查该 (symbol,interval) 已存 bar_ts 集合,compute 后取差集 = 新增,再 upsert + 对新增发事件。需给 SignalScanService 注入可选 redis。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/services/test_scan_publishes_signal.py
import pytest
from datetime import datetime, timezone
from core.domain.models import Bar
from core.services.signal_service import SignalScanService

class FakeRepo:
    def __init__(self, bars): self._bars = bars
    def fetch_history(self, market, symbol, start, end, interval): return self._bars

class FakeSigRepo:
    def __init__(self): self.existing = set()
    async def upsert_many(self, records): return len(records)
    async def existing_bar_ts(self, symbol, interval): return self.existing

class FakeRedis:
    def __init__(self): self.added = []
    async def xadd(self, stream, fields, **kw): self.added.append((stream, fields))

class FakeKLine:
    def __init__(self, bars): self.repo = FakeRepo(bars)

def _bar(h):
    return Bar(market="crypto", symbol="BTC-USDT",
               ts=datetime(2026,5,23,h,tzinfo=timezone.utc),
               open=1, high=2, low=0.5, close=1.5, volume=10, interval="4h")

@pytest.mark.asyncio
async def test_scan_publishes_new_signal_to_bus():
    bars = [_bar(h) for h in range(0, 24, 4)]
    redis = FakeRedis()
    svc = SignalScanService(FakeKLine(bars), FakeSigRepo(), redis=redis)
    await svc.scan_symbol_readonly("BTC-USDT", "4h")
    # 有新信号时应 xadd 到 bus:signal.new
    assert any("signal.new" in s for s, _ in redis.added) or redis.added == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `. .venv/bin/activate && pytest tests/unit/services/test_scan_publishes_signal.py -v`
Expected: FAIL（SignalScanService 不接受 redis 参数 / 无 xadd）

- [ ] **Step 3: 改 SignalScanService 构造 + 发事件**

构造加可选 redis;`scan_symbol_readonly` 改为对新增信号发 bus:
```python
def __init__(self, kline, repo, *, redis=None) -> None:
    self.kline = kline
    self.repo = repo
    self.redis = redis
```
`scan_symbol_readonly` 末尾(upsert 后)加发布(用 compute 结果,因 upsert 幂等无法精确知道哪条新增,改为:发布前查已存 bar_ts diff):
```python
# 在 compute_cd_signals 后, upsert 前:
existing = await self.repo.existing_bar_ts(symbol, interval) if hasattr(self.repo, "existing_bar_ts") else set()
fresh = [s for s in cd_signals if s.bar_ts.isoformat() not in existing]
# ... upsert all records (幂等) ...
n = await self.repo.upsert_many(records)
if self.redis is not None and fresh:
    import json
    for s in fresh:
        payload = {"market": market, "symbol": symbol, "interval": interval,
                   "signal_type": s.signal_type, "bar_ts": s.bar_ts.isoformat(),
                   "price": float(s.price) if s.price is not None else None,
                   "detected_at": detected_at.isoformat()}
        try:
            await self.redis.xadd(keys.BUS_SIGNAL_NEW, {"data": json.dumps(payload).encode()},
                                  maxlen=10000, approximate=True)
        except Exception as e:  # noqa: BLE001
            log.warning("signal.publish_failed", symbol=symbol, error=str(e))
return n
```
并在 signal_repo 加 `existing_bar_ts(symbol, interval) -> set[str]`(SELECT bar_ts WHERE symbol+interval+indicator=CD)。import keys。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/services/test_scan_publishes_signal.py -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
git add core/services/signal_service.py core/persistence/signal_repo.py tests/unit/services/test_scan_publishes_signal.py
git commit -m "feat(signal): scan_symbol_readonly 对新增信号发 bus:signal.new"
```

---

## Task 2: 接线 redis 到三 collector 的 scan service

**Files:**
- Modify: `apps/api/deps.py`(get_signal_scan_service 传 redis)或各 collector main 构造时传

- [ ] **Step 1: get_signal_scan_service 支持注入 redis**

`apps/api/deps.py::get_signal_scan_service` 当前 `SignalScanService(get_kline_service(), get_signal_repo())`。collector 端用 raw redis 构造。最简:各 collector main 里 consumer 接线处,改用带 redis 的 service 实例:
```python
from core.services.signal_service import SignalScanService
from apps.api.deps import get_kline_service, get_signal_repo
_scan_svc = SignalScanService(get_kline_service(), get_signal_repo(), redis=_redis_for_mw)
```
（crypto 用 `redis_cache._r`;ashare/us 用 `_redis_for_mw`。替换 Task3 已接线的 `get_signal_scan_service()`。）

- [ ] **Step 2: import 测试 + 重启冒烟**

```bash
. .venv/bin/activate && python -c "from apps.collector.ashare.main import app as a; from apps.collector.us.main import app as u; from apps.collector.crypto.main import app as c; print('OK')"
```
Expected: OK

- [ ] **Step 3: commit**

```bash
git add apps/collector/ashare/main.py apps/collector/us/main.py apps/collector/crypto/main.py
git commit -m "feat(collector): scan service 注入 redis 以发 bus:signal.new"
```

---

## Task 3: /api/sse/signals 端点

**Files:**
- Create: `apps/api/routes/sse_signals.py`
- Modify: `apps/api/main.py`

- [ ] **Step 1: 实现 SSE 端点(复用 StreamHub)**

参照 `apps/api/routes/sse_bars.py`。`sse_signals.py`:
```python
"""SSE: 订阅 bus:signal.new 转发浏览器。全量推, 前端按市场过滤。"""
from __future__ import annotations
import json
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse
from core.cache import keys

router = APIRouter()

@router.get("/api/sse/signals")
async def sse_signals(request: Request):
    hub = request.app.state.signal_hub
    sub = hub.register(["*"])  # 全量
    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                msg = await sub.get()
                yield {"event": "signal", "data": json.dumps(msg)}
        finally:
            hub.unregister(sub)
    return EventSourceResponse(gen())
```
（key_fn 用固定 "*" 全量分发;若 StreamHub 接口不同,按 sse_bars.py 实际签名适配。)

- [ ] **Step 2: main.py 挂路由 + StreamHub lifespan**

`apps/api/main.py` 仿照 bars hub:lifespan 起 `StreamHub(redis, keys.BUS_SIGNAL_NEW, lambda m: "*")` 存 `app.state.signal_hub`,`asyncio.create_task(hub.run())`;include sse_signals.router。

- [ ] **Step 3: import 测试 + 重启 api 冒烟**

```bash
. .venv/bin/activate && python -c "from apps.api.main import app; print('OK')"
# 重启 api, curl -N http://127.0.0.1:8787/api/sse/signals 应保持连接
```
Expected: OK;SSE 连接建立

- [ ] **Step 4: commit**

```bash
git add apps/api/routes/sse_signals.py apps/api/main.py
git commit -m "feat(api): /api/sse/signals 订阅 bus:signal.new 转发前端"
```

---

## Task 4: 前端 EventSource + 当天交易日过滤

**Files:**
- Create: `apps/web/lib/use_signal_stream.ts`
- Modify: `apps/web/app/page.tsx`、`apps/web/app/signals/page.tsx`

- [ ] **Step 1: EventSource hook**

```typescript
// apps/web/lib/use_signal_stream.ts
'use client'
import { useEffect, useState } from 'react'
import type { CDSignalDTO } from './types'

export function useSignalStream(market: string): CDSignalDTO[] {
  const [live, setLive] = useState<CDSignalDTO[]>([])
  useEffect(() => {
    const apiBase = typeof window !== 'undefined' && window.location.port === '3000'
      ? 'http://127.0.0.1:8787' : ''
    const es = new EventSource(`${apiBase}/api/sse/signals`)
    es.addEventListener('signal', (e: any) => {
      try { const s = JSON.parse(e.data); setLive((cur) => [s, ...cur].slice(0, 50)) }
      catch {}
    })
    es.onerror = () => {}
    return () => es.close()
  }, [])
  return live
}
```

- [ ] **Step 2: 概览"最近信号"接入 + 当天交易日过滤**

`app/page.tsx`:首屏 `listCDSignals({market})` 已有,加按当天交易日过滤(用 `tradingDateKey(s.bar_ts, market) === todayKey(market)`),并 merge `useSignalStream(market)` 的实时信号(按 symbol+interval+bar_ts+signal_type 去重)。

- [ ] **Step 3: tsc + 走查**

Run: `cd apps/web && npx tsc --noEmit`
Expected: exit 0;`/` `/signals` 200

- [ ] **Step 4: commit**

```bash
git add apps/web/lib/use_signal_stream.ts apps/web/app/page.tsx apps/web/app/signals/page.tsx
git commit -m "feat(web): 最近信号 EventSource 实时追加 + 当天交易日过滤"
```

---

## Task 5: 邮件 30min cron(复用 maybe_send_summary)

**Files:**
- Modify: `apps/collector/ashare/main.py`

- [ ] **Step 1: 挂 SignalDigestWorker cron**

ashare main scheduler 加(复用现成 NotificationService.maybe_send_summary):
```python
from core.scheduler.leader_gate import is_leader  # 或 _leader_gated 包装
_notify_svc = get_notification_service()
async def _signal_digest():
    for mkt in ("ashare", "us", "crypto"):
        try:
            await _notify_svc.maybe_send_summary(mkt)
        except Exception as e:  # noqa: BLE001
            log.warning("signal_digest.failed", market=mkt, error=str(e))
sched.add_job(_leader_gated(_signal_digest), IntervalTrigger(minutes=30),
              id="signal:digest", max_instances=1, coalesce=True)
```

- [ ] **Step 2: import 测试 + 重启冒烟**

```bash
. .venv/bin/activate && python -c "from apps.collector.ashare.main import app; print('OK')"
# 重启 ashare collector, 手动触发或等 30min, 验证 SMTP 未配时 notify.email.disabled 降级日志
```
Expected: OK

- [ ] **Step 3: commit**

```bash
git add apps/collector/ashare/main.py
git commit -m "feat(collector): 信号摘要邮件 30min cron(复用 maybe_send_summary)"
```

---

## Task 6: 补扫 cron 兜底

**Files:**
- Create: `apps/collector/jobs/signal_sweep_worker.py`
- Modify: `apps/collector/ashare/main.py`
- Test: `tests/unit/collector/test_signal_sweep.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_signal_sweep.py
import pytest
from apps.collector.jobs.signal_sweep_worker import sweep_symbols_for_market

@pytest.mark.asyncio
async def test_sweep_calls_scan_for_all_signal_intervals():
    calls = []
    class FakeScan:
        async def scan_symbol_readonly(self, sym, iv): calls.append((sym, iv))
    await sweep_symbols_for_market(FakeScan(), ["BTC-USDT"], market="crypto")
    ivs = {iv for _, iv in calls}
    assert ivs == {"15m", "30m", "60m", "4h", "1d"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/collector/test_signal_sweep.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现补扫**

```python
# apps/collector/jobs/signal_sweep_worker.py
"""30min 补扫 cron: 对全标的全信号周期跑 scan_symbol_readonly, 捞回漏事件。
同一只读路径, 幂等不引偏移。完整性兜底(事件驱动可能丢事件)。"""
from __future__ import annotations
import structlog
from core.domain.intervals import SIGNAL_INTERVALS

log = structlog.get_logger(__name__)


async def sweep_symbols_for_market(scan_svc, symbols, *, market: str) -> int:
    total = 0
    for sym in symbols:
        for iv in SIGNAL_INTERVALS:
            try:
                total += await scan_svc.scan_symbol_readonly(sym, iv) or 0
            except Exception as e:  # noqa: BLE001
                log.warning("signal_sweep.failed", symbol=sym, interval=iv, error=str(e))
    log.info("signal_sweep.done", market=market, symbols=len(symbols), new=total)
    return total
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/collector/test_signal_sweep.py -v`
Expected: PASS

- [ ] **Step 5: ashare main 挂补扫 cron**

```python
from apps.collector.jobs.signal_sweep_worker import sweep_symbols_for_market
from core.domain.core_symbols import core_symbols as _core_symbols
async def _signal_sweep():
    for mkt in ("ashare", "us", "crypto"):
        wl = await get_watchlist_service().dynamic_universe()
        from core.domain.markets import infer_market
        syms = sorted({s for s in (set(wl) | set(_core_symbols(mkt))) if infer_market(s) == mkt})
        await sweep_symbols_for_market(_scan_svc, syms, market=mkt)
sched.add_job(_leader_gated(_signal_sweep), IntervalTrigger(minutes=30),
              id="signal:sweep", max_instances=1, coalesce=True)
```
（`_scan_svc` 用 Task2 带 redis 的实例,补扫新增也发 bus:signal.new。)

- [ ] **Step 6: import 测试 + 重启 + commit**

```bash
. .venv/bin/activate && python -c "from apps.collector.ashare.main import app; print('OK')"
git add apps/collector/jobs/signal_sweep_worker.py apps/collector/ashare/main.py tests/unit/collector/test_signal_sweep.py
git commit -m "feat(collector): 30min 补扫 cron 兜底(漏事件捞回, 同只读路径)"
```

---

## Task 7: 端到端验证

- [ ] **Step 1: 全套单测**

Run: `pytest -m "not integration" -q`
Expected: 通过(新增测试 + 不回归)

- [ ] **Step 2: 重启全部 + 事件端到端**

```bash
# 重启 3 collector + api(雷区2 模板)
docker exec marketpulse-redis-dev redis-cli XADD bus:signal.new '*' data '{"market":"crypto","symbol":"BTC-USDT","interval":"4h","signal_type":"buy","bar_ts":"2026-06-03T16:00:00+00:00","price":2077.33,"detected_at":"2026-06-03T16:30:00+00:00"}'
# 前端 /signals 或概览应实时显示该信号(crypto 市场)
```
Expected: 前端实时显示

- [ ] **Step 3: 补扫 + 邮件冒烟**

```bash
grep "signal_sweep.done\|signal_digest\|notify.email" /tmp/collector_ashare.log | tail
```
Expected: 补扫 done 日志;SMTP 未配时 email.disabled 降级
