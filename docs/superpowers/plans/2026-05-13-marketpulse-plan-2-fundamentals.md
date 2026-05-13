# MarketPulse Plan 2: 基建夯实(A 股,K 线 + 板块 + 关注 + 资金流)

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking. 严格按顺序 TDD,每个 task 跑通后 commit。

**Goal:** 在 Plan 1 的 Dashboard 基础上,把 A 股的"数据底座"打通 —— 历史 K 线(日/周/月/分时)、板块成分入库、自定义关注列表、资金流采集(个股/板块/北向)。这一层是 Plan 3 事件 / Plan 4 因子的必要前置。

**Architecture:** 复用 Plan 1 的 FastAPI + APScheduler + DuckDB + SQLite + Next.js 骨架。在 `core/` 新增 4 个 service(`kline`、`sector`、`watchlist`、`fund_flow`),`apps/api/routes/` 新增 4 个路由,`apps/web/app/` 新增 4 个页面,`apps/warmup.py` 加 CLI。

**Tech Stack:** Python(akshare sina_hist / sina hq / stock_sector_detail / fund_flow_*)、pandas resample、aiosqlite、duckdb;前端 TradingView Lightweight Charts(已装)、TanStack Table、shadcn/ui Command。

**参考 spec:** `docs/superpowers/specs/2026-05-13-marketpulse-design.md`(v1.1,§2.9-2.12 / §3.6-3.8 / §7.4)

**只做 A 股,先不动 H 股 / 美股 / Crypto。** 港股 / 美股 / Crypto 的 K 线/板块/资金流功能留后续 plan。

---

## File Structure

新增文件(共 ~40 个),按层组织:

**Domain 扩展:**
- Modify: `core/domain/models.py` —— 新增 `Watchlist`、`WatchlistItem`、`Sector`、`SectorConstituent`、`FundFlowSnapshot` 五个 dataclass

**Service 层(纯业务,Plan 2 重点):**
- Create: `core/services/kline_service.py` —— K 线统一访问层(DuckDB → adapter 缺口补齐),周/月聚合
- Create: `core/services/sector_service.py` —— sina 行业 + 板块成分 CRUD
- Create: `core/services/watchlist_service.py` —— 自定义关注 CRUD
- Create: `core/services/fund_flow_service.py` —— 个股/板块/北向资金流采集

**Adapter 扩展:**
- Modify: `core/adapters/ashare.py` —— `fetch_history` 改走 sina_hist(避东财);新增 `fetch_intraday(symbol, freq)` 拉 5/15/30/60min

**Persistence 扩展:**
- Modify: `core/persistence/schema.sql` —— 加 5 张表
- Create: `core/persistence/watchlist_repo.py`
- Create: `core/persistence/sector_repo.py`
- Create: `core/persistence/fund_flow_repo.py`
- Modify: `core/persistence/duckdb_repo.py` —— 加 `delete_old_intraday()` 方法

**Scheduler 扩展:**
- Modify: `core/scheduler/scheduler.py` —— 注册 4 类新 job
- Create: `core/scheduler/fundamentals_jobs.py` —— 各类资金流和板块成分刷新 job

**API 层:**
- Create: `apps/api/routes/symbols.py` —— `/api/symbols/{sym}/bars`、`/profile`、`/fund_flow`
- Create: `apps/api/routes/sectors.py` —— `/api/sectors`(已有 hot 走 `market_extras`,这里给详情)、`/api/sectors/{name}/constituents`、`/api/sectors/{name}/fund_flow`
- Create: `apps/api/routes/watchlists.py` —— `/api/watchlists/*`
- Create: `apps/api/routes/north_flow.py` —— `/api/north_flow`

**CLI:**
- Create: `apps/warmup.py` —— `python -m apps.warmup [--symbols=...] [--days=365]`
- Modify: `Makefile` —— 加 `make warmup`

**前端页面:**
- Create: `apps/web/app/symbol/[code]/page.tsx`
- Create: `apps/web/app/sector/[name]/page.tsx`
- Create: `apps/web/app/watchlist/page.tsx`
- Create: `apps/web/app/settings/page.tsx`
- Create: `apps/web/components/KLineChart.tsx` —— TradingView Lightweight Charts 封装
- Create: `apps/web/components/FundFlowPanel.tsx`
- Create: `apps/web/components/SymbolSearch.tsx` —— shadcn Command
- Create: `apps/web/components/WatchlistTable.tsx` —— TanStack Table
- Modify: `apps/web/app/layout.tsx` —— 加导航条
- Modify: `apps/web/lib/types.ts` —— 新增类型
- Create: `apps/web/lib/symbol_api.ts`、`apps/web/lib/sector_api.ts`、`apps/web/lib/watchlist_api.ts`、`apps/web/lib/fund_flow_api.ts`

**测试:**
- Create: `tests/unit/services/test_kline_service.py`、`test_sector_service.py`、`test_watchlist_service.py`、`test_fund_flow_service.py`
- Create: `tests/unit/persistence/test_watchlist_repo.py`、`test_sector_repo.py`、`test_fund_flow_repo.py`
- Create: `tests/integration/test_api_symbols.py`、`test_api_sectors.py`、`test_api_watchlists.py`

---

## Task 1: Domain 模型扩展

**Files:**
- Modify: `core/domain/models.py`
- Modify: `tests/unit/test_domain_models.py`

- [ ] **Step 1: 在 `core/domain/models.py` 末尾追加**

```python
@dataclass(frozen=True, slots=True)
class Watchlist:
    id: int
    name: str
    is_archived: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WatchlistItem:
    watchlist_id: int
    symbol: str
    added_at: datetime


@dataclass(frozen=True, slots=True)
class Sector:
    name: str                  # 如 "玻璃行业"
    classification: str        # "sina"
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SectorConstituent:
    sector_name: str
    symbol: str


@dataclass(frozen=True, slots=True)
class FundFlowSnapshot:
    """一次时间点的资金流,主体可以是 symbol、sector_name 或 "north"。"""
    subject: str               # symbol / sector_name / "north"
    kind: Literal["symbol", "sector", "north"]
    ts: datetime
    main_net: float | None = None
    super_large_net: float | None = None
    large_net: float | None = None
    medium_net: float | None = None
    small_net: float | None = None
    pct_change: float | None = None    # sector 才有
    hgt_net: float | None = None       # north 才有
    sgt_net: float | None = None       # north 才有
```

- [ ] **Step 2: 在 `tests/unit/test_domain_models.py` 追加**

```python
from core.domain.models import (
    Watchlist, WatchlistItem, Sector, SectorConstituent, FundFlowSnapshot,
)


def test_watchlist_default_archived_false():
    w = Watchlist(id=1, name="我的关注", is_archived=False,
                  created_at=datetime.now(timezone.utc))
    assert w.name == "我的关注"
    assert w.is_archived is False


def test_watchlist_item_keys():
    item = WatchlistItem(watchlist_id=1, symbol="000858.SZ",
                          added_at=datetime.now(timezone.utc))
    assert item.symbol == "000858.SZ"


def test_sector_constituent():
    s = SectorConstituent(sector_name="玻璃行业", symbol="600660.SH")
    assert s.sector_name == "玻璃行业"


def test_fund_flow_snapshot_symbol_kind():
    f = FundFlowSnapshot(
        subject="600519.SH", kind="symbol",
        ts=datetime.now(timezone.utc),
        main_net=1_000_000.0, super_large_net=800_000.0,
    )
    assert f.kind == "symbol" and f.main_net == 1_000_000.0


def test_fund_flow_snapshot_north_kind():
    f = FundFlowSnapshot(
        subject="north", kind="north",
        ts=datetime.now(timezone.utc),
        hgt_net=5e8, sgt_net=3e8,
    )
    assert f.kind == "north"
```

- [ ] **Step 3: 跑测试**

```bash
cd /Users/xiangrong/stock/marketpulse && . .venv/bin/activate && pytest tests/unit/test_domain_models.py -v
```
Expected: 10 passed(原 5 + 新 5)

- [ ] **Step 4: Commit**

```bash
git add core/domain/models.py tests/unit/test_domain_models.py
git commit -m "feat(domain): add Watchlist/Sector/FundFlowSnapshot models (plan 2 task 1)"
```

---

## Task 2: SQLite Schema 扩展

**Files:**
- Modify: `core/persistence/schema.sql`

- [ ] **Step 1: 在 `core/persistence/schema.sql` 末尾追加**

```sql
-- Plan 2: 自定义关注
CREATE TABLE IF NOT EXISTS watchlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  is_archived INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist_items (
  watchlist_id INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  added_at TIMESTAMP NOT NULL,
  PRIMARY KEY (watchlist_id, symbol),
  FOREIGN KEY (watchlist_id) REFERENCES watchlists(id) ON DELETE CASCADE
);

-- Plan 2: 板块
CREATE TABLE IF NOT EXISTS sectors (
  name TEXT PRIMARY KEY,
  classification TEXT NOT NULL,
  updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS sector_constituents (
  sector_name TEXT NOT NULL,
  symbol TEXT NOT NULL,
  PRIMARY KEY (sector_name, symbol)
);

CREATE INDEX IF NOT EXISTS idx_sector_const_symbol ON sector_constituents(symbol);

-- Plan 2: 资金流
CREATE TABLE IF NOT EXISTS fund_flow_symbol (
  symbol TEXT NOT NULL,
  ts TIMESTAMP NOT NULL,
  main_net REAL,
  super_large_net REAL,
  large_net REAL,
  medium_net REAL,
  small_net REAL,
  PRIMARY KEY (symbol, ts)
);

CREATE INDEX IF NOT EXISTS idx_ff_symbol_ts ON fund_flow_symbol(ts DESC);

CREATE TABLE IF NOT EXISTS fund_flow_sector (
  sector_name TEXT NOT NULL,
  ts TIMESTAMP NOT NULL,
  main_net REAL,
  pct_change REAL,
  PRIMARY KEY (sector_name, ts)
);

CREATE INDEX IF NOT EXISTS idx_ff_sector_ts ON fund_flow_sector(ts DESC);

CREATE TABLE IF NOT EXISTS fund_flow_north (
  ts TIMESTAMP PRIMARY KEY,
  hgt_net REAL,
  sgt_net REAL,
  total_net REAL
);
```

- [ ] **Step 2: 删除旧 state.db,验证 schema 重建**

```bash
rm -f data/state.db
. .venv/bin/activate && python -c "
import asyncio
from core.persistence.sqlite_repo import StateRepo
async def main():
    r = StateRepo('data/state.db')
    await r.init()
    async with r.connect() as db:
        cur = await db.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")
        for row in await cur.fetchall():
            print(row[0])
asyncio.run(main())
"
```
Expected 输出包含: `app_state`、`fund_flow_north`、`fund_flow_sector`、`fund_flow_symbol`、`health_log`、`sector_constituents`、`sectors`、`watchlist_items`、`watchlists`

- [ ] **Step 3: 跑原有持久化测试确保不破**

```bash
. .venv/bin/activate && pytest tests/unit/persistence/ -v
```
Expected: 6 passed

- [ ] **Step 4: Commit**

```bash
git add core/persistence/schema.sql
git commit -m "feat(persistence): add watchlists/sectors/fund_flow tables (plan 2 task 2)"
```

---

## Task 3: WatchlistRepo

**Files:**
- Create: `core/persistence/watchlist_repo.py`
- Create: `tests/unit/persistence/test_watchlist_repo.py`

- [ ] **Step 1: 写测试** `tests/unit/persistence/test_watchlist_repo.py`:

