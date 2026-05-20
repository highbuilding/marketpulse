# 美股 K 线降级 + akshare 1d 主源接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让美股详情页 1d K 线 + 1d CD 信号立即恢复,通过引入 akshare 1d 主源 + yfinance backup 熔断器避免 IP ban,intraday 本期不接入。

**Architecture:**
- `SymbolDirectoryRepo` schema 自动升级加 `akshare_code` 列,缓存 ticker → akshare 格式映射
- `USAdapter` fetch_history 改为"akshare 主 → yfinance 备(独立 CircuitBreaker `fail=2 / reset=1800s`)";新增 `_resolve_akshare_code(symbol)` 试 `105/106/107` 前缀
- 前端美股 tab 仅显示 1d/1wk/1mo K 线 + 1d CD 信号(intraday 暂未接入)

**Tech Stack:** Python 3.11(akshare via `ak_call` 全局锁、aiosqlite、pandas、structlog、pytest)、Next.js 14(TypeScript、tsc)

**Spec:** `docs/superpowers/specs/2026-05-20-us-1d-akshare-fallback-design.md`

---

## File Structure

修改:
- `core/persistence/symbol_directory_repo.py` — schema 自动升级 + `get_akshare_code` / `set_akshare_code`
- `core/adapters/us.py` — backup_cb 拆分;dir_repo 注入;_fetch_history_akshare;_resolve_akshare_code;fetch_history 改路径
- `apps/api/deps.py::get_kline_service` — 给 us adapter 注 dir_repo
- `apps/web/lib/intervals.ts` — 美股 K 线/信号 tab 仅 1d/1wk/1mo / 1d
- `tests/unit/persistence/test_symbol_directory_repo.py` (创建)
- `tests/unit/adapters/test_us.py` — 加 5 个测试
- `docs/TODO.md` — 加未实施事项

---

## Task 1: SymbolDirectoryRepo 自动 schema 升级 + akshare_code 读写

**Files:**
- Modify: `core/persistence/symbol_directory_repo.py`
- Create: `tests/unit/persistence/test_symbol_directory_repo.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/persistence/test_symbol_directory_repo.py
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import aiosqlite
import pytest

from core.persistence.symbol_directory_repo import SymbolDirectoryRepo


@pytest.fixture
def tmp_db_path(tmp_path):
    return str(tmp_path / "state.db")


async def _init_legacy_schema(db_path: str) -> None:
    """模拟旧版 schema(无 akshare_code 列)。"""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE symbol_directory (
              symbol TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              market TEXT NOT NULL,
              updated_at TIMESTAMP NOT NULL
            )
        """)
        await db.commit()


@pytest.mark.asyncio
async def test_ensure_schema_adds_akshare_code(tmp_db_path):
    await _init_legacy_schema(tmp_db_path)
    # Reset class-level flag so the test triggers schema check
    SymbolDirectoryRepo._schema_ensured = False
    repo = SymbolDirectoryRepo(tmp_db_path)
    # 触发 _ensure_schema 走一次
    await repo.upsert_many([("AAPL", "Apple Inc.", "us")])
    # 验证 akshare_code 列存在
    async with aiosqlite.connect(tmp_db_path) as db:
        cur = await db.execute("PRAGMA table_info(symbol_directory)")
        cols = {r[1] for r in await cur.fetchall()}
    assert "akshare_code" in cols


@pytest.mark.asyncio
async def test_ensure_schema_idempotent(tmp_db_path):
    """已有 akshare_code 列时不抛错。"""
    await _init_legacy_schema(tmp_db_path)
    SymbolDirectoryRepo._schema_ensured = False
    repo = SymbolDirectoryRepo(tmp_db_path)
    await repo.upsert_many([("AAPL", "Apple Inc.", "us")])
    SymbolDirectoryRepo._schema_ensured = False  # 强制再触发
    await repo.upsert_many([("MSFT", "Microsoft", "us")])  # 不应抛 duplicate column
    async with aiosqlite.connect(tmp_db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM symbol_directory")
        count = (await cur.fetchone())[0]
    assert count == 2


@pytest.mark.asyncio
async def test_get_set_akshare_code(tmp_db_path):
    await _init_legacy_schema(tmp_db_path)
    SymbolDirectoryRepo._schema_ensured = False
    repo = SymbolDirectoryRepo(tmp_db_path)
    await repo.upsert_many([("AAPL", "Apple Inc.", "us")])
    # 初始 None
    assert await repo.get_akshare_code("AAPL") is None
    # set 后命中
    await repo.set_akshare_code("AAPL", "105.AAPL")
    assert await repo.get_akshare_code("AAPL") == "105.AAPL"
    # 不存在的 symbol
    assert await repo.get_akshare_code("UNKNOWN") is None
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
cd /Users/xiangrong/stock/marketpulse
. .venv/bin/activate && pytest tests/unit/persistence/test_symbol_directory_repo.py -v
```
Expected: 3 个 fail(`_ensure_schema` / `get_akshare_code` / `set_akshare_code` 都不存在)

