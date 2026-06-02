# 核心标的 baseline + 启动 reconcile 实施计划(审计 P0:B1/B2/B7/B-startup)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 让"核心标的"的各周期收线 bar 无条件被采集(不依赖有人看/不依赖 DB watchlist),并在冷启动/进程 kill 后自动回补缺口。消除审计 B1(美股无 baseline)、B2(前后端名单脱节)、B7(sweep 名单缺失)、B-startup(无启动回填)。

**Architecture:** 新增"核心标的"单一事实源 `CORE_SYMBOLS`;把所有采集路径(signal/fetch cron、sweep、US poller)的标的集从 `dynamic_universe()` 改为 `CORE ∪ dynamic_universe()`;新增启动 reconcile 模块,开机对 `CORE ∪ watchlist` 逐标的回补(先 1d 再聚合派生),接入 ashare/us collector lifespan。前端不动(决策 a:纯后端对齐,约定前端默认列表 ⊆ CORE)。

**Tech Stack:** Python(asyncio / APScheduler)、DuckDB、pytest。

**Spec:** `docs/2026-06-01-collection-db-backfill-audit.md` §5/§6/§8 P0。

---

## CORE_SYMBOLS 名单(zhonghuai 2026-06-02 确认)

- **ashare**:8 指数(000001.SH 399001.SZ 000300.SH 399006.SZ 000905.SH 000852.SH 000688.SH 000016.SH)+ 7 默认股(600519.SH 300750.SZ 002594.SZ 603259.SH 688981.SH 002371.SZ 300059.SZ)
- **us**:AAPL NVDA MSFT TSLA AMZN META AMD + SPY QQQ DIA
- **crypto**:BTC-USDT ETH-USDT SOL-USDT XRP-USDT TRX-USDT(维持现有固定全集)
- **hk**:暂空(无 collector)

---

## 文件结构

**新建:**
- `core/domain/core_symbols.py` — `CORE_SYMBOLS` + `core_symbols(market) -> list[str]`
- `apps/collector/startup_reconcile.py` — `run_startup_reconcile(market, repo, kline, symbols)`
- `tests/unit/domain/test_core_symbols.py`
- `tests/unit/collector/test_startup_reconcile.py`
- `tests/unit/scheduler/test_signal_jobs_core_union.py`

**改造:**
- `core/scheduler/signal_jobs.py` — scan_cd_job + fetch_intraday_job 标的集并入 CORE
- `apps/collector/us/bar_poller.py` — `_scan_symbols` 并入 CORE.us(美股 baseline)
- `apps/collector/ashare/main.py` / `apps/collector/us/main.py` — sweep symbols 改 CORE∪watchlist;接入 startup reconcile

---

## Task 1: CORE_SYMBOLS 单一事实源

**Files:** Create `core/domain/core_symbols.py`;Test `tests/unit/domain/test_core_symbols.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/domain/test_core_symbols.py
from core.domain.core_symbols import CORE_SYMBOLS, core_symbols


def test_us_core_contains_defaults_and_etfs():
    us = set(core_symbols("us"))
    assert {"AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "META", "AMD"} <= us
    assert {"SPY", "QQQ", "DIA"} <= us


def test_ashare_core_contains_indices_and_default_stocks():
    a = set(core_symbols("ashare"))
    assert "000001.SH" in a and "600519.SH" in a and "300750.SZ" in a


def test_unknown_market_returns_empty():
    assert core_symbols("hk") == [] or isinstance(core_symbols("hk"), list)
    assert core_symbols("nonexistent") == []
```

- [ ] **Step 2: 运行确认失败** — `. .venv/bin/activate && pytest tests/unit/domain/test_core_symbols.py -v`(ModuleNotFoundError)

- [ ] **Step 3: 实现**

