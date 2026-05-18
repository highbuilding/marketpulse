# 美股市场接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 US 市场功能对齐到 A 股 90%(K 线全周期 + CD 信号 + 关注页接入 + 时区显示),并把关注页改造为 4 市场 tab(A 股 / 港股 / 美股 / 加密货币,后两者本期为骨架)。

**Architecture:**
- 后端:抽 `infer_market` SSoT 到 `core/domain/markets.py` → USAdapter 补 intraday + 1d ts ET normalize + ticker 转换 → KLineService 持 dict[market→adapter] 按 symbol 路由 → SignalScanService 加 market_filter → Scheduler 加 ET 时区 US cron。
- 前端:`apps/web/lib/markets.ts` 镜像 inferMarket + 时区辅助 → 组件接收 market prop,显示按市场时区切日历 → 关注页 4-tab + 搜索 scope 跟随 tab。

**Tech Stack:** Python 3.11(FastAPI、APScheduler、yfinance、pandas、structlog、aiosqlite、duckdb)、Next.js 14(React、SWR、lightweight-charts 4.2)、pytest、tsc。

**Spec:** `docs/superpowers/specs/2026-05-18-us-market-support-design.md`

---

## File Structure

新建:
- `core/domain/markets.py` — 后端 SSoT
- `apps/web/lib/markets.ts` — 前端 SSoT 镜像
- `core/services/_us_seeds.py` — 美股 ~200 静态 seeds(独立,避免污染 service)
- `tests/unit/domain/test_markets.py`
- `tests/unit/adapters/test_us.py`
- `tests/unit/services/test_kline_routing.py`
- `tests/unit/services/test_signal_market_filter.py`
- `tests/unit/web/markets.test.mjs`(node 跑,避免引入完整 jest)

修改:
- `core/adapters/us.py` — 加 fetch_intraday + 改写 fetch_history + verify_ticker + _to_yfinance_ticker
- `core/services/kline_service.py` — 多市场路由 + 4h group_size by market
- `core/services/signal_service.py` — `scan_many(market_filter=)`
- `core/scheduler/signal_jobs.py` — `scan_cd_job(market_filter=)`
- `core/scheduler/scheduler.py` — 新加 `attach_us_signal_jobs` + 现有 A 股 cron 加 market_filter
- `core/services/symbol_directory_service.py` — `bootstrap_us_seeds` + `search(market=)` + `upsert_one`
- `core/persistence/symbol_directory_repo.py` — `search(market=)`
- `core/domain/intervals.py` — 4h `crypto_only=False`
- `apps/api/main.py` — lifespan 加 bootstrap_us_seeds
- `apps/api/deps.py` — get_kline_service 注入 dict adapters
- `apps/api/routes/symbols.py` — 删 `_infer_market`、search 加 market + 懒加载
- `apps/api/routes/cd_signals.py` — watchlist-events 加 market 参数,删 `_is_crypto`
- `apps/web/lib/intervals.ts` — 4h tab 仅 us+crypto 显示;detailSignalTabs(market)
- `apps/web/lib/signal_time.ts` — 增 market-aware 函数(保留旧别名)
- `apps/web/lib/chart_time.ts` — formatter 接收 market
- `apps/web/lib/cd_signals_api.ts` — fetchWatchlistEvents 加 market 参数
- `apps/web/lib/symbol_api.ts` — searchSymbols 加 market 参数
- `apps/web/app/watchlist/page.tsx` — 4-tab
- `apps/web/app/symbol/[code]/page.tsx` — 透传 market 给 chart / panel
- `apps/web/components/SymbolSearch.tsx` — 接收 market prop
- `apps/web/components/SignalsTable.tsx` — 接收 market
- `apps/web/components/CDSignalPanel.tsx` — 接收 market
- `apps/web/components/WatchlistSignalsPanel.tsx` — 接收 market
- `apps/web/components/KLineChart.tsx` — 接收 market
- `apps/web/components/IntradayChart.tsx` — 接收 market

---

## Phase 0 — 准备(SSoT + 类型基础)

### Task 1: 抽 `infer_market` 到 `core/domain/markets.py`(SSoT)

**Files:**
- Create: `core/domain/markets.py`
- Create: `tests/unit/domain/test_markets.py`
- Modify: `apps/api/routes/symbols.py:90-97`(删除 `_infer_market` 本地定义,改 import)
- Modify: `apps/api/routes/cd_signals.py:29-32`(删除 `_is_crypto`,改 import)
- Modify: `core/scheduler/jobs.py:21`(用 `infer_market` 替换重复段)

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/domain/test_markets.py
from core.domain.markets import infer_market, is_crypto


def test_infer_market_ashare_suffix():
    assert infer_market("600519.SH") == "ashare"
    assert infer_market("000001.SZ") == "ashare"
    assert infer_market("920001.BJ") == "ashare"
    assert infer_market("510300.SH") == "ashare"  # ETF


def test_infer_market_hk_suffix():
    assert infer_market("9988.HK") == "hk"
    assert infer_market("HSI.HK") == "hk"


def test_infer_market_crypto():
    assert infer_market("BTC/USDT") == "crypto"
    assert infer_market("ETH/USDT") == "crypto"


def test_infer_market_us_default():
    assert infer_market("AAPL") == "us"
    assert infer_market("BRK.B") == "us"  # 点号但不在白名单 → us
    assert infer_market("^GSPC") == "us"  # 美股指数
    assert infer_market("SPY") == "us"


def test_is_crypto():
    assert is_crypto("BTC/USDT")
    assert not is_crypto("AAPL")
    assert not is_crypto("600519.SH")
```

- [ ] **Step 2: 运行,确认 FAIL**

```bash
cd /Users/xiangrong/stock/marketpulse
. .venv/bin/activate && pytest tests/unit/domain/test_markets.py -v
```
Expected: `ModuleNotFoundError: No module named 'core.domain.markets'`

- [ ] **Step 3: 创建 module**

```python
# core/domain/markets.py
"""SSoT: 根据 symbol 字符串推断市场。

收口前散在:
- apps/api/routes/symbols.py::_infer_market
- apps/api/routes/cd_signals.py::_is_crypto
- core/scheduler/jobs.py(重复段)
"""
from __future__ import annotations

from typing import Literal

Market = Literal["ashare", "hk", "us", "crypto"]


def infer_market(symbol: str) -> Market:
    """根据 symbol 字符串推断市场。

    - 600519.SH / 510300.SH / 000001.SZ / 920001.BJ → ashare
    - 9988.HK / HSI.HK                              → hk
    - 含 '/' (如 BTC/USDT)                           → crypto
    - 其他 (AAPL / BRK.B / SPY / ^GSPC)             → us(兜底)

    注意: 白名单优先(.SH/.SZ/.BJ/.HK), 不要写成"含点号即非 us"的黑名单,
    否则 BRK.B 会被误判。
    """
    if symbol.endswith((".SH", ".SZ", ".BJ")):
        return "ashare"
    if symbol.endswith(".HK"):
        return "hk"
    if "/" in symbol:
        return "crypto"
    return "us"


def is_crypto(symbol: str) -> bool:
    return infer_market(symbol) == "crypto"
```

- [ ] **Step 4: 运行,确认 PASS**

```bash
pytest tests/unit/domain/test_markets.py -v
```
Expected: 5 passed

- [ ] **Step 5: 收口三个散点**

`apps/api/routes/symbols.py` 顶部 imports 加:

```python
from core.domain.markets import infer_market
```

删除 `apps/api/routes/symbols.py:90-97` 的 `def _infer_market(symbol: str) -> str | None:` 整段。

把 `apps/api/routes/symbols.py:112,123,131` 三处 `_infer_market(s)` / `_infer_market(symbol)` 改成 `infer_market(s)` / `infer_market(symbol)`。

`apps/api/routes/cd_signals.py` 顶部 imports 加:

```python
from core.domain.markets import is_crypto
```

删除 `apps/api/routes/cd_signals.py:29-32` 的 `def _is_crypto(symbol: str) -> bool:` 整段。

把 `apps/api/routes/cd_signals.py:128` 处 `_is_crypto(s)` 改成 `is_crypto(s)`。

`core/scheduler/jobs.py` 顶部 imports 加:

```python
from core.domain.markets import infer_market
```

把 `core/scheduler/jobs.py:21` 附近 "美股 ticker 无后缀" 的整段判断改成 `return infer_market(symbol)`(读原文件后用直接替换为单行)。

- [ ] **Step 6: 验证后端启动**

```bash
. .venv/bin/activate && python -c "from apps.api.main import app; print('ok')"
```
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add core/domain/markets.py tests/unit/domain/test_markets.py \
        apps/api/routes/symbols.py apps/api/routes/cd_signals.py \
        core/scheduler/jobs.py
git commit -m "refactor: 抽 infer_market 到 core/domain/markets.py 作 SSoT"
```

---

### Task 2: 前端 `apps/web/lib/markets.ts`(SSoT 镜像 + 时区辅助)

**Files:**
- Create: `apps/web/lib/markets.ts`
- Create: `tests/unit/web/markets.test.mjs`

- [ ] **Step 1: 写测试(node 原生 assert,避免引入 jest)**

```javascript
// tests/unit/web/markets.test.mjs
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { inferMarket, marketTz, tradingDateKey, todayKey, tzOffsetSeconds } from '../../../apps/web/lib/markets.ts'

test('inferMarket ashare suffix', () => {
  assert.equal(inferMarket('600519.SH'), 'ashare')
  assert.equal(inferMarket('000001.SZ'), 'ashare')
  assert.equal(inferMarket('920001.BJ'), 'ashare')
})

test('inferMarket hk', () => {
  assert.equal(inferMarket('9988.HK'), 'hk')
})

test('inferMarket crypto', () => {
  assert.equal(inferMarket('BTC/USDT'), 'crypto')
})

test('inferMarket us default + class share', () => {
  assert.equal(inferMarket('AAPL'), 'us')
  assert.equal(inferMarket('BRK.B'), 'us')
  assert.equal(inferMarket('^GSPC'), 'us')
})

test('marketTz mapping', () => {
  assert.equal(marketTz('ashare'), 'Asia/Shanghai')
  assert.equal(marketTz('us'), 'America/New_York')
})

test('tradingDateKey US: ET 自然日切分', () => {
  // 2026-05-18 00:00 ET = 2026-05-18 04:00 UTC
  assert.equal(tradingDateKey('2026-05-18T04:00:00Z', 'us'), '2026-05-18')
  // 2026-05-17 23:30 ET = 2026-05-18 03:30 UTC -> ET 还在 5/17
  assert.equal(tradingDateKey('2026-05-18T03:30:00Z', 'us'), '2026-05-17')
})

test('tradingDateKey ashare: BJT 切分', () => {
  // 2026-05-17 16:00 UTC = BJT 2026-05-18 00:00
  assert.equal(tradingDateKey('2026-05-17T16:00:00Z', 'ashare'), '2026-05-18')
})

test('tzOffsetSeconds US 夏冬令时', () => {
  // 2026-03-08 是 EDT 切换日;3/9 是 EDT(UTC-4 = -14400 秒)
  const summer = tzOffsetSeconds('us', '2026-06-15T12:00:00Z')
  assert.equal(summer, -4 * 3600)
  // 2026-01-15 是 EST(UTC-5 = -18000 秒)
  const winter = tzOffsetSeconds('us', '2026-01-15T12:00:00Z')
  assert.equal(winter, -5 * 3600)
})

test('tzOffsetSeconds ashare 固定 +8h', () => {
  assert.equal(tzOffsetSeconds('ashare', '2026-05-18T00:00:00Z'), 8 * 3600)
})
```

- [ ] **Step 2: 写实现**