- [ ] **Step 3: 改 `core/persistence/symbol_directory_repo.py`**

整段重写为:

```python
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite


class SymbolDirectoryRepo:
    # 类级 flag,保证 ALTER 只跑一次(多实例共享)
    _schema_ensured: bool = False

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if not SymbolDirectoryRepo._schema_ensured:
                await self._ensure_schema(db)
                SymbolDirectoryRepo._schema_ensured = True
            yield db

    @staticmethod
    async def _ensure_schema(db) -> None:
        """幂等加 akshare_code 列(老库升级用)。"""
        cur = await db.execute("PRAGMA table_info(symbol_directory)")
        cols = {r[1] for r in await cur.fetchall()}
        if "akshare_code" not in cols:
            await db.execute(
                "ALTER TABLE symbol_directory ADD COLUMN akshare_code TEXT"
            )
            await db.commit()

    async def upsert_many(self, items: list[tuple[str, str, str]]) -> int:
        """items: list[(symbol, name, market)]."""
        if not items:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        rows = [(s, n, m, now) for s, n, m in items]
        async with self._connect() as db:
            await db.executemany("""
                INSERT INTO symbol_directory (symbol, name, market, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                  name=excluded.name, market=excluded.market, updated_at=excluded.updated_at
            """, rows)
            await db.commit()
        return len(rows)

    async def get_name(self, symbol: str) -> str | None:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT name FROM symbol_directory WHERE symbol = ?", (symbol,),
            )
            row = await cur.fetchone()
        return row["name"] if row else None

    async def get_names(self, symbols: list[str]) -> dict[str, str]:
        if not symbols:
            return {}
        placeholders = ",".join("?" * len(symbols))
        async with self._connect() as db:
            cur = await db.execute(
                f"SELECT symbol, name FROM symbol_directory WHERE symbol IN ({placeholders})",
                symbols,
            )
            rows = await cur.fetchall()
        return {r["symbol"]: r["name"] for r in rows}

    async def search(
        self, query: str, limit: int = 20,
        *, market: str | None = None,
    ) -> list[tuple[str, str, str]]:
        q = query.strip()
        if not q:
            return []
        like = f"%{q}%"
        prefix = f"{q.upper()}%"
        params: list = [prefix, like]
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

    async def count(self) -> int:
        async with self._connect() as db:
            cur = await db.execute("SELECT COUNT(*) AS c FROM symbol_directory")
            row = await cur.fetchone()
        return int(row["c"])

    async def get_akshare_code(self, symbol: str) -> str | None:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT akshare_code FROM symbol_directory WHERE symbol = ?",
                (symbol,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        code = row["akshare_code"]
        return code if code else None

    async def set_akshare_code(self, symbol: str, code: str) -> None:
        """更新 symbol 的 akshare_code(symbol 必须已在 directory)。"""
        now = datetime.now(timezone.utc).isoformat()
        async with self._connect() as db:
            await db.execute(
                "UPDATE symbol_directory SET akshare_code = ?, updated_at = ? "
                "WHERE symbol = ?",
                (code, now, symbol),
            )
            await db.commit()
```

- [ ] **Step 4: 跑测试,确认 PASS**

```bash
. .venv/bin/activate && pytest tests/unit/persistence/test_symbol_directory_repo.py -v
```
Expected: 3 passed

- [ ] **Step 5: 后端 import smoke + 启动验证 schema 升级**

```bash
. .venv/bin/activate && python -c "from apps.api.main import app; print('ok')"
pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null; sleep 1
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' > /tmp/api.log 2>&1 &
disown
sleep 6
grep -c FATAL /tmp/api.log
. .venv/bin/activate && python -c "
import sqlite3
con = sqlite3.connect('data/state.db')
cols = [r[1] for r in con.execute('PRAGMA table_info(symbol_directory)')]
print('cols:', cols)
assert 'akshare_code' in cols, 'akshare_code missing'
print('SCHEMA OK')
"
pkill -9 -f "uvicorn apps.api.main:app"; sleep 1
```
Expected: `ok`、FATAL=0、`SCHEMA OK`