```python
# core/domain/core_symbols.py
"""SSoT: 每市场"核心标的"——无条件常驻采集的 baseline。

采集路径(signal/fetch cron、sweep、US poller、启动 reconcile)的标的集 =
CORE_SYMBOLS[market] ∪ DB watchlist。前端默认展示列表(apps/web/app/page.tsx
DEFAULT_WATCHLIST)应为各市场 CORE 的子集(决策 a:纯后端对齐,约定同步)。
"""
from __future__ import annotations

CORE_SYMBOLS: dict[str, list[str]] = {
    "ashare": [
        # 8 大盘指数
        "000001.SH", "399001.SZ", "000300.SH", "399006.SZ",
        "000905.SH", "000852.SH", "000688.SH", "000016.SH",
        # 首页默认股
        "600519.SH", "300750.SZ", "002594.SZ", "603259.SH",
        "688981.SH", "002371.SZ", "300059.SZ",
    ],
    "us": [
        "AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "META", "AMD",
        "SPY", "QQQ", "DIA",
    ],
    "crypto": ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "TRX-USDT"],
    "hk": [],
}


def core_symbols(market: str) -> list[str]:
    """返回该市场核心标的列表;未知市场返回 []。"""
    return list(CORE_SYMBOLS.get(market, []))
```

- [ ] **Step 4: 运行确认通过** — `pytest tests/unit/domain/test_core_symbols.py -v`(3 PASS)

- [ ] **Step 5: 提交**

```bash
git add core/domain/core_symbols.py tests/unit/domain/test_core_symbols.py
git commit -m "feat: CORE_SYMBOLS 核心标的单一事实源 (审计 P0 baseline)"
```

---

## Task 2: signal/fetch cron 标的集并入 CORE

**Files:** Modify `core/scheduler/signal_jobs.py`(scan_cd_job + fetch_intraday_job);Test `tests/unit/scheduler/test_signal_jobs_core_union.py`

核心:把 `symbols = await watchlist.dynamic_universe()` 改为并入 `core_symbols(market_filter)`,使 CORE 标的无条件被扫/被采(不依赖是否加入 watchlist)。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/scheduler/test_signal_jobs_core_union.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from core.scheduler.signal_jobs import scan_cd_job, fetch_intraday_job


@pytest.mark.asyncio
async def test_scan_cd_job_unions_core_symbols(monkeypatch):
    monkeypatch.setattr("core.scheduler.signal_jobs.is_trading_day", lambda m: True)
    wl = MagicMock(); wl.dynamic_universe = AsyncMock(return_value=["ZZZZ"])  # watchlist 一只非 core
    scan = MagicMock(); scan.scan_many = AsyncMock(return_value=0)
    await scan_cd_job(scan, wl, None, interval="1d", market_filter="us")
    passed = set(scan.scan_many.await_args[0][0])
    assert "AAPL" in passed and "QQQ" in passed   # core 并入
    assert "ZZZZ" in passed                          # watchlist 保留


@pytest.mark.asyncio
async def test_fetch_intraday_job_unions_core(monkeypatch):
    monkeypatch.setattr("core.scheduler.signal_jobs.is_trading_day", lambda m: True)
    wl = MagicMock(); wl.dynamic_universe = AsyncMock(return_value=[])
    kline = MagicMock(); kline.fetch_fresh_bars = AsyncMock()
    await fetch_intraday_job(kline, wl, interval="5m", market_filter="us")
    fetched = {c.kwargs.get("interval") and c.args[0] for c in kline.fetch_fresh_bars.await_args_list}
    called_syms = {c.args[0] for c in kline.fetch_fresh_bars.await_args_list}
    assert "AAPL" in called_syms   # core 标的被采 5m
```

注:`scan_cd_job` 在 `is_trading_day` 处 import,需用上面 monkeypatch 路径名核对(若 import 在函数体内 `from core.domain.market_calendar import is_trading_day`,patch 目标改成 `core.domain.market_calendar.is_trading_day`)。实现前先 `grep -n is_trading_day core/scheduler/signal_jobs.py` 确认 patch 目标。

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现** — `signal_jobs.py` 顶部 `from core.domain.core_symbols import core_symbols`。

scan_cd_job(`:30`)`symbols = await watchlist.dynamic_universe()` 改:
```python
    wl_syms = await watchlist.dynamic_universe()
    core = core_symbols(market_filter) if market_filter else []
    symbols = sorted(set(wl_syms) | set(core))
```
fetch_intraday_job(`:61-62`):
```python
    wl_syms = await watchlist.dynamic_universe()
    core = core_symbols(market_filter)
    symbols = [s for s in sorted(set(wl_syms) | set(core)) if infer_market(s) == market_filter]