```typescript
// apps/web/lib/markets.ts
// SSoT 镜像: 改这里时同步改后端 core/domain/markets.py。

export type Market = 'ashare' | 'hk' | 'us' | 'crypto'

export function inferMarket(symbol: string): Market {
  if (/\.(SH|SZ|BJ)$/.test(symbol)) return 'ashare'
  if (symbol.endsWith('.HK')) return 'hk'
  if (symbol.includes('/')) return 'crypto'
  return 'us'
}

const TZ: Record<Market, string> = {
  ashare: 'Asia/Shanghai',
  hk:     'Asia/Hong_Kong',
  us:     'America/New_York',
  crypto: 'Asia/Shanghai',  // crypto 沿用 BJT 惯例
}

export function marketTz(market: Market): string {
  return TZ[market]
}

export function tradingDateKey(iso: string, market: Market): string {
  return new Date(iso).toLocaleDateString('en-CA', { timeZone: TZ[market] })
}

export function todayKey(market: Market): string {
  return new Date().toLocaleDateString('en-CA', { timeZone: TZ[market] })
}

// 给定 ISO 时刻, 返回该市场时区相对 UTC 的 offset(秒)。
// 用 Intl.DateTimeFormat 提取 offset, 而非常量, 以处理夏/冬令时。
export function tzOffsetSeconds(market: Market, iso: string): number {
  const date = new Date(iso)
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ[market],
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  })
  const parts = fmt.formatToParts(date)
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value)
  const localAsUtc = Date.UTC(
    get('year'), get('month') - 1, get('day'),
    get('hour') === 24 ? 0 : get('hour'),  // 部分 locale 用 24:00 表示午夜
    get('minute'), get('second'),
  )
  return (localAsUtc - date.getTime()) / 1000
}
```

- [ ] **Step 3: 跑测试**

```bash
cd /Users/xiangrong/stock/marketpulse
node --experimental-strip-types --test tests/unit/web/markets.test.mjs
```
Expected: 全部 pass。如果 node 版本不支持 `--experimental-strip-types`,降级用 `tsx`:

```bash
cd apps/web && npx tsx ../../tests/unit/web/markets.test.mjs
```

- [ ] **Step 4: tsc 类型检查**

```bash
cd /Users/xiangrong/stock/marketpulse/apps/web && npx tsc --noEmit
```
Expected: exit=0

- [ ] **Step 5: Commit**

```bash
cd /Users/xiangrong/stock/marketpulse
git add apps/web/lib/markets.ts tests/unit/web/markets.test.mjs
git commit -m "feat(web): markets.ts SSoT 镜像 + 时区辅助"
```

---

## Phase 1 — USAdapter 数据层

### Task 3: USAdapter `_to_yfinance_ticker` + `fetch_intraday`

**Files:**
- Modify: `core/adapters/us.py`(加 module-level `_to_yfinance_ticker`,改 class:加 `fetch_intraday`)
- Create: `tests/unit/adapters/test_us.py`

- [ ] **Step 1: 写测试**

```python
# tests/unit/adapters/test_us.py
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from core.adapters.us import USAdapter, _to_yfinance_ticker


def test_to_yfinance_ticker_class_share():
    assert _to_yfinance_ticker("BRK.B") == "BRK-B"
    assert _to_yfinance_ticker("BF.A") == "BF-A"


def test_to_yfinance_ticker_plain():
    assert _to_yfinance_ticker("AAPL") == "AAPL"
    assert _to_yfinance_ticker("SPY") == "SPY"


def _mock_intraday_df():
    """yfinance.download intraday 返回 ET 时区的 DataFrame。"""
    idx = pd.DatetimeIndex(
        ["2026-05-15 09:30:00-04:00", "2026-05-15 10:30:00-04:00"],
        tz="America/New_York",
    )
    return pd.DataFrame({
        "Open":   [180.0, 181.0],
        "High":   [181.0, 182.0],
        "Low":    [179.0, 180.5],
        "Close":  [180.5, 181.5],
        "Volume": [100000, 120000],
    }, index=idx)


@pytest.mark.asyncio
async def test_fetch_intraday_basic():
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=_mock_intraday_df())
        bars = await adapter.fetch_intraday("AAPL", freq="60")
    assert len(bars) == 2
    assert bars[0].symbol == "AAPL"
    assert bars[0].market == "us"
    assert bars[0].interval == "60m"
    # 13:30 UTC == 09:30 EDT
    assert bars[0].ts == datetime(2026, 5, 15, 13, 30, tzinfo=timezone.utc)
    assert bars[0].open == Decimal("180.0")


@pytest.mark.asyncio
async def test_fetch_intraday_class_share_converts_ticker():
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=_mock_intraday_df())
        await adapter.fetch_intraday("BRK.B", freq="60")
    # yfinance.download 应该被以 'BRK-B' 调用(adapter 内部转换)
    call = mock_yf.download.call_args
    assert call.args[0] == "BRK-B"
    # business 层 Bar 仍标 BRK.B
    bars = mock_yf.download.return_value
    assert bars is not None  # smoke


@pytest.mark.asyncio
async def test_fetch_intraday_drops_nan():
    df = _mock_intraday_df().copy()
    df.iloc[0, df.columns.get_loc("Close")] = float("nan")
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=df)
        bars = await adapter.fetch_intraday("AAPL", freq="60")
    assert len(bars) == 1  # 第一行 NaN 被丢弃


@pytest.mark.asyncio
async def test_fetch_intraday_period_mapping():
    """1m freq → period=7d, 其他 → 60d。"""
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=_mock_intraday_df())
        await adapter.fetch_intraday("AAPL", freq="1")
    assert mock_yf.download.call_args.kwargs["period"] == "7d"
    assert mock_yf.download.call_args.kwargs["interval"] == "1m"

    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=_mock_intraday_df())
        await adapter.fetch_intraday("AAPL", freq="60")
    assert mock_yf.download.call_args.kwargs["period"] == "60d"
    assert mock_yf.download.call_args.kwargs["interval"] == "60m"
    assert mock_yf.download.call_args.kwargs["prepost"] is True
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py -v
```
Expected: `ImportError: cannot import name '_to_yfinance_ticker'`

- [ ] **Step 3: 改 `core/adapters/us.py`**

在文件顶部 imports 区加 `import pandas as pd`(如已有则跳过)。

在 imports 之后、`class USAdapter` 之前加:

```python
def _to_yfinance_ticker(symbol: str) -> str:
    """Class share 字符转换: BRK.B → BRK-B(yfinance / Alpaca 格式)。
    业务层永远见 BRK.B, adapter 进出口转换。
    """
    return symbol.replace(".", "-")
```

在 `class USAdapter` 的 `async def subscribe(...)` 之后(或 `fetch_history` 之前)插入:

```python
    async def fetch_intraday(self, symbol: str, freq: str = "5") -> list[Bar]:
        """freq: '1'/'5'/'15'/'30'/'60' min。
        yfinance 限制: 1m=7d, 5m/15m/30m/60m=60d。prepost=True 拿盘前盘后。
        """
        import pandas as pd  # 局部 import 避免顶层冲突
        interval_map = {"1": "1m", "5": "5m", "15": "15m",
                        "30": "30m", "60": "60m"}
        if freq not in interval_map:
            raise ValueError(f"unsupported freq: {freq}")
        yf_interval = interval_map[freq]
        period = "7d" if freq == "1" else "60d"
        yf_symbol = _to_yfinance_ticker(symbol)
        df = await asyncio.to_thread(
            yf.download, yf_symbol,
            period=period, interval=yf_interval,
            prepost=True, progress=False, auto_adjust=False,
        )
        out: list[Bar] = []
        for idx, row in df.iterrows():
            # yfinance intraday 返回的 index 带 ET 时区
            if idx.tzinfo is None:
                ts_utc = (
                    idx.tz_localize("America/New_York")
                    .tz_convert("UTC")
                    .to_pydatetime()
                )
            else:
                ts_utc = idx.tz_convert("UTC").to_pydatetime()
            # 跳过 NaN 行(yfinance 在 prepost 时段偶发)
            if pd.isna(row["Open"]) or pd.isna(row["Close"]):
                continue
            vol = int(row["Volume"]) if not pd.isna(row["Volume"]) else 0
            out.append(Bar(
                market="us", symbol=symbol, ts=ts_utc,
                open=Decimal(str(float(row["Open"]))),
                high=Decimal(str(float(row["High"]))),
                low=Decimal(str(float(row["Low"]))),
                close=Decimal(str(float(row["Close"]))),
                volume=vol, interval=f"{freq}m",
            ))
        return out
```

如果 `core/adapters/us.py` 文件顶部还没有 `import pandas as pd`,加上(局部 import 仍保留,确保兼容)。

- [ ] **Step 4: 跑测试**

```bash
pytest tests/unit/adapters/test_us.py -v
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add core/adapters/us.py tests/unit/adapters/test_us.py
git commit -m "feat(us): fetch_intraday via yfinance(含 prepost、NaN 丢弃、BRK.B→BRK-B)"
```

---

### Task 4: USAdapter 改写 `fetch_history` 把 1d ts normalize 到 ET 自然日 00:00

**Files:**
- Modify: `core/adapters/us.py::fetch_history`

- [ ] **Step 1: 加测试到 `tests/unit/adapters/test_us.py`**

```python
def _mock_history_df():
    """yfinance.download(start, end) 在 1d 模式返回 naive index, 类型 DatetimeIndex。"""
    idx = pd.DatetimeIndex(["2026-05-15", "2026-05-16"])
    return pd.DataFrame({
        "Open":   [200.0, 201.0],
        "High":   [202.0, 203.0],
        "Low":    [199.0, 200.0],
        "Close":  [201.0, 202.0],
        "Volume": [1000000, 900000],
    }, index=idx)


@pytest.mark.asyncio
async def test_fetch_history_normalizes_to_et_midnight():
    """1d ts 必须 normalize 为 ET 自然交易日 00:00 → UTC。
    2026-05-15 00:00 ET (EDT, UTC-4) → 2026-05-15 04:00 UTC。"""
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=_mock_history_df())
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert len(bars) == 2
    assert bars[0].ts == datetime(2026, 5, 15, 4, 0, tzinfo=timezone.utc)
    assert bars[1].ts == datetime(2026, 5, 16, 4, 0, tzinfo=timezone.utc)
    assert bars[0].interval == "1d"
    assert bars[0].market == "us"


@pytest.mark.asyncio
async def test_fetch_history_class_share():
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=_mock_history_df())
        await adapter.fetch_history(
            "BRK.B",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert mock_yf.download.call_args.args[0] == "BRK-B"
```

- [ ] **Step 2: 跑,确认 FAIL**

```bash
pytest tests/unit/adapters/test_us.py::test_fetch_history_normalizes_to_et_midnight -v
```
Expected: AssertionError(老实现没 normalize)

- [ ] **Step 3: 改写 `core/adapters/us.py::fetch_history`**

把现有 `async def fetch_history(...)` 整段替换为:

```python
    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        """1d 历史。ts 与 A 股雷区 3 对称: normalize 为该市场本地交易日 00:00 → UTC。
        美股本地 = America/New_York(自动跟夏/冬令时)。
        """
        import pandas as pd
        yf_symbol = _to_yfinance_ticker(symbol)
        df = await asyncio.to_thread(
            yf.download, yf_symbol,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False, auto_adjust=False,
        )
        out: list[Bar] = []
        for idx, row in df.iterrows():
            if idx.tzinfo is None:
                local_midnight = idx.tz_localize("America/New_York")
            else:
                local_midnight = idx.tz_convert("America/New_York")
            ts_utc = local_midnight.normalize().tz_convert("UTC").to_pydatetime()
            if pd.isna(row["Open"]) or pd.isna(row["Close"]):
                continue
            out.append(Bar(
                market="us", symbol=symbol, ts=ts_utc,
                open=Decimal(str(float(row["Open"]))),
                high=Decimal(str(float(row["High"]))),
                low=Decimal(str(float(row["Low"]))),
                close=Decimal(str(float(row["Close"]))),
                volume=int(row["Volume"]) if not pd.isna(row["Volume"]) else 0,
                interval="1d",
            ))
        return out
```