- [ ] **Step 6: Commit**

```bash
git add core/persistence/symbol_directory_repo.py tests/unit/persistence/test_symbol_directory_repo.py
git commit -m "feat(directory): symbol_directory schema 自动升级加 akshare_code 列 + 读写"
```

---

## Task 2: USAdapter backup_cb 拆分 + dir_repo 注入

**Files:**
- Modify: `core/adapters/us.py`(class 字段 + `__init__` 签名)
- Modify: `tests/unit/adapters/test_us.py`(加 1 个测试)

- [ ] **Step 1: 加测试**

把以下追加到 `tests/unit/adapters/test_us.py` 末尾:

```python
def test_us_adapter_has_backup_cb_with_strict_params():
    """yfinance backup 必须有独立 CircuitBreaker, 比 primary 更激进。"""
    adapter = USAdapter()
    assert hasattr(adapter, "backup_cb")
    assert adapter.backup_cb.fail_threshold == 2
    assert adapter.backup_cb.reset_after_s == 1800
    # 与 primary 是独立实例
    assert adapter.backup_cb is not adapter.primary_cb


def test_us_adapter_accepts_dir_repo_optional():
    """dir_repo 可选注入, 不传时 akshare 路径不可用(向后兼容)。"""
    adapter = USAdapter()
    assert adapter.dir_repo is None
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py::test_us_adapter_has_backup_cb_with_strict_params -v
```
Expected: AttributeError

- [ ] **Step 3: 改 `core/adapters/us.py::USAdapter.__init__`**

把:
```python
    def __init__(self) -> None:
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.secret = os.getenv("ALPACA_SECRET_KEY")
        self.has_primary = bool(self.api_key and self.secret)
        self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=300)
```

改为:
```python
    def __init__(self, dir_repo: "SymbolDirectoryRepo | None" = None) -> None:
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.secret = os.getenv("ALPACA_SECRET_KEY")
        self.has_primary = bool(self.api_key and self.secret)
        # primary: Alpaca, 中等阈值
        self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=300)
        # backup: yfinance, 更激进 — 429 / 网络失败 2 次熔断 30 分钟
        self.backup_cb = CircuitBreaker(fail_threshold=2, reset_after_s=1800)
        # akshare 路径需要 dir_repo 缓存 ticker → akshare_code 映射
        self.dir_repo = dir_repo
```

文件顶部 imports 加 `TYPE_CHECKING` 块(避免循环 import):

```python
from typing import Callable, TYPE_CHECKING
if TYPE_CHECKING:
    from core.persistence.symbol_directory_repo import SymbolDirectoryRepo
```

(如已有 `from typing import` 行就并入。)

- [ ] **Step 4: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py -v
```
Expected: 全部 pass(原 17 + 新 2 = 19)

- [ ] **Step 5: import smoke**

```bash
. .venv/bin/activate && python -c "from apps.api.main import app; print('ok')"
```
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add core/adapters/us.py tests/unit/adapters/test_us.py
git commit -m "feat(us): backup_cb 独立熔断器(2/1800s) + dir_repo 可选注入"
```

---

## Task 3: USAdapter `_resolve_akshare_code` 试探 + 缓存

**Files:**
- Modify: `core/adapters/us.py`(加 module-level 常量 + class method `_resolve_akshare_code`)
- Modify: `tests/unit/adapters/test_us.py`(加 3 个测试)

- [ ] **Step 1: 加测试**

