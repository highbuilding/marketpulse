from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from core.domain.models import ThemeMembership, ThemeSnapshot, ThemeState


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


class ThemeRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def upsert_snapshots(self, snapshots: list[ThemeSnapshot]) -> int:
        if not snapshots:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                s.market,
                s.theme_code,
                s.theme_name,
                s.classification,
                _iso(s.ts),
                s.pct_change,
                s.pct_change_5m,
                s.amount,
                s.amount_ratio,
                s.up_ratio,
                s.limit_up_count,
                s.member_count,
                _json(s.leader_symbols, []),
                s.divergence_score,
                s.support_score,
                _json(s.raw, {}),
                now,
            )
            for s in snapshots
        ]
        async with self._connect() as db:
            await db.executemany(
                """INSERT INTO theme_snapshots (
                     market, theme_code, theme_name, classification, ts,
                     pct_change, pct_change_5m, amount, amount_ratio, up_ratio,
                     limit_up_count, member_count, leader_symbols_json,
                     divergence_score, support_score, raw_json, updated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, theme_code, ts) DO UPDATE SET
                     theme_name=excluded.theme_name,
                     classification=excluded.classification,
                     pct_change=excluded.pct_change,
                     pct_change_5m=excluded.pct_change_5m,
                     amount=excluded.amount,
                     amount_ratio=excluded.amount_ratio,
                     up_ratio=excluded.up_ratio,
                     limit_up_count=excluded.limit_up_count,
                     member_count=excluded.member_count,
                     leader_symbols_json=excluded.leader_symbols_json,
                     divergence_score=excluded.divergence_score,
                     support_score=excluded.support_score,
                     raw_json=excluded.raw_json,
                     updated_at=excluded.updated_at""",
                rows,
            )
            await db.commit()
        return len(snapshots)

    async def list_recent_snapshots(
        self, market: str, *, limit: int = 50,
    ) -> list[ThemeSnapshot]:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT market, theme_code, theme_name, classification, ts,
                          pct_change, pct_change_5m, amount, amount_ratio, up_ratio,
                          limit_up_count, member_count, leader_symbols_json,
                          divergence_score, support_score, raw_json
                   FROM theme_snapshots
                   WHERE market = ?
                   ORDER BY ts DESC, amount DESC
                   LIMIT ?""",
                (market, limit),
            )
            rows = await cur.fetchall()
        return [
            ThemeSnapshot(
                market=r["market"],
                theme_code=r["theme_code"],
                theme_name=r["theme_name"],
                classification=r["classification"],
                ts=_dt(r["ts"]) or datetime.fromtimestamp(0, timezone.utc),
                pct_change=r["pct_change"],
                pct_change_5m=r["pct_change_5m"],
                amount=r["amount"],
                amount_ratio=r["amount_ratio"],
                up_ratio=r["up_ratio"],
                limit_up_count=r["limit_up_count"],
                member_count=r["member_count"],
                leader_symbols=_loads(r["leader_symbols_json"], []),
                divergence_score=r["divergence_score"],
                support_score=r["support_score"],
                raw=_loads(r["raw_json"], {}),
            )
            for r in rows
        ]

    async def upsert_states(self, states: list[ThemeState]) -> int:
        if not states:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                s.market,
                s.theme_code,
                s.theme_name,
                s.state,
                s.score,
                s.reason,
                _json(s.evidence, {}),
                now,
            )
            for s in states
        ]
        async with self._connect() as db:
            await db.executemany(
                """INSERT INTO theme_states (
                     market, theme_code, theme_name, state, score, reason,
                     evidence_json, updated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, theme_code) DO UPDATE SET
                     theme_name=excluded.theme_name,
                     state=excluded.state,
                     score=excluded.score,
                     reason=excluded.reason,
                     evidence_json=excluded.evidence_json,
                     updated_at=excluded.updated_at""",
                rows,
            )
            await db.commit()
        return len(states)

    async def list_states(self, market: str) -> list[ThemeState]:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT market, theme_code, theme_name, state, score, reason,
                          evidence_json, updated_at
                   FROM theme_states
                   WHERE market = ?
                   ORDER BY score DESC, updated_at DESC""",
                (market,),
            )
            rows = await cur.fetchall()
        return [
            ThemeState(
                market=r["market"],
                theme_code=r["theme_code"],
                theme_name=r["theme_name"],
                state=r["state"],
                score=r["score"],
                reason=r["reason"],
                evidence=_loads(r["evidence_json"], {}),
                updated_at=_dt(r["updated_at"]),
            )
            for r in rows
        ]

    async def upsert_memberships(self, rows: list[ThemeMembership]) -> int:
        if not rows:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        payload = [
            (
                r.market,
                r.theme_code,
                r.symbol,
                r.name,
                r.role,
                r.pct_change,
                r.amount,
                r.volume_ratio,
                None if r.is_above_intraday_avg is None else int(r.is_above_intraday_avg),
                _json(r.evidence, {}),
                now,
            )
            for r in rows
        ]
        async with self._connect() as db:
            await db.executemany(
                """INSERT INTO theme_memberships (
                     market, theme_code, symbol, name, role, pct_change, amount,
                     volume_ratio, is_above_intraday_avg, evidence_json, updated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, theme_code, symbol) DO UPDATE SET
                     name=excluded.name,
                     role=excluded.role,
                     pct_change=excluded.pct_change,
                     amount=excluded.amount,
                     volume_ratio=excluded.volume_ratio,
                     is_above_intraday_avg=excluded.is_above_intraday_avg,
                     evidence_json=excluded.evidence_json,
                     updated_at=excluded.updated_at""",
                payload,
            )
            await db.commit()
        return len(rows)

    async def list_memberships(self, market: str, theme_code: str) -> list[ThemeMembership]:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT market, theme_code, symbol, name, role, pct_change, amount,
                          volume_ratio, is_above_intraday_avg, evidence_json, updated_at
                   FROM theme_memberships
                   WHERE market = ? AND theme_code = ?
                   ORDER BY amount DESC, pct_change DESC""",
                (market, theme_code),
            )
            rows = await cur.fetchall()
        return [
            ThemeMembership(
                market=r["market"],
                theme_code=r["theme_code"],
                symbol=r["symbol"],
                name=r["name"],
                role=r["role"],
                pct_change=r["pct_change"],
                amount=r["amount"],
                volume_ratio=r["volume_ratio"],
                is_above_intraday_avg=None
                if r["is_above_intraday_avg"] is None else bool(r["is_above_intraday_avg"]),
                evidence=_loads(r["evidence_json"], {}),
                updated_at=_dt(r["updated_at"]),
            )
            for r in rows
        ]