- [ ] **Step 4: 跑测试**

```bash
pytest tests/unit/adapters/test_us.py -v
```
Expected: 7 passed(原有 5 + 新增 2)

- [ ] **Step 5: Commit**

```bash
git add core/adapters/us.py tests/unit/adapters/test_us.py
git commit -m "feat(us): fetch_history 1d ts normalize 到 ET 自然交易日 00:00"
```

---

### Task 5: USAdapter `verify_ticker`(供搜索懒加载用)

**Files:**
- Modify: `core/adapters/us.py`(加 `verify_ticker` 方法)
- Modify: `tests/unit/adapters/test_us.py`

- [ ] **Step 1: 加测试**

```python
@pytest.mark.asyncio
async def test_verify_ticker_valid():
    adapter = USAdapter()
    fake_info = MagicMock(last_price=180.0)
    fake_ticker = MagicMock(
        fast_info=fake_info,
        info={"longName": "Apple Inc."},
    )
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.Ticker = MagicMock(return_value=fake_ticker)
        ok, name = await adapter.verify_ticker("AAPL")
    assert ok is True
    assert name == "Apple Inc."


@pytest.mark.asyncio
async def test_verify_ticker_unknown():
    adapter = USAdapter()
    fake_info = MagicMock(last_price=None)
    fake_ticker = MagicMock(fast_info=fake_info, info={})
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.Ticker = MagicMock(return_value=fake_ticker)
        ok, name = await adapter.verify_ticker("ZZZZZZ")
    assert ok is False
    assert name is None


@pytest.mark.asyncio
async def test_verify_ticker_exception_returns_false():
    adapter = USAdapter()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.Ticker = MagicMock(side_effect=RuntimeError("network"))
        ok, name = await adapter.verify_ticker("AAPL")
    assert ok is False
    assert name is None
```

- [ ] **Step 2: 跑,确认 FAIL**

```bash
pytest tests/unit/adapters/test_us.py::test_verify_ticker_valid -v
```
Expected: `AttributeError: 'USAdapter' object has no attribute 'verify_ticker'`

- [ ] **Step 3: 加 `verify_ticker`(在 `health` 之前)**

```python
    async def verify_ticker(self, symbol: str) -> tuple[bool, str | None]:
        """轻量校验 + 拿 long name。供 directory 懒加载用。
        返回 (是否有效, 公司名 | None)。
        """
        yf_symbol = _to_yfinance_ticker(symbol)
        try:
            ticker = await asyncio.to_thread(lambda: yf.Ticker(yf_symbol))
            info = await asyncio.to_thread(lambda: ticker.fast_info)
            if not getattr(info, "last_price", None):
                return False, None
            long_name = await asyncio.to_thread(
                lambda: getattr(ticker, "info", {}).get("longName"),
            )
            return True, long_name
        except Exception:  # noqa: BLE001
            return False, None
```

- [ ] **Step 4: 跑测试**

```bash
pytest tests/unit/adapters/test_us.py -v
```
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add core/adapters/us.py tests/unit/adapters/test_us.py
git commit -m "feat(us): verify_ticker 供 directory 搜索懒加载用"
```

---

## Phase 2 — Service 层多市场化

### Task 6: KLineService 持 dict[market→adapter] + 按 symbol 路由

**Files:**
- Modify: `core/services/kline_service.py`
- Modify: `apps/api/deps.py::get_kline_service`
- Create: `tests/unit/services/test_kline_routing.py`

- [ ] **Step 1: 写测试**

```python
# tests/unit/services/test_kline_routing.py
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain.models import Bar
from core.services.kline_service import KLineService


def _bar(symbol: str, market: str, ts: datetime) -> Bar:
    return Bar(
        market=market, symbol=symbol, ts=ts,
        open=Decimal("1"), high=Decimal("1"), low=Decimal("1"),
        close=Decimal("1"), volume=0, interval="1d",
    )


@pytest.mark.asyncio
async def test_routes_us_symbol_to_us_adapter():
    repo = MagicMock()
    repo.fetch_history = MagicMock(return_value=[])
    repo.insert_bars = MagicMock()
    us_adapter = MagicMock()
    us_adapter.fetch_history = AsyncMock(return_value=[
        _bar("AAPL", "us", datetime(2026, 5, 15, 4, 0, tzinfo=timezone.utc)),
    ])
    ashare_adapter = MagicMock()
    ashare_adapter.fetch_history = AsyncMock()

    svc = KLineService(repo, {"us": us_adapter, "ashare": ashare_adapter})
    bars = await svc.get_bars(
        "AAPL", interval="1d",
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )
    us_adapter.fetch_history.assert_called_once()
    ashare_adapter.fetch_history.assert_not_called()
    assert bars[0].symbol == "AAPL"
    # 查缓存时也按 us
    repo.fetch_history.assert_called_with(
        "us", "AAPL", datetime(2026, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 20, tzinfo=timezone.utc), interval="1d",
    )


@pytest.mark.asyncio
async def test_routes_ashare_symbol_to_ashare_adapter():
    repo = MagicMock()
    repo.fetch_history = MagicMock(return_value=[])
    repo.insert_bars = MagicMock()
    us_adapter = MagicMock()
    us_adapter.fetch_history = AsyncMock()
    ashare_adapter = MagicMock()
    ashare_adapter.fetch_history = AsyncMock(return_value=[
        _bar("600519.SH", "ashare", datetime(2026, 5, 14, 16, tzinfo=timezone.utc)),
    ])

    svc = KLineService(repo, {"us": us_adapter, "ashare": ashare_adapter})
    await svc.get_bars(
        "600519.SH", interval="1d",
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )
    ashare_adapter.fetch_history.assert_called_once()
    us_adapter.fetch_history.assert_not_called()