```

- [ ] **Step 4: 运行确认通过** — `pytest tests/unit/scheduler/test_signal_jobs_core_union.py -v`

- [ ] **Step 5: 提交**

```bash
git add core/scheduler/signal_jobs.py tests/unit/scheduler/test_signal_jobs_core_union.py
git commit -m "feat: signal/fetch cron 标的集并入 CORE_SYMBOLS (核心标的无条件采集, 修 B1/B2)"
```

---

## Task 3: 美股 UsBarPoller baseline

**Files:** Modify `apps/collector/us/bar_poller.py`(`_scan_symbols` 并入 CORE.us);Test 追加 `tests/unit/collector/test_us_bar_poller.py`

- [ ] **Step 1: 写失败测试**(追加)

```python
@pytest.mark.asyncio
async def test_scan_symbols_includes_core_baseline():
    redis = MagicMock(); redis._r = MagicMock()
    redis._r.scan = AsyncMock(return_value=(0, []))  # 无人订阅
    poller = UsBarPoller(MagicMock(), redis, MagicMock())
    syms = await poller._scan_symbols()
    assert "AAPL" in syms and "QQQ" in syms   # CORE.us baseline 仍在采
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现** — `bar_poller.py` 顶部 `from core.domain.core_symbols import core_symbols`;`_scan_symbols` return 前并入:
```python
        active.update(core_symbols("us"))   # 美股 baseline: 核心标的无条件轮询
        return active
```

- [ ] **Step 4: 运行确认通过** — `pytest tests/unit/collector/test_us_bar_poller.py -v`

- [ ] **Step 5: 提交**

```bash
git add apps/collector/us/bar_poller.py tests/unit/collector/test_us_bar_poller.py
git commit -m "feat: 美股 UsBarPoller 并入 CORE baseline (无人看也采核心标的, 修 B1)"
```

---

## Task 4: 启动 reconcile 模块

**Files:** Create `apps/collector/startup_reconcile.py`;Test `tests/unit/collector/test_startup_reconcile.py`

逻辑:开机对每个标的——先 `fetch_fresh_bars("1d", 深窗口)` 补日线,再 `fetch_fresh_bars(5m/15m/30m)` 补直取 intraday(adapter 自带窗口),最后 `aggregate_derived_for_symbol(window_60m=None, window_4h=None, window_1wk=None, window_1mo=None)` 全量重聚合派生(顺带缓解 B3 中段缺口)。逐标的 throttle。失败仅 warning。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/collector/test_startup_reconcile.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from apps.collector.startup_reconcile import run_startup_reconcile


@pytest.mark.asyncio
async def test_reconcile_fetches_1d_first_then_intraday_then_aggregates(monkeypatch):
    repo = MagicMock()
    kline = MagicMock(); kline.fetch_fresh_bars = AsyncMock()
    agg = AsyncMock()
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", agg)
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    await run_startup_reconcile("us", repo, kline, ["AAPL"])
    intervals = [c.kwargs["interval"] for c in kline.fetch_fresh_bars.await_args_list]
    assert intervals[0] == "1d"                      # 1d 先
    assert set(intervals) >= {"1d", "5m", "15m", "30m"}
    agg.assert_awaited()                              # 之后聚合派生


@pytest.mark.asyncio
async def test_reconcile_one_symbol_failure_does_not_abort(monkeypatch):
    repo = MagicMock()
    kline = MagicMock()
    kline.fetch_fresh_bars = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr("apps.collector.startup_reconcile.aggregate_derived_for_symbol", AsyncMock())
    monkeypatch.setattr("apps.collector.startup_reconcile.asyncio.sleep", AsyncMock())
    # 不抛出
    await run_startup_reconcile("us", repo, kline, ["AAPL", "MSFT"])
