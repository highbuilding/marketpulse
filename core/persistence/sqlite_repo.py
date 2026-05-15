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
            # WAL: 读写不互斥, signal scan/watchlist add/scheduler cron 并发写更稳;
            # synchronous=NORMAL: WAL 模式下足够安全 (fsync 减少, 性能 +显著)。
            # 文件级设置, 持久化, 设一次永久生效。
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
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
