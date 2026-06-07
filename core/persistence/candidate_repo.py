from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from core.domain.models import TradeCandidate


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _json(value: Any, default: Any) -> str:
    return json.dumps(default if value is None else value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


class CandidateRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    def _row_to_candidate(self, row: aiosqlite.Row) -> TradeCandidate:
        return TradeCandidate(
            id=row["id"],
            market=row["market"],
            candidate_key=row["candidate_key"],
            symbol=row["symbol"],
            name=row["name"],
            theme_code=row["theme_code"],
            theme_name=row["theme_name"],
            candidate_type=row["candidate_type"],
            decision=row["decision"],
            score=row["score"],
            reasons=_loads(row["reasons_json"], []),
            risks=_loads(row["risks_json"], []),
            evidence=_loads(row["evidence_json"], {}),
            status=row["status"],
            generated_at=_dt(row["generated_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    async def upsert(self, candidate: TradeCandidate) -> int:
        now = datetime.now(timezone.utc).isoformat()
        generated_at = (
            candidate.generated_at.astimezone(timezone.utc).isoformat()
            if candidate.generated_at else now
        )
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO trade_candidates (
                     market, candidate_key, symbol, name, theme_code, theme_name,
                     candidate_type, decision, score, reasons_json, risks_json,
                     evidence_json, status, generated_at, updated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, candidate_key) DO UPDATE SET
                     symbol=excluded.symbol,
                     name=excluded.name,
                     theme_code=excluded.theme_code,
                     theme_name=excluded.theme_name,
                     candidate_type=excluded.candidate_type,
                     decision=excluded.decision,
                     score=excluded.score,
                     reasons_json=excluded.reasons_json,
                     risks_json=excluded.risks_json,
                     evidence_json=excluded.evidence_json,
                     status=excluded.status,
                     generated_at=excluded.generated_at,
                     updated_at=excluded.updated_at""",
                (
                    candidate.market,
                    candidate.candidate_key,
                    candidate.symbol,
                    candidate.name,
                    candidate.theme_code,
                    candidate.theme_name,
                    candidate.candidate_type,
                    candidate.decision,
                    candidate.score,
                    _json(candidate.reasons, []),
                    _json(candidate.risks, []),
                    _json(candidate.evidence, {}),
                    candidate.status,
                    generated_at,
                    now,
                ),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT id FROM trade_candidates WHERE market = ? AND candidate_key = ?",
                (candidate.market, candidate.candidate_key),
            )
            row = await cur.fetchone()
        return int(row["id"])

    async def list_active(self, market: str, *, limit: int = 50) -> list[TradeCandidate]:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT id, market, candidate_key, symbol, name, theme_code, theme_name,
                          candidate_type, decision, score, reasons_json, risks_json,
                          evidence_json, status, generated_at, updated_at
                   FROM trade_candidates
                   WHERE market = ? AND status = 'active'
                   ORDER BY score DESC, generated_at DESC
                   LIMIT ?""",
                (market, limit),
            )
            rows = await cur.fetchall()
        return [self._row_to_candidate(r) for r in rows]

    async def set_status(self, market: str, candidate_key: str, status: str) -> None:
        async with self._connect() as db:
            await db.execute(
                """UPDATE trade_candidates
                   SET status = ?, updated_at = ?
                   WHERE market = ? AND candidate_key = ?""",
                (status, datetime.now(timezone.utc).isoformat(), market, candidate_key),
            )
            await db.commit()