```python
@pytest.mark.asyncio
async def test_resolve_akshare_code_cached():
    """已缓存时直接返回, 不调 ak_call。"""
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value="105.AAPL")
    adapter = USAdapter(dir_repo=fake_repo)
    with patch("core.adapters.us.ak_call") as mock_ak:
        result = await adapter._resolve_akshare_code("AAPL")
    assert result == "105.AAPL"
    mock_ak.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_akshare_code_probes_105_first():
    """未缓存 → 试 105.X, 命中后回写。"""
    import pandas as pd
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value=None)
    fake_repo.set_akshare_code = AsyncMock()
    adapter = USAdapter(dir_repo=fake_repo)
    fake_df = pd.DataFrame({"日期": ["2026-01-02"], "开盘": [180.0],
                            "收盘": [181.0], "最高": [181.5], "最低": [179.5],
                            "成交量": [1000000]})
    with patch("core.adapters.us.ak_call", new=AsyncMock(return_value=fake_df)) as mock_ak:
        result = await adapter._resolve_akshare_code("AAPL")
    assert result == "105.AAPL"
    fake_repo.set_akshare_code.assert_awaited_once_with("AAPL", "105.AAPL")
    # 只调一次 ak_call(105 试探立即命中)
    assert mock_ak.await_count == 1


@pytest.mark.asyncio
async def test_resolve_akshare_code_falls_back_106():
    """105 抛异常 → 试 106 → 命中。"""
    import pandas as pd
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value=None)
    fake_repo.set_akshare_code = AsyncMock()
    adapter = USAdapter(dir_repo=fake_repo)
    fake_df = pd.DataFrame({"日期": ["2026-01-02"], "开盘": [200.0],
                            "收盘": [201.0], "最高": [202.0], "最低": [199.0],
                            "成交量": [500000]})

    async def fake_ak_call(func_name, *args, **kwargs):
        if "105." in kwargs.get("symbol", ""):
            raise RuntimeError("not found")
        return fake_df

    with patch("core.adapters.us.ak_call", side_effect=fake_ak_call):
        result = await adapter._resolve_akshare_code("XYZ")
    assert result == "106.XYZ"
    fake_repo.set_akshare_code.assert_awaited_once_with("XYZ", "106.XYZ")


@pytest.mark.asyncio
async def test_resolve_akshare_code_all_fail_returns_none():
    """三种前缀全失败 → None, 不写库。"""
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value=None)
    fake_repo.set_akshare_code = AsyncMock()
    adapter = USAdapter(dir_repo=fake_repo)
    with patch("core.adapters.us.ak_call",
               side_effect=RuntimeError("not found")):
        result = await adapter._resolve_akshare_code("ZZZZ")
    assert result is None
    fake_repo.set_akshare_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_akshare_code_no_repo_returns_none():
    """没注入 dir_repo → 直接返 None。"""
    adapter = USAdapter()  # dir_repo=None
    result = await adapter._resolve_akshare_code("AAPL")
    assert result is None
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py -k resolve_akshare -v
```
Expected: AttributeError(`_resolve_akshare_code` 不存在)

- [ ] **Step 3: 改 `core/adapters/us.py`**

文件顶部 imports 区加(如已有忽略):
```python
from core.integrations.akshare import ak_call
```

在 `_to_yfinance_ticker` 之后,`class USAdapter` 之前加 module-level 常量:

```python
# akshare 美股交易所代码: 105=NASDAQ, 106=NYSE, 107=AMEX
_AKSHARE_PREFIXES: tuple[str, ...] = ("105", "106", "107")
```

在 `class USAdapter` 内,`verify_ticker` 之后加方法:

```python
    async def _resolve_akshare_code(self, symbol: str) -> str | None:
        """返回 akshare 美股 code(如 '105.AAPL')。

        - 已缓存 → 直接返回
        - 未缓存 → 试 105/106/107, 首次成功后回写 directory
        - 全失败或未注入 dir_repo → None
        """
        if self.dir_repo is None:
            return None
        cached = await self.dir_repo.get_akshare_code(symbol)
        if cached:
            return cached

        # akshare 不接受 BRK.B, 试横杠版本(yfinance 格式 BRK-B)+ 原版
        candidates = []
        yf_sym = _to_yfinance_ticker(symbol)
        candidates.append(yf_sym)
        if symbol != yf_sym:
            candidates.append(symbol)

        for candidate in candidates:
            for prefix in _AKSHARE_PREFIXES:
                ak_code = f"{prefix}.{candidate}"
                try:
                    df = await ak_call(
                        "stock_us_hist",
                        symbol=ak_code, period="daily",
                        start_date="20260101", end_date="20260110",
                        adjust="",
                        caller=f"us.resolve:{symbol}:{ak_code}",
                    )
                    if df is not None and len(df) > 0:
                        await self.dir_repo.set_akshare_code(symbol, ak_code)
                        log.info("us.akshare_code_resolved",
                                 symbol=symbol, code=ak_code)
                        return ak_code
                except Exception:  # noqa: BLE001
                    continue
        log.warning("us.akshare_code_unresolved", symbol=symbol)
        return None
```

- [ ] **Step 4: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py -k resolve_akshare -v
```
Expected: 5 passed

- [ ] **Step 5: 全量回归**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py -v
```
Expected: 24 passed(原 19 + 新 5)

- [ ] **Step 6: import smoke**

```bash
. .venv/bin/activate && python -c "from apps.api.main import app; print('ok')"
```

- [ ] **Step 7: Commit**

```bash
git add core/adapters/us.py tests/unit/adapters/test_us.py
git commit -m "feat(us): _resolve_akshare_code 试 105/106/107 前缀 + directory 缓存"
```