```

- [ ] **Step 2: 运行确认失败**

- [ ] **Step 3: 实现**

```python
# apps/collector/startup_reconcile.py
"""启动 reconcile: collector 开机对核心+watchlist 标的回补缺口。

冷启动填历史、kill 后补断档(各市场 adapter 窗口内)。先 1d 再聚合派生
(1wk/1mo 依赖 1d 先到位)。失败单标的 try/except, 不阻塞启动。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from apps.collector.jobs.aggregate_derived import aggregate_derived_for_symbol

log = structlog.get_logger(__name__)

_DIRECT_INTRADAY = ("5m", "15m", "30m")   # 源头直取(sina/REST), adapter 自带窗口
_DAILY_LOOKBACK_DAYS = 1000
THROTTLE_S = 0.3


async def run_startup_reconcile(market: str, repo, kline, symbols: list[str]) -> None:
    log.info("startup_reconcile.start", market=market, symbols=len(symbols))
    now = datetime.now(timezone.utc)
    filled = 0
    for sym in symbols:
        try:
            # 1) 1d 先(深窗口, 冷启动填史 + 给 1wk/1mo 聚合打底)
            await kline.fetch_fresh_bars(
                sym, interval="1d",
                start=now - timedelta(days=_DAILY_LOOKBACK_DAYS), end=now)
            # 2) 直取 intraday(adapter 返回自带窗口, 覆盖近端缺口)
            for iv in _DIRECT_INTRADAY:
                await kline.fetch_fresh_bars(
                    sym, interval=iv, start=now - timedelta(days=60), end=now)
            # 3) 全量重聚合派生(60m/4h 从 5m; 1wk/1mo 从 1d) —— 顺带修中段缺口
            await aggregate_derived_for_symbol(
                repo, market, sym,
                window_60m=None, window_4h=None, window_1wk=None, window_1mo=None)
            filled += 1
        except Exception as e:  # noqa: BLE001
            log.warning("startup_reconcile.symbol_failed",
                        market=market, symbol=sym, error=str(e))
        await asyncio.sleep(THROTTLE_S)
    log.info("startup_reconcile.done", market=market, filled=filled, total=len(symbols))
```

- [ ] **Step 4: 运行确认通过** — `pytest tests/unit/collector/test_startup_reconcile.py -v`

- [ ] **Step 5: 提交**

```bash
git add apps/collector/startup_reconcile.py tests/unit/collector/test_startup_reconcile.py
git commit -m "feat: 启动 reconcile 模块 (冷启动填史 + kill后补断档, 修 B-startup)"
```

---

## Task 5: 接入 ashare/us main(sweep CORE∪watchlist + reconcile)

**Files:** Modify `apps/collector/ashare/main.py`、`apps/collector/us/main.py`;验证靠 import smoke。

- [ ] **Step 1: sweep symbols 改 CORE∪watchlist**

两个 main 里 sweep_derived 的 symbols 来源(当前读 `*_backfill_symbols.txt` + 缺失 fallback),改为:
```python
    from core.domain.core_symbols import core_symbols
    _wl = await get_watchlist_service().dynamic_universe()
    _market = "ashare"  # us 文件里改 "us"
    _sweep_syms = sorted({s for s in set(_wl) | set(core_symbols(_market))})
    # us 还需按市场过滤: [s for s in ... if infer_market(s)=="us"]; ashare 同理
```
(ashare 用 `infer_market(s)=="ashare"` 过滤;保留 txt 读取作可选叠加或直接移除。)

- [ ] **Step 2: 接入 startup reconcile(lifespan 内, 非阻塞 task)**

两个 main lifespan 内(scheduler 启动后),加:
```python
    from apps.collector.startup_reconcile import run_startup_reconcile
    from core.domain.core_symbols import core_symbols
    _recon_wl = await get_watchlist_service().dynamic_universe()
    _recon_syms = [s for s in sorted(set(_recon_wl) | set(core_symbols(_market)))
                   if infer_market(s) == _market]
    _reconcile_task = asyncio.create_task(
        run_startup_reconcile(_market, bar_repo, kline, _recon_syms),
        name=f"{_market}.startup_reconcile")
```
finally 段加 `_reconcile_task.cancel()` + await(同其他 task 模式)。`infer_market` 从 `core.domain.markets` import。

- [ ] **Step 3: 验证 import**

Run: `. .venv/bin/activate && python -c "from apps.collector.ashare.main import app; from apps.collector.us.main import app as u; from apps.api.main import app as a; from apps.collector.crypto.main import app as c; print('all import ok')"`

- [ ] **Step 4: 提交**

```bash
git add apps/collector/ashare/main.py apps/collector/us/main.py
git commit -m "feat: ashare/us 接入启动 reconcile + sweep 用 CORE∪watchlist (修 B7/B-startup)"
```

---

## 收尾验证

- [ ] `pytest -m "not integration" -q`(除既有 index_minute 2 例外全绿)
- [ ] 后端 4 进程 import OK
- [ ] 重启 ashare + us collector(雷区 2 模板),日志确认 `startup_reconcile.start/done` + 4 进程健康
- [ ] 实测:停 us collector 几分钟后重启,确认 reconcile 把停机期间缺口补上(查 bars_us 5m max(ts) 跟上)
- [ ] 更新审计文档 B1/B2/B7/B-startup 标记已修
