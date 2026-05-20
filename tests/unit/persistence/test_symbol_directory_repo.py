from __future__ import annotations

import aiosqlite
import pytest

from core.persistence.symbol_directory_repo import SymbolDirectoryRepo


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
async def test_ensure_schema_adds_akshare_code(tmp_path):
    db_path = str(tmp_path / "state.db")
    await _init_legacy_schema(db_path)
    SymbolDirectoryRepo._schema_ensured = False
    repo = SymbolDirectoryRepo(db_path)
    await repo.upsert_many([("AAPL", "Apple Inc.", "us")])
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("PRAGMA table_info(symbol_directory)")
        cols = {r[1] for r in await cur.fetchall()}
    assert "akshare_code" in cols


@pytest.mark.asyncio
async def test_ensure_schema_idempotent(tmp_path):
    """已有 akshare_code 列时不抛错。"""
    db_path = str(tmp_path / "state.db")
    await _init_legacy_schema(db_path)
    SymbolDirectoryRepo._schema_ensured = False
    repo = SymbolDirectoryRepo(db_path)
    await repo.upsert_many([("AAPL", "Apple Inc.", "us")])
    SymbolDirectoryRepo._schema_ensured = False  # 强制再触发一次
    await repo.upsert_many([("MSFT", "Microsoft", "us")])  # 不应抛 duplicate column
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM symbol_directory")
        count = (await cur.fetchone())[0]
    assert count == 2


@pytest.mark.asyncio
async def test_get_set_akshare_code(tmp_path):
    db_path = str(tmp_path / "state.db")
    await _init_legacy_schema(db_path)
    SymbolDirectoryRepo._schema_ensured = False
    repo = SymbolDirectoryRepo(db_path)
    await repo.upsert_many([("AAPL", "Apple Inc.", "us")])
    assert await repo.get_akshare_code("AAPL") is None
    await repo.set_akshare_code("AAPL", "105.AAPL")
    assert await repo.get_akshare_code("AAPL") == "105.AAPL"
    assert await repo.get_akshare_code("UNKNOWN") is None