@pytest.mark.asyncio
async def test_raises_when_no_adapter_for_market():
    repo = MagicMock()
    svc = KLineService(repo, {"ashare": MagicMock()})
    with pytest.raises(ValueError, match="no adapter for market=us"):
        await svc.get_bars(
            "AAPL", interval="1d",
            start=datetime(2026, 5, 1, tzinfo=timezone.utc),
            end=datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
```

- [ ] **Step 2: 跑,确认 FAIL**

```bash
pytest tests/unit/services/test_kline_routing.py -v
```
Expected: 构造签名不匹配 `TypeError: __init__() got an unexpected keyword`

- [ ] **Step 3: 改 `core/services/kline_service.py`**

把 `class KLineService` 的整段重写:

```python
class KLineService:
    def __init__(
        self, bar_repo: BarRepo,
        adapters: dict[str, "MarketAdapter"],
    ) -> None:
        self.repo = bar_repo
        self.adapters = adapters

    def _adapter_for(self, symbol: str) -> "MarketAdapter":
        from core.domain.markets import infer_market
        m = infer_market(symbol)
        a = self.adapters.get(m)
        if a is None:
            raise ValueError(f"no adapter for market={m} (symbol={symbol})")
        return a

    async def get_bars(
        self, symbol: str, *, interval: Interval,
        start: datetime, end: datetime,
    ) -> list[Bar]:
        if interval == "4h":
            from core.domain.markets import infer_market
            market = infer_market(symbol)
            group_size = _FOUR_HOUR_GROUP_BY_MARKET.get(market, 4)
            sixty = await self._get_intraday(symbol, "60m", start, end)
            return _group_resample(sixty, group_size, "4h")
        if interval in _RESAMPLED:
            daily = await self._get_daily(symbol, start, end)
            return _resample(daily, interval)
        if interval in _INTRADAY:
            return await self._get_intraday(symbol, interval, start, end)
        if interval == "1d":
            return await self._get_daily(symbol, start, end)
        raise ValueError(f"unsupported interval: {interval}")

    async def _get_daily(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        from core.domain.markets import infer_market
        market = infer_market(symbol)
        cached = self.repo.fetch_history(market, symbol, start, end, interval="1d")
        if cached and self._covers(cached, start, end):
            return cached
        bars = await self._adapter_for(symbol).fetch_history(symbol, start, end)
        self.repo.insert_bars(bars)
        return bars

    @staticmethod
    def _covers(bars: list[Bar], start: datetime, end: datetime) -> bool:
        # ... 保留原有实现, 不改动
```

把 `_covers` 整段保留;`_get_intraday` 改:

```python
    async def _get_intraday(
        self, symbol: str, interval: str, start: datetime, end: datetime,
    ) -> list[Bar]:
        from core.domain.markets import infer_market
        if interval == "1m":
            bars = await self._adapter_for(symbol).fetch_intraday(symbol, freq="1")
            return [b for b in bars if start <= b.ts <= end]
        market = infer_market(symbol)
        cached = self.repo.fetch_history(market, symbol, start, end, interval=interval)
        if cached and self._covers(cached, start, end):
            return cached
        freq = interval.replace("m", "")
        bars = await self._adapter_for(symbol).fetch_intraday(symbol, freq=freq)
        self.repo.insert_bars(bars)
        return [b for b in bars if start <= b.ts <= end]
```

文件顶部常量区(已有 `_FOUR_HOUR_GROUP = 4`)替换为:

```python
_FOUR_HOUR_GROUP_BY_MARKET: dict[str, int] = {
    "us":     4,  # 美股 prepost 16 根 60m / 天 → 4 根 4h
    "crypto": 4,  # crypto 24h 连续, 6 根 4h / 天
    "ashare": 4,  # A 股 4 根 60m / 天 → 4h ≡ 1d, 通常不展示
    "hk":     4,  # 同上
}
```

旧 `_FOUR_HOUR_GROUP` 删除。

- [ ] **Step 4: 改 `apps/api/deps.py::get_kline_service`**

```python
@lru_cache(maxsize=1)
def get_kline_service() -> KLineService:
    registry = get_registry()
    adapters = {m: registry.get(m) for m in registry.markets()}
    return KLineService(get_bar_repo(), adapters)
```

(去掉原来的 `AShareAdapter()` 直构;改用 registry 拿所有 adapter。`AShareAdapter` import 如不再被其他代码用,可一并删除,否则保留。)

确认 `core/adapters/registry.py` 有 `get(market)` 方法,如没有看 `markets()` 返回 + 内部字典确认。Read:

```bash
cat core/adapters/registry.py
```

如果没有 `get(market)`,在 Task 6 这步加一个最小实现(把 `markets()` 后面的访问公开):

```python
def get(self, market: str):
    return self._adapters[market]
```

- [ ] **Step 5: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/services/test_kline_routing.py tests/unit/adapters/test_us.py tests/unit/domain/test_markets.py -v
```
Expected: all pass

- [ ] **Step 6: 后端 smoke**

```bash
python -c "from apps.api.main import app; print('ok')"
```
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add core/services/kline_service.py apps/api/deps.py core/adapters/registry.py \
        tests/unit/services/test_kline_routing.py
git commit -m "refactor(kline): 持 dict[market→adapter], 按 symbol 自动路由"
```

---

### Task 7: SignalScanService `scan_many(market_filter=)`

**Files:**
- Modify: `core/services/signal_service.py::scan_many`
- Create: `tests/unit/services/test_signal_market_filter.py`

- [ ] **Step 1: 写测试**

```python
# tests/unit/services/test_signal_market_filter.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.services.signal_service import SignalScanService


@pytest.mark.asyncio
async def test_scan_many_filters_by_market():
    kline = MagicMock()
    repo = MagicMock()
    svc = SignalScanService(kline, repo)
    # 用 monkeypatch 把 scan_symbol 替换为 mock, 验证只被 US symbol 调用
    svc.scan_symbol = AsyncMock(return_value=1)
    universe = ["AAPL", "600519.SH", "9988.HK", "BTC/USDT", "SPY"]
    await svc.scan_many(universe, "60m", market_filter="us")
    called_syms = [c.args[0] for c in svc.scan_symbol.call_args_list]
    assert set(called_syms) == {"AAPL", "SPY"}


@pytest.mark.asyncio
async def test_scan_many_no_filter_keeps_all():
    kline = MagicMock()
    repo = MagicMock()
    svc = SignalScanService(kline, repo)
    svc.scan_symbol = AsyncMock(return_value=0)
    universe = ["AAPL", "600519.SH"]
    await svc.scan_many(universe, "60m")
    called_syms = [c.args[0] for c in svc.scan_symbol.call_args_list]
    assert set(called_syms) == {"AAPL", "600519.SH"}
```

- [ ] **Step 2: 跑,确认 FAIL**

```bash
pytest tests/unit/services/test_signal_market_filter.py -v
```
Expected: `TypeError: scan_many() got an unexpected keyword argument 'market_filter'`

- [ ] **Step 3: 改 `core/services/signal_service.py::scan_many`**

```python
    async def scan_many(
        self, symbols: list[str], interval: Interval,
        *, market_filter: str | None = None,
    ) -> int:
        if market_filter:
            from core.domain.markets import infer_market
            symbols = [s for s in symbols if infer_market(s) == market_filter]
        total = 0
        for sym in symbols:
            try:
                total += await self.scan_symbol(sym, interval)
            except Exception as e:  # noqa: BLE001
                log.warning("signal.scan_failed",
                            symbol=sym, interval=interval, error=str(e))
        return total
```

- [ ] **Step 4: 跑测试**

```bash
pytest tests/unit/services/test_signal_market_filter.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add core/services/signal_service.py tests/unit/services/test_signal_market_filter.py
git commit -m "feat(signal): scan_many 加 market_filter 参数"
```

---

### Task 8: scan_cd_job 透传 market_filter,既有 A 股 cron 都加 ashare 过滤

**Files:**
- Modify: `core/scheduler/signal_jobs.py`
- Modify: `core/scheduler/scheduler.py::attach_signal_jobs`(给现有 A 股 cron 加 `market_filter='ashare'`)

- [ ] **Step 1: 改 `core/scheduler/signal_jobs.py::scan_cd_job`**

```python
async def scan_cd_job(
    signal_scan: SignalScanService,
    watchlist: WatchlistService,
    *, interval: str, market_filter: str | None = None,
) -> None:
    symbols = await watchlist.dynamic_universe()
    if not symbols:
        log.debug("cd.scan_skipped_empty_watchlist", interval=interval,
                  market_filter=market_filter)
        return
    n = await signal_scan.scan_many(
        symbols, interval, market_filter=market_filter,
    )
    log.info("cd.scan_done", interval=interval,
             market_filter=market_filter, symbols=len(symbols), new=n)
```

- [ ] **Step 2: 改 `core/scheduler/scheduler.py::attach_signal_jobs`**

把所有 `kwargs={"interval": "..."}` 改成包含 `market_filter`,例如:

```python
sched.add_job(
    scan_cd_job,
    CronTrigger(day_of_week="mon-fri", hour="2-7", minute="*/15"),
    id="cd:15m",
    kwargs={"interval": "15m", "market_filter": "ashare"},
    **common,
)
```

对 `cd:15m / cd:30m / cd:60m:1030 / cd:60m:1130 / cd:60m:1430 / cd:60m:1500 / cd:4h / cd:1d` 全部加 `market_filter="ashare"`。**注意现有 4h 当前实质是 crypto-only(scheduler 里 cd:4h 是 A 股那个 job,有点错位:看实际代码,如确实 A 股 4h 信号无意义可保留 `market_filter="ashare"`,或改为 `"crypto"`)— 实施时检查现有 cd:4h job 注释和意图,默认保留 `ashare` 与现状一致,记录在 commit body 说明**。

- [ ] **Step 3: 跑现有信号测试 + 后端 import smoke**

```bash
pytest tests/unit/services/test_signal_market_filter.py -v
python -c "from apps.api.main import app; print('ok')"
```

- [ ] **Step 4: Commit**

```bash
git add core/scheduler/signal_jobs.py core/scheduler/scheduler.py
git commit -m "feat(scheduler): scan_cd_job 透传 market_filter, A 股 cron 标记 ashare"
```

---

### Task 9: Scheduler `attach_us_signal_jobs`(ET 时区 cron)

**Files:**
- Modify: `core/scheduler/scheduler.py`
- Modify: `apps/api/main.py::lifespan`(调用新函数)

- [ ] **Step 1: 改 `core/scheduler/scheduler.py`**,在文件末尾加:

```python
def attach_us_signal_jobs(
    sched: AsyncIOScheduler,
    *, signal_scan: SignalScanService, watchlist: WatchlistService,
) -> None:
    """美股 CD 信号扫描 cron(ET 时区, 自动跟夏/冬令时)。
    扫描区间: 盘前 04:00 ET 到盘后 20:00 ET, 共 16 小时。
    """
    common = dict(args=(signal_scan, watchlist), max_instances=1, coalesce=True,
                  misfire_grace_time=300)
    et = "America/New_York"
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="4-19", minute="*/15", timezone=et),
        id="cd:us:15m",
        kwargs={"interval": "15m", "market_filter": "us"}, **common,
    )
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="4-19", minute="*/30", timezone=et),
        id="cd:us:30m",
        kwargs={"interval": "30m", "market_filter": "us"}, **common,
    )
    # 60m 一根收盘 +5min, ET 05:05 - 20:05 每小时
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="5-20", minute="5", timezone=et),
        id="cd:us:60m",
        kwargs={"interval": "60m", "market_filter": "us"}, **common,
    )
    # 4h 收盘点 ET 08:00 / 12:00 / 16:00 / 20:00, 各 +5
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="8,12,16,20", minute="5", timezone=et),
        id="cd:us:4h",
        kwargs={"interval": "4h", "market_filter": "us"}, **common,
    )
    # 1d 收盘后 ET 20:05
    sched.add_job(
        scan_cd_job,
        CronTrigger(day_of_week="mon-fri", hour="20", minute="5", timezone=et),
        id="cd:us:1d",
        kwargs={"interval": "1d", "market_filter": "us"}, **common,
    )
    log.info("scheduler.us_signal_jobs_attached")
```

- [ ] **Step 2: 改 `apps/api/main.py::lifespan`**

在现有 `attach_signal_jobs(sched, ...)` 之后加:

```python
from core.scheduler.scheduler import attach_us_signal_jobs
attach_us_signal_jobs(
    sched,
    signal_scan=get_signal_scan_service(),
    watchlist=get_watchlist_service(),
)
```

(`attach_us_signal_jobs` 加到 `from core.scheduler.scheduler import ...` 的现有 import 行里。)

- [ ] **Step 3: 后端 smoke**

```bash
python -c "from apps.api.main import app; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: 验证 cron 注册成功(轻量)**

```bash
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' > /tmp/api.log 2>&1 &
disown
sleep 6
grep -E "scheduler.signal_jobs_attached|scheduler.us_signal_jobs_attached" /tmp/api.log
```
Expected: 两行都有

```bash
pkill -9 -f "uvicorn apps.api.main:app"; sleep 1
```

- [ ] **Step 5: Commit**

```bash
git add core/scheduler/scheduler.py apps/api/main.py
git commit -m "feat(scheduler): 美股 CD 信号 cron(ET 时区 04:00-20:00 prepost 全程)"
```

---

## Phase 3 — Directory + Routes

### Task 10: 美股 directory seeds + `bootstrap_us_seeds`

**Files:**
- Create: `core/services/_us_seeds.py`
- Modify: `core/services/symbol_directory_service.py`
- Modify: `apps/api/main.py::lifespan`(调用)

- [ ] **Step 1: 创建 `core/services/_us_seeds.py`**