---

## Task 4: USAdapter fetch_history 改路径(akshare 主 / yfinance 备)

**Files:**
- Modify: `core/adapters/us.py`(加 `_fetch_history_akshare`;现 `fetch_history` 整体重写为分发器;原内容重命名为 `_fetch_history_yfinance`)
- Modify: `core/adapters/us.py`(`fetch_snapshot` 加 backup_cb)
- Modify: `tests/unit/adapters/test_us.py`(加 5 个测试)

- [ ] **Step 1: 加测试**

```python
@pytest.mark.asyncio
async def test_fetch_history_uses_akshare_when_resolved():
    """akshare 主源命中时返回 akshare 数据,不调 yfinance。"""
    import pandas as pd
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value="105.AAPL")
    adapter = USAdapter(dir_repo=fake_repo)
    fake_df = pd.DataFrame({
        "日期": ["2026-05-19"], "开盘": [296.97], "收盘": [298.97],
        "最高": [300.51], "最低": [296.35], "成交量": [42243561],
    })
    with patch("core.adapters.us.ak_call", new=AsyncMock(return_value=fake_df)) as mock_ak, \
         patch("core.adapters.us.yf") as mock_yf:
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    mock_yf.download.assert_not_called()
    assert len(bars) == 1
    assert bars[0].symbol == "AAPL"
    assert bars[0].market == "us"
    assert bars[0].interval == "1d"
    # 5/19 ET 00:00 EDT (UTC-4) → UTC 5/19 04:00
    assert bars[0].ts == datetime(2026, 5, 19, 4, 0, tzinfo=timezone.utc)
    assert bars[0].close == Decimal("298.97")


@pytest.mark.asyncio
async def test_fetch_history_falls_back_to_yfinance_when_akshare_fails():
    """akshare 抛 → fallback yfinance(backup_cb 未熔断时)。"""
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value="105.AAPL")
    adapter = USAdapter(dir_repo=fake_repo)
    yf_df = _mock_history_df()
    with patch("core.adapters.us.ak_call",
               side_effect=RuntimeError("akshare network")), \
         patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=yf_df)
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert len(bars) == 2  # _mock_history_df 返 2 行
    mock_yf.download.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_history_raises_when_yfinance_circuit_open():
    """akshare 失败 + yfinance backup_cb 已熔断 → AdapterError。"""
    from core.adapters.base import AdapterError
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value="105.AAPL")
    adapter = USAdapter(dir_repo=fake_repo)
    # 强制熔断
    adapter.backup_cb.state = "open"
    adapter.backup_cb.opened_at = 9999999999.0  # 远未来, 不会自动 half-open
    with patch("core.adapters.us.ak_call",
               side_effect=RuntimeError("akshare fail")):
        with pytest.raises(AdapterError, match="circuit open"):
            await adapter.fetch_history(
                "AAPL",
                datetime(2026, 5, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 20, tzinfo=timezone.utc),
            )


@pytest.mark.asyncio
async def test_fetch_history_yfinance_failure_records_backup_cb():
    """akshare 抛 → yfinance 抛 → backup_cb.failure_count 增加。"""
    fake_repo = MagicMock()
    fake_repo.get_akshare_code = AsyncMock(return_value="105.AAPL")
    adapter = USAdapter(dir_repo=fake_repo)
    initial = adapter.backup_cb.failure_count
    with patch("core.adapters.us.ak_call",
               side_effect=RuntimeError("akshare")), \
         patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(side_effect=RuntimeError("yfinance 429"))
        with pytest.raises(Exception):
            await adapter.fetch_history(
                "AAPL",
                datetime(2026, 5, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 20, tzinfo=timezone.utc),
            )
    assert adapter.backup_cb.failure_count == initial + 1


@pytest.mark.asyncio
async def test_fetch_history_no_dir_repo_skips_to_yfinance():
    """没 dir_repo → akshare 路径返空 → 走 yfinance。"""
    adapter = USAdapter()  # 不注入 dir_repo
    yf_df = _mock_history_df()
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.download = MagicMock(return_value=yf_df)
        bars = await adapter.fetch_history(
            "AAPL",
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
    assert len(bars) == 2
    mock_yf.download.assert_called_once()
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py -k fetch_history -v
```
Expected: 5 个新测试 fail(因为 fetch_history 还没改)

- [ ] **Step 3: 改 `core/adapters/us.py::fetch_history`**

把当前 `async def fetch_history` 整段(从 def 到 return out)**重命名为 `_fetch_history_yfinance`**,然后在它**之前**新加 `fetch_history` 分发器 + `_fetch_history_akshare`:

```python
    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        """1d 历史。
        路径: akshare 主源 → yfinance 备份(backup_cb 控制)。
        """
        # 主源: akshare
        try:
            bars = await self._fetch_history_akshare(symbol, start, end)
            if bars:
                return bars
        except Exception as e:  # noqa: BLE001
            log.warning("us.akshare_history_failed",
                        symbol=symbol, error=str(e))

        # 备份: yfinance(circuit breaker 控制)
        if not self.backup_cb.can_execute():
            log.warning("us.yfinance_circuit_open_skip_history", symbol=symbol)
            raise AdapterError(
                f"akshare unavailable and yfinance circuit open for {symbol}",
                source="us",
            )
        try:
            bars = await self._fetch_history_yfinance(symbol, start, end)
            self.backup_cb.record_success()
            return bars
        except Exception as e:
            self.backup_cb.record_failure()
            raise AdapterError(
                f"both akshare and yfinance failed for {symbol}: {e}",
                source="us",
            ) from e

    async def _fetch_history_akshare(
        self, symbol: str, start: datetime, end: datetime,
    ) -> list[Bar]:
        """akshare stock_us_hist 拿 1d。
        ts normalize: 'YYYY-MM-DD' → ET 自然交易日 00:00 → UTC(雷区 3 对称)。
        """
        if self.dir_repo is None:
            return []  # 上层会 fallback
        ak_code = await self._resolve_akshare_code(symbol)
        if ak_code is None:
            raise RuntimeError(f"failed to resolve akshare code for {symbol}")

        sd = start.strftime("%Y%m%d")
        ed = end.strftime("%Y%m%d")
        df = await ak_call(
            "stock_us_hist",
            symbol=ak_code, period="daily",
            start_date=sd, end_date=ed, adjust="",
            caller=f"us.fetch_history:{symbol}",
        )
        out: list[Bar] = []
        for _, row in df.iterrows():
            date_str = str(row["日期"])
            # ET 自然日 00:00 → UTC(对称 A 股雷区 3)
            et_midnight = pd.Timestamp(date_str).tz_localize("America/New_York")
            ts_utc = et_midnight.tz_convert("UTC").to_pydatetime()
            if pd.isna(row["开盘"]) or pd.isna(row["收盘"]):
                continue
            out.append(Bar(
                market="us", symbol=symbol, ts=ts_utc,
                open=Decimal(str(float(row["开盘"]))),
                high=Decimal(str(float(row["最高"]))),
                low=Decimal(str(float(row["最低"]))),
                close=Decimal(str(float(row["收盘"]))),
                volume=int(row["成交量"]) if not pd.isna(row["成交量"]) else 0,
                interval="1d",
            ))
        return out
```

确保 `pd` import 在文件顶部(应该已有)。

- [ ] **Step 4: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py -v
```
Expected: 全部 pass(原 24 + 新 5 = 29)

- [ ] **Step 5: import smoke**

```bash
. .venv/bin/activate && python -c "from apps.api.main import app; print('ok')"
```

- [ ] **Step 6: Commit**

```bash
git add core/adapters/us.py tests/unit/adapters/test_us.py
git commit -m "feat(us): fetch_history akshare 主 / yfinance 备(熔断保护)"
```

---

## Task 5: deps.py 给 us adapter 注入 dir_repo + fetch_snapshot 加 backup_cb

**Files:**
- Modify: `apps/api/deps.py::get_kline_service`
- Modify: `core/adapters/us.py::fetch_snapshot`
- Modify: `tests/unit/adapters/test_us.py`(加 1 个测试)

- [ ] **Step 1: 加测试**

```python
@pytest.mark.asyncio
async def test_fetch_snapshot_skips_yfinance_when_circuit_open():
    """Alpaca 失败 + yfinance backup_cb 已熔断 → 静默返空,不抛。"""
    adapter = USAdapter()
    # 设没 alpaca + yfinance 熔断
    adapter.has_primary = False
    adapter.backup_cb.state = "open"
    adapter.backup_cb.opened_at = 9999999999.0
    with patch("core.adapters.us.yf") as mock_yf:
        mock_yf.Ticker = MagicMock()
        result = await adapter.fetch_snapshot(["AAPL"])
    assert result == []
    mock_yf.Ticker.assert_not_called()
```

- [ ] **Step 2: 跑测试,确认 FAIL**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py::test_fetch_snapshot_skips_yfinance_when_circuit_open -v
```
Expected: fail(当前 fetch_snapshot 没用 backup_cb)