```python
import pytest

from core.persistence.sqlite_repo import StateRepo
from core.persistence.watchlist_repo import WatchlistRepo


@pytest.fixture
async def repo(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return WatchlistRepo(str(tmp_path / "state.db"))


@pytest.mark.asyncio
async def test_create_and_list_watchlists(repo):
    wl_id = await repo.create_watchlist("我的关注")
    assert wl_id > 0
    items = await repo.list_watchlists(include_archived=False)
    assert len(items) == 1
    assert items[0].name == "我的关注"
    assert items[0].is_archived is False


@pytest.mark.asyncio
async def test_archive_hides_from_default_list(repo):
    a = await repo.create_watchlist("A")
    b = await repo.create_watchlist("B")
    await repo.archive_watchlist(a)
    actives = await repo.list_watchlists(include_archived=False)
    assert {w.id for w in actives} == {b}
    all_ = await repo.list_watchlists(include_archived=True)
    assert {w.id for w in all_} == {a, b}


@pytest.mark.asyncio
async def test_add_and_remove_symbol(repo):
    wl = await repo.create_watchlist("X")
    await repo.add_symbol(wl, "600519.SH")
    await repo.add_symbol(wl, "000858.SZ")
    assert sorted(await repo.list_symbols(wl)) == ["000858.SZ", "600519.SH"]
    await repo.remove_symbol(wl, "600519.SH")
    assert await repo.list_symbols(wl) == ["000858.SZ"]


@pytest.mark.asyncio
async def test_add_symbol_idempotent(repo):
    wl = await repo.create_watchlist("X")
    await repo.add_symbol(wl, "600519.SH")
    await repo.add_symbol(wl, "600519.SH")
    assert await repo.list_symbols(wl) == ["600519.SH"]


@pytest.mark.asyncio
async def test_all_active_symbols_for_scheduler(repo):
    a = await repo.create_watchlist("A")
    b = await repo.create_watchlist("B")
    arc = await repo.create_watchlist("ARC")
    await repo.add_symbol(a, "600519.SH")
    await repo.add_symbol(b, "000858.SZ")
    await repo.add_symbol(arc, "300750.SZ")
    await repo.archive_watchlist(arc)
    syms = await repo.all_active_symbols()
    assert sorted(syms) == ["000858.SZ", "600519.SH"]


@pytest.mark.asyncio
async def test_rename_watchlist(repo):
    wl = await repo.create_watchlist("旧名")
    await repo.rename_watchlist(wl, "新名")
    items = await repo.list_watchlists(include_archived=False)
    assert items[0].name == "新名"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
. .venv/bin/activate && pytest tests/unit/persistence/test_watchlist_repo.py -v
```
Expected: ImportError

- [ ] **Step 3: 实现** `core/persistence/watchlist_repo.py`:

```python
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from core.domain.models import Watchlist


class WatchlistRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            yield db

    async def create_watchlist(self, name: str) -> int:
        async with self._connect() as db:
            cur = await db.execute(
                "INSERT INTO watchlists (name, is_archived, created_at) VALUES (?, 0, ?)",
                (name, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
            return cur.lastrowid

    async def list_watchlists(self, include_archived: bool = False) -> list[Watchlist]:
        sql = "SELECT id, name, is_archived, created_at FROM watchlists"
        if not include_archived:
            sql += " WHERE is_archived = 0"
        sql += " ORDER BY id"
        async with self._connect() as db:
            cur = await db.execute(sql)
            rows = await cur.fetchall()
        return [
            Watchlist(
                id=r["id"], name=r["name"],
                is_archived=bool(r["is_archived"]),
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    async def rename_watchlist(self, wl_id: int, new_name: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE watchlists SET name = ? WHERE id = ?", (new_name, wl_id),
            )
            await db.commit()

    async def archive_watchlist(self, wl_id: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE watchlists SET is_archived = 1 WHERE id = ?", (wl_id,),
            )
            await db.commit()

    async def add_symbol(self, wl_id: int, symbol: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "INSERT OR IGNORE INTO watchlist_items (watchlist_id, symbol, added_at) "
                "VALUES (?, ?, ?)",
                (wl_id, symbol, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def remove_symbol(self, wl_id: int, symbol: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM watchlist_items WHERE watchlist_id = ? AND symbol = ?",
                (wl_id, symbol),
            )
            await db.commit()

    async def list_symbols(self, wl_id: int) -> list[str]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? ORDER BY symbol",
                (wl_id,),
            )
            rows = await cur.fetchall()
        return [r["symbol"] for r in rows]

    async def all_active_symbols(self) -> list[str]:
        """所有未归档列表里的 symbol 去重并集,给 Scheduler 用。"""
        async with self._connect() as db:
            cur = await db.execute("""
                SELECT DISTINCT wi.symbol
                FROM watchlist_items wi
                JOIN watchlists w ON w.id = wi.watchlist_id
                WHERE w.is_archived = 0
            """)
            rows = await cur.fetchall()
        return [r["symbol"] for r in rows]
```

- [ ] **Step 4: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/persistence/test_watchlist_repo.py -v
```
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add core/persistence/watchlist_repo.py tests/unit/persistence/test_watchlist_repo.py
git commit -m "feat(persistence): add WatchlistRepo (plan 2 task 3)"
```

---

## Task 4: SectorRepo

**Files:**
- Create: `core/persistence/sector_repo.py`
- Create: `tests/unit/persistence/test_sector_repo.py`

- [ ] **Step 1: 写测试** `tests/unit/persistence/test_sector_repo.py`:

```python
from datetime import datetime, timezone

import pytest

from core.persistence.sector_repo import SectorRepo
from core.persistence.sqlite_repo import StateRepo


@pytest.fixture
async def repo(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return SectorRepo(str(tmp_path / "state.db"))


@pytest.mark.asyncio
async def test_upsert_sector_with_constituents(repo):
    await repo.upsert_sector("玻璃行业", "sina", ["600660.SH", "601636.SH"])
    sectors = await repo.list_sectors()
    assert "玻璃行业" in {s.name for s in sectors}
    syms = await repo.list_constituents("玻璃行业")
    assert sorted(syms) == ["600660.SH", "601636.SH"]


@pytest.mark.asyncio
async def test_upsert_replaces_constituents(repo):
    await repo.upsert_sector("X", "sina", ["A.SZ", "B.SZ"])
    await repo.upsert_sector("X", "sina", ["A.SZ", "C.SZ"])  # 替换
    syms = await repo.list_constituents("X")
    assert sorted(syms) == ["A.SZ", "C.SZ"]


@pytest.mark.asyncio
async def test_sectors_of_symbol(repo):
    await repo.upsert_sector("玻璃", "sina", ["600660.SH"])
    await repo.upsert_sector("建材", "sina", ["600660.SH", "601636.SH"])
    sectors = await repo.sectors_of_symbol("600660.SH")
    assert sorted(sectors) == ["建材", "玻璃"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
. .venv/bin/activate && pytest tests/unit/persistence/test_sector_repo.py -v
```
Expected: ImportError

- [ ] **Step 3: 实现** `core/persistence/sector_repo.py`:

```python
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from core.domain.models import Sector


class SectorRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON")
            yield db

    async def upsert_sector(
        self, name: str, classification: str, symbols: list[str],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO sectors (name, classification, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET classification=excluded.classification, "
                "updated_at=excluded.updated_at",
                (name, classification, now),
            )
            await db.execute(
                "DELETE FROM sector_constituents WHERE sector_name = ?", (name,),
            )
            if symbols:
                await db.executemany(
                    "INSERT INTO sector_constituents (sector_name, symbol) VALUES (?, ?)",
                    [(name, s) for s in symbols],
                )
            await db.commit()

    async def list_sectors(self) -> list[Sector]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT name, classification, updated_at FROM sectors ORDER BY name"
            )
            rows = await cur.fetchall()
        return [
            Sector(
                name=r["name"], classification=r["classification"],
                updated_at=datetime.fromisoformat(r["updated_at"]),
            )
            for r in rows
        ]

    async def list_constituents(self, sector_name: str) -> list[str]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT symbol FROM sector_constituents WHERE sector_name = ? ORDER BY symbol",
                (sector_name,),
            )
            rows = await cur.fetchall()
        return [r["symbol"] for r in rows]

    async def sectors_of_symbol(self, symbol: str) -> list[str]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT sector_name FROM sector_constituents WHERE symbol = ? "
                "ORDER BY sector_name",
                (symbol,),
            )
            rows = await cur.fetchall()
        return [r["sector_name"] for r in rows]
```

- [ ] **Step 4: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/persistence/test_sector_repo.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add core/persistence/sector_repo.py tests/unit/persistence/test_sector_repo.py
git commit -m "feat(persistence): add SectorRepo (plan 2 task 4)"
```

---

## Task 5: FundFlowRepo

**Files:**
- Create: `core/persistence/fund_flow_repo.py`
- Create: `tests/unit/persistence/test_fund_flow_repo.py`

- [ ] **Step 1: 写测试** `tests/unit/persistence/test_fund_flow_repo.py`:

```python
from datetime import datetime, timezone, timedelta

import pytest

from core.domain.models import FundFlowSnapshot
from core.persistence.fund_flow_repo import FundFlowRepo
from core.persistence.sqlite_repo import StateRepo


def _snap_symbol(sym, ts, main):
    return FundFlowSnapshot(
        subject=sym, kind="symbol", ts=ts, main_net=main,
        super_large_net=main * 0.5, large_net=main * 0.3,
        medium_net=main * 0.1, small_net=main * 0.1,
    )


