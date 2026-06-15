from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from core.domain.models import (
    ThemeConstituent,
    ThemeDefinition,
    ThemeMembership,
    ThemeSnapshot,
    ThemeState,
)


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

    def _row_to_definition(self, row: aiosqlite.Row) -> ThemeDefinition:
        return ThemeDefinition(
            market=row["market"],
            theme_code=row["theme_code"],
            theme_name=row["theme_name"],
            classification=row["classification"],
            priority=row["priority"],
            enabled=bool(row["enabled"]),
            source=row["source"],
            seed_version=row["seed_version"],
            note=row["note"],
            member_count=int(row["member_count"] or 0),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    def _row_to_constituent(self, row: aiosqlite.Row) -> ThemeConstituent:
        return ThemeConstituent(
            market=row["market"],
            theme_code=row["theme_code"],
            symbol=row["symbol"],
            name=row["name"],
            role_hint=row["role_hint"],
            weight=row["weight"],
            enabled=bool(row["enabled"]),
            source=row["source"],
            seed_version=row["seed_version"],
            note=row["note"],
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    async def seed_definitions(
        self,
        definitions: list[ThemeDefinition],
        constituents: list[ThemeConstituent],
    ) -> tuple[int, int]:
        """幂等导入内置 seed。

        只插入缺失项, 不覆盖用户后续对 enabled/priority/name/members 的手工调整。
        """
        now = datetime.now(timezone.utc).isoformat()
        theme_rows = [
            (
                d.market,
                d.theme_code,
                d.theme_name,
                d.classification,
                d.priority,
                int(d.enabled),
                d.source,
                d.seed_version,
                d.note,
                now,
                now,
            )
            for d in definitions
        ]
        member_rows = [
            (
                c.market,
                c.theme_code,
                c.symbol,
                c.name,
                c.role_hint,
                c.weight,
                int(c.enabled),
                c.source,
                c.seed_version,
                c.note,
                now,
                now,
            )
            for c in constituents
        ]
        async with self._connect() as db:
            before = db.total_changes
            await db.executemany(
                """INSERT INTO theme_universe (
                     market, theme_code, theme_name, classification, priority,
                     enabled, source, seed_version, note, created_at, updated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, theme_code) DO NOTHING""",
                theme_rows,
            )
            after_themes = db.total_changes
            await db.executemany(
                """INSERT INTO theme_constituents (
                     market, theme_code, symbol, name, role_hint, weight, enabled,
                     source, seed_version, note, created_at, updated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, theme_code, symbol) DO NOTHING""",
                member_rows,
            )
            await db.commit()
            after_members = db.total_changes
        return after_themes - before, after_members - after_themes

    async def upsert_definition(self, definition: ThemeDefinition) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO theme_universe (
                     market, theme_code, theme_name, classification, priority,
                     enabled, source, seed_version, note, created_at, updated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, theme_code) DO UPDATE SET
                     theme_name=excluded.theme_name,
                     classification=excluded.classification,
                     priority=excluded.priority,
                     enabled=excluded.enabled,
                     note=excluded.note,
                     updated_at=excluded.updated_at""",
                (
                    definition.market,
                    definition.theme_code,
                    definition.theme_name,
                    definition.classification,
                    definition.priority,
                    int(definition.enabled),
                    definition.source,
                    definition.seed_version,
                    definition.note,
                    definition.created_at.astimezone(timezone.utc).isoformat()
                    if definition.created_at else now,
                    now,
                ),
            )
            await db.commit()

    async def list_definitions(
        self,
        market: str,
        *,
        include_disabled: bool = True,
    ) -> list[ThemeDefinition]:
        where = ["u.market = ?"]
        args: list[object] = [market]
        if not include_disabled:
            where.append("u.enabled = 1")
        async with self._connect() as db:
            cur = await db.execute(
                f"""SELECT u.market, u.theme_code, u.theme_name, u.classification,
                           u.priority, u.enabled, u.source, u.seed_version, u.note,
                           u.created_at, u.updated_at,
                           COALESCE(SUM(CASE WHEN c.enabled = 1 THEN 1 ELSE 0 END), 0) AS member_count
                    FROM theme_universe u
                    LEFT JOIN theme_constituents c
                      ON c.market = u.market AND c.theme_code = u.theme_code
                    WHERE {' AND '.join(where)}
                    GROUP BY u.market, u.theme_code
                    ORDER BY
                      CASE u.priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                      u.enabled DESC,
                      u.updated_at DESC""",
                args,
            )
            rows = await cur.fetchall()
        return [self._row_to_definition(r) for r in rows]

    async def get_definition(self, market: str, theme_code: str) -> ThemeDefinition | None:
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT u.market, u.theme_code, u.theme_name, u.classification,
                          u.priority, u.enabled, u.source, u.seed_version, u.note,
                          u.created_at, u.updated_at,
                          COALESCE(SUM(CASE WHEN c.enabled = 1 THEN 1 ELSE 0 END), 0) AS member_count
                   FROM theme_universe u
                   LEFT JOIN theme_constituents c
                     ON c.market = u.market AND c.theme_code = u.theme_code
                   WHERE u.market = ? AND u.theme_code = ?
                   GROUP BY u.market, u.theme_code""",
                (market, theme_code),
            )
            row = await cur.fetchone()
        return self._row_to_definition(row) if row else None

    async def delete_definition(self, market: str, theme_code: str) -> None:
        definition = await self.get_definition(market, theme_code)
        if definition is None:
            return
        async with self._connect() as db:
            if definition.source == "seed":
                now = datetime.now(timezone.utc).isoformat()
                await db.execute(
                    """UPDATE theme_universe
                       SET enabled = 0, updated_at = ?
                       WHERE market = ? AND theme_code = ?""",
                    (now, market, theme_code),
                )
            else:
                await db.execute(
                    "DELETE FROM theme_universe WHERE market = ? AND theme_code = ?",
                    (market, theme_code),
                )
            await db.commit()

    async def upsert_constituent(self, constituent: ThemeConstituent) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO theme_constituents (
                     market, theme_code, symbol, name, role_hint, weight, enabled,
                     source, seed_version, note, created_at, updated_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, theme_code, symbol) DO UPDATE SET
                     name=excluded.name,
                     role_hint=excluded.role_hint,
                     weight=excluded.weight,
                     enabled=excluded.enabled,
                     note=excluded.note,
                     updated_at=excluded.updated_at""",
                (
                    constituent.market,
                    constituent.theme_code,
                    constituent.symbol,
                    constituent.name,
                    constituent.role_hint,
                    constituent.weight,
                    int(constituent.enabled),
                    constituent.source,
                    constituent.seed_version,
                    constituent.note,
                    constituent.created_at.astimezone(timezone.utc).isoformat()
                    if constituent.created_at else now,
                    now,
                ),
            )
            await db.commit()

    async def list_static_constituents(
        self,
        market: str,
        theme_code: str,
        *,
        include_disabled: bool = True,
    ) -> list[ThemeConstituent]:
        where = ["market = ?", "theme_code = ?"]
        args: list[object] = [market, theme_code]
        if not include_disabled:
            where.append("enabled = 1")
        async with self._connect() as db:
            cur = await db.execute(
                f"""SELECT market, theme_code, symbol, name, role_hint, weight,
                           enabled, source, seed_version, note, created_at, updated_at
                    FROM theme_constituents
                    WHERE {' AND '.join(where)}
                    ORDER BY enabled DESC, COALESCE(weight, 0) DESC, updated_at DESC""",
                args,
            )
            rows = await cur.fetchall()
        return [self._row_to_constituent(r) for r in rows]

    async def delete_constituent(self, market: str, theme_code: str, symbol: str) -> None:
        rows = await self.list_static_constituents(market, theme_code, include_disabled=True)
        existing = next((r for r in rows if r.symbol == symbol), None)
        if existing is None:
            return
        async with self._connect() as db:
            if existing.source == "seed":
                now = datetime.now(timezone.utc).isoformat()
                await db.execute(
                    """UPDATE theme_constituents
                       SET enabled = 0, updated_at = ?
                       WHERE market = ? AND theme_code = ? AND symbol = ?""",
                    (now, market, theme_code, symbol),
                )
            else:
                await db.execute(
                    """DELETE FROM theme_constituents
                       WHERE market = ? AND theme_code = ? AND symbol = ?""",
                    (market, theme_code, symbol),
                )
            await db.commit()

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

    async def list_snapshots_window(
        self, market: str, *, start: datetime, end: datetime, limit: int = 2000,
    ) -> list[ThemeSnapshot]:
        """按时间窗取题材快照, 升序返回, 供盘后回放重建状态变迁曲线。"""
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT market, theme_code, theme_name, classification, ts,
                          pct_change, pct_change_5m, amount, amount_ratio, up_ratio,
                          limit_up_count, member_count, leader_symbols_json,
                          divergence_score, support_score, raw_json
                   FROM theme_snapshots
                   WHERE market = ? AND ts >= ? AND ts <= ?
                   ORDER BY ts ASC, theme_code ASC
                   LIMIT ?""",
                (market, _iso(start), _iso(end), limit),
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
