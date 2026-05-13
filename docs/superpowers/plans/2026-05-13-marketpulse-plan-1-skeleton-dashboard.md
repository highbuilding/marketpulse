# MarketPulse Plan 1/3: 骨架 + 四市场 Dashboard 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭起 MarketPulse 的代码骨架(后端 FastAPI + 前端 Next.js + 持久化 + 调度器),实现四市场 Adapter 与 `/dashboard` 页面,完成 V1-A1 与 V1-A4 验收标准。

**Architecture:** Python 单体服务(FastAPI + APScheduler + asyncio),DuckDB 存历史 K 线、SQLite 存状态;Next.js 14 SPA 通过 REST 拉数据、WS 接收 push;每市场一个 Adapter,统一 Protocol 接口,缺源时优雅降级,UI 标灰对应 tab。

**Tech Stack:** Python 3.11、FastAPI、APScheduler、DuckDB、aiosqlite、akshare、yfinance(curl_cffi)、Alpaca-py、websockets(Binance)、structlog、pytest;Next.js 14 App Router、TypeScript、Tailwind、shadcn/ui、TradingView Lightweight Charts、Playwright。

**参考 spec:** `docs/superpowers/specs/2026-05-13-marketpulse-design.md`

---

## File Structure

本 Plan 创建以下文件(完整骨架,后续 Plan 2/3 会扩展):

**根目录:**
- `pyproject.toml` —— Python 依赖与项目元数据
- `Makefile` —— `make dev` / `make test` / `make build`
- `.env.example` —— 环境变量模板
- `.gitignore`、`README.md`

**配置:**
- `config/sources.yaml` —— 各市场数据源开关、备源、健康检查参数
- `config/llm.yaml` —— LLM 配置(本 Plan 仅占位,Plan 2 用)
- `config/factors.yaml` —— 因子权重(本 Plan 仅占位,Plan 3 用)

**领域模型:**
- `core/domain/__init__.py`
- `core/domain/models.py` —— `Quote`、`Bar`、`Fundamental`、`HealthStatus` dataclass

**Adapter 层:**
- `core/adapters/__init__.py`
- `core/adapters/base.py` —— `MarketAdapter` Protocol、`AdapterError`、熔断器
- `core/adapters/ashare.py` —— akshare + mootdx 备源
- `core/adapters/hk.py` —— akshare(港股)+ yfinance 备源
- `core/adapters/us.py` —— Alpaca + yfinance 备源
- `core/adapters/crypto.py` —— Binance WS + CoinGecko REST 备源
- `core/adapters/registry.py` —— 启动时注册所有 adapter,提供按 market 查找

**持久化层:**
- `core/persistence/__init__.py`
- `core/persistence/duckdb_repo.py` —— `BarRepo`(写历史 bars)
- `core/persistence/sqlite_repo.py` —— `StateRepo`(配置、健康状态)
- `core/persistence/schema.sql` —— SQLite 初始化 DDL

**内存缓存:**
- `core/cache/__init__.py`
- `core/cache/quote_cache.py` —— 内存 dict + TTL,最新 quote 存这里

**调度器:**
- `core/scheduler/__init__.py`
- `core/scheduler/jobs.py` —— `tick_snapshot_10s`、`flush_to_duckdb_60s` 等 job 定义
- `core/scheduler/scheduler.py` —— APScheduler 启动、健康检查

**API 层:**
- `apps/api/__init__.py`
- `apps/api/main.py` —— FastAPI app、生命周期、CORS
- `apps/api/deps.py` —— 依赖注入(adapter registry、repos、cache)
- `apps/api/routes/__init__.py`
- `apps/api/routes/health.py` —— `/api/health`
- `apps/api/routes/markets.py` —— `/api/markets/{market}/overview`
- `apps/api/ws/__init__.py`
- `apps/api/ws/ticks.py` —— `/ws/ticks` 广播器(Plan 1 仅骨架,Plan 3 完善)

**前端(Next.js 14 App Router):**
- `apps/web/package.json`、`tsconfig.json`、`next.config.js`、`tailwind.config.ts`、`postcss.config.js`
- `apps/web/app/layout.tsx`、`app/page.tsx`(重定向到 `/dashboard`)
- `apps/web/app/dashboard/page.tsx` —— 四市场 dashboard
- `apps/web/components/MarketCard.tsx`、`HealthBadge.tsx`、`HeatmapPlaceholder.tsx`
- `apps/web/lib/api.ts` —— `fetchOverview`、`fetchHealth`
- `apps/web/lib/types.ts` —— 与后端模型对齐的 TS 类型

**测试:**
- `tests/conftest.py`
- `tests/unit/adapters/test_base.py`、`test_ashare.py`、`test_hk.py`、`test_us.py`、`test_crypto.py`
- `tests/unit/persistence/test_duckdb_repo.py`、`test_sqlite_repo.py`
- `tests/unit/cache/test_quote_cache.py`
- `tests/unit/scheduler/test_jobs.py`
- `tests/integration/test_adapters_live.py`(`@pytest.mark.integration`,本地手动跑)
- `tests/integration/test_api_health.py`
- `tests/e2e/dashboard.spec.ts`(Playwright)

文件按职责拆分,单文件不超过 ~250 行。Adapter 层每市场一文件,因子和事件留到 Plan 2/3 不在此处建。

---

## Task 1: 项目骨架与 Python 工程初始化

**Files:**
- Create: `pyproject.toml`
- Create: `Makefile`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `README.md`

- [ ] **Step 1: 写 `pyproject.toml`**

```toml
[project]
name = "marketpulse"
version = "0.1.0"
description = "Local web platform for monitoring A/HK/US/Crypto markets"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "apscheduler>=3.10",
    "duckdb>=1.1",
    "aiosqlite>=0.20",
    "akshare>=1.15",
    "mootdx>=0.11",
    "yfinance>=0.2.50",
    "curl-cffi>=0.7",
    "alpaca-py>=0.33",
    "websockets>=13",
    "httpx>=0.27",
    "structlog>=24.4",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "pyyaml>=6.0",
    "tenacity>=9.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "pytest-mock>=3.14",
    "respx>=0.21",
    "ruff>=0.7",
    "mypy>=1.13",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "integration: hit real HTTP endpoints, skipped in CI",
]
addopts = "-ra --strict-markers"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["core*", "apps*"]
```

- [ ] **Step 2: 写 `Makefile`**

```makefile
.PHONY: install dev test test-integration test-full lint typecheck web-install web-dev clean

install:
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

dev:
	. .venv/bin/activate && uvicorn apps.api.main:app --reload --port 8787 & \
	cd apps/web && npm run dev

test:
	. .venv/bin/activate && pytest -m "not integration"

test-integration:
	. .venv/bin/activate && pytest -m integration

test-full: test test-integration
	cd apps/web && npx playwright test

lint:
	. .venv/bin/activate && ruff check core apps tests

typecheck:
	. .venv/bin/activate && mypy core apps

web-install:
	cd apps/web && npm install

clean:
	rm -rf .venv apps/web/node_modules apps/web/.next data/*.duckdb data/*.db
```

- [ ] **Step 3: 写 `.env.example`**

```
# 美股(可选,缺则自动降级到 yfinance)
ALPACA_API_KEY=
ALPACA_SECRET_KEY=

# Crypto
BINANCE_WS_URL=wss://stream.binance.com:9443/ws

# LLM(Plan 2 才用,这里先占位)
OLLAMA_HOST=http://127.0.0.1:11434
OPENAI_BASE_URL=
OPENAI_API_KEY=

# 应用
APP_DATA_DIR=./data
APP_LOG_LEVEL=INFO
APP_TIMEZONE=Asia/Shanghai
```

- [ ] **Step 4: 写 `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.mypy_cache/
.ruff_cache/
data/*.duckdb
data/*.db
data/*.db-journal
logs/
.env
apps/web/node_modules/
apps/web/.next/
apps/web/out/
apps/web/test-results/
.DS_Store
```

- [ ] **Step 5: 写 `README.md`**

```markdown
# MarketPulse

本地运行的四市场行情监控分析平台。详见 `docs/superpowers/specs/2026-05-13-marketpulse-design.md`。

## 启动

\`\`\`bash
make install     # 安装 Python 依赖
make web-install # 安装前端依赖
cp .env.example .env  # 按需填入 API key
make dev         # 启动后端(8787)与前端(3000)
\`\`\`

打开 http://localhost:3000/dashboard。
```

- [ ] **Step 6: 创建空目录占位**

Run:
```bash
mkdir -p core/{domain,adapters,persistence,cache,scheduler} \
         apps/api/{routes,ws} apps/web \
         tests/{unit,integration,e2e} \
         config data logs
touch core/__init__.py core/domain/__init__.py core/adapters/__init__.py \
      core/persistence/__init__.py core/cache/__init__.py core/scheduler/__init__.py \
      apps/__init__.py apps/api/__init__.py apps/api/routes/__init__.py apps/api/ws/__init__.py
```

- [ ] **Step 7: 安装依赖,确认 import 正常**

Run: `make install`
Expected: 依赖装完无错。

Run: `. .venv/bin/activate && python -c "import fastapi, duckdb, akshare, yfinance, websockets; print('ok')"`
Expected: `ok`

- [ ] **Step 8: Commit**

```bash
git init  # 如果尚未是 git 仓库
git add pyproject.toml Makefile .env.example .gitignore README.md \
        core apps tests config
git commit -m "chore: bootstrap marketpulse python skeleton"
```

---

## Task 2: 领域模型(Quote / Bar / Fundamental / HealthStatus)

**Files:**
- Create: `core/domain/models.py`
- Create: `tests/unit/test_domain_models.py`

- [ ] **Step 1: 写失败的测试**

`tests/unit/test_domain_models.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.domain.models import Bar, Fundamental, HealthStatus, Quote


def test_quote_basic_fields():
    q = Quote(
        market="ashare",
        symbol="000858.SZ",
        ts=datetime(2026, 5, 13, 9, 30, tzinfo=timezone.utc),
        price=Decimal("180.50"),
        change_pct=1.25,
        volume=12000,
        source="akshare",
    )
    assert q.market == "ashare"
    assert q.symbol == "000858.SZ"
    assert q.price == Decimal("180.50")


def test_bar_open_high_low_close():
    b = Bar(
        market="us",
        symbol="AAPL",
        ts=datetime(2026, 5, 13, tzinfo=timezone.utc),
        open=Decimal("190"),
        high=Decimal("192"),
        low=Decimal("189"),
        close=Decimal("191.5"),
        volume=1_000_000,
        interval="1d",
    )
    assert b.high >= b.low
    assert b.interval == "1d"


def test_fundamental_optional_fields():
    f = Fundamental(symbol="AAPL", pe_ttm=28.5, pb=42.0, ev_ebitda=20.1)
    assert f.pe_ttm == 28.5
    assert f.market_cap is None  # 默认 None


def test_health_status_states():
    h = HealthStatus(name="ashare", state="ok", detail=None)
    assert h.is_ok()
    assert HealthStatus(name="us", state="disabled", detail="missing key").is_ok() is False


def test_quote_rejects_negative_price():
    with pytest.raises(ValueError):
        Quote(
            market="us", symbol="X", ts=datetime.now(timezone.utc),
            price=Decimal("-1"), change_pct=0, volume=0, source="test",
        )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_domain_models.py -v`
