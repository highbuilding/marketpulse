from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from core.domain.models import AITradeOpinion


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _json(value: Any, default: Any) -> str:
    return json.dumps(default if value is None else value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


class AITradeOpinionRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    def _row_to_opinion(self, row: aiosqlite.Row) -> AITradeOpinion:
        return AITradeOpinion(
            id=row["id"],
            market=row["market"],
            opinion_key=row["opinion_key"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            target_name=row["target_name"],
            decision=row["decision"],
            confidence=row["confidence"],
            title=row["title"],
            thesis=row["thesis"],
            reasons=_loads(row["reasons_json"], []),
            risks=_loads(row["risks_json"], []),
            evidence=_loads(row["evidence_json"], {}),
            source_candidate_id=row["source_candidate_id"],
            generated_at=_dt(row["generated_at"]),
            expires_at=_dt(row["expires_at"]),
            status=row["status"],
        )

    async def upsert(self, opinion: AITradeOpinion) -> int:
        now = datetime.now(timezone.utc).isoformat()
        generated_at = (
            opinion.generated_at.astimezone(timezone.utc).isoformat()
            if opinion.generated_at else now
        )
        expires_at = (
            opinion.expires_at.astimezone(timezone.utc).isoformat()
            if opinion.expires_at else None
        )
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO ai_trade_opinions (
                     market, opinion_key, target_type, target_id, target_name,
                     decision, confidence, title, thesis, reasons_json, risks_json,
                     evidence_json, source_candidate_id, generated_at, expires_at, status
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, opinion_key) DO UPDATE SET
                     target_type=excluded.target_type,
                     target_id=excluded.target_id,
                     target_name=excluded.target_name,
                     decision=excluded.decision,
                     confidence=excluded.confidence,
                     title=excluded.title,
                     thesis=excluded.thesis,
                     reasons_json=excluded.reasons_json,
                     risks_json=excluded.risks_json,
                     evidence_json=excluded.evidence_json,
                     source_candidate_id=excluded.source_candidate_id,
                     generated_at=excluded.generated_at,
                     expires_at=excluded.expires_at,
                     status=excluded.status""",
                (
                    opinion.market,
                    opinion.opinion_key,
                    opinion.target_type,
                    opinion.target_id,
                    opinion.target_name,
                    opinion.decision,
                    opinion.confidence,
                    opinion.title,
                    opinion.thesis,
                    _json(opinion.reasons, []),
                    _json(opinion.risks, []),
                    _json(opinion.evidence, {}),
                    opinion.source_candidate_id,
                    generated_at,
                    expires_at,
                    opinion.status,
                ),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT id FROM ai_trade_opinions WHERE market = ? AND opinion_key = ?",
                (opinion.market, opinion.opinion_key),
            )
            row = await cur.fetchone()
        return int(row["id"])

    async def list_active(self, market: str, *, limit: int = 50) -> list[AITradeOpinion]:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT id, market, opinion_key, target_type, target_id, target_name,
                          decision, confidence, title, thesis, reasons_json, risks_json,
                          evidence_json, source_candidate_id, generated_at, expires_at, status
                   FROM ai_trade_opinions
                   WHERE market = ? AND status = 'active'
                   ORDER BY generated_at DESC, id DESC
                   LIMIT ?""",
                (market, limit),
            )
            rows = await cur.fetchall()
        return [self._row_to_opinion(r) for r in rows]

    async def get(self, opinion_id: int) -> AITradeOpinion | None:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT id, market, opinion_key, target_type, target_id, target_name,
                          decision, confidence, title, thesis, reasons_json, risks_json,
                          evidence_json, source_candidate_id, generated_at, expires_at, status
                   FROM ai_trade_opinions
                   WHERE id = ?""",
                (opinion_id,),
            )
            row = await cur.fetchone()
        return self._row_to_opinion(row) if row else None