- [ ] **Step 3: 改 `core/adapters/us.py::fetch_snapshot`**

整段替换:

```python
    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]:
        if self.has_primary and self.primary_cb.can_execute():
            try:
                quotes = await asyncio.to_thread(self._fetch_snapshot_alpaca, symbols)
                self.primary_cb.record_success()
                return quotes
            except Exception as e:
                self.primary_cb.record_failure()
                log.warning("us.alpaca_failed", error=str(e))
        # yfinance backup, 熔断保护
        if not self.backup_cb.can_execute():
            log.debug("us.yfinance_circuit_open_skip_snapshot")
            return []
        try:
            quotes = await asyncio.to_thread(self._fetch_snapshot_yfinance, symbols)
            if quotes:
                self.backup_cb.record_success()
            else:
                # 整批 0 quote 视作失败(可能全部 429)
                self.backup_cb.record_failure()
            return quotes
        except Exception as e:
            self.backup_cb.record_failure()
            raise AdapterError(f"both primary and backup failed: {e}", source="us") from e
```

- [ ] **Step 4: 改 `apps/api/deps.py::get_kline_service`**

Read 当前实现,在创建 KLineService 之前加给 us adapter 注入 dir_repo:

```python
@lru_cache(maxsize=1)
def get_kline_service() -> KLineService:
    registry = get_registry()
    adapters = {m: registry.get(m) for m in registry.markets()}
    # 给 us adapter 注入 dir_repo, 启用 akshare 主源路径
    if "us" in adapters:
        adapters["us"].dir_repo = get_symbol_directory_repo()
    return KLineService(get_bar_repo(), adapters)
```

- [ ] **Step 5: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_us.py -v
```
Expected: 30 passed

- [ ] **Step 6: 启动 + 验证 us adapter 拿到 dir_repo**

```bash
pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null; sleep 1
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' > /tmp/api.log 2>&1 &
disown
sleep 6
grep -c FATAL /tmp/api.log
# 验证 us adapter dir_repo 已注入
. .venv/bin/activate && python -c "
from apps.api.deps import get_kline_service
svc = get_kline_service()
us = svc.adapters['us']
print('us.dir_repo:', us.dir_repo)
assert us.dir_repo is not None, 'dir_repo not injected'
print('INJECT OK')
"
pkill -9 -f "uvicorn apps.api.main:app"; sleep 1
```
Expected: `INJECT OK`,FATAL=0

- [ ] **Step 7: Commit**

```bash
git add core/adapters/us.py apps/api/deps.py tests/unit/adapters/test_us.py
git commit -m "feat(us): fetch_snapshot 加 backup_cb 熔断 + deps 注入 dir_repo"
```

---

## Task 6: 前端 K 线/信号 tab 美股仅 1d/1wk/1mo + 文档

**Files:**
- Modify: `apps/web/lib/intervals.ts::klineTabsForMarket`
- Modify: `apps/web/lib/intervals.ts::detailSignalTabs`
- Modify: `docs/TODO.md`

- [ ] **Step 1: 改 `apps/web/lib/intervals.ts`**

把现有 `klineTabsForMarket`:
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

改为:
```typescript
export function klineTabsForMarket(
  market: string | null,
): { key: Interval; label: string }[] {
  // 美股 intraday 暂未接入(yfinance ban + akshare 长窗口拉不动),仅 1d/1wk/1mo
  if (market === 'us') {
    return INTERVAL_SPECS
      .filter((s) => s.isKline && ['1d', '1wk', '1mo'].includes(s.key))
      .map((s) => ({ key: s.key, label: s.labelCn }))
  }
  const allowFourH = market === 'crypto'
  return INTERVAL_SPECS
    .filter((s) => s.isKline && (s.key !== '4h' || allowFourH))
    .map((s) => ({ key: s.key, label: s.labelCn }))
}
```

把现有 `detailSignalTabs`:
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

改为:
```typescript
export function detailSignalTabs(
  market: string | null,
): { key: DetailSignalInterval; label: string }[] {
  // 美股仅 1d 信号(intraday 暂未接入)
  if (market === 'us') {
    return INTERVAL_SPECS
      .filter((s) => s.isSignal && s.key === '1d')
      .map((s) => ({ key: s.key as DetailSignalInterval, label: s.labelCn }))
  }
  const allowFourH = market === 'crypto'
  return INTERVAL_SPECS
    .filter((s) => s.isSignal && (s.key !== '4h' || allowFourH))
    .map((s) => ({ key: s.key as DetailSignalInterval, label: s.labelCn }))
}
```

- [ ] **Step 2: tsc**

```bash
cd /Users/xiangrong/stock/marketpulse/apps/web && npx tsc --noEmit
```
Expected: exit=0

- [ ] **Step 3: 更新 `docs/TODO.md`**

在文件末尾加:

```markdown