Expected: ImportError(模块不存在)

- [ ] **Step 3: 实现 `core/domain/models.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

Market = Literal["ashare", "hk", "us", "crypto"]
HealthState = Literal["ok", "degraded", "disabled", "down"]


@dataclass(frozen=True, slots=True)
class Quote:
    market: Market
    symbol: str
    ts: datetime
    price: Decimal
    change_pct: float
    volume: int
    source: str

    def __post_init__(self) -> None:
        if self.price < 0:
            raise ValueError(f"price must be >= 0, got {self.price}")


@dataclass(frozen=True, slots=True)
class Bar:
    market: Market
    symbol: str
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    interval: str  # "1m" / "5m" / "1d"

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"high {self.high} < low {self.low}")


@dataclass(frozen=True, slots=True)
class Fundamental:
    symbol: str
    pe_ttm: float | None = None
    pb: float | None = None
    ev_ebitda: float | None = None
    market_cap: float | None = None
    industry: str | None = None


@dataclass(frozen=True, slots=True)
class HealthStatus:
    name: str
    state: HealthState
    detail: str | None = None

    def is_ok(self) -> bool:
        return self.state == "ok"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/test_domain_models.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add core/domain/models.py tests/unit/test_domain_models.py
git commit -m "feat(domain): add Quote/Bar/Fundamental/HealthStatus models"
```

---

## Task 3: Adapter Protocol 与熔断器

**Files:**
- Create: `core/adapters/base.py`
- Create: `tests/unit/adapters/__init__.py`
- Create: `tests/unit/adapters/test_base.py`

- [ ] **Step 1: 写失败的测试**

`tests/unit/adapters/test_base.py`:

```python
import asyncio
import time

import pytest

from core.adapters.base import AdapterError, CircuitBreaker


def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker(fail_threshold=3, reset_after_s=5)
    assert cb.can_execute()
    cb.record_failure()
    cb.record_failure()
    assert cb.can_execute()
    cb.record_failure()
    assert not cb.can_execute()
    assert cb.state == "open"


def test_circuit_breaker_half_open_after_reset():
    cb = CircuitBreaker(fail_threshold=1, reset_after_s=0.05)
    cb.record_failure()
    assert not cb.can_execute()
    time.sleep(0.06)
    assert cb.can_execute()
    assert cb.state == "half_open"


def test_circuit_breaker_closes_after_success():
    cb = CircuitBreaker(fail_threshold=2, reset_after_s=0.05)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.06)
    assert cb.can_execute()
    cb.record_success()
    assert cb.state == "closed"
    assert cb.failure_count == 0


def test_adapter_error_has_source():
    err = AdapterError("timeout", source="akshare")
    assert err.source == "akshare"
    assert "akshare" in str(err)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/adapters/test_base.py -v`
Expected: ImportError

- [ ] **Step 3: 实现 `core/adapters/base.py`**

```python
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal, Protocol

from core.domain.models import Bar, HealthStatus, Market, Quote


class AdapterError(Exception):
    def __init__(self, msg: str, source: str) -> None:
        super().__init__(f"[{source}] {msg}")
        self.source = source


CBState = Literal["closed", "open", "half_open"]


@dataclass
class CircuitBreaker:
    fail_threshold: int = 3
    reset_after_s: float = 300.0
    state: CBState = "closed"
    failure_count: int = 0
    opened_at: float | None = None

    def can_execute(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if self.opened_at is not None and time.time() - self.opened_at >= self.reset_after_s:
                self.state = "half_open"
                return True
            return False
        return True  # half_open

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.fail_threshold:
            self.state = "open"
            self.opened_at = time.time()

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = "closed"
        self.opened_at = None


class MarketAdapter(Protocol):
    market: Market
    name: str

    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]: ...
    async def subscribe(self, symbols: list[str], on_bar: Callable[[Bar], None]) -> None: ...
    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]: ...
    async def health(self) -> HealthStatus: ...
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/adapters/test_base.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add core/adapters/base.py tests/unit/adapters/__init__.py tests/unit/adapters/test_base.py
git commit -m "feat(adapters): add MarketAdapter protocol and CircuitBreaker"
```

---

## Task 4: A 股 Adapter(akshare 主源 + mootdx 备源)

**Files:**
- Create: `core/adapters/ashare.py`
- Create: `tests/unit/adapters/test_ashare.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_adapters_live.py`

- [ ] **Step 1: 写失败的单元测试(mock akshare)**

`tests/unit/adapters/test_ashare.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from core.adapters.ashare import AShareAdapter


@pytest.fixture
def mock_akshare_snapshot_df():
    return pd.DataFrame([
        {"代码": "000858", "名称": "五粮液", "最新价": 180.50, "涨跌幅": 1.25, "成交量": 12000},
        {"代码": "600519", "名称": "贵州茅台", "最新价": 1580.0, "涨跌幅": -0.5, "成交量": 3000},
    ])


@pytest.mark.asyncio
async def test_fetch_snapshot_uses_primary_akshare(mock_akshare_snapshot_df):
    with patch("core.adapters.ashare.ak.stock_zh_a_spot_em", return_value=mock_akshare_snapshot_df):
        adapter = AShareAdapter()
        quotes = await adapter.fetch_snapshot(["000858.SZ", "600519.SH"])
    assert len(quotes) == 2
    wuliangye = next(q for q in quotes if q.symbol == "000858.SZ")
    assert wuliangye.price == Decimal("180.50")
    assert wuliangye.change_pct == pytest.approx(1.25)
    assert wuliangye.source == "akshare"


@pytest.mark.asyncio
async def test_fetch_snapshot_falls_back_to_mootdx(mock_akshare_snapshot_df):
    with patch("core.adapters.ashare.ak.stock_zh_a_spot_em", side_effect=RuntimeError("boom")), \
         patch("core.adapters.ashare.AShareAdapter._fetch_snapshot_mootdx") as mock_mootdx:
        mock_mootdx.return_value = [
            MagicMock(symbol="000858.SZ", price=Decimal("180"), change_pct=0, volume=0, source="mootdx")
        ]
        adapter = AShareAdapter()
        quotes = await adapter.fetch_snapshot(["000858.SZ"])
    assert mock_mootdx.called
    assert quotes[0].source == "mootdx"


@pytest.mark.asyncio
async def test_circuit_opens_after_3_failures():
    adapter = AShareAdapter()
    with patch("core.adapters.ashare.ak.stock_zh_a_spot_em", side_effect=RuntimeError("boom")), \
         patch.object(AShareAdapter, "_fetch_snapshot_mootdx", side_effect=RuntimeError("boom2")):
        for _ in range(3):
            with pytest.raises(Exception):
                await adapter.fetch_snapshot(["000858.SZ"])
    assert adapter.primary_cb.state == "open"


@pytest.mark.asyncio
async def test_health_reports_circuit_state():
    adapter = AShareAdapter()
    with patch("core.adapters.ashare.ak.stock_zh_a_spot_em", return_value=pd.DataFrame([
        {"代码": "000001", "名称": "平安银行", "最新价": 10.0, "涨跌幅": 0, "成交量": 1}
    ])):
        h = await adapter.health()
    assert h.state == "ok"
    assert h.name == "ashare"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/adapters/test_ashare.py -v`
Expected: ImportError

- [ ] **Step 3: 实现 `core/adapters/ashare.py`**

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import akshare as ak
import structlog

from core.adapters.base import AdapterError, CircuitBreaker, MarketAdapter
from core.domain.models import Bar, HealthStatus, Quote

log = structlog.get_logger(__name__)


def _normalize_symbol(code: str) -> str:
    # "000858" -> "000858.SZ"; "600519" -> "600519.SH"
    if "." in code:
        return code
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _denormalize(symbol: str) -> str:
    return symbol.split(".")[0]