@pytest.fixture
async def repo(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return FundFlowRepo(str(tmp_path / "state.db"))


@pytest.mark.asyncio
async def test_save_and_query_symbol_flow(repo):
    base = datetime(2026, 5, 13, 9, 30, tzinfo=timezone.utc)
    await repo.save_symbol_flows([
        _snap_symbol("600519.SH", base, 1e7),
        _snap_symbol("600519.SH", base + timedelta(minutes=30), 1.2e7),
    ])
    rows = await repo.query_symbol_flow("600519.SH",
                                         start=base - timedelta(hours=1),
                                         end=base + timedelta(hours=2))
    assert len(rows) == 2
    assert rows[0].main_net == pytest.approx(1e7)


@pytest.mark.asyncio
async def test_save_north_flow(repo):
    ts = datetime(2026, 5, 13, 10, 0, tzinfo=timezone.utc)
    await repo.save_north_flow(FundFlowSnapshot(
        subject="north", kind="north", ts=ts,
        hgt_net=5e8, sgt_net=3e8,
    ))
    flows = await repo.query_north_flow(
        start=ts - timedelta(hours=1), end=ts + timedelta(hours=1),
    )
    assert len(flows) == 1
    assert flows[0].hgt_net == pytest.approx(5e8)


@pytest.mark.asyncio
async def test_save_sector_flow(repo):
    ts = datetime(2026, 5, 13, 10, 5, tzinfo=timezone.utc)
    await repo.save_sector_flows([
        FundFlowSnapshot(subject="玻璃行业", kind="sector", ts=ts,
                         main_net=2e7, pct_change=3.5),
    ])
    rows = await repo.query_sector_flow("玻璃行业",
                                         start=ts - timedelta(hours=1),
                                         end=ts + timedelta(hours=1))
    assert len(rows) == 1
    assert rows[0].pct_change == pytest.approx(3.5)


@pytest.mark.asyncio
async def test_purge_old(repo):
    old = datetime.now(timezone.utc) - timedelta(days=40)
    fresh = datetime.now(timezone.utc) - timedelta(days=5)
    await repo.save_symbol_flows([_snap_symbol("X", old, 1e6),
                                    _snap_symbol("X", fresh, 2e6)])
    deleted = await repo.purge_old_symbol(days=30)
    assert deleted == 1
    rows = await repo.query_symbol_flow("X",
                                         start=old - timedelta(days=1),
                                         end=datetime.now(timezone.utc))
    assert len(rows) == 1
    assert rows[0].ts >= fresh - timedelta(seconds=1)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
. .venv/bin/activate && pytest tests/unit/persistence/test_fund_flow_repo.py -v
```
Expected: ImportError

- [ ] **Step 3: 实现** `core/persistence/fund_flow_repo.py`:

```python
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import aiosqlite

from core.domain.models import FundFlowSnapshot


class FundFlowRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def save_symbol_flows(self, items: list[FundFlowSnapshot]) -> None:
        if not items:
            return
        rows = [(
            f.subject, f.ts.astimezone(timezone.utc).isoformat(),
            f.main_net, f.super_large_net, f.large_net, f.medium_net, f.small_net,
        ) for f in items]
        async with self._connect() as db:
            await db.executemany("""
                INSERT INTO fund_flow_symbol (symbol, ts, main_net, super_large_net,
                                              large_net, medium_net, small_net)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, ts) DO UPDATE SET
                  main_net=excluded.main_net,
                  super_large_net=excluded.super_large_net,
                  large_net=excluded.large_net,
                  medium_net=excluded.medium_net,
                  small_net=excluded.small_net
            """, rows)
            await db.commit()

    async def query_symbol_flow(
        self, symbol: str, start: datetime, end: datetime,
    ) -> list[FundFlowSnapshot]:
        async with self._connect() as db:
            cur = await db.execute("""
                SELECT symbol, ts, main_net, super_large_net, large_net, medium_net, small_net
                FROM fund_flow_symbol
                WHERE symbol = ? AND ts BETWEEN ? AND ?
                ORDER BY ts
            """, (symbol, start.astimezone(timezone.utc).isoformat(),
                   end.astimezone(timezone.utc).isoformat()))
            rows = await cur.fetchall()
        return [_to_symbol_snapshot(r) for r in rows]

    async def save_sector_flows(self, items: list[FundFlowSnapshot]) -> None:
        if not items:
            return
        rows = [(f.subject, f.ts.astimezone(timezone.utc).isoformat(),
                  f.main_net, f.pct_change) for f in items]
        async with self._connect() as db:
            await db.executemany("""
                INSERT INTO fund_flow_sector (sector_name, ts, main_net, pct_change)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(sector_name, ts) DO UPDATE SET
                  main_net=excluded.main_net, pct_change=excluded.pct_change
            """, rows)
            await db.commit()

    async def query_sector_flow(
        self, sector_name: str, start: datetime, end: datetime,
    ) -> list[FundFlowSnapshot]:
        async with self._connect() as db:
            cur = await db.execute("""
                SELECT sector_name, ts, main_net, pct_change
                FROM fund_flow_sector
                WHERE sector_name = ? AND ts BETWEEN ? AND ?
                ORDER BY ts
            """, (sector_name, start.astimezone(timezone.utc).isoformat(),
                   end.astimezone(timezone.utc).isoformat()))
            rows = await cur.fetchall()
        return [FundFlowSnapshot(
            subject=r["sector_name"], kind="sector",
            ts=datetime.fromisoformat(r["ts"]),
            main_net=r["main_net"], pct_change=r["pct_change"],
        ) for r in rows]

    async def save_north_flow(self, snap: FundFlowSnapshot) -> None:
        total = (snap.hgt_net or 0) + (snap.sgt_net or 0)
        async with self._connect() as db:
            await db.execute("""
                INSERT INTO fund_flow_north (ts, hgt_net, sgt_net, total_net)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(ts) DO UPDATE SET
                  hgt_net=excluded.hgt_net, sgt_net=excluded.sgt_net,
                  total_net=excluded.total_net
            """, (snap.ts.astimezone(timezone.utc).isoformat(),
                   snap.hgt_net, snap.sgt_net, total))
            await db.commit()

    async def query_north_flow(
        self, start: datetime, end: datetime,
    ) -> list[FundFlowSnapshot]:
        async with self._connect() as db:
            cur = await db.execute("""
                SELECT ts, hgt_net, sgt_net FROM fund_flow_north
                WHERE ts BETWEEN ? AND ?
                ORDER BY ts
            """, (start.astimezone(timezone.utc).isoformat(),
                   end.astimezone(timezone.utc).isoformat()))
            rows = await cur.fetchall()
        return [FundFlowSnapshot(
            subject="north", kind="north",
            ts=datetime.fromisoformat(r["ts"]),
            hgt_net=r["hgt_net"], sgt_net=r["sgt_net"],
        ) for r in rows]

    async def purge_old_symbol(self, days: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with self._connect() as db:
            cur = await db.execute(
                "DELETE FROM fund_flow_symbol WHERE ts < ?", (cutoff,),
            )
            await db.commit()
            return cur.rowcount

    async def purge_old_sector(self, days: int = 90) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with self._connect() as db:
            cur = await db.execute(
                "DELETE FROM fund_flow_sector WHERE ts < ?", (cutoff,),
            )
            await db.commit()
            return cur.rowcount

    async def purge_old_north(self, days: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        async with self._connect() as db:
            cur = await db.execute(
                "DELETE FROM fund_flow_north WHERE ts < ?", (cutoff,),
            )
            await db.commit()
            return cur.rowcount


def _to_symbol_snapshot(r) -> FundFlowSnapshot:
    return FundFlowSnapshot(
        subject=r["symbol"], kind="symbol",
        ts=datetime.fromisoformat(r["ts"]),
        main_net=r["main_net"], super_large_net=r["super_large_net"],
        large_net=r["large_net"], medium_net=r["medium_net"], small_net=r["small_net"],
    )
```

- [ ] **Step 4: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/persistence/test_fund_flow_repo.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add core/persistence/fund_flow_repo.py tests/unit/persistence/test_fund_flow_repo.py
git commit -m "feat(persistence): add FundFlowRepo (plan 2 task 5)"
```

---

## Task 6: AShareAdapter 扩展(fetch_history 走 sina_hist)

**Files:**
- Modify: `core/adapters/ashare.py`
- Modify: `tests/unit/adapters/test_ashare.py`

> Plan 1 的 `fetch_history` 走 `ak.stock_zh_a_hist`(东财),在你这边不通。改成走新浪历史接口 `https://finance.sina.com.cn/realstock/company/{code}/hisdata/klc_kl.js`。同时新增 `fetch_intraday`。

- [ ] **Step 1: 探接口可用性**

```bash
. .venv/bin/activate && NO_PROXY='*' python3 << 'EOF'
import akshare as ak, time
t = time.time()
df = ak.stock_zh_a_daily(symbol="sh600519", start_date="20260101", end_date="20260513", adjust="qfq")
print(f"stock_zh_a_daily(sina): {df.shape} {time.time()-t:.2f}s")
print(df.tail(3))

t = time.time()
df = ak.stock_zh_a_minute(symbol="sh600519", period="5", adjust="qfq")
print(f"\nstock_zh_a_minute(5min): {df.shape} {time.time()-t:.2f}s")
print(df.tail(3))
EOF
```
Expected: 两个 shape 都非空,延时 < 5s。**记下接口实际字段名,下面实现里要用。**

- [ ] **Step 2: 改 `core/adapters/ashare.py`,替换 `fetch_history`,新增 `fetch_intraday`**

替换 `fetch_history` 方法:

```python
    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        sina_code = _to_sina_code(symbol)  # sh600519 / sz000858
        df = await asyncio.to_thread(
            ak.stock_zh_a_daily,
            symbol=sina_code,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        out: list[Bar] = []
        for _, row in df.iterrows():
            ts = datetime.combine(row["date"], datetime.min.time(), tzinfo=timezone.utc)
            out.append(Bar(
                market="ashare", symbol=symbol, ts=ts,
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=int(row["volume"]),
                interval="1d",
            ))
        return out
```

在文件末尾追加:

```python
    async def fetch_intraday(self, symbol: str, freq: str = "5") -> list[Bar]:
        """freq: '1'/'5'/'15'/'30'/'60' min。"""
        sina_code = _to_sina_code(symbol)
        df = await asyncio.to_thread(
            ak.stock_zh_a_minute,
            symbol=sina_code, period=freq, adjust="qfq",
        )
        out: list[Bar] = []
        interval = f"{freq}m"
        for _, row in df.iterrows():
            ts = datetime.fromisoformat(str(row["day"]).replace(" ", "T") + "+00:00")
            out.append(Bar(
                market="ashare", symbol=symbol, ts=ts,
                open=Decimal(str(row["open"])), high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])), close=Decimal(str(row["close"])),
                volume=int(float(row["volume"])),
                interval=interval,
            ))
        return out
```

- [ ] **Step 3: 在 `tests/unit/adapters/test_ashare.py` 追加测试**

```python
import pandas as pd
from datetime import date

_DAILY_DF = pd.DataFrame([
    {"date": date(2026, 5, 12), "open": 1340.0, "high": 1360.0, "low": 1330.0,
     "close": 1354.55, "volume": 5_000_000},
    {"date": date(2026, 5, 13), "open": 1354.5, "high": 1358.6, "low": 1338.0,
     "close": 1344.09, "volume": 5_696_787},
])

_5MIN_DF = pd.DataFrame([
    {"day": "2026-05-13 09:35:00", "open": 1350.0, "high": 1351.0, "low": 1349.0,
     "close": 1350.5, "volume": 100_000},
])


@pytest.mark.asyncio
async def test_fetch_history_uses_sina_daily():
    adapter = AShareAdapter()
    with patch("core.adapters.ashare.ak.stock_zh_a_daily", return_value=_DAILY_DF):
        bars = await adapter.fetch_history(
            "600519.SH",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 14, tzinfo=timezone.utc),
        )
    assert len(bars) == 2
    assert bars[1].close == Decimal("1344.09")
    assert bars[1].interval == "1d"


@pytest.mark.asyncio
async def test_fetch_intraday_5min():
    adapter = AShareAdapter()
    with patch("core.adapters.ashare.ak.stock_zh_a_minute", return_value=_5MIN_DF):
        bars = await adapter.fetch_intraday("600519.SH", freq="5")
    assert len(bars) == 1
    assert bars[0].interval == "5m"
    assert bars[0].close == Decimal("1350.5")
```

- [ ] **Step 4: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_ashare.py -v
```
Expected: 全部 passed(原 5 + 新 2 = 7)

- [ ] **Step 5: Commit**

```bash
git add core/adapters/ashare.py tests/unit/adapters/test_ashare.py
git commit -m "feat(adapters/ashare): fetch_history via sina daily; add fetch_intraday (plan 2 task 6)"
```

---

## Task 7: KLineService(K 线统一访问层)

**Files:**
- Create: `core/services/kline_service.py`
- Create: `tests/unit/services/test_kline_service.py`

- [ ] **Step 1: 写测试** `tests/unit/services/test_kline_service.py`:

```python
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain.models import Bar
from core.services.kline_service import KLineService


def _bar(symbol, day_offset, interval="1d", close=100.0):
    ts = datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(days=day_offset)
    return Bar(
        market="ashare", symbol=symbol, ts=ts,
        open=Decimal(str(close - 1)), high=Decimal(str(close + 2)),
        low=Decimal(str(close - 2)), close=Decimal(str(close)),
        volume=1_000_000, interval=interval,
    )


@pytest.mark.asyncio
async def test_get_bars_cache_hit_returns_from_duckdb():
    repo = MagicMock()
    repo.fetch_history.return_value = [_bar("600519.SH", i, close=100 + i) for i in range(10)]
    adapter = MagicMock()
    adapter.fetch_history = AsyncMock()
    svc = KLineService(repo, adapter)
    bars = await svc.get_bars(
        "600519.SH",
        interval="1d",
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 10, tzinfo=timezone.utc),
    )
    assert len(bars) == 10
    # 缓存命中,没调 adapter
    adapter.fetch_history.assert_not_called()


@pytest.mark.asyncio
async def test_get_bars_cache_miss_calls_adapter_then_writes_back():
    repo = MagicMock()
    repo.fetch_history.side_effect = [[], [_bar("X", i) for i in range(5)]]
    adapter = MagicMock()
    adapter.fetch_history = AsyncMock(return_value=[_bar("X", i) for i in range(5)])
    svc = KLineService(repo, adapter)
    bars = await svc.get_bars(
        "X", interval="1d",
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 5, tzinfo=timezone.utc),
    )
    adapter.fetch_history.assert_called_once()
    repo.insert_bars.assert_called_once()
    assert len(bars) == 5


@pytest.mark.asyncio
async def test_get_bars_weekly_resamples_daily():
    repo = MagicMock()
    # 14 个连续交易日
    daily = [_bar("X", i, close=100 + i) for i in range(14)]
    repo.fetch_history.return_value = daily
    adapter = MagicMock()
    svc = KLineService(repo, adapter)
    weeks = await svc.get_bars(
        "X", interval="1wk",
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    # 14 天 ≈ 2-3 个 W 桶
    assert 1 <= len(weeks) <= 3
    assert all(b.interval == "1wk" for b in weeks)


@pytest.mark.asyncio
async def test_get_intraday_calls_adapter_intraday_and_writes():
    repo = MagicMock()
    repo.fetch_history.return_value = []
    adapter = MagicMock()
    adapter.fetch_intraday = AsyncMock(return_value=[_bar("X", 0, interval="5m")])
    svc = KLineService(repo, adapter)
    bars = await svc.get_bars(
        "X", interval="5m",
        start=datetime(2026, 5, 13, tzinfo=timezone.utc),
        end=datetime(2026, 5, 13, 23, tzinfo=timezone.utc),
    )
    adapter.fetch_intraday.assert_called_once_with("X", freq="5")
    repo.insert_bars.assert_called_once()
    assert bars[0].interval == "5m"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
. .venv/bin/activate && pytest tests/unit/services/test_kline_service.py -v
```
Expected: ImportError

- [ ] **Step 3: 实现** `core/services/kline_service.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import pandas as pd
import structlog

from core.adapters.base import MarketAdapter
from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)

Interval = Literal["1d", "1wk", "1mo", "1m", "5m", "15m", "30m", "60m"]

_INTRADAY = {"1m", "5m", "15m", "30m", "60m"}
_RESAMPLED = {"1wk": "W-FRI", "1mo": "ME"}


class KLineService:
    def __init__(self, bar_repo: BarRepo, adapter: MarketAdapter) -> None:
        self.repo = bar_repo
        self.adapter = adapter

    async def get_bars(
        self, symbol: str, *, interval: Interval,
        start: datetime, end: datetime,
    ) -> list[Bar]:
        if interval in _RESAMPLED:
            daily = await self._get_daily(symbol, start, end)
            return _resample(daily, interval)
        if interval in _INTRADAY:
            return await self._get_intraday(symbol, interval, start, end)
        if interval == "1d":
            return await self._get_daily(symbol, start, end)
        raise ValueError(f"unsupported interval: {interval}")

    async def _get_daily(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        cached = self.repo.fetch_history("ashare", symbol, start, end, interval="1d")
        if cached:
            return cached
        bars = await self.adapter.fetch_history(symbol, start, end)
        self.repo.insert_bars(bars)
        return bars

    async def _get_intraday(
        self, symbol: str, interval: str, start: datetime, end: datetime,
    ) -> list[Bar]:
        cached = self.repo.fetch_history("ashare", symbol, start, end, interval=interval)
        if cached:
            return cached
        freq = interval.replace("m", "")
        bars = await self.adapter.fetch_intraday(symbol, freq=freq)
        self.repo.insert_bars(bars)
        # 过滤 start/end 窗口
        return [b for b in bars if start <= b.ts <= end]


def _resample(daily: list[Bar], interval: str) -> list[Bar]:
    if not daily:
        return []
    df = pd.DataFrame([{
        "ts": b.ts, "open": float(b.open), "high": float(b.high),
        "low": float(b.low), "close": float(b.close), "volume": b.volume,
    } for b in daily]).set_index("ts")
    rule = _RESAMPLED[interval]
    agg = df.resample(rule).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna()
    sample = daily[0]
    return [Bar(
        market=sample.market, symbol=sample.symbol,
        ts=ts.to_pydatetime().replace(tzinfo=timezone.utc),
        open=Decimal(str(r["open"])), high=Decimal(str(r["high"])),
        low=Decimal(str(r["low"])), close=Decimal(str(r["close"])),
        volume=int(r["volume"]), interval=interval,
    ) for ts, r in agg.iterrows()]
```

- [ ] **Step 4: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/services/test_kline_service.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add core/services/kline_service.py tests/unit/services/test_kline_service.py
git commit -m "feat(services): add KLineService with cache + resample (plan 2 task 7)"
```

---

## Task 8: WatchlistService

**Files:**
- Create: `core/services/watchlist_service.py`
- Create: `tests/unit/services/test_watchlist_service.py`

- [ ] **Step 1: 写测试** `tests/unit/services/test_watchlist_service.py`:

```python
import pytest

from core.persistence.sqlite_repo import StateRepo
from core.persistence.watchlist_repo import WatchlistRepo
from core.services.watchlist_service import WatchlistService


@pytest.fixture
async def svc(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return WatchlistService(WatchlistRepo(str(tmp_path / "state.db")))


@pytest.mark.asyncio
async def test_bootstrap_creates_default_watchlist(svc):
    await svc.bootstrap_default()
    items = await svc.list_all()
    assert len(items) == 1
    assert items[0].name == "我的关注"


@pytest.mark.asyncio
async def test_bootstrap_idempotent(svc):
    await svc.bootstrap_default()
    await svc.bootstrap_default()
    items = await svc.list_all()
    assert len(items) == 1


@pytest.mark.asyncio
async def test_dynamic_universe_unions_active_lists(svc):
    a = await svc.create("A")
    b = await svc.create("B")
    await svc.add_symbol(a, "600519.SH")
    await svc.add_symbol(b, "000858.SZ")
    syms = await svc.dynamic_universe()
    assert sorted(syms) == ["000858.SZ", "600519.SH"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
. .venv/bin/activate && pytest tests/unit/services/test_watchlist_service.py -v
```
Expected: ImportError

- [ ] **Step 3: 实现** `core/services/watchlist_service.py`:

```python
from __future__ import annotations

from core.domain.models import Watchlist
from core.persistence.watchlist_repo import WatchlistRepo


_DEFAULT_NAME = "我的关注"


class WatchlistService:
    def __init__(self, repo: WatchlistRepo) -> None:
        self.repo = repo

    async def bootstrap_default(self) -> None:
        existing = await self.repo.list_watchlists(include_archived=True)
        if any(w.name == _DEFAULT_NAME for w in existing):
            return
        await self.repo.create_watchlist(_DEFAULT_NAME)

    async def list_all(self, include_archived: bool = False) -> list[Watchlist]:
        return await self.repo.list_watchlists(include_archived=include_archived)

    async def create(self, name: str) -> int:
        return await self.repo.create_watchlist(name)

    async def rename(self, wl_id: int, new_name: str) -> None:
        await self.repo.rename_watchlist(wl_id, new_name)

    async def archive(self, wl_id: int) -> None:
        await self.repo.archive_watchlist(wl_id)

    async def add_symbol(self, wl_id: int, symbol: str) -> None:
        await self.repo.add_symbol(wl_id, symbol)

    async def remove_symbol(self, wl_id: int, symbol: str) -> None:
        await self.repo.remove_symbol(wl_id, symbol)

    async def list_symbols(self, wl_id: int) -> list[str]:
        return await self.repo.list_symbols(wl_id)

    async def dynamic_universe(self) -> list[str]:
        return await self.repo.all_active_symbols()
```

- [ ] **Step 4: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/services/test_watchlist_service.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add core/services/watchlist_service.py tests/unit/services/test_watchlist_service.py
git commit -m "feat(services): add WatchlistService with default bootstrap (plan 2 task 8)"
```

---

## Task 9: SectorService(板块成分抓取与刷新)

**Files:**
- Create: `core/services/sector_service.py`
- Create: `tests/unit/services/test_sector_service.py`

- [ ] **Step 1: 探接口字段名**

```bash
. .venv/bin/activate && NO_PROXY='*' python3 << 'EOF'
import akshare as ak
df = ak.stock_sector_detail(sector="new_blhy")  # 玻璃行业
print("columns:", list(df.columns))
print(df.head(3))
EOF
```

Expected: 至少有 `代码` 列(包含 `sh.../sz.../bj...` 前缀)。**记下字段名,后面实现要用。**

- [ ] **Step 2: 写测试** `tests/unit/services/test_sector_service.py`:

```python
from unittest.mock import patch

import pandas as pd
import pytest

from core.persistence.sector_repo import SectorRepo
from core.persistence.sqlite_repo import StateRepo
from core.services.sector_service import SectorService


@pytest.fixture
async def svc(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return SectorService(SectorRepo(str(tmp_path / "state.db")))


_SECTOR_DETAIL_DF = pd.DataFrame([
    {"代码": "sh600660", "名称": "福耀玻璃"},
    {"代码": "sh601636", "名称": "旗滨集团"},
    {"代码": "sz000012", "名称": "南玻A"},
])

_NEW_SINA_INDUSTRY_DF = pd.DataFrame([
    {"label": "new_blhy", "板块": "玻璃行业", "公司家数": 3,
     "平均价格": 22.0, "涨跌幅": 1.5, "股票名称": "福耀玻璃",
     "个股-涨跌幅": 5.0, "个股-当前价": 35.0, "个股-涨跌额": 1.5,
     "总成交量": 1000, "总成交额": 5000, "上涨家数": 2, "下跌家数": 1},
])


@pytest.mark.asyncio
async def test_refresh_sector_writes_constituents(svc):
    with patch("core.services.sector_service.ak.stock_sector_detail",
               return_value=_SECTOR_DETAIL_DF):
        n = await svc.refresh_sector("new_blhy", display_name="玻璃行业")
    assert n == 3
    syms = await svc.list_constituents("玻璃行业")
    assert sorted(syms) == ["000012.SZ", "600660.SH", "601636.SH"]


@pytest.mark.asyncio
async def test_refresh_all_iterates_known_labels(svc):
    with patch("core.services.sector_service.ak.stock_sector_spot",
               return_value=_NEW_SINA_INDUSTRY_DF), \
         patch("core.services.sector_service.ak.stock_sector_detail",
               return_value=_SECTOR_DETAIL_DF):
        total = await svc.refresh_all_sina()
    assert total == 3
    sectors = await svc.list_sectors()
    assert any(s.name == "玻璃行业" for s in sectors)
```

- [ ] **Step 3: 跑测试确认失败**

```bash
. .venv/bin/activate && pytest tests/unit/services/test_sector_service.py -v
```
Expected: ImportError

- [ ] **Step 4: 实现** `core/services/sector_service.py`:

```python
from __future__ import annotations

import asyncio

import akshare as ak
import structlog

from core.domain.models import Sector
from core.persistence.sector_repo import SectorRepo

log = structlog.get_logger(__name__)


def _to_symbol(sina_code: str) -> str:
    """sh600660 → 600660.SH, sz000012 → 000012.SZ, bj920001 → 920001.BJ"""
    if len(sina_code) < 3:
        return sina_code
    mkt = sina_code[:2].upper()
    code = sina_code[2:]
    return f"{code}.{mkt}" if mkt in {"SH", "SZ", "BJ"} else sina_code


class SectorService:
    def __init__(self, repo: SectorRepo) -> None:
        self.repo = repo

    async def refresh_sector(self, label: str, display_name: str) -> int:
        df = await asyncio.to_thread(ak.stock_sector_detail, sector=label)
        symbols = [_to_symbol(str(c)) for c in df["代码"].tolist()]
        await self.repo.upsert_sector(display_name, "sina", symbols)
        return len(symbols)

    async def refresh_all_sina(self) -> int:
        """读 stock_sector_spot 拿到所有 sina 板块的 (label, 板块名),再逐一拉成分。"""
        spot = await asyncio.to_thread(ak.stock_sector_spot, indicator="新浪行业")
        total = 0
        for _, row in spot.iterrows():
            label = str(row["label"])
            name = str(row["板块"])
            try:
                total += await self.refresh_sector(label, name)
            except Exception as e:  # noqa: BLE001
                log.warning("sector.refresh_failed", label=label, name=name, error=str(e))
        return total

    async def list_sectors(self) -> list[Sector]:
        return await self.repo.list_sectors()

    async def list_constituents(self, sector_name: str) -> list[str]:
        return await self.repo.list_constituents(sector_name)

    async def sectors_of(self, symbol: str) -> list[str]:
        return await self.repo.sectors_of_symbol(symbol)
```

- [ ] **Step 5: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/services/test_sector_service.py -v
```
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add core/services/sector_service.py tests/unit/services/test_sector_service.py
git commit -m "feat(services): add SectorService (refresh sina industries) (plan 2 task 9)"
```

---

## Task 10: FundFlowService

**Files:**
- Create: `core/services/fund_flow_service.py`
- Create: `tests/unit/services/test_fund_flow_service.py`

- [ ] **Step 1: 探接口字段名**

```bash
. .venv/bin/activate && NO_PROXY='*' python3 << 'EOF'
import akshare as ak
# 个股资金流(走 sina,单股)
try:
    df = ak.stock_individual_fund_flow(stock="600519", market="sh")
    print("individual:", list(df.columns), df.shape)
    print(df.tail(3))
except Exception as e:
    print("individual FAIL:", type(e).__name__, str(e)[:100])

# 北向资金
try:
    df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
    print("\nnorth:", list(df.columns), df.shape)
    print(df.tail(3))
except Exception as e:
    print("north FAIL:", type(e).__name__, str(e)[:100])
EOF
```
Expected: 至少能拿到 `日期/最新价/涨跌幅/主力净流入...` 字段。 **记下字段名。** 如果某个 API 在你的网络下不通,**测试用 mock,实现也保留**(scheduler 会让单次失败不影响其他)。

- [ ] **Step 2: 写测试** `tests/unit/services/test_fund_flow_service.py`:

```python
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from core.persistence.fund_flow_repo import FundFlowRepo
from core.persistence.sqlite_repo import StateRepo
from core.services.fund_flow_service import FundFlowService


_SYMBOL_FLOW_DF = pd.DataFrame([
    {"日期": "2026-05-13",
     "主力净流入-净额": 10_000_000, "超大单净流入-净额": 5_000_000,
     "大单净流入-净额": 3_000_000, "中单净流入-净额": 1_500_000,
     "小单净流入-净额": 500_000},
])

_NORTH_DF = pd.DataFrame([
    {"日期": "2026-05-13", "当日资金流入": 8e8, "当日余额": 2e9, "历史累计净买额": 1e12},
])


@pytest.fixture
async def svc(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return FundFlowService(FundFlowRepo(str(tmp_path / "state.db")))


@pytest.mark.asyncio
async def test_pull_symbol_flow(svc):
    with patch("core.services.fund_flow_service.ak.stock_individual_fund_flow",
               return_value=_SYMBOL_FLOW_DF):
        n = await svc.pull_symbol_flow("600519.SH")
    assert n == 1
    rows = await svc.query_symbol("600519.SH",
                                   start=datetime(2026, 5, 1, tzinfo=timezone.utc),
                                   end=datetime(2026, 5, 14, tzinfo=timezone.utc))
    assert len(rows) == 1
    assert rows[0].main_net == pytest.approx(1e7)


@pytest.mark.asyncio
async def test_pull_north_flow(svc):
    with patch("core.services.fund_flow_service.ak.stock_hsgt_north_net_flow_in_em",
               return_value=_NORTH_DF):
        await svc.pull_north_flow()
    rows = await svc.query_north(
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert rows[0].hgt_net is not None
```

- [ ] **Step 3: 跑测试确认失败**

```bash
. .venv/bin/activate && pytest tests/unit/services/test_fund_flow_service.py -v
```
Expected: ImportError

- [ ] **Step 4: 实现** `core/services/fund_flow_service.py`:

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import akshare as ak
import structlog

from core.domain.models import FundFlowSnapshot
from core.persistence.fund_flow_repo import FundFlowRepo

log = structlog.get_logger(__name__)


def _split_symbol(symbol: str) -> tuple[str, str]:
    """600519.SH → ('600519', 'sh')."""
    code, mkt = symbol.split(".")
    return code, mkt.lower()


def _parse_ts(s: str) -> datetime:
    """'2026-05-13' / '2026-05-13 09:30:00' → datetime UTC."""
    if " " in s:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(s + "T00:00:00+00:00")


class FundFlowService:
    def __init__(self, repo: FundFlowRepo) -> None:
        self.repo = repo

    async def pull_symbol_flow(self, symbol: str) -> int:
        code, mkt = _split_symbol(symbol)
        df = await asyncio.to_thread(
            ak.stock_individual_fund_flow, stock=code, market=mkt,
        )
        snapshots: list[FundFlowSnapshot] = []
        for _, row in df.iterrows():
            try:
                ts = _parse_ts(str(row["日期"]))
                snapshots.append(FundFlowSnapshot(
                    subject=symbol, kind="symbol", ts=ts,
                    main_net=_num(row.get("主力净流入-净额")),
                    super_large_net=_num(row.get("超大单净流入-净额")),
                    large_net=_num(row.get("大单净流入-净额")),
                    medium_net=_num(row.get("中单净流入-净额")),
                    small_net=_num(row.get("小单净流入-净额")),
                ))
            except (KeyError, ValueError, TypeError) as e:
                log.warning("symbol_flow.parse_failed", symbol=symbol, error=str(e))
        await self.repo.save_symbol_flows(snapshots)
        return len(snapshots)

    async def query_symbol(self, symbol: str, start: datetime, end: datetime) -> list[FundFlowSnapshot]:
        return await self.repo.query_symbol_flow(symbol, start, end)

    async def pull_north_flow(self) -> None:
        df = await asyncio.to_thread(
            ak.stock_hsgt_north_net_flow_in_em, symbol="北上",
        )
        # 取最近一行
        row = df.iloc[-1]
        ts = _parse_ts(str(row["日期"]))
        total = _num(row.get("当日资金流入")) or 0.0
        await self.repo.save_north_flow(FundFlowSnapshot(
            subject="north", kind="north", ts=ts,
            hgt_net=total * 0.6,    # 简化: V1 不细分沪/深,先平均
            sgt_net=total * 0.4,
        ))

    async def query_north(self, start: datetime, end: datetime) -> list[FundFlowSnapshot]:
        return await self.repo.query_north_flow(start, end)


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
```

- [ ] **Step 5: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/services/test_fund_flow_service.py -v
```
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add core/services/fund_flow_service.py tests/unit/services/test_fund_flow_service.py
git commit -m "feat(services): add FundFlowService (symbol + north) (plan 2 task 10)"
```

---

## Task 11: Symbols API 路由

**Files:**
- Create: `apps/api/routes/symbols.py`
- Modify: `apps/api/main.py`
- Modify: `apps/api/deps.py`
- Create: `tests/integration/test_api_symbols.py`

- [ ] **Step 1: 在 `apps/api/deps.py` 末尾追加**

```python
from core.adapters.ashare import AShareAdapter  # noqa: E402
from core.persistence.fund_flow_repo import FundFlowRepo  # noqa: E402
from core.persistence.sector_repo import SectorRepo  # noqa: E402
from core.persistence.watchlist_repo import WatchlistRepo  # noqa: E402
from core.services.fund_flow_service import FundFlowService  # noqa: E402
from core.services.kline_service import KLineService  # noqa: E402
from core.services.sector_service import SectorService  # noqa: E402
from core.services.watchlist_service import WatchlistService  # noqa: E402


@lru_cache(maxsize=1)
def get_kline_service() -> KLineService:
    return KLineService(get_bar_repo(), AShareAdapter())


@lru_cache(maxsize=1)
def get_watchlist_repo() -> WatchlistRepo:
    return WatchlistRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_watchlist_service() -> WatchlistService:
    return WatchlistService(get_watchlist_repo())


@lru_cache(maxsize=1)
def get_sector_repo() -> SectorRepo:
    return SectorRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_sector_service() -> SectorService:
    return SectorService(get_sector_repo())


@lru_cache(maxsize=1)
def get_fund_flow_repo() -> FundFlowRepo:
    return FundFlowRepo(str(_DATA / "state.db"))


@lru_cache(maxsize=1)
def get_fund_flow_service() -> FundFlowService:
    return FundFlowService(get_fund_flow_repo())
```

- [ ] **Step 2: 实现** `apps/api/routes/symbols.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from apps.api.deps import get_fund_flow_service, get_kline_service
from core.services.fund_flow_service import FundFlowService
from core.services.kline_service import KLineService

router = APIRouter(prefix="/api/symbols", tags=["symbols"])

_VALID_INTERVALS = {"1d", "1wk", "1mo", "1m", "5m", "15m", "30m", "60m"}


class BarDTO(BaseModel):
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class BarsResponse(BaseModel):
    symbol: str
    interval: str
    bars: list[BarDTO]


class FundFlowRowDTO(BaseModel):
    ts: str
    main_net: float | None
    super_large_net: float | None
    large_net: float | None
    medium_net: float | None
    small_net: float | None


class FundFlowResponse(BaseModel):
    symbol: str
    rows: list[FundFlowRowDTO]


@router.get("/{symbol}/bars", response_model=BarsResponse)
async def bars(
    symbol: str,
    interval: str = Query("1d"),
    days: int = Query(365, ge=1, le=3650),
    svc: KLineService = Depends(get_kline_service),
) -> BarsResponse:
    if interval not in _VALID_INTERVALS:
        raise HTTPException(400, f"invalid interval: {interval}")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    bars = await svc.get_bars(symbol, interval=interval, start=start, end=end)
    return BarsResponse(
        symbol=symbol, interval=interval,
        bars=[BarDTO(
            ts=b.ts.isoformat(),
            open=float(b.open), high=float(b.high),
            low=float(b.low), close=float(b.close),
            volume=b.volume,
        ) for b in bars],
    )


@router.get("/{symbol}/fund_flow", response_model=FundFlowResponse)
async def fund_flow(
    symbol: str,
    days: int = Query(30, ge=1, le=365),
    svc: FundFlowService = Depends(get_fund_flow_service),
) -> FundFlowResponse:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = await svc.query_symbol(symbol, start, end)
    return FundFlowResponse(
        symbol=symbol,
        rows=[FundFlowRowDTO(
            ts=r.ts.isoformat(), main_net=r.main_net,
            super_large_net=r.super_large_net, large_net=r.large_net,
            medium_net=r.medium_net, small_net=r.small_net,
        ) for r in rows],
    )
```

- [ ] **Step 3: 在 `apps/api/main.py` 挂载**

修改 import 行:
```python
from apps.api.routes import health, market_extras, markets, symbols
```
追加 `app.include_router(symbols.router)`(在 ticks 之前)。

- [ ] **Step 4: 写集成测试** `tests/integration/test_api_symbols.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from apps.api.deps import get_kline_service
from apps.api.main import app
from core.domain.models import Bar


def _bar(symbol, day, close=100.0):
    return Bar(market="ashare", symbol=symbol,
               ts=datetime(2026, 5, day, tzinfo=timezone.utc),
               open=Decimal("99"), high=Decimal("101"), low=Decimal("98"),
               close=Decimal(str(close)), volume=1_000_000, interval="1d")


def test_bars_returns_400_for_bad_interval():
    with TestClient(app) as client:
        resp = client.get("/api/symbols/600519.SH/bars?interval=2d")
    assert resp.status_code == 400


def test_bars_returns_ok(monkeypatch):
    svc = get_kline_service()
    fake = AsyncMock(return_value=[_bar("600519.SH", 13, 1344.09)])
    monkeypatch.setattr(svc, "get_bars", fake)
    with TestClient(app) as client:
        resp = client.get("/api/symbols/600519.SH/bars?interval=1d&days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "600519.SH"
    assert body["interval"] == "1d"
    assert len(body["bars"]) == 1
    assert body["bars"][0]["close"] == pytest.approx(1344.09)


def test_fund_flow_returns_ok(monkeypatch):
    from apps.api.deps import get_fund_flow_service
    svc = get_fund_flow_service()
    monkeypatch.setattr(svc, "query_symbol", AsyncMock(return_value=[]))
    with TestClient(app) as client:
        resp = client.get("/api/symbols/600519.SH/fund_flow?days=10")
    assert resp.status_code == 200
    assert resp.json()["rows"] == []
```

- [ ] **Step 5: 跑测试**

```bash
. .venv/bin/activate && pytest tests/integration/test_api_symbols.py -v
```
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add apps/api/deps.py apps/api/main.py apps/api/routes/symbols.py tests/integration/test_api_symbols.py
git commit -m "feat(api): add /api/symbols/{sym}/bars and /fund_flow (plan 2 task 11)"
```

---

## Task 12: Sectors API 路由(详情)

**Files:**
- Create: `apps/api/routes/sectors.py`
- Modify: `apps/api/main.py`
- Create: `tests/integration/test_api_sectors.py`

- [ ] **Step 1: 实现** `apps/api/routes/sectors.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from apps.api.deps import get_fund_flow_service, get_sector_service
from core.services.fund_flow_service import FundFlowService
from core.services.sector_service import SectorService

router = APIRouter(prefix="/api/sectors", tags=["sectors-detail"])


class SectorInfo(BaseModel):
    name: str
    classification: str
    updated_at: str


class SectorListResponse(BaseModel):
    sectors: list[SectorInfo]


class ConstituentsResponse(BaseModel):
    sector_name: str
    symbols: list[str]


class SectorFundFlowRow(BaseModel):
    ts: str
    main_net: float | None
    pct_change: float | None


class SectorFundFlowResponse(BaseModel):
    sector_name: str
    rows: list[SectorFundFlowRow]


@router.get("/list", response_model=SectorListResponse)
async def sector_list(svc: SectorService = Depends(get_sector_service)) -> SectorListResponse:
    sectors = await svc.list_sectors()
    return SectorListResponse(sectors=[
        SectorInfo(name=s.name, classification=s.classification,
                   updated_at=s.updated_at.isoformat())
        for s in sectors
    ])


@router.get("/{name}/constituents", response_model=ConstituentsResponse)
async def sector_constituents(
    name: str,
    svc: SectorService = Depends(get_sector_service),
) -> ConstituentsResponse:
    syms = await svc.list_constituents(name)
    if not syms:
        raise HTTPException(404, f"sector not found or empty: {name}")
    return ConstituentsResponse(sector_name=name, symbols=syms)


@router.get("/{name}/fund_flow", response_model=SectorFundFlowResponse)
async def sector_fund_flow(
    name: str,
    days: int = Query(30, ge=1, le=365),
    svc: FundFlowService = Depends(get_fund_flow_service),
) -> SectorFundFlowResponse:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = await svc.repo.query_sector_flow(name, start, end)
    return SectorFundFlowResponse(
        sector_name=name,
        rows=[SectorFundFlowRow(ts=r.ts.isoformat(),
                                 main_net=r.main_net, pct_change=r.pct_change)
              for r in rows],
    )
```

- [ ] **Step 2: 在 `apps/api/main.py` import 和挂载**

```python
from apps.api.routes import health, market_extras, markets, sectors, symbols
...
app.include_router(sectors.router)
```

- [ ] **Step 3: 写集成测试** `tests/integration/test_api_sectors.py`:

```python
from fastapi.testclient import TestClient

from apps.api.main import app


def test_list_sectors_empty_db_returns_empty():
    with TestClient(app) as client:
        resp = client.get("/api/sectors/list")
    assert resp.status_code == 200
    assert "sectors" in resp.json()


def test_constituents_404_for_unknown():
    with TestClient(app) as client:
        resp = client.get("/api/sectors/UNKNOWN/constituents")
    assert resp.status_code == 404
```

- [ ] **Step 4: 跑测试**

```bash
. .venv/bin/activate && pytest tests/integration/test_api_sectors.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add apps/api/routes/sectors.py apps/api/main.py tests/integration/test_api_sectors.py
git commit -m "feat(api): add /api/sectors/list, /constituents, /fund_flow (plan 2 task 12)"
```

---

## Task 13: Watchlists API 路由

**Files:**
- Create: `apps/api/routes/watchlists.py`
- Modify: `apps/api/main.py`
- Create: `tests/integration/test_api_watchlists.py`

- [ ] **Step 1: 实现** `apps/api/routes/watchlists.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apps.api.deps import get_watchlist_service
from core.services.watchlist_service import WatchlistService

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])


class WatchlistDTO(BaseModel):
    id: int
    name: str
    is_archived: bool
    created_at: str


class ListResponse(BaseModel):
    watchlists: list[WatchlistDTO]


class CreateBody(BaseModel):
    name: str


class CreateResp(BaseModel):
    id: int


class RenameBody(BaseModel):
    name: str


class SymbolsResp(BaseModel):
    watchlist_id: int
    symbols: list[str]


class AddSymbolBody(BaseModel):
    symbol: str


@router.get("", response_model=ListResponse)
async def list_all(svc: WatchlistService = Depends(get_watchlist_service)) -> ListResponse:
    items = await svc.list_all()
    return ListResponse(watchlists=[
        WatchlistDTO(id=w.id, name=w.name, is_archived=w.is_archived,
                     created_at=w.created_at.isoformat())
        for w in items
    ])


@router.post("", response_model=CreateResp)
async def create(body: CreateBody, svc: WatchlistService = Depends(get_watchlist_service)) -> CreateResp:
    if not body.name.strip():
        raise HTTPException(400, "name cannot be empty")
    wl_id = await svc.create(body.name.strip())
    return CreateResp(id=wl_id)


@router.patch("/{wl_id}", status_code=204)
async def rename(wl_id: int, body: RenameBody,
                  svc: WatchlistService = Depends(get_watchlist_service)) -> None:
    if not body.name.strip():
        raise HTTPException(400, "name cannot be empty")
    await svc.rename(wl_id, body.name.strip())


@router.delete("/{wl_id}", status_code=204)
async def archive(wl_id: int, svc: WatchlistService = Depends(get_watchlist_service)) -> None:
    await svc.archive(wl_id)


@router.get("/{wl_id}/symbols", response_model=SymbolsResp)
async def list_symbols(wl_id: int,
                        svc: WatchlistService = Depends(get_watchlist_service)) -> SymbolsResp:
    syms = await svc.list_symbols(wl_id)
    return SymbolsResp(watchlist_id=wl_id, symbols=syms)


@router.post("/{wl_id}/symbols", status_code=204)
async def add_symbol(wl_id: int, body: AddSymbolBody,
                      svc: WatchlistService = Depends(get_watchlist_service)) -> None:
    await svc.add_symbol(wl_id, body.symbol)


@router.delete("/{wl_id}/symbols/{symbol}", status_code=204)
async def remove_symbol(wl_id: int, symbol: str,
                         svc: WatchlistService = Depends(get_watchlist_service)) -> None:
    await svc.remove_symbol(wl_id, symbol)
```

- [ ] **Step 2: 在 `apps/api/main.py` 挂载**

```python
from apps.api.routes import health, market_extras, markets, sectors, symbols, watchlists
...
app.include_router(watchlists.router)
```

- [ ] **Step 3: 写测试** `tests/integration/test_api_watchlists.py`:

```python
from fastapi.testclient import TestClient

from apps.api.main import app


def test_create_list_add_symbol_flow():
    with TestClient(app) as client:
        # create
        resp = client.post("/api/watchlists", json={"name": "测试 List"})
        assert resp.status_code == 200
        wl_id = resp.json()["id"]

        # list
        resp = client.get("/api/watchlists")
        names = [w["name"] for w in resp.json()["watchlists"]]
        assert "测试 List" in names

        # add symbol
        resp = client.post(f"/api/watchlists/{wl_id}/symbols",
                            json={"symbol": "600519.SH"})
        assert resp.status_code == 204

        # list symbols
        resp = client.get(f"/api/watchlists/{wl_id}/symbols")
        assert resp.json()["symbols"] == ["600519.SH"]

        # remove
        resp = client.delete(f"/api/watchlists/{wl_id}/symbols/600519.SH")
        assert resp.status_code == 204
        resp = client.get(f"/api/watchlists/{wl_id}/symbols")
        assert resp.json()["symbols"] == []

        # archive
        resp = client.delete(f"/api/watchlists/{wl_id}")
        assert resp.status_code == 204
        names = [w["name"] for w in client.get("/api/watchlists").json()["watchlists"]]
        assert "测试 List" not in names


def test_create_rejects_empty_name():
    with TestClient(app) as client:
        resp = client.post("/api/watchlists", json={"name": "   "})
    assert resp.status_code == 400
```

- [ ] **Step 4: 跑测试**

```bash
. .venv/bin/activate && pytest tests/integration/test_api_watchlists.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add apps/api/routes/watchlists.py apps/api/main.py tests/integration/test_api_watchlists.py
git commit -m "feat(api): add /api/watchlists CRUD (plan 2 task 13)"
```

---

## Task 14: North Flow API 路由

**Files:**
- Create: `apps/api/routes/north_flow.py`
- Modify: `apps/api/main.py`

- [ ] **Step 1: 实现** `apps/api/routes/north_flow.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from apps.api.deps import get_fund_flow_service
from core.services.fund_flow_service import FundFlowService

router = APIRouter(prefix="/api/north_flow", tags=["north_flow"])


class NorthFlowRow(BaseModel):
    ts: str
    hgt_net: float | None
    sgt_net: float | None


class NorthFlowResponse(BaseModel):
    rows: list[NorthFlowRow]


@router.get("", response_model=NorthFlowResponse)
async def north_flow(
    days: int = Query(30, ge=1, le=365),
    svc: FundFlowService = Depends(get_fund_flow_service),
) -> NorthFlowResponse:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = await svc.query_north(start, end)
    return NorthFlowResponse(rows=[
        NorthFlowRow(ts=r.ts.isoformat(), hgt_net=r.hgt_net, sgt_net=r.sgt_net)
        for r in rows
    ])
```

- [ ] **Step 2: 在 `apps/api/main.py` 挂载**

```python
from apps.api.routes import health, market_extras, markets, north_flow, sectors, symbols, watchlists
...
app.include_router(north_flow.router)
```

- [ ] **Step 3: 验证可访问**

```bash
. .venv/bin/activate && python -c "from apps.api.main import app; from fastapi.testclient import TestClient
with TestClient(app) as c:
    r = c.get('/api/north_flow?days=10'); print(r.status_code, r.json())"
```
Expected: 200 + `{"rows": []}`

- [ ] **Step 4: Commit**

```bash
git add apps/api/routes/north_flow.py apps/api/main.py
git commit -m "feat(api): add /api/north_flow (plan 2 task 14)"
```

---

## Task 15: Scheduler 集成基建 jobs

**Files:**
- Create: `core/scheduler/fundamentals_jobs.py`
- Modify: `core/scheduler/scheduler.py`
- Modify: `apps/api/main.py`(lifespan 加 bootstrap)

- [ ] **Step 1: 写** `core/scheduler/fundamentals_jobs.py`:

```python
from __future__ import annotations

import structlog

from core.services.fund_flow_service import FundFlowService
from core.services.sector_service import SectorService
from core.services.watchlist_service import WatchlistService

log = structlog.get_logger(__name__)


async def pull_north_flow_job(svc: FundFlowService) -> None:
    try:
        await svc.pull_north_flow()
        log.info("north_flow.pulled")
    except Exception as e:  # noqa: BLE001
        log.warning("north_flow.failed", error=str(e))


async def pull_watchlist_symbol_flow_job(
    ff: FundFlowService, wl: WatchlistService,
) -> None:
    symbols = await wl.dynamic_universe()
    pulled = 0
    for s in symbols:
        try:
            pulled += await ff.pull_symbol_flow(s)
        except Exception as e:  # noqa: BLE001
            log.warning("symbol_flow.failed", symbol=s, error=str(e))
    log.info("symbol_flow.batch_done", symbols=len(symbols), rows=pulled)


async def refresh_sectors_job(svc: SectorService) -> None:
    try:
        total = await svc.refresh_all_sina()
        log.info("sectors.refreshed", total=total)
    except Exception as e:  # noqa: BLE001
        log.warning("sectors.refresh_failed", error=str(e))


async def purge_fund_flow_job(ff: FundFlowService) -> None:
    s = await ff.repo.purge_old_symbol(days=30)
    sec = await ff.repo.purge_old_sector(days=90)
    n = await ff.repo.purge_old_north(days=30)
    log.info("fund_flow.purged", symbol=s, sector=sec, north=n)
```

- [ ] **Step 2: 修改** `core/scheduler/scheduler.py` —— 在 `build_scheduler` 末尾追加资金流/板块 jobs

```python
from apscheduler.triggers.cron import CronTrigger

from core.scheduler.fundamentals_jobs import (
    pull_north_flow_job, pull_watchlist_symbol_flow_job,
    refresh_sectors_job, purge_fund_flow_job,
)
from core.services.fund_flow_service import FundFlowService
from core.services.sector_service import SectorService
from core.services.watchlist_service import WatchlistService


def attach_fundamentals_jobs(
    sched: AsyncIOScheduler,
    *, fund_flow: FundFlowService, watchlist: WatchlistService, sector: SectorService,
) -> None:
    sched.add_job(
        pull_north_flow_job, IntervalTrigger(minutes=1),
        args=(fund_flow,),
        id="ff:north", max_instances=1, coalesce=True,
    )
    sched.add_job(
        pull_watchlist_symbol_flow_job, IntervalTrigger(minutes=30),
        args=(fund_flow, watchlist),
        id="ff:symbols", max_instances=1, coalesce=True,
    )
    sched.add_job(
        refresh_sectors_job, CronTrigger(hour=9, minute=25),
        args=(sector,),
        id="sectors:refresh", max_instances=1, coalesce=True,
    )
    sched.add_job(
        purge_fund_flow_job, CronTrigger(hour=2, minute=0),
        args=(fund_flow,),
        id="ff:purge", max_instances=1, coalesce=True,
    )
    log.info("scheduler.fundamentals_attached")
```

- [ ] **Step 3: 修改** `apps/api/main.py` 的 `lifespan` —— 调 `bootstrap_default()` 和 `attach_fundamentals_jobs`

替换原 lifespan:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    state_repo = get_state_repo()
    await state_repo.init()

    # Plan 2: bootstrap default watchlist
    await get_watchlist_service().bootstrap_default()

    registry = get_registry()
    cache = get_quote_cache()
    bar_repo = get_bar_repo()

    sched = build_scheduler(registry, cache, bar_repo)
    from core.scheduler.scheduler import attach_fundamentals_jobs
    attach_fundamentals_jobs(
        sched,
        fund_flow=get_fund_flow_service(),
        watchlist=get_watchlist_service(),
        sector=get_sector_service(),
    )
    sched.start()
    log.info("app.started", markets=registry.markets())
    try:
        yield
    finally:
        sched.shutdown(wait=False)
        log.info("app.stopped")
```

补充 import:

```python
from apps.api.deps import (
    get_bar_repo, get_fund_flow_service, get_quote_cache, get_registry,
    get_sector_service, get_state_repo, get_watchlist_service,
)
```

- [ ] **Step 4: 验证 app 能启动**

```bash
. .venv/bin/activate && python -c "
from apps.api.main import app
from fastapi.testclient import TestClient
with TestClient(app) as c:
    r = c.get('/api/health'); print('health:', r.status_code)
    r = c.get('/api/watchlists'); print('wl:', r.status_code, len(r.json()['watchlists']))
"
```
Expected: `health: 200` + `wl: 200 1`(默认 "我的关注")

- [ ] **Step 5: 跑全量单测确保没破东西**

```bash
. .venv/bin/activate && pytest tests/unit/ -q
```
Expected: 全 passed

- [ ] **Step 6: Commit**

```bash
git add core/scheduler/fundamentals_jobs.py core/scheduler/scheduler.py apps/api/main.py
git commit -m "feat(scheduler): wire fundamentals jobs + bootstrap default watchlist (plan 2 task 15)"
```

---

## Task 16: `warmup` CLI

**Files:**
- Create: `apps/warmup.py`
- Modify: `Makefile`

- [ ] **Step 1: 写 CLI** `apps/warmup.py`:

```python
"""首次回填历史 K 线到 DuckDB。

Usage:
  python -m apps.warmup --symbols 600519.SH,000858.SZ --days 365
  python -m apps.warmup --from-watchlist --days 365
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from tqdm import tqdm

from apps.api.deps import (
    get_bar_repo, get_kline_service, get_watchlist_service,
)

log = structlog.get_logger(__name__)


async def warmup(symbols: list[str], days: int) -> None:
    svc = get_kline_service()
    repo = get_bar_repo()  # 触发 DuckDB 初始化
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    for sym in tqdm(symbols, desc="warmup"):
        try:
            bars = await svc.get_bars(sym, interval="1d", start=start, end=end)
            log.info("warmup.ok", symbol=sym, count=len(bars))
        except Exception as e:  # noqa: BLE001
            log.warning("warmup.failed", symbol=sym, error=str(e))


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", help="逗号分隔 symbol,如 600519.SH,000858.SZ")
    p.add_argument("--from-watchlist", action="store_true",
                    help="使用所有未归档关注列表的并集")
    p.add_argument("--days", type=int, default=365)
    args = p.parse_args()

    if args.from_watchlist:
        symbols = await get_watchlist_service().dynamic_universe()
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        p.error("must provide --symbols or --from-watchlist")
        return

    if not symbols:
        log.warning("warmup.no_symbols")
        return
    await warmup(symbols, args.days)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Makefile 加 target**

在 `Makefile` 末尾追加(注意 TAB 缩进):

```makefile
warmup:
	. .venv/bin/activate && NO_PROXY='*' python -m apps.warmup --from-watchlist --days 365
```

- [ ] **Step 3: 手动验证(可选,需要真实网络)**

```bash
. .venv/bin/activate && NO_PROXY='*' python -m apps.warmup --symbols 600519.SH --days 30
```
Expected: 进度条 100%,日志输出 `warmup.ok symbol=600519.SH count≈20`,DuckDB 落库。

- [ ] **Step 4: Commit**

```bash
git add apps/warmup.py Makefile
git commit -m "feat(cli): add warmup script + make warmup target (plan 2 task 16)"
```

---

## Task 17: 前端类型 + API client 扩展

**Files:**
- Modify: `apps/web/lib/types.ts`
- Create: `apps/web/lib/symbol_api.ts`
- Create: `apps/web/lib/sector_api.ts`
- Create: `apps/web/lib/watchlist_api.ts`
- Create: `apps/web/lib/fund_flow_api.ts`

- [ ] **Step 1: 在 `apps/web/lib/types.ts` 末尾追加**

```ts
export type Interval = '1d' | '1wk' | '1mo' | '1m' | '5m' | '15m' | '30m' | '60m'

export interface BarDTO {
  ts: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface BarsResponse {
  symbol: string
  interval: Interval
  bars: BarDTO[]
}

export interface FundFlowRow {
  ts: string
  main_net: number | null
  super_large_net: number | null
  large_net: number | null
  medium_net: number | null
  small_net: number | null
}

export interface FundFlowResponse {
  symbol: string
  rows: FundFlowRow[]
}

export interface NorthFlowRow {
  ts: string
  hgt_net: number | null
  sgt_net: number | null
}

export interface SectorInfo {
  name: string
  classification: string
  updated_at: string
}

export interface Watchlist {
  id: number
  name: string
  is_archived: boolean
  created_at: string
}
```

- [ ] **Step 2:** 创建 `apps/web/lib/symbol_api.ts`:

```ts
import type { BarsResponse, FundFlowResponse, Interval } from './types'

export async function fetchBars(symbol: string, interval: Interval, days: number): Promise<BarsResponse> {
  const r = await fetch(`/api/symbols/${symbol}/bars?interval=${interval}&days=${days}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function fetchSymbolFundFlow(symbol: string, days = 30): Promise<FundFlowResponse> {
  const r = await fetch(`/api/symbols/${symbol}/fund_flow?days=${days}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}
```

- [ ] **Step 3:** 创建 `apps/web/lib/sector_api.ts`:

```ts
import type { SectorInfo } from './types'

export async function fetchSectorList(): Promise<{ sectors: SectorInfo[] }> {
  const r = await fetch('/api/sectors/list', { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function fetchSectorConstituents(name: string): Promise<{ sector_name: string; symbols: string[] }> {
  const r = await fetch(`/api/sectors/${encodeURIComponent(name)}/constituents`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}
```

- [ ] **Step 4:** 创建 `apps/web/lib/watchlist_api.ts`:

```ts
import type { Watchlist } from './types'

export async function listWatchlists(): Promise<{ watchlists: Watchlist[] }> {
  const r = await fetch('/api/watchlists', { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function createWatchlist(name: string): Promise<{ id: number }> {
  const r = await fetch('/api/watchlists', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function renameWatchlist(id: number, name: string): Promise<void> {
  const r = await fetch(`/api/watchlists/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!r.ok) throw new Error(`${r.status}`)
}

export async function archiveWatchlist(id: number): Promise<void> {
  const r = await fetch(`/api/watchlists/${id}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${r.status}`)
}

export async function listWatchlistSymbols(id: number): Promise<{ symbols: string[] }> {
  const r = await fetch(`/api/watchlists/${id}/symbols`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}

export async function addWatchlistSymbol(id: number, symbol: string): Promise<void> {
  const r = await fetch(`/api/watchlists/${id}/symbols`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ symbol }),
  })
  if (!r.ok) throw new Error(`${r.status}`)
}

export async function removeWatchlistSymbol(id: number, symbol: string): Promise<void> {
  const r = await fetch(`/api/watchlists/${id}/symbols/${encodeURIComponent(symbol)}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${r.status}`)
}
```

- [ ] **Step 5:** 创建 `apps/web/lib/fund_flow_api.ts`:

```ts
import type { NorthFlowRow } from './types'

export async function fetchNorthFlow(days = 30): Promise<{ rows: NorthFlowRow[] }> {
  const r = await fetch(`/api/north_flow?days=${days}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.json()
}
```

- [ ] **Step 6:** build 验证

```bash
cd apps/web && npm run build 2>&1 | tail -3
```
Expected: 构建成功。

- [ ] **Step 7: Commit**

```bash
git add apps/web/lib
git commit -m "feat(web): add typed clients for symbol/sector/watchlist/fund_flow (plan 2 task 17)"
```

---

## Task 18: KLineChart 组件(TradingView Lightweight Charts)

**Files:**
- Create: `apps/web/components/KLineChart.tsx`

- [ ] **Step 1: 实现**

```tsx
'use client'

import { createChart, IChartApi, CandlestickData, HistogramData } from 'lightweight-charts'
import { useEffect, useRef } from 'react'

import type { BarDTO } from '@/lib/types'

export interface KLineChartProps {
  bars: BarDTO[]
  height?: number
}

export function KLineChart({ bars, height = 400 }: KLineChartProps) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      height,
      layout: { background: { color: '#0a0a0a' }, textColor: '#d4d4d4' },
      grid: { vertLines: { color: '#262626' }, horzLines: { color: '#262626' } },
      timeScale: { timeVisible: true },
    })
    chartRef.current = chart

    const candle = chart.addCandlestickSeries({
      upColor: '#22c55e', downColor: '#ef4444',
      borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
    })
    const volume = chart.addHistogramSeries({
      priceFormat: { type: 'volume' }, priceScaleId: '',
      color: '#525252',
    })
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })

    const candleData: CandlestickData[] = bars.map((b) => ({
      time: (new Date(b.ts).getTime() / 1000) as any,
      open: b.open, high: b.high, low: b.low, close: b.close,
    }))
    const volData: HistogramData[] = bars.map((b) => ({
      time: (new Date(b.ts).getTime() / 1000) as any,
      value: b.volume,
      color: b.close >= b.open ? '#22c55e44' : '#ef444444',
    }))
    candle.setData(candleData)
    volume.setData(volData)
    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth })
    })
    ro.observe(ref.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [bars, height])

  return <div ref={ref} className="w-full" />
}
```

- [ ] **Step 2: build 验证**

```bash
cd apps/web && npm run build 2>&1 | tail -5
```
Expected: 构建成功。

- [ ] **Step 3: Commit**

```bash
git add apps/web/components/KLineChart.tsx
git commit -m "feat(web): add KLineChart component using TradingView Lightweight Charts (plan 2 task 18)"
```

---

## Task 19: 个股详情页 `/symbol/[code]`

**Files:**
- Create: `apps/web/app/symbol/[code]/page.tsx`
- Create: `apps/web/components/FundFlowPanel.tsx`

- [ ] **Step 1:** 创建 `apps/web/components/FundFlowPanel.tsx`:

```tsx
'use client'

import useSWR from 'swr'

import { fetchSymbolFundFlow } from '@/lib/symbol_api'

function fmt(v: number | null): string {
  if (v == null) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(2)} 万`
  return v.toFixed(0)
}

export function FundFlowPanel({ symbol }: { symbol: string }) {
  const { data, isLoading } = useSWR(
    `fund:${symbol}`, () => fetchSymbolFundFlow(symbol, 30),
    { refreshInterval: 60_000 },
  )

  return (
    <section className="rounded-lg border border-neutral-800 p-4 bg-neutral-950">
      <h2 className="text-lg font-semibold mb-3">资金流(近 30 日)</h2>
      {isLoading && <p className="text-sm text-neutral-500">加载中…</p>}
      {data && data.rows.length === 0 && (
        <p className="text-sm text-neutral-500">暂无数据。需先 `make warmup` 或等待 scheduler 拉取。</p>
      )}
      {data && data.rows.length > 0 && (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-neutral-400 text-xs">
              <th className="text-left py-1">日期</th>
              <th className="text-right">主力净流入</th>
              <th className="text-right">超大单</th>
              <th className="text-right">大单</th>
              <th className="text-right">中单</th>
              <th className="text-right">小单</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.slice(-15).reverse().map((r) => (
              <tr key={r.ts} className="border-t border-neutral-800">
                <td className="py-1 font-mono">{r.ts.slice(0, 10)}</td>
                <td className={`text-right tabular-nums ${(r.main_net ?? 0) >= 0 ? 'text-red-400' : 'text-green-400'}`}>{fmt(r.main_net)}</td>
                <td className="text-right tabular-nums">{fmt(r.super_large_net)}</td>
                <td className="text-right tabular-nums">{fmt(r.large_net)}</td>
                <td className="text-right tabular-nums">{fmt(r.medium_net)}</td>
                <td className="text-right tabular-nums">{fmt(r.small_net)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
```

- [ ] **Step 2:** 创建 `apps/web/app/symbol/[code]/page.tsx`:

```tsx
'use client'

import { useState } from 'react'
import useSWR from 'swr'

import { KLineChart } from '@/components/KLineChart'
import { FundFlowPanel } from '@/components/FundFlowPanel'
import { fetchBars } from '@/lib/symbol_api'
import type { Interval } from '@/lib/types'

const INTERVALS: { key: Interval; label: string }[] = [
  { key: '1d', label: '日线' },
  { key: '1wk', label: '周线' },
  { key: '1mo', label: '月线' },
  { key: '60m', label: '60分' },
  { key: '15m', label: '15分' },
  { key: '5m', label: '5分' },
]

export default function SymbolPage({ params }: { params: { code: string } }) {
  const symbol = decodeURIComponent(params.code)
  const [interval, setInterval] = useState<Interval>('1d')

  const { data, error, isLoading } = useSWR(
    `bars:${symbol}:${interval}`,
    () => fetchBars(symbol, interval, interval.endsWith('d') || interval.endsWith('wk') || interval.endsWith('mo') ? 365 : 5),
    { refreshInterval: 60_000 },
  )

  return (
    <main className="p-6 max-w-7xl mx-auto space-y-4">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold font-mono">{symbol}</h1>
          <p className="text-xs text-neutral-500 mt-1">A 股个股详情</p>
        </div>
        <a href="/dashboard" className="text-xs text-neutral-400 hover:text-neutral-200">← 返回 Dashboard</a>
      </header>

      <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
        <div className="flex gap-1 mb-3">
          {INTERVALS.map((iv) => (
            <button
              key={iv.key}
              onClick={() => setInterval(iv.key)}
              className={`px-2 py-1 text-xs rounded ${interval === iv.key ? 'bg-neutral-700 text-white' : 'bg-neutral-900 text-neutral-400 hover:bg-neutral-800'}`}
            >
              {iv.label}
            </button>
          ))}
        </div>
        {isLoading && <p className="text-sm text-neutral-500">加载 K 线…</p>}
        {error && <p className="text-sm text-red-400">加载失败:{String(error)}</p>}
        {data && <KLineChart bars={data.bars} height={420} />}
      </section>

      <FundFlowPanel symbol={symbol} />
    </main>
  )
}
```

- [ ] **Step 3: build**

```bash
cd apps/web && npm run build 2>&1 | tail -10
```
Expected: 增加 `/symbol/[code]` 路由,构建成功。

- [ ] **Step 4: Commit**

```bash
git add apps/web/components/FundFlowPanel.tsx apps/web/app/symbol
git commit -m "feat(web): add /symbol/[code] detail page with KLine + fund flow (plan 2 task 19)"
```

---

## Task 20: 关注列表页 `/watchlist`

**Files:**
- Create: `apps/web/app/watchlist/page.tsx`

- [ ] **Step 1: 实现**

```tsx
'use client'

import { useState } from 'react'
import useSWR, { mutate } from 'swr'

import { addWatchlistSymbol, listWatchlists, listWatchlistSymbols, removeWatchlistSymbol } from '@/lib/watchlist_api'

export default function WatchlistPage() {
  const { data: lists } = useSWR('wls', listWatchlists)
  const [activeId, setActiveId] = useState<number | null>(null)
  const currentId = activeId ?? lists?.watchlists[0]?.id ?? null

  const { data: items } = useSWR(
    currentId ? `wl:${currentId}` : null,
    () => listWatchlistSymbols(currentId!),
  )

  const [newSym, setNewSym] = useState('')

  async function onAdd() {
    if (!currentId || !newSym.trim()) return
    await addWatchlistSymbol(currentId, newSym.trim().toUpperCase())
    setNewSym('')
    mutate(`wl:${currentId}`)
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
        <a href="/dashboard" className="text-xs text-neutral-400 hover:text-neutral-200">← Dashboard</a>
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

      <div className="flex gap-2 items-center">
        <input
          value={newSym}
          onChange={(e) => setNewSym(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') onAdd() }}
          placeholder="600519.SH"
          className="bg-neutral-900 border border-neutral-700 text-sm rounded px-2 py-1 font-mono text-white"
        />
        <button onClick={onAdd} className="bg-blue-600 text-white text-sm rounded px-3 py-1">
          加入
        </button>
      </div>

      <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
        {items?.symbols.length === 0 && <p className="text-sm text-neutral-500">空</p>}
        <ul className="space-y-1">
          {items?.symbols.map((s) => (
            <li key={s} className="flex justify-between items-center py-1 border-b border-neutral-800">
              <a href={`/symbol/${encodeURIComponent(s)}`} className="font-mono hover:text-blue-400">{s}</a>
              <button onClick={() => onRemove(s)} className="text-xs text-red-400 hover:text-red-300">移除</button>
            </li>
          ))}
        </ul>
      </section>
    </main>
  )
}
```

- [ ] **Step 2: build + 在 layout 加导航**

修改 `apps/web/app/layout.tsx`,在 `<body>` 内顶部加 `<nav>`:

```tsx
import './globals.css'
import type { Metadata } from 'next'

export const metadata: Metadata = { title: 'MarketPulse', description: '四市场行情监控' }

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen">
        <nav className="border-b border-neutral-800 bg-neutral-950 px-6 py-3 flex gap-6 text-sm">
          <a href="/dashboard" className="font-bold">MarketPulse</a>
          <a href="/dashboard" className="text-neutral-400 hover:text-neutral-200">Dashboard</a>
          <a href="/watchlist" className="text-neutral-400 hover:text-neutral-200">我的关注</a>
        </nav>
        {children}
      </body>
    </html>
  )
}
```

- [ ] **Step 3: build**

```bash
cd apps/web && npm run build 2>&1 | tail -10
```
Expected: 多出 `/watchlist`,构建成功。

- [ ] **Step 4: Commit**

```bash
git add apps/web/app/watchlist apps/web/app/layout.tsx
git commit -m "feat(web): add /watchlist page with CRUD + nav (plan 2 task 20)"
```

---

## Task 21: 板块详情页 `/sector/[name]`

**Files:**
- Create: `apps/web/app/sector/[name]/page.tsx`

- [ ] **Step 1: 实现**

```tsx
'use client'

import useSWR from 'swr'

import { fetchSectorConstituents } from '@/lib/sector_api'

export default function SectorPage({ params }: { params: { name: string } }) {
  const name = decodeURIComponent(params.name)
  const { data, error, isLoading } = useSWR(
    `sector:${name}`, () => fetchSectorConstituents(name),
  )

  return (
    <main className="p-6 max-w-7xl mx-auto space-y-4">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold">{name}</h1>
        <a href="/dashboard" className="text-xs text-neutral-400 hover:text-neutral-200">← Dashboard</a>
      </header>

      <section className="rounded-lg border border-neutral-800 bg-neutral-950 p-4">
        <h2 className="text-lg font-semibold mb-3">成分股</h2>
        {isLoading && <p className="text-sm text-neutral-500">加载中…</p>}
        {error && (
          <p className="text-sm text-yellow-400">板块数据尚未抓取。运行 scheduler 或调用 sector refresh。</p>
        )}
        {data && (
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
            {data.symbols.map((s) => (
              <a key={s} href={`/symbol/${encodeURIComponent(s)}`}
                className="font-mono text-sm py-1 px-2 rounded bg-neutral-900 hover:bg-neutral-800 hover:text-blue-400">
                {s}
              </a>
            ))}
          </div>
        )}
      </section>
    </main>
  )
}
```

- [ ] **Step 2: 在 dashboard 热力图加点击跳转**

修改 `apps/web/components/SectorHeatmap.tsx`,把外层 div 换成 `<a>`:

替换 `<div ... key={s.name} className={...} title={...}>` 包裹的整块为:

```tsx
<a
  key={s.name}
  href={`/sector/${encodeURIComponent(s.name)}`}
  className={clsx(
    'rounded p-2 text-white text-xs flex flex-col justify-between hover:opacity-80',
    bgFor(s.change_pct),
  )}
  title={`领涨:${s.leader_name} ${s.leader_change_pct.toFixed(2)}% / 公司家数:${s.company_count}`}
>
  <div className="font-medium truncate">{s.name}</div>
  <div className="tabular-nums font-mono">
    {s.change_pct >= 0 ? '+' : ''}{s.change_pct.toFixed(2)}%
  </div>
</a>
```

- [ ] **Step 3: build**

```bash
cd apps/web && npm run build 2>&1 | tail -10
```
Expected: 多出 `/sector/[name]`,构建成功。

- [ ] **Step 4: Commit**

```bash
git add apps/web/app/sector apps/web/components/SectorHeatmap.tsx
git commit -m "feat(web): add /sector/[name] page + clickable heatmap (plan 2 task 21)"
```

---

## Task 22: 端到端集成冒烟 + 收尾

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 跑全量后端测试**

```bash
. .venv/bin/activate && pytest -q
```
Expected: 全 passed(单元 + 集成,约 65+)

- [ ] **Step 2: 启动 dev 联调**

```bash
lsof -ti :8787,3000 | xargs kill -9 2>/dev/null
. .venv/bin/activate && NO_PROXY='*' uvicorn apps.api.main:app --port 8787 > /tmp/mp-be.log 2>&1 &
cd apps/web && npm run dev > /tmp/mp-fe.log 2>&1 &
sleep 5
curl -s http://127.0.0.1:8787/api/health | python3 -m json.tool | head -10
curl -s http://127.0.0.1:8787/api/watchlists
echo ""
curl -s -o /dev/null -w "dashboard: %{http_code}\n" http://localhost:3000/dashboard
curl -s -o /dev/null -w "watchlist: %{http_code}\n" http://localhost:3000/watchlist
curl -s -o /dev/null -w "symbol detail: %{http_code}\n" http://localhost:3000/symbol/600519.SH
```
Expected: 全 200。

- [ ] **Step 3: 拉一次 warmup 真数据**

```bash
. .venv/bin/activate && NO_PROXY='*' python -m apps.warmup --symbols 600519.SH,000858.SZ,300750.SZ --days 90
```
Expected: 进度条 100%,日志显示 ~60 条 bars 落库 per symbol。

- [ ] **Step 4: 浏览器验收**

打开 `http://localhost:3000/symbol/600519.SH`,看到:
- 顶部:symbol 名
- K 线图:茅台近 1 年日线
- 切换"周线/月线/15分"等多周期
- 资金流面板:可能空(还没拉),或显示几条

打开 `http://localhost:3000/watchlist`:
- 默认有 "我的关注" tab
- 输入 `600519.SH`,加入,刷新看到
- 点击跳转个股详情

打开 `http://localhost:3000/dashboard`:
- 行业热力图,点击玻璃行业 → 跳转 `/sector/玻璃行业`(可能 404 因为板块成分还没抓,显示警告)

- [ ] **Step 5: 更新 `README.md`**

在原 README 末尾追加:

```markdown
## Plan 2 新增功能

- `/symbol/[code]`:个股详情(K 线 + 资金流)
- `/watchlist`:自定义关注列表
- `/sector/[name]`:板块详情

首次使用:

```bash
make warmup            # 回填关注列表 symbols 的 1 年日线
```

API 速查:

- `GET /api/symbols/{sym}/bars?interval=1d&days=365`
- `GET /api/symbols/{sym}/fund_flow?days=30`
- `GET /api/sectors/list`
- `GET /api/sectors/{name}/constituents`
- `GET /api/watchlists` (+ POST/PATCH/DELETE)
- `GET /api/north_flow?days=30`
```

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: add Plan 2 features to README (plan 2 task 22 — complete)"
```

---

## Self-Review

**Spec 覆盖检查**

| Spec 要求 | 覆盖 Task |
|---|---|
| §2.9 KLine Service | Task 6, 7 |
| §2.10 Sector Service | Task 4, 9, 12 |
| §2.11 Watchlist Service | Task 3, 8, 13, 20 |
| §2.12 FundFlow Service | Task 5, 10, 11, 14 |
| §3.6 K 线按需拉取数据流 | Task 7 |
| §3.7 资金流采集 | Task 10, 15 |
| §3.8 板块成分刷新 | Task 9, 15 |
| §7.4 5 张新表 | Task 2 |
| V1-A0 K 线可查 | Task 6-7, 11, 18-19 |
| V1-A0 板块成分入库 | Task 4, 9, 15, 21 |
| V1-A0 自定义关注 CRUD | Task 3, 8, 13, 20 |
| V1-A0 资金流时间序列 | Task 5, 10, 11, 14, 15 |
| V1-A0 个股详情页 | Task 19 |
| `make warmup` | Task 16 |

**Placeholder 扫描**:全文搜索 TBD/TODO/implement later → 无。

**类型一致性**:
- `WatchlistRepo.list_watchlists` / `archive_watchlist` / `add_symbol` 等方法名在 Task 3 定义,Task 8 / 13 / 20 一致引用
- `FundFlowSnapshot.kind ∈ {symbol, sector, north}` 在 Task 1 定义,Task 5/10/11/14 一致
- `Interval` 类型在前端 Task 17 定义,Task 18-19 一致引用
- API DTO 字段(`bars`, `rows`, `watchlists`, `symbols`, `gainers`, `losers`)前后端命名一致

**Scope 检查**:22 个 task,严格按 TDD,每个完成后 commit,任一点中断都可独立验收。

---

## 执行选择

Plan complete and saved to `docs/superpowers/plans/2026-05-13-marketpulse-plan-2-fundamentals.md`.

执行方式两种:
1. **Subagent-Driven** —— 每 task 一个独立 subagent(之前 Plan 1 经验:权限拦截较多,建议跳过)
2. **Inline Execution(推荐)** —— 主会话顺序执行,checkpoint 在 task 5 / 11 / 15 / 22 停下让你看

我准备走 **Inline**。开始执行吗?