## 美股 intraday 接入(2026-05-20 spec)

- 数据源调研:stooq.com / pandas-datareader / 等 yfinance ban 解封后接回 / 购入 Alpaca paid
- akshare `BRK.B` 类 class share ticker 格式探索
- yfinance 解封后启用熔断恢复路径(backup_cb 已写好,fail_threshold=2/reset_after_s=1800)
- 美股 directory `_US_SEEDS` 启动期批量预热 akshare_code(如果用户体验慢)
- 美股 intraday 暂未接入,前端只显示 1d/1wk/1mo K 线 + 1d CD 信号
```

- [ ] **Step 4: 后端 + 前端 e2e 启动验证**

```bash
cd /Users/xiangrong/stock/marketpulse
pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null; sleep 1
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' > /tmp/api.log 2>&1 &
disown
sleep 6
# 验证 1d K 线接口走 akshare 通了
curl -s --max-time 60 -o /tmp/aapl_bars.json -w "http=%{http_code} time=%{time_total}s\n" \
  "http://localhost:8787/api/symbols/AAPL/bars?interval=1d&days=30"
python -c "
import json
d = json.load(open('/tmp/aapl_bars.json'))
print('bars:', len(d.get('bars', [])))
if d.get('bars'):
    print('first ts:', d['bars'][0]['ts'])
    print('last ts:', d['bars'][-1]['ts'])
"
echo "---"
grep -E "akshare_code_resolved|akshare_history_failed" /tmp/api.log | tail -5
grep -c FATAL /tmp/api.log
pkill -9 -f "uvicorn apps.api.main:app"; sleep 1
```
Expected:
- `bars: ≥1`
- 至少一条 `akshare_code_resolved symbol=AAPL code=105.AAPL`
- FATAL=0

- [ ] **Step 5: Commit**

```bash
git add apps/web/lib/intervals.ts docs/TODO.md
git commit -m "feat(web): 美股 K 线/信号 tab 仅 1d/1wk/1mo, intraday 暂未接入登记 TODO"
```

---

## Self-Review

**Spec coverage**:
- §1.1 A1 yfinance 熔断 → Task 2(backup_cb)+ Task 4 / Task 5(应用熔断逻辑)✓
- §1.1 A2 akshare 1d 主源 → Task 4 ✓
- §1.1 ticker 映射 → Task 3 ✓
- §1.1 schema 自动升级 → Task 1 ✓
- §1.1 1d ts ET normalize → Task 4 (`_fetch_history_akshare` 用 `tz_localize('America/New_York').tz_convert('UTC')`)✓
- §1.1 UI 文案 → Task 6(tab 不显示 intraday,无需额外文案)✓
- §1.2 非目标 — 全未做(intraday/4h/Alpaca paid/富途)✓
- §3 改动面 — 6 个文件全覆盖(directory_repo + us.py + deps.py + intervals.ts + 测试 + TODO)✓
- §6 测试 — Task 1-5 共 14 个新测试 ✓

**Placeholder 扫描**:无 TBD/TODO,每步均含完整代码或具体命令。

**Type 一致性**:
- `_resolve_akshare_code(symbol) -> str | None` — Task 3 / Task 4 调用一致
- `_fetch_history_akshare(symbol, start, end) -> list[Bar]` — Task 4 内部一致
- `dir_repo: SymbolDirectoryRepo | None` — Task 2 字段 / Task 3 / Task 4 / Task 5 调用一致
- `backup_cb: CircuitBreaker(2, 1800)` — Task 2 / 4 / 5 一致

**风险点(实施时格外注意)**:
- Task 1 中 `_schema_ensured` 是类级 flag,测试 fixture 需要重置(已在测试中处理)
- Task 4 ts 转换 `pd.Timestamp(date_str).tz_localize('America/New_York')` — 等同 `pd.Timestamp('2026-05-19', tz='America/New_York')`,验证 EDT 时返 `2026-05-19 04:00 UTC`
- Task 5 给 us adapter setattr `dir_repo = ...` 是简化方案,避免改 registry 签名;但因 `lru_cache`,得在 service 拿到时立即注入(在 `get_kline_service` 里做对的)
- Task 6 美股 4h tab 之前 spec 设计是 us+crypto,现在调整为只 crypto,这是降级,前端不影响其他市场