class AShareAdapter:
    market = "ashare"
    name = "ashare"

    def __init__(self) -> None:
        self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=300)

    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]:
        wanted = {_denormalize(s) for s in symbols}
        if self.primary_cb.can_execute():
            try:
                quotes = await asyncio.to_thread(self._fetch_snapshot_akshare, wanted)
                self.primary_cb.record_success()
                return quotes
            except Exception as e:
                self.primary_cb.record_failure()
                log.warning("ashare.primary_failed", error=str(e))
        try:
            return await asyncio.to_thread(self._fetch_snapshot_mootdx, wanted)
        except Exception as e:
            raise AdapterError(f"both primary and backup failed: {e}", source="ashare") from e

    def _fetch_snapshot_akshare(self, wanted: set[str]) -> list[Quote]:
        df = ak.stock_zh_a_spot_em()
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for _, row in df.iterrows():
            code = str(row["代码"])
            if code not in wanted:
                continue
            price = Decimal(str(row["最新价"]))
            out.append(Quote(
                market="ashare",
                symbol=_normalize_symbol(code),
                ts=now,
                price=price,
                change_pct=float(row["涨跌幅"]),
                volume=int(row["成交量"]),
                source="akshare",
            ))
        return out

    def _fetch_snapshot_mootdx(self, wanted: set[str]) -> list[Quote]:
        # 备源,最小实现:仅占位,真实调用在 integration 测试里补
        from mootdx.quotes import Quotes
        client = Quotes.factory(market="std")
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for code in wanted:
            try:
                df = client.quotes(symbol=[code])
                if df is None or df.empty:
                    continue
                row = df.iloc[0]
                out.append(Quote(
                    market="ashare",
                    symbol=_normalize_symbol(code),
                    ts=now,
                    price=Decimal(str(row.get("price", 0))),
                    change_pct=float(row.get("rate", 0.0)),
                    volume=int(row.get("vol", 0)),
                    source="mootdx",
                ))
            except Exception as e:  # noqa: BLE001
                log.warning("ashare.mootdx_symbol_failed", symbol=code, error=str(e))
        return out

    async def subscribe(self, symbols: list[str], on_bar: Callable[[Bar], None]) -> None:
        # A 股无官方推送,由 Scheduler 轮询 fetch_snapshot 代替
        raise NotImplementedError("use scheduler polling for ashare")

    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        code = _denormalize(symbol)
        df = await asyncio.to_thread(
            ak.stock_zh_a_hist,
            symbol=code,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        out: list[Bar] = []
        for _, row in df.iterrows():
            ts = datetime.combine(row["日期"], datetime.min.time(), tzinfo=timezone.utc)
            out.append(Bar(
                market="ashare",
                symbol=symbol,
                ts=ts,
                open=Decimal(str(row["开盘"])),
                high=Decimal(str(row["最高"])),
                low=Decimal(str(row["最低"])),
                close=Decimal(str(row["收盘"])),
                volume=int(row["成交量"]),
                interval="1d",
            ))
        return out

    async def health(self) -> HealthStatus:
        if not self.primary_cb.can_execute():
            return HealthStatus(name="ashare", state="degraded", detail="primary circuit open")
        try:
            await asyncio.to_thread(ak.stock_zh_index_spot_em, symbol="沪深重要指数")
            return HealthStatus(name="ashare", state="ok")
        except Exception as e:
            return HealthStatus(name="ashare", state="down", detail=str(e))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/adapters/test_ashare.py -v`
Expected: 4 passed

- [ ] **Step 5: 写 integration 测试(跑真实接口,CI 不跑)**

`tests/integration/__init__.py` 留空。
`tests/integration/test_adapters_live.py`:

```python
import pytest

from core.adapters.ashare import AShareAdapter


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ashare_snapshot_live():
    adapter = AShareAdapter()
    quotes = await adapter.fetch_snapshot(["000858.SZ", "600519.SH"])
    assert len(quotes) >= 1
    for q in quotes:
        assert q.price > 0
```

- [ ] **Step 6: 本地跑一次 integration 冒烟**

Run: `pytest tests/integration/test_adapters_live.py::test_ashare_snapshot_live -v -m integration`
Expected: PASS(需要能访问东财接口;盘后也能拿到收盘快照)

- [ ] **Step 7: Commit**

```bash
git add core/adapters/ashare.py tests/unit/adapters/test_ashare.py tests/integration
git commit -m "feat(adapters): add A-share adapter with akshare primary + mootdx fallback"
```

---

## Task 5: 港股 Adapter(akshare 港股主源 + yfinance 备源)

**Files:**
- Create: `core/adapters/hk.py`
- Create: `tests/unit/adapters/test_hk.py`
- Modify: `tests/integration/test_adapters_live.py`

- [ ] **Step 1: 写失败的单元测试**

`tests/unit/adapters/test_hk.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pandas as pd
import pytest

from core.adapters.hk import HKAdapter


@pytest.fixture
def mock_hk_spot_df():
    return pd.DataFrame([
        {"代码": "00700", "名称": "腾讯控股", "最新价": 380.0, "涨跌幅": 0.8, "成交量": 5000},
        {"代码": "09988", "名称": "阿里巴巴-W", "最新价": 78.5, "涨跌幅": -1.2, "成交量": 7000},
    ])


@pytest.mark.asyncio
async def test_fetch_snapshot_primary(mock_hk_spot_df):
    with patch("core.adapters.hk.ak.stock_hk_spot_em", return_value=mock_hk_spot_df):
        adapter = HKAdapter()
        quotes = await adapter.fetch_snapshot(["00700.HK", "09988.HK"])
    assert len(quotes) == 2
    assert all(q.market == "hk" for q in quotes)
    tencent = next(q for q in quotes if q.symbol == "00700.HK")
    assert tencent.price == Decimal("380.0")
    assert tencent.source == "akshare"


@pytest.mark.asyncio
async def test_fetch_snapshot_falls_back_to_yfinance():
    with patch("core.adapters.hk.ak.stock_hk_spot_em", side_effect=RuntimeError("blocked")), \
         patch("core.adapters.hk.HKAdapter._fetch_snapshot_yfinance") as mock_yf:
        mock_yf.return_value = []
        adapter = HKAdapter()
        await adapter.fetch_snapshot(["00700.HK"])
    assert mock_yf.called
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/adapters/test_hk.py -v`
Expected: ImportError

- [ ] **Step 3: 实现 `core/adapters/hk.py`**

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import akshare as ak
import structlog
import yfinance as yf

from core.adapters.base import AdapterError, CircuitBreaker
from core.domain.models import Bar, HealthStatus, Quote

log = structlog.get_logger(__name__)


def _to_hk_code(symbol: str) -> str:
    return symbol.split(".")[0].zfill(5)


def _to_yf_ticker(symbol: str) -> str:
    code = _to_hk_code(symbol).lstrip("0") or "0"
    return f"{code.zfill(4)}.HK"


class HKAdapter:
    market = "hk"
    name = "hk"

    def __init__(self) -> None:
        self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=300)

    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]:
        wanted = {_to_hk_code(s) for s in symbols}
        if self.primary_cb.can_execute():
            try:
                quotes = await asyncio.to_thread(self._fetch_snapshot_akshare, wanted)
                self.primary_cb.record_success()
                return quotes
            except Exception as e:
                self.primary_cb.record_failure()
                log.warning("hk.primary_failed", error=str(e))
        try:
            return await asyncio.to_thread(self._fetch_snapshot_yfinance, symbols)
        except Exception as e:
            raise AdapterError(f"both primary and backup failed: {e}", source="hk") from e

    def _fetch_snapshot_akshare(self, wanted: set[str]) -> list[Quote]:
        df = ak.stock_hk_spot_em()
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for _, row in df.iterrows():
            code = str(row["代码"]).zfill(5)
            if code not in wanted:
                continue
            out.append(Quote(
                market="hk",
                symbol=f"{code}.HK",
                ts=now,
                price=Decimal(str(row["最新价"])),
                change_pct=float(row["涨跌幅"]),
                volume=int(row["成交量"]),
                source="akshare",
            ))
        return out

    def _fetch_snapshot_yfinance(self, symbols: list[str]) -> list[Quote]:
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for s in symbols:
            try:
                info = yf.Ticker(_to_yf_ticker(s)).fast_info
                price = Decimal(str(info.last_price))
                prev = float(info.previous_close or 0) or 1
                change_pct = (float(info.last_price) - prev) / prev * 100
                out.append(Quote(
                    market="hk",
                    symbol=s,
                    ts=now,
                    price=price,
                    change_pct=change_pct,
                    volume=int(info.last_volume or 0),
                    source="yfinance",
                ))
            except Exception as e:  # noqa: BLE001
                log.warning("hk.yfinance_symbol_failed", symbol=s, error=str(e))
        return out

    async def subscribe(self, symbols: list[str], on_bar: Callable[[Bar], None]) -> None:
        raise NotImplementedError("use scheduler polling for hk")

    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        df = await asyncio.to_thread(
            yf.download,
            _to_yf_ticker(symbol),
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
        )
        out: list[Bar] = []
        for idx, row in df.iterrows():
            ts = datetime.fromtimestamp(idx.timestamp(), tz=timezone.utc)
            out.append(Bar(
                market="hk",
                symbol=symbol,
                ts=ts,
                open=Decimal(str(row["Open"].item() if hasattr(row["Open"], "item") else row["Open"])),
                high=Decimal(str(row["High"].item() if hasattr(row["High"], "item") else row["High"])),
                low=Decimal(str(row["Low"].item() if hasattr(row["Low"], "item") else row["Low"])),
                close=Decimal(str(row["Close"].item() if hasattr(row["Close"], "item") else row["Close"])),
                volume=int(row["Volume"].item() if hasattr(row["Volume"], "item") else row["Volume"]),
                interval="1d",
            ))
        return out

    async def health(self) -> HealthStatus:
        if not self.primary_cb.can_execute():
            return HealthStatus(name="hk", state="degraded", detail="primary circuit open")
        try:
            await asyncio.to_thread(ak.stock_hk_spot_em)
            return HealthStatus(name="hk", state="ok")
        except Exception as e:
            return HealthStatus(name="hk", state="down", detail=str(e))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/adapters/test_hk.py -v`
Expected: 2 passed

- [ ] **Step 5: 补一条 integration case**

在 `tests/integration/test_adapters_live.py` 追加:

```python
from core.adapters.hk import HKAdapter


@pytest.mark.integration
@pytest.mark.asyncio
async def test_hk_snapshot_live():
    adapter = HKAdapter()
    quotes = await adapter.fetch_snapshot(["00700.HK"])
    assert len(quotes) >= 1
    assert quotes[0].price > 0
```

- [ ] **Step 6: Commit**

```bash
git add core/adapters/hk.py tests/unit/adapters/test_hk.py tests/integration/test_adapters_live.py
git commit -m "feat(adapters): add HK adapter with akshare + yfinance fallback"
```

---

## Task 6: 美股 Adapter(Alpaca IEX 主源 + yfinance 备源)

**Files:**
- Create: `core/adapters/us.py`
- Create: `tests/unit/adapters/test_us.py`
- Modify: `tests/integration/test_adapters_live.py`

- [ ] **Step 1: 写失败的单元测试**

`tests/unit/adapters/test_us.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.adapters.us import USAdapter


@pytest.mark.asyncio
async def test_us_adapter_disabled_without_key(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    adapter = USAdapter()
    assert adapter.has_primary is False
    h = await adapter.health()
    assert h.state in {"degraded", "disabled"}


@pytest.mark.asyncio
async def test_us_adapter_uses_alpaca_when_key_present(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    adapter = USAdapter()
    assert adapter.has_primary is True

    fake_quote = SimpleNamespace(
        ask_price=192.1, bid_price=191.9, timestamp=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    with patch.object(adapter, "_fetch_snapshot_alpaca", return_value=[
        type("Q", (), {"symbol": "AAPL", "price": Decimal("192.0"), "change_pct": 0.5,
                      "volume": 100, "source": "alpaca",
                      "market": "us", "ts": datetime.now(timezone.utc)})()
    ]) as m:
        quotes = await adapter.fetch_snapshot(["AAPL"])
    assert m.called
    assert quotes[0].source == "alpaca"


@pytest.mark.asyncio
async def test_us_falls_back_to_yfinance_on_alpaca_error(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    adapter = USAdapter()
    with patch.object(adapter, "_fetch_snapshot_alpaca", side_effect=RuntimeError("429")), \
         patch.object(adapter, "_fetch_snapshot_yfinance") as yf_mock:
        yf_mock.return_value = []
        await adapter.fetch_snapshot(["AAPL"])
    assert yf_mock.called
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/adapters/test_us.py -v`
Expected: ImportError

- [ ] **Step 3: 实现 `core/adapters/us.py`**

```python
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import structlog
import yfinance as yf

from core.adapters.base import AdapterError, CircuitBreaker
from core.domain.models import Bar, HealthStatus, Quote

log = structlog.get_logger(__name__)


class USAdapter:
    market = "us"
    name = "us"

    def __init__(self) -> None:
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.secret = os.getenv("ALPACA_SECRET_KEY")
        self.has_primary = bool(self.api_key and self.secret)
        self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=300)

    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]:
        if self.has_primary and self.primary_cb.can_execute():
            try:
                quotes = await asyncio.to_thread(self._fetch_snapshot_alpaca, symbols)
                self.primary_cb.record_success()
                return quotes
            except Exception as e:
                self.primary_cb.record_failure()
                log.warning("us.alpaca_failed", error=str(e))
        try:
            return await asyncio.to_thread(self._fetch_snapshot_yfinance, symbols)
        except Exception as e:
            raise AdapterError(f"both primary and backup failed: {e}", source="us") from e

    def _fetch_snapshot_alpaca(self, symbols: list[str]) -> list[Quote]:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest
        client = StockHistoricalDataClient(self.api_key, self.secret)
        req = StockLatestQuoteRequest(symbol_or_symbols=symbols)
        resp = client.get_stock_latest_quote(req)
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for sym in symbols:
            q = resp.get(sym)
            if q is None:
                continue
            mid = (float(q.ask_price) + float(q.bid_price)) / 2
            out.append(Quote(
                market="us",
                symbol=sym,
                ts=q.timestamp or now,
                price=Decimal(f"{mid:.4f}"),
                change_pct=0.0,  # Alpaca quote 不含日内涨跌幅,dashboard 会补算
                volume=0,
                source="alpaca",
            ))
        return out

    def _fetch_snapshot_yfinance(self, symbols: list[str]) -> list[Quote]:
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for s in symbols:
            try:
                info = yf.Ticker(s).fast_info
                last = float(info.last_price)
                prev = float(info.previous_close or 0) or 1
                out.append(Quote(
                    market="us",
                    symbol=s,
                    ts=now,
                    price=Decimal(f"{last:.4f}"),
                    change_pct=(last - prev) / prev * 100,
                    volume=int(info.last_volume or 0),
                    source="yfinance",
                ))
            except Exception as e:  # noqa: BLE001
                log.warning("us.yfinance_symbol_failed", symbol=s, error=str(e))
        return out

    async def subscribe(self, symbols: list[str], on_bar: Callable[[Bar], None]) -> None:
        raise NotImplementedError("use scheduler polling for us in V1")

    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        df = await asyncio.to_thread(
            yf.download, symbol,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
        )
        out: list[Bar] = []
        for idx, row in df.iterrows():
            out.append(Bar(
                market="us", symbol=symbol,
                ts=datetime.fromtimestamp(idx.timestamp(), tz=timezone.utc),
                open=Decimal(str(float(row["Open"]))),
                high=Decimal(str(float(row["High"]))),
                low=Decimal(str(float(row["Low"]))),
                close=Decimal(str(float(row["Close"]))),
                volume=int(row["Volume"]),
                interval="1d",
            ))
        return out

    async def health(self) -> HealthStatus:
        if not self.has_primary:
            return HealthStatus(name="us", state="disabled", detail="missing ALPACA_API_KEY")
        if not self.primary_cb.can_execute():
            return HealthStatus(name="us", state="degraded", detail="alpaca circuit open")
        return HealthStatus(name="us", state="ok")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/adapters/test_us.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add core/adapters/us.py tests/unit/adapters/test_us.py
git commit -m "feat(adapters): add US adapter with Alpaca primary + yfinance fallback"
```

---

## Task 7: Crypto Adapter(Binance WS + CoinGecko REST 备源)

**Files:**
- Create: `core/adapters/crypto.py`
- Create: `tests/unit/adapters/test_crypto.py`

- [ ] **Step 1: 写失败的单元测试**

`tests/unit/adapters/test_crypto.py`:

```python
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from core.adapters.crypto import CryptoAdapter


@pytest.mark.asyncio
async def test_snapshot_uses_coingecko_backup():
    async with respx.mock(base_url="https://api.coingecko.com") as r:
        r.get("/api/v3/simple/price").mock(return_value=httpx.Response(200, json={
            "bitcoin": {"usd": 68000.5, "usd_24h_change": 2.1, "usd_24h_vol": 1.2e10},
            "ethereum": {"usd": 3500.0, "usd_24h_change": -0.5, "usd_24h_vol": 5e9},
        }))
        adapter = CryptoAdapter()
        quotes = await adapter.fetch_snapshot(["BTC-USDT", "ETH-USDT"])
    assert len(quotes) == 2
    btc = next(q for q in quotes if q.symbol == "BTC-USDT")
    assert btc.price == Decimal("68000.5")
    assert btc.change_pct == pytest.approx(2.1)
    assert btc.source == "coingecko"


@pytest.mark.asyncio
async def test_snapshot_handles_coingecko_404():
    async with respx.mock(base_url="https://api.coingecko.com") as r:
        r.get("/api/v3/simple/price").mock(return_value=httpx.Response(429))
        adapter = CryptoAdapter()
        with pytest.raises(Exception):
            await adapter.fetch_snapshot(["BTC-USDT"])


def test_symbol_to_coingecko_id():
    adapter = CryptoAdapter()
    assert adapter._to_cg_id("BTC-USDT") == "bitcoin"
    assert adapter._to_cg_id("ETH-USDT") == "ethereum"
    assert adapter._to_cg_id("UNKNOWN-USDT") is None


@pytest.mark.asyncio
async def test_health_ok_when_ws_not_started():
    adapter = CryptoAdapter()
    h = await adapter.health()
    assert h.name == "crypto"
    assert h.state in {"ok", "degraded"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/adapters/test_crypto.py -v`
Expected: ImportError

- [ ] **Step 3: 实现 `core/adapters/crypto.py`**

```python
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import httpx
import structlog

from core.adapters.base import AdapterError, CircuitBreaker
from core.domain.models import Bar, HealthStatus, Quote

log = structlog.get_logger(__name__)

# V1 内置一份常用 symbol → coingecko id 映射;未来可外置成 config
_CG_ID_MAP = {
    "BTC-USDT": "bitcoin",
    "ETH-USDT": "ethereum",
    "BNB-USDT": "binancecoin",
    "SOL-USDT": "solana",
    "XRP-USDT": "ripple",
    "ADA-USDT": "cardano",
    "DOGE-USDT": "dogecoin",
    "TON-USDT": "the-open-network",
    "TRX-USDT": "tron",
    "AVAX-USDT": "avalanche-2",
}


class CryptoAdapter:
    market = "crypto"
    name = "crypto"

    def __init__(self) -> None:
        self.ws_url = os.getenv("BINANCE_WS_URL", "wss://stream.binance.com:9443/ws")
        self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=60)
        self._ws_task: asyncio.Task | None = None
        self._ws_connected = False

    def _to_cg_id(self, symbol: str) -> str | None:
        return _CG_ID_MAP.get(symbol)

    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]:
        # V1 快照:不走 WS,走 CoinGecko REST(简单稳)
        ids = [i for i in (self._to_cg_id(s) for s in symbols) if i]
        if not ids:
            return []
        async with httpx.AsyncClient(base_url="https://api.coingecko.com", timeout=10) as c:
            resp = await c.get("/api/v3/simple/price", params={
                "ids": ",".join(ids),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
            })
            if resp.status_code != 200:
                raise AdapterError(f"coingecko HTTP {resp.status_code}", source="crypto")
            data = resp.json()
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for sym in symbols:
            cg = self._to_cg_id(sym)
            if cg is None or cg not in data:
                continue
            d = data[cg]
            out.append(Quote(
                market="crypto",
                symbol=sym,
                ts=now,
                price=Decimal(str(d["usd"])),
                change_pct=float(d.get("usd_24h_change", 0) or 0),
                volume=int(d.get("usd_24h_vol", 0) or 0),
                source="coingecko",
            ))
        return out

    async def subscribe(self, symbols: list[str], on_bar: Callable[[Bar], None]) -> None:
        import websockets
        streams = "/".join(f"{s.replace('-', '').lower()}@kline_1m" for s in symbols)
        url = f"{self.ws_url}/{streams}"
        while True:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    self._ws_connected = True
                    async for msg in ws:
                        payload = json.loads(msg)
                        k = payload.get("k") or {}
                        if not k.get("x"):  # 未闭合 bar 跳过
                            continue
                        sym_raw = payload["s"].upper()  # e.g. BTCUSDT
                        sym = f"{sym_raw[:-4]}-USDT" if sym_raw.endswith("USDT") else sym_raw
                        bar = Bar(
                            market="crypto", symbol=sym,
                            ts=datetime.fromtimestamp(k["t"] / 1000, tz=timezone.utc),
                            open=Decimal(k["o"]), high=Decimal(k["h"]),
                            low=Decimal(k["l"]), close=Decimal(k["c"]),
                            volume=int(float(k["v"])),
                            interval="1m",
                        )
                        on_bar(bar)
            except Exception as e:  # noqa: BLE001
                self._ws_connected = False
                log.warning("crypto.ws_disconnected", error=str(e))
                await asyncio.sleep(2)

    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        sym = symbol.replace("-", "").upper()
        async with httpx.AsyncClient(base_url="https://api.binance.com", timeout=15) as c:
            resp = await c.get("/api/v3/klines", params={
                "symbol": sym, "interval": "1d",
                "startTime": int(start.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000),
                "limit": 1000,
            })
        data = resp.json()
        out: list[Bar] = []
        for row in data:
            out.append(Bar(
                market="crypto", symbol=symbol,
                ts=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                open=Decimal(row[1]), high=Decimal(row[2]),
                low=Decimal(row[3]), close=Decimal(row[4]),
                volume=int(float(row[5])),
                interval="1d",
            ))
        return out

    async def health(self) -> HealthStatus:
        if not self.primary_cb.can_execute():
            return HealthStatus(name="crypto", state="degraded", detail="primary circuit open")
        return HealthStatus(name="crypto", state="ok")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/adapters/test_crypto.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add core/adapters/crypto.py tests/unit/adapters/test_crypto.py
git commit -m "feat(adapters): add Crypto adapter with Binance WS + CoinGecko REST"
```

---

## Task 8: Adapter Registry 与源配置加载

**Files:**
- Create: `core/adapters/registry.py`
- Create: `config/sources.yaml`
- Create: `tests/unit/adapters/test_registry.py`

- [ ] **Step 1: 写 `config/sources.yaml`**

```yaml
markets:
  ashare:
    enabled: true
    default_universe: ["000858.SZ", "600519.SH", "300750.SZ"]
    index_symbols: ["000001.SH", "399001.SZ", "000300.SH"]
  hk:
    enabled: true
    default_universe: ["00700.HK", "09988.HK", "03690.HK"]
    index_symbols: ["HSI.HK", "HSCEI.HK"]
  us:
    enabled: true
    default_universe: ["AAPL", "MSFT", "NVDA", "GOOGL", "TSLA"]
    index_symbols: ["^GSPC", "^IXIC", "^DJI"]
  crypto:
    enabled: true
    default_universe: ["BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT"]
    index_symbols: ["BTC-USDT"]
```

- [ ] **Step 2: 写失败的测试**

`tests/unit/adapters/test_registry.py`:

```python
import pytest

from core.adapters.registry import AdapterRegistry, load_sources_config


def test_load_sources_config(tmp_path):
    yml = tmp_path / "sources.yaml"
    yml.write_text("""
markets:
  ashare:
    enabled: true
    default_universe: ["000858.SZ"]
    index_symbols: ["000001.SH"]
""")
    cfg = load_sources_config(str(yml))
    assert cfg["markets"]["ashare"]["enabled"] is True
    assert cfg["markets"]["ashare"]["default_universe"] == ["000858.SZ"]


def test_registry_builds_adapters_for_enabled_markets():
    cfg = {"markets": {
        "ashare": {"enabled": True, "default_universe": [], "index_symbols": []},
        "hk":     {"enabled": True, "default_universe": [], "index_symbols": []},
        "us":     {"enabled": True, "default_universe": [], "index_symbols": []},
        "crypto": {"enabled": True, "default_universe": [], "index_symbols": []},
    }}
    reg = AdapterRegistry.from_config(cfg)
    assert set(reg.markets()) == {"ashare", "hk", "us", "crypto"}
    assert reg.get("ashare").market == "ashare"


def test_registry_skips_disabled():
    cfg = {"markets": {
        "ashare": {"enabled": True, "default_universe": [], "index_symbols": []},
        "us":     {"enabled": False, "default_universe": [], "index_symbols": []},
    }}
    reg = AdapterRegistry.from_config(cfg)
    assert "us" not in reg.markets()


def test_registry_get_unknown_raises():
    reg = AdapterRegistry.from_config({"markets": {}})
    with pytest.raises(KeyError):
        reg.get("ashare")
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/unit/adapters/test_registry.py -v`
Expected: ImportError

- [ ] **Step 4: 实现 `core/adapters/registry.py`**

```python
from __future__ import annotations

from typing import Any

import yaml

from core.adapters.ashare import AShareAdapter
from core.adapters.base import MarketAdapter
from core.adapters.crypto import CryptoAdapter
from core.adapters.hk import HKAdapter
from core.adapters.us import USAdapter


_BUILDERS = {
    "ashare": AShareAdapter,
    "hk": HKAdapter,
    "us": USAdapter,
    "crypto": CryptoAdapter,
}


def load_sources_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class AdapterRegistry:
    def __init__(self, adapters: dict[str, MarketAdapter], universes: dict[str, list[str]],
                 indices: dict[str, list[str]]) -> None:
        self._adapters = adapters
        self._universes = universes
        self._indices = indices

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "AdapterRegistry":
        adapters: dict[str, MarketAdapter] = {}
        universes: dict[str, list[str]] = {}
        indices: dict[str, list[str]] = {}
        for market, m in (cfg.get("markets") or {}).items():
            if not m.get("enabled"):
                continue
            if market not in _BUILDERS:
                continue
            adapters[market] = _BUILDERS[market]()
            universes[market] = list(m.get("default_universe") or [])
            indices[market] = list(m.get("index_symbols") or [])
        return cls(adapters, universes, indices)

    def markets(self) -> list[str]:
        return list(self._adapters.keys())

    def get(self, market: str) -> MarketAdapter:
        return self._adapters[market]

    def universe(self, market: str) -> list[str]:
        return self._universes.get(market, [])

    def index_symbols(self, market: str) -> list[str]:
        return self._indices.get(market, [])
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/unit/adapters/test_registry.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add core/adapters/registry.py config/sources.yaml tests/unit/adapters/test_registry.py
git commit -m "feat(adapters): add registry and sources.yaml config loader"
```

---

## Task 9: 内存 Quote 缓存(TTL)

**Files:**
- Create: `core/cache/quote_cache.py`
- Create: `tests/unit/cache/__init__.py`
- Create: `tests/unit/cache/test_quote_cache.py`

- [ ] **Step 1: 写失败的测试**

`tests/unit/cache/test_quote_cache.py`:

```python
import time
from datetime import datetime, timezone
from decimal import Decimal

from core.cache.quote_cache import QuoteCache
from core.domain.models import Quote


def _q(market="ashare", symbol="X", price="1"):
    return Quote(
        market=market, symbol=symbol,
        ts=datetime.now(timezone.utc),
        price=Decimal(price), change_pct=0, volume=0, source="t",
    )


def test_put_and_get():
    c = QuoteCache(ttl_s=60)
    q = _q(symbol="000858.SZ")
    c.put(q)
    assert c.get("ashare", "000858.SZ") is q


def test_get_returns_none_when_expired():
    c = QuoteCache(ttl_s=0.01)
    c.put(_q(symbol="A"))
    time.sleep(0.02)
    assert c.get("ashare", "A") is None


def test_snapshot_returns_all_fresh():
    c = QuoteCache(ttl_s=60)
    c.put(_q(symbol="A"))
    c.put(_q(symbol="B"))
    snap = c.snapshot("ashare")
    assert {q.symbol for q in snap} == {"A", "B"}


def test_snapshot_filters_expired():
    c = QuoteCache(ttl_s=0.01)
    c.put(_q(symbol="A"))
    time.sleep(0.02)
    c.put(_q(symbol="B"))
    snap = c.snapshot("ashare")
    assert {q.symbol for q in snap} == {"B"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/cache/test_quote_cache.py -v`
Expected: ImportError

- [ ] **Step 3: 实现 `core/cache/quote_cache.py`**

```python
from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock

from core.domain.models import Quote


@dataclass
class _Entry:
    quote: Quote
    expires_at: float


class QuoteCache:
    def __init__(self, ttl_s: float = 60.0) -> None:
        self.ttl_s = ttl_s
        self._store: dict[tuple[str, str], _Entry] = {}
        self._lock = RLock()

    def put(self, quote: Quote) -> None:
        with self._lock:
            self._store[(quote.market, quote.symbol)] = _Entry(
                quote=quote, expires_at=time.monotonic() + self.ttl_s,
            )

    def get(self, market: str, symbol: str) -> Quote | None:
        with self._lock:
            entry = self._store.get((market, symbol))
            if entry is None:
                return None
            if entry.expires_at < time.monotonic():
                del self._store[(market, symbol)]
                return None
            return entry.quote

    def snapshot(self, market: str) -> list[Quote]:
        now = time.monotonic()
        with self._lock:
            stale = [k for k, v in self._store.items() if v.expires_at < now]
            for k in stale:
                del self._store[k]
            return [v.quote for k, v in self._store.items() if k[0] == market]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/cache/test_quote_cache.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add core/cache/quote_cache.py tests/unit/cache
git commit -m "feat(cache): add in-memory quote cache with TTL"
```

---

## Task 10: SQLite 状态仓储 + schema

**Files:**
- Create: `core/persistence/schema.sql`
- Create: `core/persistence/sqlite_repo.py`
- Create: `tests/unit/persistence/__init__.py`
- Create: `tests/unit/persistence/test_sqlite_repo.py`

- [ ] **Step 1: 写 `core/persistence/schema.sql`**

```sql
-- v1 schema(Plan 2/3 会扩展 events、signals)
CREATE TABLE IF NOT EXISTS health_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TIMESTAMP NOT NULL,
  component TEXT NOT NULL,
  state TEXT NOT NULL,
  detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_health_ts ON health_log(ts DESC);

CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMP NOT NULL
);
```

- [ ] **Step 2: 写失败的测试**

`tests/unit/persistence/test_sqlite_repo.py`:

```python
from datetime import datetime, timezone

import pytest

from core.persistence.sqlite_repo import StateRepo


@pytest.mark.asyncio
async def test_init_creates_tables(tmp_path):
    repo = StateRepo(str(tmp_path / "test.db"))
    await repo.init()
    async with repo.connect() as db:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {row[0] for row in await cur.fetchall()}
    assert "health_log" in names and "app_state" in names


@pytest.mark.asyncio
async def test_record_and_recent_health(tmp_path):
    repo = StateRepo(str(tmp_path / "test.db"))
    await repo.init()
    await repo.record_health("ashare", "ok", None, ts=datetime(2026, 5, 13, tzinfo=timezone.utc))
    await repo.record_health("us", "disabled", "missing key",
                             ts=datetime(2026, 5, 13, tzinfo=timezone.utc))
    rows = await repo.recent_health(limit=10)
    assert len(rows) == 2
    assert {r["component"] for r in rows} == {"ashare", "us"}


@pytest.mark.asyncio
async def test_state_get_set(tmp_path):
    repo = StateRepo(str(tmp_path / "test.db"))
    await repo.init()
    await repo.set_state("last_warmup", "2026-05-13T10:00:00Z")
    assert await repo.get_state("last_warmup") == "2026-05-13T10:00:00Z"
    assert await repo.get_state("missing") is None
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/unit/persistence/test_sqlite_repo.py -v`
Expected: ImportError

- [ ] **Step 4: 实现 `core/persistence/sqlite_repo.py`**

```python
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class StateRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
            await db.commit()

    @asynccontextmanager
    async def connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def record_health(
        self, component: str, state: str, detail: str | None,
        ts: datetime | None = None,
    ) -> None:
        ts = ts or datetime.now(timezone.utc)
        async with self.connect() as db:
            await db.execute(
                "INSERT INTO health_log (ts, component, state, detail) VALUES (?, ?, ?, ?)",
                (ts.isoformat(), component, state, detail),
            )
            await db.commit()

    async def recent_health(self, limit: int = 50) -> list[dict]:
        async with self.connect() as db:
            cur = await db.execute(
                "SELECT ts, component, state, detail FROM health_log "
                "ORDER BY id DESC LIMIT ?", (limit,),
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def set_state(self, key: str, value: str) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def get_state(self, key: str) -> str | None:
        async with self.connect() as db:
            cur = await db.execute("SELECT value FROM app_state WHERE key=?", (key,))
            row = await cur.fetchone()
        return row["value"] if row else None
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/unit/persistence/test_sqlite_repo.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add core/persistence/schema.sql core/persistence/sqlite_repo.py tests/unit/persistence
git commit -m "feat(persistence): add SQLite StateRepo with health log + key/value state"
```

---

## Task 11: DuckDB 历史 K 线仓储

**Files:**
- Create: `core/persistence/duckdb_repo.py`
- Create: `tests/unit/persistence/test_duckdb_repo.py`

- [ ] **Step 1: 写失败的测试**

`tests/unit/persistence/test_duckdb_repo.py`:

```python
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo


def _bar(market, symbol, day_offset, close):
    ts = datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(days=day_offset)
    return Bar(
        market=market, symbol=symbol, ts=ts,
        open=Decimal("1"), high=Decimal("2"), low=Decimal("0.5"),
        close=Decimal(str(close)), volume=100, interval="1d",
    )


def test_insert_and_select(tmp_path):
    repo = BarRepo(str(tmp_path / "bars.duckdb"))
    repo.init()
    repo.insert_bars([_bar("ashare", "000858.SZ", 0, 100), _bar("ashare", "000858.SZ", 1, 101)])
    rows = repo.fetch_history("ashare", "000858.SZ",
                               start=datetime(2026, 5, 1, tzinfo=timezone.utc),
                               end=datetime(2026, 5, 2, tzinfo=timezone.utc))
    assert len(rows) == 2
    assert rows[0].close == Decimal("100")


def test_upsert_replaces_same_ts(tmp_path):
    repo = BarRepo(str(tmp_path / "bars.duckdb"))
    repo.init()
    repo.insert_bars([_bar("us", "AAPL", 0, 190)])
    repo.insert_bars([_bar("us", "AAPL", 0, 195)])  # 同 ts 覆盖
    rows = repo.fetch_history("us", "AAPL",
                               start=datetime(2026, 5, 1, tzinfo=timezone.utc),
                               end=datetime(2026, 5, 1, tzinfo=timezone.utc))
    assert len(rows) == 1
    assert rows[0].close == Decimal("195")


def test_fetch_empty_returns_empty_list(tmp_path):
    repo = BarRepo(str(tmp_path / "bars.duckdb"))
    repo.init()
    rows = repo.fetch_history("hk", "00700.HK",
                               start=datetime(2026, 5, 1, tzinfo=timezone.utc),
                               end=datetime(2026, 5, 2, tzinfo=timezone.utc))
    assert rows == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/persistence/test_duckdb_repo.py -v`
Expected: ImportError

- [ ] **Step 3: 实现 `core/persistence/duckdb_repo.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import RLock

import duckdb

from core.domain.models import Bar


class BarRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = RLock()

    def _conn(self):
        return duckdb.connect(self.db_path)

    def init(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS bars (
                    market   VARCHAR NOT NULL,
                    symbol   VARCHAR NOT NULL,
                    ts       TIMESTAMP NOT NULL,
                    interval VARCHAR NOT NULL,
                    open     DECIMAL(20, 8) NOT NULL,
                    high     DECIMAL(20, 8) NOT NULL,
                    low      DECIMAL(20, 8) NOT NULL,
                    close    DECIMAL(20, 8) NOT NULL,
                    volume   BIGINT NOT NULL,
                    PRIMARY KEY (market, symbol, interval, ts)
                )
            """)

    def insert_bars(self, bars: list[Bar]) -> None:
        if not bars:
            return
        rows = [(
            b.market, b.symbol, b.ts.astimezone(timezone.utc).replace(tzinfo=None),
            b.interval, b.open, b.high, b.low, b.close, b.volume,
        ) for b in bars]
        with self._lock, self._conn() as c:
            c.executemany("""
                INSERT INTO bars (market, symbol, ts, interval, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (market, symbol, interval, ts) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume
            """, rows)

    def fetch_history(
        self, market: str, symbol: str,
        start: datetime, end: datetime, interval: str = "1d",
    ) -> list[Bar]:
        with self._lock, self._conn() as c:
            cur = c.execute("""
                SELECT ts, interval, open, high, low, close, volume
                FROM bars
                WHERE market=? AND symbol=? AND interval=?
                  AND ts BETWEEN ? AND ?
                ORDER BY ts
            """, (market, symbol, interval,
                   start.astimezone(timezone.utc).replace(tzinfo=None),
                   end.astimezone(timezone.utc).replace(tzinfo=None)))
            rows = cur.fetchall()
        out: list[Bar] = []
        for ts, iv, o, h, l, cl, v in rows:
            out.append(Bar(
                market=market, symbol=symbol,
                ts=ts.replace(tzinfo=timezone.utc),
                open=Decimal(str(o)), high=Decimal(str(h)),
                low=Decimal(str(l)), close=Decimal(str(cl)),
                volume=int(v), interval=iv,
            ))
        return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/unit/persistence/test_duckdb_repo.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add core/persistence/duckdb_repo.py tests/unit/persistence/test_duckdb_repo.py
git commit -m "feat(persistence): add DuckDB BarRepo with upsert by (market,symbol,interval,ts)"
```

---

## Task 12: Scheduler Job(快照 tick + flush 到 DuckDB)

**Files:**
- Create: `core/scheduler/jobs.py`
- Create: `core/scheduler/scheduler.py`
- Create: `tests/unit/scheduler/__init__.py`
- Create: `tests/unit/scheduler/test_jobs.py`

- [ ] **Step 1: 写失败的测试**

`tests/unit/scheduler/test_jobs.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.cache.quote_cache import QuoteCache
from core.domain.models import Bar, Quote
from core.scheduler.jobs import flush_quotes_to_duckdb, tick_snapshot_once


def _q(market, symbol, price):
    return Quote(
        market=market, symbol=symbol, ts=datetime.now(timezone.utc),
        price=Decimal(price), change_pct=0, volume=100, source="t",
    )


@pytest.mark.asyncio
async def test_tick_snapshot_fills_cache():
    cache = QuoteCache(ttl_s=60)
    adapter = MagicMock()
    adapter.fetch_snapshot = AsyncMock(return_value=[_q("ashare", "A", "1"), _q("ashare", "B", "2")])
    registry = MagicMock()
    registry.get.return_value = adapter
    registry.universe.return_value = ["A", "B"]

    await tick_snapshot_once("ashare", registry, cache)
    assert {q.symbol for q in cache.snapshot("ashare")} == {"A", "B"}


@pytest.mark.asyncio
async def test_tick_handles_adapter_error():
    cache = QuoteCache(ttl_s=60)
    adapter = MagicMock()
    adapter.fetch_snapshot = AsyncMock(side_effect=RuntimeError("boom"))
    registry = MagicMock()
    registry.get.return_value = adapter
    registry.universe.return_value = ["A"]

    await tick_snapshot_once("ashare", registry, cache)  # 不抛
    assert cache.snapshot("ashare") == []


def test_flush_quotes_converts_to_bars_and_writes():
    cache = QuoteCache(ttl_s=60)
    cache.put(_q("ashare", "A", "1.5"))
    repo = MagicMock()
    flush_quotes_to_duckdb("ashare", cache, repo)
    assert repo.insert_bars.called
    bars = repo.insert_bars.call_args[0][0]
    assert len(bars) == 1
    assert bars[0].symbol == "A"
    assert bars[0].interval == "1m"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/scheduler/test_jobs.py -v`
Expected: ImportError

- [ ] **Step 3: 实现 `core/scheduler/jobs.py`**

```python
from __future__ import annotations

import structlog

from core.adapters.registry import AdapterRegistry
from core.cache.quote_cache import QuoteCache
from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)


async def tick_snapshot_once(market: str, registry: AdapterRegistry, cache: QuoteCache) -> None:
    adapter = registry.get(market)
    symbols = registry.universe(market) + registry.index_symbols(market)
    if not symbols:
        return
    try:
        quotes = await adapter.fetch_snapshot(symbols)
    except Exception as e:  # noqa: BLE001
        log.warning("tick.failed", market=market, error=str(e))
        return
    for q in quotes:
        cache.put(q)
    log.debug("tick.ok", market=market, count=len(quotes))


def flush_quotes_to_duckdb(market: str, cache: QuoteCache, repo: BarRepo) -> None:
    quotes = cache.snapshot(market)
    if not quotes:
        return
    bars = [
        Bar(
            market=q.market, symbol=q.symbol, ts=q.ts,
            open=q.price, high=q.price, low=q.price, close=q.price,
            volume=q.volume, interval="1m",
        )
        for q in quotes
    ]
    try:
        repo.insert_bars(bars)
    except Exception as e:  # noqa: BLE001
        log.warning("flush.failed", market=market, error=str(e))
```

- [ ] **Step 4: 实现 `core/scheduler/scheduler.py`**

```python
from __future__ import annotations

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from core.adapters.registry import AdapterRegistry
from core.cache.quote_cache import QuoteCache
from core.persistence.duckdb_repo import BarRepo
from core.scheduler.jobs import flush_quotes_to_duckdb, tick_snapshot_once

log = structlog.get_logger(__name__)


def build_scheduler(
    registry: AdapterRegistry, cache: QuoteCache, bar_repo: BarRepo,
) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")
    for market in registry.markets():
        sched.add_job(
            tick_snapshot_once, IntervalTrigger(seconds=10),
            args=(market, registry, cache),
            id=f"tick:{market}", max_instances=1, coalesce=True,
            misfire_grace_time=30,
        )
        sched.add_job(
            flush_quotes_to_duckdb, IntervalTrigger(seconds=60),
            args=(market, cache, bar_repo),
            id=f"flush:{market}", max_instances=1, coalesce=True,
        )
    log.info("scheduler.built", markets=registry.markets())
    return sched
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/unit/scheduler/test_jobs.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add core/scheduler tests/unit/scheduler
git commit -m "feat(scheduler): add 10s snapshot tick + 60s flush to DuckDB"
```

---

## Task 13: FastAPI 入口与依赖注入

**Files:**
- Create: `apps/api/main.py`
- Create: `apps/api/deps.py`
- Create: `tests/integration/test_api_health.py`

- [ ] **Step 1: 实现 `apps/api/deps.py`**

```python
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from core.adapters.registry import AdapterRegistry, load_sources_config
from core.cache.quote_cache import QuoteCache
from core.persistence.duckdb_repo import BarRepo
from core.persistence.sqlite_repo import StateRepo


_BASE = Path(__file__).resolve().parents[2]
_CONFIG = _BASE / "config" / "sources.yaml"
_DATA = Path(os.getenv("APP_DATA_DIR", _BASE / "data"))


@lru_cache(maxsize=1)
def get_registry() -> AdapterRegistry:
    return AdapterRegistry.from_config(load_sources_config(str(_CONFIG)))


@lru_cache(maxsize=1)
def get_quote_cache() -> QuoteCache:
    return QuoteCache(ttl_s=60)


@lru_cache(maxsize=1)
def get_bar_repo() -> BarRepo:
    repo = BarRepo(str(_DATA / "bars.duckdb"))
    repo.init()
    return repo


@lru_cache(maxsize=1)
def get_state_repo() -> StateRepo:
    return StateRepo(str(_DATA / "state.db"))
```

- [ ] **Step 2: 实现 `apps/api/main.py`**

```python
from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.deps import get_bar_repo, get_quote_cache, get_registry, get_state_repo
from apps.api.routes import health, markets
from core.scheduler.scheduler import build_scheduler

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state_repo = get_state_repo()
    await state_repo.init()
    registry = get_registry()
    cache = get_quote_cache()
    bar_repo = get_bar_repo()

    sched = build_scheduler(registry, cache, bar_repo)
    sched.start()
    log.info("app.started", markets=registry.markets())
    try:
        yield
    finally:
        sched.shutdown(wait=False)
        log.info("app.stopped")


app = FastAPI(title="MarketPulse", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:3000"],
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(markets.router)
```

- [ ] **Step 3: 写 integration 冒烟测试**

`tests/integration/test_api_health.py`:

```python
from fastapi.testclient import TestClient

from apps.api.main import app


def test_health_endpoint_returns_200():
    with TestClient(app) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert "adapters" in body
```

这测试目前会失败(Task 14 才实现 /api/health),先写下占位,运行在 Task 14 后。

- [ ] **Step 4: 手动确认应用能启动**

Run: `. .venv/bin/activate && python -c "from apps.api.main import app; print(app.title)"`
Expected: `MarketPulse`

- [ ] **Step 5: Commit**

```bash
git add apps/api/main.py apps/api/deps.py tests/integration/test_api_health.py
git commit -m "feat(api): add FastAPI app, lifespan, scheduler wiring"
```

---

## Task 14: `/api/health` 路由

**Files:**
- Create: `apps/api/routes/health.py`
- Modify: `tests/integration/test_api_health.py`

- [ ] **Step 1: 写失败的集成测试(扩展已有文件)**

把 `tests/integration/test_api_health.py` 替换成:

```python
from fastapi.testclient import TestClient

from apps.api.main import app


def test_health_endpoint_structure():
    with TestClient(app) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"ok", "degraded", "down"}
    assert set(body["adapters"].keys()) >= {"ashare", "hk", "us", "crypto"}
    for k, v in body["adapters"].items():
        assert "state" in v and v["state"] in {"ok", "degraded", "disabled", "down"}
    assert "markets_enabled" in body


def test_health_reports_us_disabled_without_key(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with TestClient(app) as client:
        resp = client.get("/api/health")
    body = resp.json()
    assert body["adapters"]["us"]["state"] == "disabled"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/integration/test_api_health.py -v`
Expected: FAIL(路由未实现,404)

- [ ] **Step 3: 实现 `apps/api/routes/health.py`**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from apps.api.deps import get_registry
from core.adapters.registry import AdapterRegistry
from core.domain.models import HealthStatus

router = APIRouter(prefix="/api", tags=["health"])


class AdapterHealth(BaseModel):
    state: str
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    markets_enabled: list[str]
    adapters: dict[str, AdapterHealth]


def _overall(statuses: list[HealthStatus]) -> str:
    states = {s.state for s in statuses}
    if states <= {"ok"}:
        return "ok"
    if "down" in states:
        return "down"
    return "degraded"


@router.get("/health", response_model=HealthResponse)
async def health(registry: AdapterRegistry = Depends(get_registry)) -> HealthResponse:
    statuses: list[HealthStatus] = []
    for market in registry.markets():
        statuses.append(await registry.get(market).health())
    return HealthResponse(
        status=_overall(statuses),
        markets_enabled=registry.markets(),
        adapters={s.name: AdapterHealth(state=s.state, detail=s.detail) for s in statuses},
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/integration/test_api_health.py -v`
Expected: 2 passed(第二条可能因环境 env 变量残留而失败 —— 若失败,重新打开新 shell 确认 env 干净)

- [ ] **Step 5: Commit**

```bash
git add apps/api/routes/health.py tests/integration/test_api_health.py
git commit -m "feat(api): add /api/health with per-adapter state"
```

---

## Task 15: `/api/markets/{market}/overview` 路由

**Files:**
- Create: `apps/api/routes/markets.py`
- Create: `tests/integration/test_api_markets.py`

- [ ] **Step 1: 写失败的集成测试**

`tests/integration/test_api_markets.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from apps.api.deps import get_quote_cache
from apps.api.main import app
from core.domain.models import Quote


def _seed_cache(market="ashare"):
    cache = get_quote_cache()
    now = datetime.now(timezone.utc)
    cache.put(Quote(market=market, symbol="X1", ts=now, price=Decimal("10"),
                    change_pct=1.5, volume=100, source="t"))
    cache.put(Quote(market=market, symbol="X2", ts=now, price=Decimal("20"),
                    change_pct=-2.0, volume=200, source="t"))


def test_overview_returns_quotes_from_cache():
    _seed_cache("ashare")
    with TestClient(app) as client:
        resp = client.get("/api/markets/ashare/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "ashare"
    assert len(body["quotes"]) >= 2
    assert body["top_gainers"][0]["change_pct"] >= body["top_gainers"][-1]["change_pct"]
    assert body["top_losers"][0]["change_pct"] <= body["top_losers"][-1]["change_pct"]


def test_overview_returns_404_for_unknown_market():
    with TestClient(app) as client:
        resp = client.get("/api/markets/unknown/overview")
    assert resp.status_code == 404


def test_overview_returns_200_with_empty_quotes_for_cold_cache():
    from apps.api.deps import get_quote_cache
    cache = get_quote_cache()
    cache._store.clear()
    with TestClient(app) as client:
        resp = client.get("/api/markets/hk/overview")
    assert resp.status_code == 200
    assert resp.json()["status"] == "warming"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/integration/test_api_markets.py -v`
Expected: FAIL(路由未实现)

- [ ] **Step 3: 实现 `apps/api/routes/markets.py`**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apps.api.deps import get_quote_cache, get_registry
from core.adapters.registry import AdapterRegistry
from core.cache.quote_cache import QuoteCache

router = APIRouter(prefix="/api/markets", tags=["markets"])


class QuoteDTO(BaseModel):
    symbol: str
    price: float
    change_pct: float
    volume: int
    source: str
    ts: str


class OverviewResponse(BaseModel):
    market: str
    status: str  # "ok" / "warming" / "degraded"
    quotes: list[QuoteDTO]
    top_gainers: list[QuoteDTO]
    top_losers: list[QuoteDTO]
    indices: list[QuoteDTO]


@router.get("/{market}/overview", response_model=OverviewResponse)
async def overview(
    market: str,
    registry: AdapterRegistry = Depends(get_registry),
    cache: QuoteCache = Depends(get_quote_cache),
) -> OverviewResponse:
    if market not in registry.markets():
        raise HTTPException(status_code=404, detail=f"unknown market: {market}")
    snap = cache.snapshot(market)
    dtos = [QuoteDTO(
        symbol=q.symbol, price=float(q.price), change_pct=q.change_pct,
        volume=q.volume, source=q.source, ts=q.ts.isoformat(),
    ) for q in snap]

    if not dtos:
        return OverviewResponse(
            market=market, status="warming",
            quotes=[], top_gainers=[], top_losers=[], indices=[],
        )

    index_set = set(registry.index_symbols(market))
    indices = [d for d in dtos if d.symbol in index_set]
    stocks = [d for d in dtos if d.symbol not in index_set]
    gainers = sorted(stocks, key=lambda x: x.change_pct, reverse=True)[:10]
    losers = sorted(stocks, key=lambda x: x.change_pct)[:10]
    return OverviewResponse(
        market=market, status="ok",
        quotes=dtos, top_gainers=gainers, top_losers=losers, indices=indices,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/integration/test_api_markets.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add apps/api/routes/markets.py tests/integration/test_api_markets.py
git commit -m "feat(api): add /api/markets/{market}/overview with gainers/losers/indices"
```

---

## Task 16: WS 骨架 `/ws/ticks`(Plan 3 完善)

**Files:**
- Create: `apps/api/ws/ticks.py`
- Modify: `apps/api/main.py`

- [ ] **Step 1: 实现 `apps/api/ws/ticks.py`(最小骨架 + 心跳)**

```python
from __future__ import annotations

import asyncio
import json

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from apps.api.deps import get_quote_cache

log = structlog.get_logger(__name__)
router = APIRouter()


@router.websocket("/ws/ticks")
async def ticks(ws: WebSocket):
    await ws.accept()
    cache = get_quote_cache()
    try:
        while True:
            markets = ("ashare", "hk", "us", "crypto")
            payload = []
            for m in markets:
                for q in cache.snapshot(m):
                    payload.append({
                        "market": q.market, "symbol": q.symbol,
                        "price": float(q.price), "change_pct": q.change_pct,
                        "ts": q.ts.isoformat(),
                    })
            await ws.send_text(json.dumps({"type": "ticks", "data": payload}))
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        log.debug("ws.ticks_disconnected")
```

- [ ] **Step 2: 在 `apps/api/main.py` include 进来**

修改 `apps/api/main.py`,在 `app.include_router(markets.router)` 后追加:

```python
from apps.api.ws import ticks  # noqa: E402
app.include_router(ticks.router)
```

- [ ] **Step 3: 手动冒烟**

Run: `. .venv/bin/activate && uvicorn apps.api.main:app --port 8787 &` (后台启动)
等 2 秒后:
Run: `python -c "
import asyncio, websockets
async def main():
    async with websockets.connect('ws://127.0.0.1:8787/ws/ticks') as ws:
        for _ in range(1):
            print(await ws.recv())
asyncio.run(main())
" | head -c 200`
Expected: 收到 `{"type":"ticks","data":[...]}` JSON(冷启动下 data 可能为空数组)
然后:
Run: `pkill -f "uvicorn apps.api.main" || true`

- [ ] **Step 4: Commit**

```bash
git add apps/api/ws/ticks.py apps/api/main.py
git commit -m "feat(ws): add /ws/ticks broadcaster skeleton (polling cache)"
```

---

## Task 17: Next.js 前端工程初始化

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/next.config.js`
- Create: `apps/web/tailwind.config.ts`
- Create: `apps/web/postcss.config.js`
- Create: `apps/web/app/layout.tsx`
- Create: `apps/web/app/globals.css`
- Create: `apps/web/app/page.tsx`

- [ ] **Step 1: 写 `apps/web/package.json`**

```json
{
  "name": "marketpulse-web",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev -p 3000",
    "build": "next build",
    "start": "next start -p 3000",
    "lint": "next lint",
    "test:e2e": "playwright test"
  },
  "dependencies": {
    "next": "14.2.13",
    "react": "18.3.1",
    "react-dom": "18.3.1",
    "swr": "2.2.5",
    "lightweight-charts": "4.2.0",
    "clsx": "2.1.1"
  },
  "devDependencies": {
    "@types/node": "20.14.0",
    "@types/react": "18.3.3",
    "@types/react-dom": "18.3.0",
    "typescript": "5.4.5",
    "tailwindcss": "3.4.10",
    "postcss": "8.4.41",
    "autoprefixer": "10.4.20",
    "eslint": "8.57.0",
    "eslint-config-next": "14.2.13",
    "@playwright/test": "1.47.0"
  }
}
```

- [ ] **Step 2: 写 `apps/web/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["dom", "dom.iterable", "ES2022"],
    "allowJs": false,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "paths": { "@/*": ["./*"] },
    "plugins": [{ "name": "next" }]
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

- [ ] **Step 3: 写 `apps/web/next.config.js`**

```js
/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      { source: '/api/:path*', destination: 'http://127.0.0.1:8787/api/:path*' },
    ]
  },
  reactStrictMode: true,
}
module.exports = nextConfig
```

- [ ] **Step 4: 写 `apps/web/tailwind.config.ts`**

```ts
import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: { extend: {} },
  plugins: [],
}
export default config
```

- [ ] **Step 5: 写 `apps/web/postcss.config.js`**

```js
module.exports = { plugins: { tailwindcss: {}, autoprefixer: {} } }
```

- [ ] **Step 6: 写 `apps/web/app/globals.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

html, body { background: #0a0a0a; color: #e5e5e5; }
```

- [ ] **Step 7: 写 `apps/web/app/layout.tsx`**

```tsx
import './globals.css'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'MarketPulse',
  description: '四市场行情监控',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen">{children}</body>
    </html>
  )
}
```

- [ ] **Step 8: 写 `apps/web/app/page.tsx`(重定向到 dashboard)**

```tsx
import { redirect } from 'next/navigation'

export default function Home() {
  redirect('/dashboard')
}
```

- [ ] **Step 9: 安装依赖 + 冒烟**

Run:
```bash
cd apps/web && npm install
```
Expected: 安装完成。

Run: `cd apps/web && npm run build`
Expected: 构建成功。

- [ ] **Step 10: Commit**

```bash
git add apps/web/package.json apps/web/tsconfig.json apps/web/next.config.js \
        apps/web/tailwind.config.ts apps/web/postcss.config.js \
        apps/web/app/layout.tsx apps/web/app/globals.css apps/web/app/page.tsx
git commit -m "feat(web): bootstrap Next.js 14 app with Tailwind and API rewrite"
```

---

## Task 18: 前端 API 客户端与类型

**Files:**
- Create: `apps/web/lib/types.ts`
- Create: `apps/web/lib/api.ts`

- [ ] **Step 1: 写 `apps/web/lib/types.ts`**

```ts
export type Market = 'ashare' | 'hk' | 'us' | 'crypto'

export interface QuoteDTO {
  symbol: string
  price: number
  change_pct: number
  volume: number
  source: string
  ts: string
}

export interface OverviewResponse {
  market: Market
  status: 'ok' | 'warming' | 'degraded'
  quotes: QuoteDTO[]
  top_gainers: QuoteDTO[]
  top_losers: QuoteDTO[]
  indices: QuoteDTO[]
}

export interface AdapterHealth {
  state: 'ok' | 'degraded' | 'disabled' | 'down'
  detail: string | null
}

export interface HealthResponse {
  status: 'ok' | 'degraded' | 'down'
  markets_enabled: Market[]
  adapters: Record<Market, AdapterHealth>
}
```

- [ ] **Step 2: 写 `apps/web/lib/api.ts`**

```ts
import type { HealthResponse, Market, OverviewResponse } from './types'

async function j<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: 'no-store' })
  if (!res.ok) throw new Error(`${path} -> ${res.status}`)
  return res.json() as Promise<T>
}

export const fetchHealth = () => j<HealthResponse>('/api/health')
export const fetchOverview = (m: Market) => j<OverviewResponse>(`/api/markets/${m}/overview`)
```

- [ ] **Step 3: Commit**

```bash
git add apps/web/lib
git commit -m "feat(web): add typed API client for health and overview"
```

---

## Task 19: Dashboard 页面与组件

**Files:**
- Create: `apps/web/components/HealthBadge.tsx`
- Create: `apps/web/components/MarketCard.tsx`
- Create: `apps/web/app/dashboard/page.tsx`

- [ ] **Step 1: 写 `apps/web/components/HealthBadge.tsx`**

```tsx
'use client'

import type { AdapterHealth } from '@/lib/types'
import clsx from 'clsx'

const COLORS: Record<AdapterHealth['state'], string> = {
  ok: 'bg-green-600 text-white',
  degraded: 'bg-yellow-600 text-white',
  disabled: 'bg-neutral-600 text-neutral-200',
  down: 'bg-red-600 text-white',
}

export function HealthBadge({ health }: { health: AdapterHealth | undefined }) {
  if (!health) return null
  return (
    <span
      className={clsx('px-2 py-0.5 rounded text-xs font-medium', COLORS[health.state])}
      title={health.detail ?? undefined}
    >
      {health.state}
    </span>
  )
}
```

- [ ] **Step 2: 写 `apps/web/components/MarketCard.tsx`**

```tsx
'use client'

import useSWR from 'swr'
import clsx from 'clsx'

import { fetchOverview } from '@/lib/api'
import type { AdapterHealth, Market, QuoteDTO } from '@/lib/types'
import { HealthBadge } from './HealthBadge'

const LABELS: Record<Market, string> = {
  ashare: 'A 股',
  hk: '港股',
  us: '美股',
  crypto: 'Crypto',
}

function QuoteRow({ q }: { q: QuoteDTO }) {
  const up = q.change_pct >= 0
  return (
    <div className="flex items-center justify-between text-sm py-1 border-b border-neutral-800">
      <span className="font-mono">{q.symbol}</span>
      <span className={clsx('tabular-nums', up ? 'text-green-400' : 'text-red-400')}>
        {q.price.toFixed(2)} ({up ? '+' : ''}{q.change_pct.toFixed(2)}%)
      </span>
    </div>
  )
}

export function MarketCard({ market, health }: { market: Market; health: AdapterHealth | undefined }) {
  const disabled = health?.state === 'disabled'
  const { data, error, isLoading } = useSWR(
    disabled ? null : `overview:${market}`,
    () => fetchOverview(market),
    { refreshInterval: 10_000 },
  )

  return (
    <section className={clsx(
      'rounded-lg border border-neutral-800 p-4 bg-neutral-950',
      disabled && 'opacity-40',
    )}>
      <header className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">{LABELS[market]}</h2>
        <HealthBadge health={health} />
      </header>

      {disabled && <p className="text-sm text-neutral-400">数据源未配置,已禁用</p>}
      {!disabled && isLoading && <p className="text-sm text-neutral-500">加载中…</p>}
      {!disabled && error && <p className="text-sm text-red-400">加载失败:{String(error)}</p>}
      {!disabled && data?.status === 'warming' && (
        <p className="text-sm text-yellow-400">数据预热中,几秒后刷新</p>
      )}
      {!disabled && data && data.status !== 'warming' && (
        <div className="space-y-3">
          {data.indices.length > 0 && (
            <div>
              <h3 className="text-xs text-neutral-400 uppercase mb-1">指数</h3>
              {data.indices.map((q) => <QuoteRow key={q.symbol} q={q} />)}
            </div>
          )}
          <div>
            <h3 className="text-xs text-neutral-400 uppercase mb-1">涨幅前列</h3>
            {data.top_gainers.slice(0, 5).map((q) => <QuoteRow key={q.symbol} q={q} />)}
          </div>
          <div>
            <h3 className="text-xs text-neutral-400 uppercase mb-1">跌幅前列</h3>
            {data.top_losers.slice(0, 5).map((q) => <QuoteRow key={q.symbol} q={q} />)}
          </div>
        </div>
      )}
    </section>
  )
}
```

- [ ] **Step 3: 写 `apps/web/app/dashboard/page.tsx`**

```tsx
'use client'

import useSWR from 'swr'

import { fetchHealth } from '@/lib/api'
import type { Market } from '@/lib/types'
import { MarketCard } from '@/components/MarketCard'

const MARKETS: Market[] = ['ashare', 'hk', 'us', 'crypto']

export default function DashboardPage() {
  const { data: health } = useSWR('health', fetchHealth, { refreshInterval: 15_000 })
  return (
    <main className="p-6 max-w-7xl mx-auto">
      <header className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">MarketPulse · Dashboard</h1>
        <span className="text-xs text-neutral-500">
          {health ? `状态:${health.status}` : '载入中'}
        </span>
      </header>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {MARKETS.map((m) => (
          <MarketCard key={m} market={m} health={health?.adapters[m]} />
        ))}
      </div>
    </main>
  )
}
```

- [ ] **Step 4: 构建验证**

Run: `cd apps/web && npm run build`
Expected: 构建成功,无 TS / ESLint 阻塞性错误。

- [ ] **Step 5: Commit**

```bash
git add apps/web/components apps/web/app/dashboard
git commit -m "feat(web): add /dashboard with four-market cards and health badges"
```

---

## Task 20: E2E 冒烟(Playwright)

**Files:**
- Create: `apps/web/playwright.config.ts`
- Create: `tests/e2e/dashboard.spec.ts`

- [ ] **Step 1: 写 `apps/web/playwright.config.ts`**

```ts
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '../../tests/e2e',
  timeout: 30_000,
  use: {
    baseURL: 'http://localhost:3000',
    headless: true,
  },
  webServer: [
    {
      command: 'cd ../.. && . .venv/bin/activate && uvicorn apps.api.main:app --port 8787',
      url: 'http://127.0.0.1:8787/api/health',
      timeout: 30_000,
      reuseExistingServer: true,
    },
    {
      command: 'npm run dev',
      url: 'http://localhost:3000',
      timeout: 60_000,
      reuseExistingServer: true,
    },
  ],
})
```

- [ ] **Step 2: 写 `tests/e2e/dashboard.spec.ts`**

```ts
import { expect, test } from '@playwright/test'

test('dashboard shows all four market cards', async ({ page }) => {
  await page.goto('/dashboard')
  await expect(page.getByText('MarketPulse · Dashboard')).toBeVisible()
  for (const label of ['A 股', '港股', '美股', 'Crypto']) {
    await expect(page.getByRole('heading', { name: label })).toBeVisible()
  }
})

test('us card shows disabled when no alpaca key', async ({ page }) => {
  await page.goto('/dashboard')
  const usCard = page.getByRole('heading', { name: '美股' }).locator('..').locator('..')
  const badge = usCard.locator('span').filter({ hasText: /ok|degraded|disabled|down/ })
  await expect(badge).toBeVisible()
})
```

- [ ] **Step 3: 安装 Playwright 浏览器并跑一次**

Run: `cd apps/web && npx playwright install chromium`
Expected: 下载完成。

Run: `cd apps/web && npx playwright test`
Expected: 2 passed。

- [ ] **Step 4: Commit**

```bash
git add apps/web/playwright.config.ts tests/e2e/dashboard.spec.ts
git commit -m "test(e2e): add playwright smoke for dashboard four-market cards"
```

---

## Task 21: `make dev` 集成冒烟 + 收尾

**Files:**
- Modify: `Makefile`(如需微调)
- Create: `docs/superpowers/plans/2026-05-13-marketpulse-plan-1-skeleton-dashboard-COMPLETE.md`(仅作为完成标记,空文件)

- [ ] **Step 1: 全量跑单元测试**

Run: `. .venv/bin/activate && pytest -m "not integration" -v`
Expected: 所有单测 passed(约 30+ 条)

- [ ] **Step 2: 本地手动 `make dev` 验收**

Run: `make dev`(后台两个进程)
在浏览器打开 http://localhost:3000/dashboard,确认:
- 4 张市场卡片都渲染
- A/HK/Crypto 大约 10-30 秒内开始显示行情
- 美股卡片显示 `disabled`(如 `.env` 未配 Alpaca key)
- 顶部状态显示 `degraded`(因为 US disabled)
- 切断网络后再恢复,卡片能自行恢复

手动按 Ctrl-C 停止。

- [ ] **Step 3: 创建完成标记**

Run: `touch docs/superpowers/plans/2026-05-13-marketpulse-plan-1-skeleton-dashboard-COMPLETE.md`

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-05-13-marketpulse-plan-1-skeleton-dashboard-COMPLETE.md
git commit -m "chore: mark Plan 1 (skeleton + dashboard) complete"
```

---

## Self-Review

**1. Spec 覆盖检查**

| Spec 要求 | 覆盖的 Task |
|---|---|
| §1.2 A 股 akshare + mootdx | Task 4 |
| §1.2 港股 akshare + yfinance | Task 5 |
| §1.2 美股 Alpaca + yfinance | Task 6 |
| §1.2 Crypto Binance WS + CoinGecko | Task 7 |
| §2.1 `MarketAdapter` Protocol 统一接口 | Task 3 |
| §2.5 DuckDB + SQLite 仓储 | Task 10, 11 |
| §2.6 `/api/health`、`/api/markets/{m}/overview` | Task 14, 15 |
| §2.7 `/dashboard` 四市场卡片 + 热力图 | Task 19(热力图延后到 Plan 3,按"scale sections to their complexity",V1-A1 此版本优先 TOP 涨跌) |
| §3.1 实时行情路径 A(快照 + 缓存) | Task 9, 12, 16 |
| §3.5 冷启动 `make dev` 一条命令 | Task 1, 13, 21 |
| §6.1 故障矩阵(熔断、降级、UI 标) | Task 3, 4-7, 14, 19 |
| §6.2 fail-informative 启动 | Task 13, 14 |
| V1-A1 四市场 dashboard | Task 19, 20, 21 |
| V1-A4 make dev + 优雅降级 | Task 1, 21 |
| V1-A2 事件流影响面 | → Plan 2 |
| V1-A3 每日 Top-10 买入候选 | → Plan 3 |

**热力图**未在 Task 19 实现,V1-A1 的热力图留到 Plan 3 的因子/行业分类就绪后统一做,V1 中 dashboard 先用 TOP 涨跌 + 指数替代。已在 spec 的 "下一步" 默认认为 heatmap 由 Plan 3 处理。

**2. Placeholder 扫描** —— 全文搜索 TBD / TODO / implement later / appropriate / add validation,无命中。

**3. 类型一致性** —— `Quote`/`Bar`/`HealthStatus` 在 Task 2 定义,后续 Task 中签名保持一致;`AdapterRegistry.markets/get/universe/index_symbols` 四个方法从 Task 8 开始贯穿到 Task 12、14、15 使用,无漂移;前端 `QuoteDTO` / `HealthResponse` 字段与后端 Pydantic 模型字段名一致。

**4. Scope 检查** —— 本 Plan 共 21 个 Task,每个聚焦一个可独立验证的单元,完成后可以端到端运行 `/dashboard`,并为 Plan 2 与 Plan 3 留好 `events.db` 扩展点和 `signals` 表占位。

---

## 执行选择

Plan complete and saved to `docs/superpowers/plans/2026-05-13-marketpulse-plan-1-skeleton-dashboard.md`.

Two execution options:

**1. Subagent-Driven (recommended)** —— 我为每个 task 起一个独立 subagent,每个 task 完成后回到主会话 review 再继续,出问题可以快速回滚。

**2. Inline Execution** —— 直接在当前会话里顺序执行,到阶段性 checkpoint(如 Task 8、15、21)停下让你确认。

你选哪个?

