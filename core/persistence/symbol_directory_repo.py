from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite


class SymbolDirectoryRepo:
    # 类级 flag，保证 ALTER 只跑一次(多实例共享)
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
            try:
                await db.execute(
                    "ALTER TABLE symbol_directory ADD COLUMN akshare_code TEXT"
                )
                await db.commit()
            except Exception:  # 并发启动时其他进程已加列, 忽略
                pass

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
        """批量查 name，缺失的不在返回 dict 里。"""
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
        """模糊搜索：symbol prefix 或 name 子串。可按 market 过滤。"""
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
        """查 symbol 对应的 akshare 调用格式（如 105.AAPL），不存在返回 None。"""
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
        """更新 symbol 的 akshare_code（symbol 必须已在 directory）。"""
        now = datetime.now(timezone.utc).isoformat()
        async with self._connect() as db:
            await db.execute(
                "UPDATE symbol_directory SET akshare_code = ?, updated_at = ? "
                "WHERE symbol = ?",
                (code, now, symbol),
            )
            await db.commit()