```python
"""美股 directory 种子: 大盘 ETF + 道指 + NASDAQ100 头部 + S&P100 + 中概股。
不调外部 API, 启动期纯本地写库(避开雷区 1 mini_racer)。
"""
from __future__ import annotations

US_SEEDS: list[tuple[str, str, str]] = [
    # 大盘指数 ETF
    ("SPY", "SPDR S&P 500 ETF", "us"),
    ("QQQ", "Invesco QQQ Trust", "us"),
    ("DIA", "SPDR Dow Jones Industrial Average ETF", "us"),
    ("IWM", "iShares Russell 2000 ETF", "us"),
    ("VTI", "Vanguard Total Stock Market ETF", "us"),
    ("VOO", "Vanguard S&P 500 ETF", "us"),
    # 行业 ETF
    ("XLF", "Financial Select Sector SPDR", "us"),
    ("XLK", "Technology Select Sector SPDR", "us"),
    ("XLE", "Energy Select Sector SPDR", "us"),
    ("XLV", "Health Care Select Sector SPDR", "us"),
    ("XLI", "Industrial Select Sector SPDR", "us"),
    ("XLY", "Consumer Discretionary Select Sector SPDR", "us"),
    ("XLP", "Consumer Staples Select Sector SPDR", "us"),
    ("XLU", "Utilities Select Sector SPDR", "us"),
    ("XLB", "Materials Select Sector SPDR", "us"),
    ("XLRE", "Real Estate Select Sector SPDR", "us"),
    ("XLC", "Communication Services Select Sector SPDR", "us"),
    # 主题 ETF
    ("ARKK", "ARK Innovation ETF", "us"),
    ("SMH", "VanEck Semiconductor ETF", "us"),
    ("SOXX", "iShares Semiconductor ETF", "us"),
    ("GLD", "SPDR Gold Shares", "us"),
    ("SLV", "iShares Silver Trust", "us"),
    ("TLT", "iShares 20+ Year Treasury Bond ETF", "us"),
    # 大盘股 (按市值大致排序, 涵盖 NASDAQ100 + DJIA + S&P 头部)
    ("AAPL", "Apple Inc.", "us"),
    ("MSFT", "Microsoft Corporation", "us"),
    ("GOOGL", "Alphabet Inc. Class A", "us"),
    ("GOOG", "Alphabet Inc. Class C", "us"),
    ("AMZN", "Amazon.com Inc.", "us"),
    ("META", "Meta Platforms Inc.", "us"),
    ("NVDA", "NVIDIA Corporation", "us"),
    ("TSLA", "Tesla Inc.", "us"),
    ("BRK.B", "Berkshire Hathaway Inc. Class B", "us"),
    ("AVGO", "Broadcom Inc.", "us"),
    ("JPM", "JPMorgan Chase & Co.", "us"),
    ("V", "Visa Inc.", "us"),
    ("UNH", "UnitedHealth Group Incorporated", "us"),
    ("MA", "Mastercard Incorporated", "us"),
    ("HD", "Home Depot Inc.", "us"),
    ("PG", "Procter & Gamble Co.", "us"),
    ("LLY", "Eli Lilly and Company", "us"),
    ("ORCL", "Oracle Corporation", "us"),
    ("CRM", "Salesforce Inc.", "us"),
    ("COST", "Costco Wholesale Corporation", "us"),
    ("ABBV", "AbbVie Inc.", "us"),
    ("WMT", "Walmart Inc.", "us"),
    ("KO", "Coca-Cola Company", "us"),
    ("PEP", "PepsiCo Inc.", "us"),
    ("BAC", "Bank of America Corporation", "us"),
    ("AMD", "Advanced Micro Devices Inc.", "us"),
    ("ADBE", "Adobe Inc.", "us"),
    ("NFLX", "Netflix Inc.", "us"),
    ("CSCO", "Cisco Systems Inc.", "us"),
    ("MRK", "Merck & Co. Inc.", "us"),
    ("CVX", "Chevron Corporation", "us"),
    ("XOM", "Exxon Mobil Corporation", "us"),
    ("ABT", "Abbott Laboratories", "us"),
    ("TMO", "Thermo Fisher Scientific Inc.", "us"),
    ("ACN", "Accenture plc", "us"),
    ("DHR", "Danaher Corporation", "us"),
    ("DIS", "Walt Disney Company", "us"),
    ("INTC", "Intel Corporation", "us"),
    ("QCOM", "Qualcomm Incorporated", "us"),
    ("TXN", "Texas Instruments Incorporated", "us"),
    ("INTU", "Intuit Inc.", "us"),
    ("AMAT", "Applied Materials Inc.", "us"),
    ("PYPL", "PayPal Holdings Inc.", "us"),
    ("UBER", "Uber Technologies Inc.", "us"),
    ("SHOP", "Shopify Inc.", "us"),
    ("SQ", "Block Inc.", "us"),
    ("PLTR", "Palantir Technologies Inc.", "us"),
    ("COIN", "Coinbase Global Inc.", "us"),
    ("HOOD", "Robinhood Markets Inc.", "us"),
    ("RBLX", "Roblox Corporation", "us"),
    ("U", "Unity Software Inc.", "us"),
    ("SNAP", "Snap Inc.", "us"),
    ("PINS", "Pinterest Inc.", "us"),
    ("ABNB", "Airbnb Inc.", "us"),
    ("DASH", "DoorDash Inc.", "us"),
    ("SNOW", "Snowflake Inc.", "us"),
    ("DDOG", "Datadog Inc.", "us"),
    ("CRWD", "CrowdStrike Holdings Inc.", "us"),
    ("ZS", "Zscaler Inc.", "us"),
    ("NET", "Cloudflare Inc.", "us"),
    ("MDB", "MongoDB Inc.", "us"),
    ("OKTA", "Okta Inc.", "us"),
    ("TEAM", "Atlassian Corporation", "us"),
    ("ZM", "Zoom Video Communications Inc.", "us"),
    ("DOCU", "DocuSign Inc.", "us"),
    ("ROKU", "Roku Inc.", "us"),
    ("SPOT", "Spotify Technology S.A.", "us"),
    ("F", "Ford Motor Company", "us"),
    ("GM", "General Motors Company", "us"),
    ("RIVN", "Rivian Automotive Inc.", "us"),
    ("LCID", "Lucid Group Inc.", "us"),
    ("NIO", "NIO Inc.", "us"),
    ("XPEV", "XPeng Inc.", "us"),
    ("LI", "Li Auto Inc.", "us"),
    ("BABA", "Alibaba Group Holding Limited", "us"),
    ("PDD", "PDD Holdings Inc.", "us"),
    ("JD", "JD.com Inc.", "us"),
    ("BIDU", "Baidu Inc.", "us"),
    ("BILI", "Bilibili Inc.", "us"),
    ("TME", "Tencent Music Entertainment Group", "us"),
    ("IQ", "iQIYI Inc.", "us"),
    ("BA", "Boeing Company", "us"),
    ("CAT", "Caterpillar Inc.", "us"),
    ("GS", "Goldman Sachs Group Inc.", "us"),
    ("MS", "Morgan Stanley", "us"),
    ("WFC", "Wells Fargo & Company", "us"),
    ("C", "Citigroup Inc.", "us"),
    ("AXP", "American Express Company", "us"),
    ("BLK", "BlackRock Inc.", "us"),
    ("SCHW", "Charles Schwab Corporation", "us"),
    ("MCD", "McDonald's Corporation", "us"),
    ("SBUX", "Starbucks Corporation", "us"),
    ("NKE", "Nike Inc.", "us"),
    ("LULU", "Lululemon Athletica Inc.", "us"),
    ("BKNG", "Booking Holdings Inc.", "us"),
    ("MAR", "Marriott International Inc.", "us"),
    ("CMCSA", "Comcast Corporation", "us"),
    ("T", "AT&T Inc.", "us"),
    ("VZ", "Verizon Communications Inc.", "us"),
    ("CVS", "CVS Health Corporation", "us"),
    ("UNP", "Union Pacific Corporation", "us"),
    ("UPS", "United Parcel Service Inc.", "us"),
    ("FDX", "FedEx Corporation", "us"),
    ("HON", "Honeywell International Inc.", "us"),
    ("GE", "General Electric Company", "us"),
    ("LMT", "Lockheed Martin Corporation", "us"),
    ("RTX", "RTX Corporation", "us"),
    ("MMM", "3M Company", "us"),
    ("IBM", "International Business Machines Corporation", "us"),
    ("NOW", "ServiceNow Inc.", "us"),
    ("PANW", "Palo Alto Networks Inc.", "us"),
    ("FTNT", "Fortinet Inc.", "us"),
    ("MU", "Micron Technology Inc.", "us"),
    ("LRCX", "Lam Research Corporation", "us"),
    ("KLAC", "KLA Corporation", "us"),
    ("ASML", "ASML Holding N.V.", "us"),
    ("TSM", "Taiwan Semiconductor Manufacturing Company Limited", "us"),
    ("ARM", "Arm Holdings plc", "us"),
    ("MRVL", "Marvell Technology Inc.", "us"),
    ("ON", "ON Semiconductor Corporation", "us"),
    ("MCHP", "Microchip Technology Incorporated", "us"),
    ("ANET", "Arista Networks Inc.", "us"),
    ("DELL", "Dell Technologies Inc.", "us"),
    ("HPQ", "HP Inc.", "us"),
    ("WBD", "Warner Bros. Discovery Inc.", "us"),
    ("PARA", "Paramount Global", "us"),
    ("SNAP", "Snap Inc.", "us"),
]
```

(实际行数自行清理重复;此处约 130 条够用。)

- [ ] **Step 2: 改 `core/services/symbol_directory_service.py`**

顶部 imports 加:

```python
from core.services._us_seeds import US_SEEDS
```

在 `bootstrap_seeds` 之后加方法:

```python
    async def bootstrap_us_seeds(self) -> int:
        """美股静态 seeds, 启动时刷一次。纯本地写库, 无外部调用。"""
        n = await self.repo.upsert_many(US_SEEDS)
        log.info("symbol_directory.us_seeds_bootstrapped", count=n)
        return n

    async def upsert_one(self, symbol: str, name: str, market: str) -> None:
        await self.repo.upsert_many([(symbol, name, market)])
```

- [ ] **Step 3: 改 `apps/api/main.py::lifespan`**

在 `await dir_svc.bootstrap_seeds()` 之后加:

```python
    await dir_svc.bootstrap_us_seeds()
```

- [ ] **Step 4: 后端 smoke + 启动验证**

```bash
. .venv/bin/activate && python -c "from apps.api.main import app; print('ok')"
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' > /tmp/api.log 2>&1 &
disown
sleep 6
grep "us_seeds_bootstrapped" /tmp/api.log
curl -s "http://localhost:8787/api/symbols/search?q=AAPL" | python -m json.tool
pkill -9 -f "uvicorn apps.api.main:app"; sleep 1
```
Expected: 日志有 `us_seeds_bootstrapped count=...`;curl 返回包含 `"symbol":"AAPL"` 的 hit。

- [ ] **Step 5: Commit**

```bash
git add core/services/_us_seeds.py core/services/symbol_directory_service.py apps/api/main.py
git commit -m "feat(directory): 美股 ~130 静态 seeds + bootstrap_us_seeds"
```

---

### Task 11: directory `search(market=)` 过滤 + symbols route 加 market 参数 + 懒加载

**Files:**
- Modify: `core/persistence/symbol_directory_repo.py::search`
- Modify: `core/services/symbol_directory_service.py::search`
- Modify: `apps/api/routes/symbols.py::search`

- [ ] **Step 1: 改 `core/persistence/symbol_directory_repo.py::search`**

整段替换:

```python
    async def search(
        self, query: str, limit: int = 20,
        *, market: str | None = None,
    ) -> list[tuple[str, str, str]]:
        """模糊搜索: symbol prefix 或 name 子串。可按 market 过滤。"""
        q = query.strip()
        if not q:
            return []
        like = f"%{q}%"
        prefix = f"{q.upper()}%"
        params: list = [prefix, like, prefix]
        sql = """
            SELECT symbol, name, market FROM symbol_directory
            WHERE (symbol LIKE ? OR name LIKE ?)
        """
        if market:
            sql += " AND market = ?"
            params.append(market)
        sql += """
            ORDER BY
              CASE WHEN symbol LIKE ? THEN 0 ELSE 1 END,
              symbol
            LIMIT ?
        """
        params.extend([prefix, limit])
        async with self._connect() as db:
            cur = await db.execute(sql, params)
            rows = await cur.fetchall()
        return [(r["symbol"], r["name"], r["market"]) for r in rows]
```

- [ ] **Step 2: 改 `core/services/symbol_directory_service.py::search`**

```python
    async def search(
        self, query: str, limit: int = 20,
        *, market: str | None = None,
    ) -> list[tuple[str, str, str]]:
        return await self.repo.search(query, limit, market=market)
```

- [ ] **Step 3: 改 `apps/api/routes/symbols.py::search`**

替换整段为:

```python
import re

from apps.api.deps import get_registry
from core.adapters.registry import AdapterRegistry


_US_TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def _looks_like_us_ticker(q: str) -> bool:
    return bool(_US_TICKER_RE.match(q.upper()))


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, max_length=50),
    market: str | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
    svc: SymbolDirectoryService = Depends(get_symbol_directory_service),
    registry: AdapterRegistry = Depends(get_registry),
) -> SearchResponse:
    hits = await svc.search(q, limit, market=market)
    if hits:
        return SearchResponse(query=q, hits=[
            SearchHit(symbol=s, name=n, market=m) for s, n, m in hits
        ])
    # 美股懒加载: market 为 'us' 或未指定, 且 q 像 US ticker → yfinance verify
    if market in (None, "us") and _looks_like_us_ticker(q):
        try:
            us_adapter = registry.get("us")
        except KeyError:
            return SearchResponse(query=q, hits=[])
        sym = q.upper()
        ok, name = await us_adapter.verify_ticker(sym)
        if ok:
            await svc.upsert_one(sym, name or sym, "us")
            return SearchResponse(query=q, hits=[
                SearchHit(symbol=sym, name=name or sym, market="us"),
            ])
    return SearchResponse(query=q, hits=[])
```

**注意**:`registry.get` 如果用 dict 访问可能抛 KeyError 也可能没此方法,实施时确认 `core/adapters/registry.py` 接口。若 `get` 不存在,加一行 `def get(self, m): return self._adapters[m]`(已在 Task 6 加过则跳过)。

- [ ] **Step 4: 后端 smoke**

```bash
. .venv/bin/activate && python -c "from apps.api.main import app; print('ok')"
```

- [ ] **Step 5: 浏览器/curl 手工验证(API 跑起来后)**

```bash
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' > /tmp/api.log 2>&1 &
disown
sleep 6
# 美股 seed
curl -s "http://localhost:8787/api/symbols/search?q=AAPL&market=us" | python -m json.tool
# 不在 seed, 但 yfinance 能验证 — 这步依赖外网, 失败也不阻塞
curl -s "http://localhost:8787/api/symbols/search?q=NVDX&market=us" | python -m json.tool
# market=ashare 应过滤掉美股
curl -s "http://localhost:8787/api/symbols/search?q=AAPL&market=ashare" | python -m json.tool
pkill -9 -f "uvicorn apps.api.main:app"; sleep 1
```
Expected: 第 1 个 curl 返回 AAPL;第 3 个 curl 返回 hits=[]。

- [ ] **Step 6: Commit**

```bash
git add core/persistence/symbol_directory_repo.py core/services/symbol_directory_service.py \
        apps/api/routes/symbols.py
git commit -m "feat(directory): search 加 market 过滤 + 美股 ticker 懒加载"
```

---

### Task 12: `intervals.py` 4h 改 multi-market + cd_signals watchlist-events 加 market 参数

**Files:**
- Modify: `core/domain/intervals.py`
- Modify: `apps/api/routes/cd_signals.py`

- [ ] **Step 1: 改 `core/domain/intervals.py`**

