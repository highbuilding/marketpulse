from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from core.domain.models import MarketEvent


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None) -> dict[str, Any]:
    return json.loads(value) if value else {}


class MarketEventRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    def _row_to_event(self, row: aiosqlite.Row) -> MarketEvent:
        return MarketEvent(
            id=row["id"],
            market=row["market"],
            event_type=row["event_type"],
            severity=row["severity"],
            subject_type=row["subject_type"],
            subject_id=row["subject_id"],
            title=row["title"],
            summary=row["summary"],
            evidence=_loads(row["evidence_json"]),
            occurred_at=_dt(row["occurred_at"]),
            created_at=_dt(row["created_at"]),
        )

    async def add(self, event: MarketEvent) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        async with self._connect() as db:
            cur = await db.execute(
                """INSERT INTO market_events (
                     market, event_type, severity, subject_type, subject_id,
                     title, summary, evidence_json, occurred_at, created_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.market,
                    event.event_type,
                    event.severity,
                    event.subject_type,
                    event.subject_id,
                    event.title,
                    event.summary,
                    _json(event.evidence),
                    event.occurred_at.astimezone(timezone.utc).isoformat(),
                    created_at,
                ),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def list_recent(
        self, market: str, *, limit: int = 100,
        subject_type: str | None = None,
        subject_id: str | None = None,
    ) -> list[MarketEvent]:
        sql = [
            """SELECT id, market, event_type, severity, subject_type, subject_id,
                      title, summary, evidence_json, occurred_at, created_at
               FROM market_events
               WHERE market = ?""",
        ]
        args: list[object] = [market]
        if subject_type:
            sql.append("AND subject_type = ?")
            args.append(subject_type)
        if subject_id:
            sql.append("AND subject_id = ?")
            args.append(subject_id)
        sql.append("ORDER BY occurred_at DESC, id DESC LIMIT ?")
        args.append(limit)
        async with self._connect() as db:
            cur = await db.execute(" ".join(sql), args)
            rows = await cur.fetchall()
        return [self._row_to_event(r) for r in rows]
