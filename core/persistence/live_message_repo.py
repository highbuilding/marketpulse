from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from core.domain.models import LiveMessage


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _json(value: Any, default: Any) -> str:
    return json.dumps(default if value is None else value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


class LiveMessageRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    def _row_to_message(self, row: aiosqlite.Row) -> LiveMessage:
        return LiveMessage(
            id=row["id"],
            market=row["market"],
            ts=_dt(row["ts"]) or datetime.now(timezone.utc),
            level=row["level"],
            category=row["category"],
            title=row["title"],
            body=row["body"],
            theme_code=row["theme_code"],
            symbol=row["symbol"],
            symbols=_loads(row["symbols_json"], []),
            source_event=row["source_event"],
            source_event_id=row["source_event_id"],
            dedupe_key=row["dedupe_key"],
            payload=_loads(row["payload_json"], {}),
            rule_version=row["rule_version"],
            created_at=_dt(row["created_at"]),
        )

    async def insert_many(self, messages: list[LiveMessage]) -> int:
        if not messages:
            return 0
        now = datetime.now(timezone.utc)
        rows = [
            (
                m.id,
                m.market,
                _iso(m.ts),
                m.level,
                m.category,
                m.title,
                m.body,
                m.theme_code,
                m.symbol,
                _json(m.symbols, []),
                m.source_event,
                m.source_event_id,
                m.dedupe_key,
                _json(m.payload, {}),
                m.rule_version,
                _iso(m.created_at or now),
            )
            for m in messages
        ]
        async with self._connect() as db:
            before = db.total_changes
            await db.executemany(
                """INSERT OR IGNORE INTO live_messages (
                     id, market, ts, level, category, title, body, theme_code,
                     symbol, symbols_json, source_event, source_event_id,
                     dedupe_key, payload_json, rule_version, created_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            await db.commit()
            return db.total_changes - before

    async def list_recent(
        self,
        market: str,
        *,
        limit: int = 50,
        category: str | None = None,
    ) -> list[LiveMessage]:
        where = ["market = ?"]
        args: list[object] = [market]
        if category:
            where.append("category = ?")
            args.append(category)
        args.append(max(1, min(limit, 200)))
        async with self._connect() as db:
            cur = await db.execute(
                f"""SELECT * FROM live_messages
                    WHERE {' AND '.join(where)}
                    ORDER BY ts DESC
                    LIMIT ?""",
                args,
            )
            rows = await cur.fetchall()
        return [self._row_to_message(r) for r in rows]

    async def list_window(
        self,
        market: str,
        *,
        start: datetime,
        end: datetime,
        category: str | None = None,
        limit: int = 500,
    ) -> list[LiveMessage]:
        where = ["market = ?", "ts >= ?", "ts <= ?"]
        args: list[object] = [market, _iso(start), _iso(end)]
        if category:
            where.append("category = ?")
            args.append(category)
        args.append(max(1, min(limit, 1000)))
        async with self._connect() as db:
            cur = await db.execute(
                f"""SELECT * FROM live_messages
                    WHERE {' AND '.join(where)}
                    ORDER BY ts DESC
                    LIMIT ?""",
                args,
            )
            rows = await cur.fetchall()
        return [self._row_to_message(r) for r in rows]