把:
```python
    IntervalSpec("4h",  "4小时",  True,  True,  500,  1,  True),
```
改为:
```python
    IntervalSpec("4h",  "4小时",  True,  True,  500,  1,  False),
```

`crypto_only` 字段保留(后端逻辑不再依赖此字段过滤 4h tab,前端按 market 过滤)。

- [ ] **Step 2: 改 `apps/api/routes/cd_signals.py::watchlist_events`**

把:
```python
@router.get("/watchlist-events", response_model=ListResponse)
async def watchlist_events(
    interval: str = Query(...),
    limit: int = Query(100, ge=1, le=500),
    repo: SignalRepo = Depends(get_signal_repo),
    wl_svc: WatchlistService = Depends(get_watchlist_service),
) -> ListResponse:
    """关注列表的所有标的在指定周期上的最近 N 条信号(按 bar_ts 倒序)。
    4h 仅 crypto 标的有意义(股票 4h≡1d), 自动按市场过滤。"""
    if interval not in SIGNAL_INTERVALS_SET:
        raise HTTPException(400, f"unsupported interval: {interval}")
    symbols = await wl_svc.dynamic_universe()
    if interval == "4h":
        symbols = [s for s in symbols if _is_crypto(s)]
    if not symbols:
        return ListResponse(signals=[])
    sigs = await repo.list_recent(intervals=[interval], symbols=symbols, limit=limit)
    return ListResponse(signals=[_to_dto(s) for s in sigs])
```

改为:
```python
from core.domain.markets import infer_market  # 顶部加

@router.get("/watchlist-events", response_model=ListResponse)
async def watchlist_events(
    interval: str = Query(...),
    market: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    repo: SignalRepo = Depends(get_signal_repo),
    wl_svc: WatchlistService = Depends(get_watchlist_service),
) -> ListResponse:
    """关注列表的所有标的在指定周期上的最近 N 条信号(按 bar_ts 倒序)。
    market 给定时按市场过滤; 4h 仅在 us+crypto 标的上有意义。
    """
    if interval not in SIGNAL_INTERVALS_SET:
        raise HTTPException(400, f"unsupported interval: {interval}")
    symbols = await wl_svc.dynamic_universe()
    if market:
        symbols = [s for s in symbols if infer_market(s) == market]
    if interval == "4h":
        symbols = [s for s in symbols if infer_market(s) in ("crypto", "us")]
    if not symbols:
        return ListResponse(signals=[])
    sigs = await repo.list_recent(intervals=[interval], symbols=symbols, limit=limit)
    return ListResponse(signals=[_to_dto(s) for s in sigs])
```

Task 1 已经把 `_is_crypto` 改用 SSoT,这步只是去掉对它的最后一处使用,改为统一用 `infer_market`。如果 Task 1 后仍有 `is_crypto` import,本步可保留(语义等价)。

- [ ] **Step 3: 后端 smoke**

```bash
. .venv/bin/activate && python -c "from apps.api.main import app; print('ok')"
```

- [ ] **Step 4: Commit**

```bash
git add core/domain/intervals.py apps/api/routes/cd_signals.py
git commit -m "feat(api): 4h 多市场化 + watchlist-events 加 market 参数"
```

---

## Phase 4 — 前端时区与图表

### Task 13: 前端 lib 适配 market 参数

**Files:**
- Modify: `apps/web/lib/intervals.ts`
- Modify: `apps/web/lib/cd_signals_api.ts::fetchWatchlistEvents`
- Modify: `apps/web/lib/symbol_api.ts::searchSymbols`
- Modify: `apps/web/lib/signal_time.ts`(新增 market-aware 函数)

- [ ] **Step 1: 改 `apps/web/lib/intervals.ts`**

`klineTabsForMarket` 已存在,把 `cryptoOnly` 改 false 后,该函数当前会让所有市场都看 4h。改成"4h 仅 us+crypto":

```typescript
export function klineTabsForMarket(
  market: string | null,
): { key: Interval; label: string }[] {
  const allowFourH = market === 'us' || market === 'crypto'
  return INTERVAL_SPECS
    .filter((s) => s.isKline && (s.key !== '4h' || allowFourH))
    .map((s) => ({ key: s.key, label: s.labelCn }))
}
```

`detailSignalTabs` 改为接收 market 参数:

```typescript
export function detailSignalTabs(
  market: string | null,
): { key: DetailSignalInterval; label: string }[] {
  const allowFourH = market === 'us' || market === 'crypto'
  return INTERVAL_SPECS
    .filter((s) => s.isSignal && (s.key !== '4h' || allowFourH))
    .map((s) => ({ key: s.key as DetailSignalInterval, label: s.labelCn }))
}
```

**注意**:`DetailSignalInterval` 类型当前是 `'15m' | '30m' | '60m' | '1d'`(不含 4h)。改 `apps/web/lib/types.ts`:

```typescript
export type DetailSignalInterval = '15m' | '30m' | '60m' | '4h' | '1d'
```

- [ ] **Step 2: 改 `apps/web/lib/cd_signals_api.ts::fetchWatchlistEvents`**

```typescript
export async function fetchWatchlistEvents(
  interval: string, limit = 100, market?: string,
): Promise<{ signals: CDSignalDTO[] }> {
  const sp = new URLSearchParams({ interval, limit: String(limit) })
  if (market) sp.set('market', market)
  const r = await fetch(`/api/cd-signals/watchlist-events?${sp}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}
```

- [ ] **Step 3: 改 `apps/web/lib/symbol_api.ts::searchSymbols`**

```typescript
export async function searchSymbols(
  q: string, limit = 20, market?: string,
): Promise<{ query: string; hits: SearchHit[] }> {
  const sp = new URLSearchParams({ q, limit: String(limit) })
  if (market) sp.set('market', market)
  const r = await fetch(`/api/symbols/search?${sp}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}
```

- [ ] **Step 4: 改 `apps/web/lib/signal_time.ts`(增加 market-aware,保留 BJT 别名)**

整段重写:

```typescript
import type { AnySignalInterval } from './types'
import type { Market } from './markets'
import { marketTz, todayKey, tradingDateKey } from './markets'

// adapter 已把 1d ts normalize 为本市场自然交易日 00:00, 这里直通。
export function effectiveTsIso(iso: string, _interval: AnySignalInterval): string {
  return iso
}

// BJT 自然日 key, 保留作 A 股专用别名(老代码兼容)
export function bjtDateKey(iso: string): string {
  return tradingDateKey(iso, 'ashare')
}

export function todayBjtKey(): string {
  return todayKey('ashare')
}

// Market-aware: 按市场时区切分交易日 key
export function marketDateKey(iso: string, market: Market): string {
  return tradingDateKey(iso, market)
}

export function fmtSignalTs(
  iso: string,
  interval: AnySignalInterval,
  market: Market = 'ashare',
): string {
  const tz = marketTz(market)
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
  const parts = fmt.formatToParts(new Date(iso))
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  if (interval === '1d') {
    return `${get('year')}-${get('month')}-${get('day')}`
  }
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`
}
```

- [ ] **Step 5: tsc 检查**

```bash
cd /Users/xiangrong/stock/marketpulse/apps/web && npx tsc --noEmit
```
Expected: exit=0(可能有 SignalsTable / CDSignalPanel / WatchlistSignalsPanel 的旧调用签名报错,这是 Task 14 要修的)。

如果 tsc 错只来自上面三个文件签名 mismatch,**先暂时把这几个调用点改为传 `'ashare'` 让 tsc 过**(Task 14 会改成真正 market-aware)。具体:
- `SignalsTable.tsx:42` `fmtSignalTs(s.bar_ts, interval)` → `fmtSignalTs(s.bar_ts, interval, 'ashare')`
- `CDSignalPanel.tsx` `detailSignalTabs()` → `detailSignalTabs(null)` (传 null 不让 4h 出现)
- `WatchlistSignalsPanel.tsx` `detailSignalTabs()` 类似

跑 tsc 再 commit。

- [ ] **Step 6: Commit**

```bash
cd /Users/xiangrong/stock/marketpulse
git add apps/web/lib/intervals.ts apps/web/lib/cd_signals_api.ts \
        apps/web/lib/symbol_api.ts apps/web/lib/signal_time.ts apps/web/lib/types.ts \
        apps/web/components/SignalsTable.tsx \
        apps/web/components/CDSignalPanel.tsx \
        apps/web/components/WatchlistSignalsPanel.tsx
git commit -m "feat(web): lib 适配 market 参数(signal_time / intervals / api)"
```

---

### Task 14: chart 时区按 market 渲染

**Files:**
- Modify: `apps/web/lib/chart_time.ts`
- Modify: `apps/web/components/KLineChart.tsx`
- Modify: `apps/web/components/IntradayChart.tsx`
- Modify: `apps/web/app/symbol/[code]/page.tsx`(把 market 透传给 chart)

- [ ] **Step 1: 改 `apps/web/lib/chart_time.ts`**

整段重写:

```typescript
import { TickMarkType, type Time } from 'lightweight-charts'
import { marketTz, type Market } from './markets'

// 用 Intl.DateTimeFormat 按市场时区取 Y/M/D/H/m/s parts。
function tzParts(time: Time, market: Market): {
  Y: string; M: string; D: string; H: string; mi: string; s: string
} {
  if (typeof time === 'string') {
    // 日线: time 已是 YYYY-MM-DD(已按市场时区算过)
    const [Y, M, D] = time.split('-')
    return { Y, M, D, H: '00', mi: '00', s: '00' }
  }
  // intraday: time 是 fake-UTC seconds(原 UTC + tzOffsetSeconds), 直接用 getUTC*
  // 这要求 KLineChart/IntradayChart 在生成 time 时按当前市场的 offset 调过
  const d = new Date((time as number) * 1000)
  return {
    Y: String(d.getUTCFullYear()),
    M: String(d.getUTCMonth() + 1).padStart(2, '0'),
    D: String(d.getUTCDate()).padStart(2, '0'),
    H: String(d.getUTCHours()).padStart(2, '0'),
    mi: String(d.getUTCMinutes()).padStart(2, '0'),
    s: String(d.getUTCSeconds()).padStart(2, '0'),
  }
}

export function makeChartCrosshairFormatter(market: Market) {
  return (time: Time): string => {
    const { Y, M, D, H, mi, s } = tzParts(time, market)
    if (typeof time === 'string') return `${Y}-${M}-${D}`
    return `${Y}-${M}-${D} ${H}:${mi}:${s}`
  }
}

export function makeChartTickFormatter(market: Market) {
  return (time: Time, type: TickMarkType): string => {
    const { Y, M, D, H, mi } = tzParts(time, market)
    if (type === TickMarkType.Year) return Y
    if (type === TickMarkType.Month) return `${Y}-${M}`
    if (type === TickMarkType.DayOfMonth) return `${M}-${D}`
    return `${H}:${mi}`
  }
}

// 兼容老调用: 默认 ashare
export const fmtChartCrosshair = makeChartCrosshairFormatter('ashare')
export const fmtChartTick = makeChartTickFormatter('ashare')

// (suppress 'marketTz' unused if not used here)
void marketTz
```

- [ ] **Step 2: 改 `apps/web/components/KLineChart.tsx`**

加 `market` prop 到 `KLineChartProps`:

```typescript
import type { Market } from '@/lib/markets'
import { tzOffsetSeconds } from '@/lib/markets'
import { makeChartCrosshairFormatter, makeChartTickFormatter } from '@/lib/chart_time'

export interface KLineChartProps {
  bars: BarDTO[]
  interval: Interval
  market: Market
  height?: number
  signals?: SignalMarker[]
}
```

把 `function toBarTime` 改成接收 market:

```typescript
function toBarTime(iso: string, interval: Interval, market: Market): Time {
  if (INTRADAY.has(interval)) {
    return ((new Date(iso).getTime() / 1000) + tzOffsetSeconds(market, iso)) as Time
  }
  // 日线: 用市场时区切日历, 得 YYYY-MM-DD
  return new Date(iso).toLocaleDateString('en-CA',
    { timeZone: market === 'us' ? 'America/New_York'
              : market === 'hk' ? 'Asia/Hong_Kong'
              : 'Asia/Shanghai' }) as Time
}
```

`createChart` 内 localization/timeScale 改:

```typescript
const chart = createChart(ref.current, {
  height,
  layout: { ... },
  grid: { ... },
  localization: { timeFormatter: makeChartCrosshairFormatter(market) },
  timeScale: {
    timeVisible: intraday,
    secondsVisible: false,
    borderColor: '#262626',
    tickMarkFormatter: makeChartTickFormatter(market),
  },
  rightPriceScale: { borderColor: '#262626' },
})
```

`candleData` / `volData` 的 `toBarTime` 调用都加 `, market` 参数。`markers` 的 `toBarTime` 同。

`useEffect deps` 加 `market`。

```typescript
export function KLineChart({ bars, interval, market, height = 400, signals }: KLineChartProps) {
  ...
  useEffect(() => {
    ...
  }, [bars, height, interval, intraday, signals, market])
```

`candleData` / `volData` 调用 `toBarTime(b.ts, interval, market)`。`markers.map` 内 `toBarTime(s.ts, interval, market)`。

- [ ] **Step 3: 改 `apps/web/components/IntradayChart.tsx`**

加 `market` prop:

```typescript
import type { Market } from '@/lib/markets'
import { tzOffsetSeconds } from '@/lib/markets'
import { makeChartCrosshairFormatter, makeChartTickFormatter } from '@/lib/chart_time'

export interface IntradayChartProps {
  bars: BarDTO[]
  market: Market
  height?: number
  prevClose?: number | null
}
```

把模块级 `toChartTime` 改为接收 market(从外部传):

```typescript
function toChartTime(iso: string, market: Market): number {
  return (new Date(iso).getTime() / 1000) + tzOffsetSeconds(market, iso)
}
```

`createChart` 的 `localization` / `timeScale.tickMarkFormatter` 改用 `makeChartCrosshairFormatter(market)` / `makeChartTickFormatter(market)`。`useEffect` 内所有 `toChartTime(...)` 调用加 `, market`。`useEffect deps` 加 `market`。

- [ ] **Step 4: 改 `apps/web/app/symbol/[code]/page.tsx`**

`<IntradayChart ...>` 和 `<KLineChart ...>` 都加 `market={profile?.market ?? 'ashare'}` prop。

例如:
```tsx
{interval === '1m' && data && todayBars.length > 0 && (
  <IntradayChart
    bars={todayBars}
    prevClose={prevClose}
    height={420}
    market={profile?.market ?? 'ashare'}
  />
)}
...
{interval !== '1m' && data && data.bars.length > 0 && (
  <KLineChart
    bars={data.bars}
    interval={interval}
    market={profile?.market ?? 'ashare'}
    height={420}
    signals={signalInterval ? markers : undefined}
  />
)}
```

`profile?.market` 类型可能是 `Market | null | undefined`,fallback `'ashare'` 安全。

- [ ] **Step 5: 检查 `IndexCard.tsx` 是否需要改**

`apps/web/components/IndexCard.tsx` 当前用 `(new Date(p.ts).getTime() / 1000) + 8 * 3600` 写死 BJT 偏移。指数卡只在 A 股 dashboard 上显示,**本期不动**,保留 BJT。本步骤不修改该文件。

- [ ] **Step 6: tsc 检查 + 浏览器手工 smoke**

```bash
cd /Users/xiangrong/stock/marketpulse/apps/web && npx tsc --noEmit
```
Expected: exit=0

- [ ] **Step 7: Commit**

```bash
cd /Users/xiangrong/stock/marketpulse
git add apps/web/lib/chart_time.ts \
        apps/web/components/KLineChart.tsx \
        apps/web/components/IntradayChart.tsx \
        apps/web/app/symbol/\[code\]/page.tsx
git commit -m "feat(web): chart 时区按 market 渲染(KLine/Intraday 接 market prop)"
```

---

### Task 15: 详情页 CDSignalPanel + SignalsTable 接收 market

**Files:**
- Modify: `apps/web/components/CDSignalPanel.tsx`
- Modify: `apps/web/components/SignalsTable.tsx`
- Modify: `apps/web/components/WatchlistSignalsPanel.tsx`
- Modify: `apps/web/app/symbol/[code]/page.tsx`

- [ ] **Step 1: 改 `apps/web/components/SignalsTable.tsx`**

`SignalsTable` 接收 `market: Market` prop:

```typescript
import type { Market } from '@/lib/markets'

export function SignalsTable({
  signals, interval, market, showSymbol = false,
}: {
  signals: CDSignalDTO[]
  interval: AnySignalInterval
  market: Market
  showSymbol?: boolean
}) {
  ...
  {fmtSignalTs(s.bar_ts, interval, market)}
  ...
}
```

- [ ] **Step 2: 改 `apps/web/components/CDSignalPanel.tsx`**

接收 `market` prop,内部分组用 `tradingDateKey(market)` / `todayKey(market)`,tab 用 `detailSignalTabs(market)`:

```typescript
import type { Market } from '@/lib/markets'
import { tradingDateKey, todayKey } from '@/lib/markets'
import { detailSignalTabs } from '@/lib/intervals'

export function CDSignalPanel({ symbol, market }: { symbol: string; market: Market }) {
  const TABS = useMemo(() => detailSignalTabs(market), [market])
  const [interval, setInterval] = useState<DetailSignalInterval>(
    TABS.some((t) => t.key === '60m') ? '60m' : (TABS[0]?.key ?? '60m'),
  )
  ...
  // 分组:
  const { today, history } = useMemo(() => {
    const tk = todayKey(market)
    const today: CDSignalDTO[] = []
    const history: CDSignalDTO[] = []
    for (const s of signals) {
      const key = tradingDateKey(s.bar_ts, market)
      ;(key === tk ? today : history).push(s)
    }
    return { today, history }
  }, [signals, market])
  ...
  // SignalsTable 调用:
  <SignalsTable signals={today} interval={interval} market={market} />
  <SignalsTable signals={history} interval={interval} market={market} />
```

`TABS` 改为 `useMemo` 后,原 `const TABS = detailSignalTabs()` 删除。

- [ ] **Step 3: 改 `apps/web/components/WatchlistSignalsPanel.tsx`**

接收 `market: Market` prop;`fetchWatchlistEvents(activeInterval, 100, market)`;`bjtDateKey/todayBjtKey` 改为 `tradingDateKey(s.bar_ts, market)` / `todayKey(market)`;`SignalsTable` 加 `market` prop。

具体改动(集中改 `WatchlistSignalsPanel.tsx`):
```typescript
import { tradingDateKey, todayKey, type Market } from '@/lib/markets'

export function WatchlistSignalsPanel({
  symbols, market,
}: { symbols: string[]; market: Market }) {
  ...
  const { data, isLoading } = useSWR(
    `wl:events:${activeInterval}:${market}:${symbols.join(',')}`,
    () => fetchWatchlistEvents(activeInterval, 100, market),
    { refreshInterval: 30_000 },
  )
  ...
  const { today, history } = useMemo(() => {
    const tk = todayKey(market)
    const today: CDSignalDTO[] = []
    const history: CDSignalDTO[] = []
    for (const s of signals) {
      const key = tradingDateKey(s.bar_ts, market)
      ;(key === tk ? today : history).push(s)
    }
    return { today, history }
  }, [signals, market])
  ...
  <SignalsTable signals={today} interval={activeInterval} market={market} showSymbol />
  <SignalsTable signals={history} interval={activeInterval} market={market} showSymbol />
```

`bjtDateKey / effectiveTsIso / todayBjtKey` imports 删除。`isCrypto` 函数也可删(由 Task 18 处理 4h tab 控制)。

`tabs` 现有逻辑("仅在 watchlist 含 crypto 标的时才展示 4h tab")改为"当 market 为 us / crypto 时展示 4h":

```typescript
const tabs = useMemo(() => {
  const allowFourH = market === 'us' || market === 'crypto'
  return ALL_TABS.filter((t) => t.key !== '4h' || allowFourH)
}, [market])
```

- [ ] **Step 4: 改 `apps/web/app/symbol/[code]/page.tsx`**

`<CDSignalPanel symbol={symbol} market={profile?.market ?? 'ashare'} />`

- [ ] **Step 5: tsc**

```bash
cd /Users/xiangrong/stock/marketpulse/apps/web && npx tsc --noEmit
```
Expected: exit=0

- [ ] **Step 6: Commit**

```bash
cd /Users/xiangrong/stock/marketpulse
git add apps/web/components/SignalsTable.tsx \
        apps/web/components/CDSignalPanel.tsx \
        apps/web/components/WatchlistSignalsPanel.tsx \
        apps/web/app/symbol/\[code\]/page.tsx
git commit -m "feat(web): 信号表/详情面板/事件流接 market prop, 按市场时区分组"
```

---

## Phase 5 — 关注页 4-tab

### Task 16: SymbolSearch 接收 market prop

**Files:**
- Modify: `apps/web/components/SymbolSearch.tsx`

- [ ] **Step 1: 改 `apps/web/components/SymbolSearch.tsx`**

```typescript
interface Props {
  placeholder?: string
  market?: string  // 限定搜索 scope
  onSelect: (hit: SearchHit) => void
}

export function SymbolSearch({ placeholder = '搜索代码或名称…', market, onSelect }: Props) {
  ...
  // useEffect 内:
  const resp = await searchSymbols(q.trim(), 15, market)
  ...
}
```

`useEffect deps` 加 `market`(搜索结果会随 tab 切换刷新)。

- [ ] **Step 2: tsc**

```bash
cd /Users/xiangrong/stock/marketpulse/apps/web && npx tsc --noEmit
```
Expected: exit=0

- [ ] **Step 3: Commit**

```bash
cd /Users/xiangrong/stock/marketpulse
git add apps/web/components/SymbolSearch.tsx
git commit -m "feat(web): SymbolSearch 接 market prop, 限定搜索 scope"
```

---

### Task 17: 关注页 4-tab + 搜索 scope 跟随 + HK/Crypto 骨架

**Files:**
- Modify: `apps/web/app/watchlist/page.tsx`

- [ ] **Step 1: 改 `apps/web/app/watchlist/page.tsx`**

整段重写(保留现有 SymbolRow / SymbolPage 框架,在 SymbolPage 内加 tab 状态):

```tsx
'use client'

import { useMemo, useState } from 'react'
import useSWR, { mutate } from 'swr'

import { SymbolSearch } from '@/components/SymbolSearch'
import { WatchlistSignalsPanel } from '@/components/WatchlistSignalsPanel'
import {
  addWatchlistSymbol, listWatchlists, listWatchlistSymbols, removeWatchlistSymbol,
} from '@/lib/watchlist_api'
import { fetchSymbolProfile, fetchSymbolQuote } from '@/lib/symbol_api'
import { inferMarket, type Market } from '@/lib/markets'

// (保留原有 fmtVolume 和 SymbolRow 完全不变 — 略)

const MARKET_TABS: { key: Market; label: string; placeholder: string }[] = [
  { key: 'ashare', label: 'A 股',  placeholder: '搜索代码或名称(如 600519 / 茅台)' },
  { key: 'hk',     label: '港股',  placeholder: '搜索港股(如 9988 / 腾讯)' },
  { key: 'us',     label: '美股',  placeholder: '搜索美股(如 AAPL / Apple)' },
  { key: 'crypto', label: '加密货币', placeholder: '搜索加密货币(如 BTC/USDT)' },
]

export default function WatchlistPage() {
  const { data: lists } = useSWR('wls', listWatchlists)
  const [activeId, setActiveId] = useState<number | null>(null)
  const currentId = activeId ?? lists?.watchlists[0]?.id ?? null

  const [marketTab, setMarketTab] = useState<Market>('ashare')

  const { data: items } = useSWR(
    currentId ? `wl:${currentId}` : null,
    () => listWatchlistSymbols(currentId!),
  )

  const symbolsForTab = useMemo(
    () => (items?.symbols ?? []).filter((s) => inferMarket(s) === marketTab),
    [items, marketTab],
  )

  const tabMeta = MARKET_TABS.find((t) => t.key === marketTab)!

  // HK / Crypto 骨架: tab 存在但本期不接入实数据
  const isSkeletonTab = marketTab === 'hk' || marketTab === 'crypto'

  async function onAdd(hitSymbol: string) {
    if (!currentId) return
    await addWatchlistSymbol(currentId, hitSymbol)
    mutate(`wl:${currentId}`)
    setTimeout(() => {
      mutate((key) => typeof key === 'string' && key.startsWith('wl:events:'))
    }, 6_000)
  }

  async function onRemove(sym: string) {
    if (!currentId) return
    await removeWatchlistSymbol(currentId, sym)
    mutate(`wl:${currentId}`)
  }

  return (
    <main className="p-6 max-w-7xl mx-auto space-y-4">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold">我的关注</h1>
        <a href="/market" className="text-xs text-neutral-400 hover:text-neutral-200">← 市场</a>
      </header>

      <div className="flex gap-2">
        {lists?.watchlists.map((w) => (
          <button
            key={w.id}
            onClick={() => setActiveId(w.id)}
            className={`px-3 py-1 text-sm rounded ${w.id === currentId ? 'bg-neutral-700 text-white' : 'bg-neutral-900 text-neutral-400'}`}
          >
            {w.name}
          </button>
        ))}
      </div>

      {/* 市场 tab */}
      <div className="flex gap-1 border-b border-neutral-800">
        {MARKET_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setMarketTab(t.key)}
            className={`px-3 py-1.5 text-sm border-b-2 transition-colors ${
              marketTab === t.key
                ? 'text-white border-blue-500'
                : 'text-neutral-400 border-transparent hover:text-neutral-200'
            }`}
          >
            {t.label}
            {isSkeletonTab && t.key === marketTab && (
              <span className="ml-2 text-xs text-neutral-500">(骨架)</span>
            )}
          </button>
        ))}
      </div>

      <SymbolSearch
        key={marketTab}
        market={marketTab}
        placeholder={tabMeta.placeholder}
        onSelect={(hit) => onAdd(hit.symbol)}
      />

      <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
        <div className="grid grid-cols-[1fr_100px_90px_110px_40px] gap-2 text-xs text-neutral-500 pb-2 border-b border-neutral-800">
          <span>标的</span>
          <span className="text-right">价格</span>
          <span className="text-right">涨跌幅</span>
          <span className="text-right">成交量</span>
          <span />
        </div>
        {isSkeletonTab && symbolsForTab.length === 0 && (
          <p className="text-sm text-neutral-500 mt-3">
            {marketTab === 'hk' ? '港股' : '加密货币'} 行情/信号本期暂未接入,可先添加标的占位。
          </p>
        )}
        {!isSkeletonTab && symbolsForTab.length === 0 && (
          <p className="text-sm text-neutral-500 mt-3">空</p>
        )}
        <ul>
          {symbolsForTab.map((s) => (
            <SymbolRow key={s} symbol={s} onRemove={onRemove} />
          ))}
        </ul>
      </section>

      <WatchlistSignalsPanel
        symbols={symbolsForTab}
        market={marketTab}
      />
    </main>
  )
}
```

注意:**`SymbolRow` 整段保留**不变;只在 `SymbolPage` 内加 tab + filter。

- [ ] **Step 2: tsc**

```bash
cd /Users/xiangrong/stock/marketpulse/apps/web && npx tsc --noEmit
```
Expected: exit=0

- [ ] **Step 3: 后端 + 前端 启动 + 浏览器手工**

```bash
cd /Users/xiangrong/stock/marketpulse
pkill -9 -f "uvicorn apps.api.main:app"; sleep 1
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' > /tmp/api.log 2>&1 &
disown && sleep 6
grep -c FATAL /tmp/api.log
```
Expected: 0

启动前端(如未启动):
```bash
cd apps/web && nohup npx next dev -p 3000 > /tmp/next-dev.log 2>&1 &
disown
```

浏览器:`http://localhost:3000/watchlist`,验证:
- 4 个 tab 切换,搜索框 placeholder 跟着变,搜索结果不跨市场
- A 股 tab 显示已有 watchlist 标的;US tab 空,搜 `AAPL` 出现并能添加
- HK + Crypto tab 显示骨架文案

- [ ] **Step 4: Commit**

```bash
cd /Users/xiangrong/stock/marketpulse
git add apps/web/app/watchlist/page.tsx
git commit -m "feat(web): 关注页 4 市场 tab + 搜索 scope 跟随 + HK/Crypto 骨架"
```

---

## Phase 6 — 端到端验证

### Task 18: 端到端冒烟 + 更新 CLAUDE.md + TODO 登记

**Files:**
- Modify: `CLAUDE.md`(SSoT 表加 `infer_market`,雷区无新增)
- Modify: `docs/TODO.md`(登记 §9 未实施事项)

- [ ] **Step 1: 后端 + 前端验收**

```bash
cd /Users/xiangrong/stock/marketpulse
pkill -9 -f "uvicorn apps.api.main:app"; sleep 1
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' > /tmp/api.log 2>&1 &
disown && sleep 6
grep -E "us_seeds_bootstrapped|us_signal_jobs_attached" /tmp/api.log
grep -c FATAL /tmp/api.log
curl -s "http://localhost:8787/api/symbols/search?q=AAPL&market=us" | python -m json.tool
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8787/api/symbols/AAPL/profile"
curl -s "http://localhost:8787/api/symbols/AAPL/bars?interval=1d&days=30" | python -c "import sys,json; d=json.load(sys.stdin); print('bars:', len(d['bars']))"
```
Expected:
- `us_seeds_bootstrapped` 和 `us_signal_jobs_attached` 都在日志里
- FATAL 计数为 0
- search 返回 AAPL
- profile 返回 200
- bars 返回 ≥1 条

- [ ] **Step 2: 浏览器手工(列出关键路径)**

按顺序验:
1. `/watchlist` 切到"美股" tab,添加 AAPL → 列表显示,信号面板 60m tab 出现"加载中"或具体信号
2. 点 AAPL → `/symbol/AAPL` 详情页,K 线 1d → 显示 ET 自然日时间(右下角 attribution 已无,Task 之前已修)
3. 切 60m → 时间显示按 ET(如 ET 09:30 显示 `2026-XX-XX 09:30`),不是 BJT
4. 切 4h → 显示 4h tab(因 market='us'),4 根/天
5. CDSignalPanel 4h tab 也出现(detailSignalTabs(us) 含 4h),"当天 vs 历史"按 ET 切分
6. 切回 `/watchlist` A 股 tab,验证现有 A 股标的不受影响(BJT 时区正常)

- [ ] **Step 3: 更新 `CLAUDE.md`**

在"规范 1:单一事实源(SSoT)收口表" 加一行(在 `Symbol market 推断` 现有那行,把位置改为新的 SSoT):

```
| Symbol market 推断 | `core/domain/markets.py::infer_market` | route / scheduler / kline_service 4 处入口 |
```

(替换现有 `apps/api/routes/symbols.py::_infer_market` 那行。)

如果 `CLAUDE.md` 中"当前活跃约束"一节适合,加一行:

```
- **美股 4h tab** 在 watchlist + 详情页都显示(prepost 16h ÷ 4 = 4 根/天);港股 4h 仅 detail 页可见但与 1d 等价,无新增信号
```

- [ ] **Step 4: 更新 `docs/TODO.md`**

加一节:

```markdown
## 美股接入未实施事项(spec §9)

- `signal_service.scan_symbol(regular_only=)`:美股盘前盘后噪声过滤选项 + UI "Extended Hours" toggle
- 4h bucket 按时钟对齐(消除 yfinance 偶发缺 bar 时的偏移)
- 富途 SDK 接入(yfinance 失效时的 Plan B)
- 美股 dashboard 板块卡(SPY / QQQ / DIA 主要指数代理)
- 美股资金流(institutional holders / 13F)— 待数据源调研
- HK / Crypto 关注页内容接入(本期骨架,功能开发中)
- K 图 markers 新 bar 自动同步(SWR refreshInterval 在交易时段开)
```

- [ ] **Step 5: 全量类型/测试 final pass**

```bash
. .venv/bin/activate
pytest tests/unit/ -v
cd apps/web && npx tsc --noEmit
```
Expected: pytest 全部通过,tsc exit=0。

- [ ] **Step 6: Commit + 完成**

```bash
cd /Users/xiangrong/stock/marketpulse
git add CLAUDE.md docs/TODO.md
git commit -m "docs: 美股接入完成, 更新 SSoT 表 + TODO 登记未实施事项"
```

---

## 自审清单(Plan Self-Review)

**Spec 覆盖**:
- §1 目标 USAdapter intraday → Task 3-5 ✓
- §1 目标 K 线全周期 → Task 6(KLineService 多市场化) + Task 12 + Task 13-14(前端 tab 配置) ✓
- §1 目标 CD 信号 15m/30m/60m/4h/1d → Task 7-9 + Task 12 ✓
- §1 目标 关注页 4-tab → Task 16-17 ✓
- §1 目标 directory 200 seeds + 懒加载 → Task 10-11 ✓
- §1 目标 前端美股 ET 显示 → Task 13-15 ✓
- §1 目标 4h 美股 4 根/天 → Task 6 `_FOUR_HOUR_GROUP_BY_MARKET` ✓
- §4.1 infer_market SSoT → Task 1 ✓
- §4.2 USAdapter `_to_yfinance_ticker` / verify_ticker → Task 3, 5 ✓
- §4.2 1d ts ET normalize → Task 4 ✓
- §4.4 scheduler market_filter / cron → Task 8, 9 ✓
- §4.5 seeds + 懒加载 → Task 10, 11 ✓
- §4.6 前端市场感知组件 → Task 13-17 ✓

**Placeholder 扫描**:无 TODO / TBD;每步均有完整代码或具体命令。

**类型一致性**:
- 后端 `infer_market` 返回 `Market` Literal,前后端命名一致
- 前端 `Market` 类型在 `types.ts` 已存在,`markets.ts` re-export 同步类型
- `DetailSignalInterval` 在 Task 13 步骤 1 加 `'4h'`,后续 `detailSignalTabs(market)` 调用一致
- `fetchWatchlistEvents(interval, limit, market)` 第 3 参数 = 后端 `?market=`,与 `searchSymbols(q, limit, market)` 第 3 参数模式一致
- `SignalsTable` 加 `market` prop 必填(非 optional),所有调用点(Task 15 步骤 3 + Task 17)都已传

**Scope check**:18 task,文件覆盖面 ~30 文件,中等规模;每个 task 5-30 分钟。

**风险点(执行时格外注意)**:
- Task 6 中 `registry.get(market)` 取决于 `core/adapters/registry.py` 当前实现,可能需要先加 `get` 方法
- Task 13 步骤 5 提到的"先把旧调用点改 ashare 让 tsc 过",必须留好后续 Task 15 真正补齐 — 已显式标注
- Task 9 lifespan 接入 `attach_us_signal_jobs` 必须放在 `attach_signal_jobs` 之后,避免重叠
- Task 17 中 SymbolSearch 用 `key={marketTab}` 强制重新挂载,清空 query 状态(切 tab 体验)
- Phase 2 修改 KLineService 构造签名时,所有 `KLineService(repo, adapter)` 旧调用都得改;现有代码只在 `apps/api/deps.py::get_kline_service` 单点,但实施者要确认 grep
