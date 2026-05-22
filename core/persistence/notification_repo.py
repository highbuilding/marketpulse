"""通知子系统持久化(SQLite, 与 watchlists / signals 同库)。

三张表:
- notification_recipients: 收件人列表(按市场分组, channel 预留 wechat)
- symbol_notification_config: 每个 symbol 启用的 interval(JSON, 1d 必有)
- notification_audit: 推送审计 + snapshot hash 去重
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite


@dataclass(frozen=True)
class Recipient:
    id: int
    market: str
    channel: str
    address: str
    enabled: bool


@dataclass(frozen=True)
class SymbolConfig:
    symbol: str
    intervals: list[str]


class NotificationRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    # ---------- recipients ----------

    async def list_recipients(
        self, market: str | None = None, *, only_enabled: bool = False,
    ) -> list[Recipient]:
        sql = ["SELECT id, market, channel, address, enabled FROM notification_recipients WHERE 1=1"]
        args: list = []
        if market:
            sql.append("AND market = ?")
            args.append(market)
        if only_enabled:
            sql.append("AND enabled = 1")
        sql.append("ORDER BY market, channel, address")
        async with self._connect() as db:
            cur = await db.execute(" ".join(sql), args)
            rows = await cur.fetchall()
        return [
            Recipient(
                id=r["id"], market=r["market"], channel=r["channel"],
                address=r["address"], enabled=bool(r["enabled"]),
            )
            for r in rows
        ]

    async def add_recipient(self, market: str, channel: str, address: str) -> int:
        async with self._connect() as db:
            cur = await db.execute(
                """INSERT INTO notification_recipients
                   (market, channel, address, enabled, created_at)
                   VALUES (?, ?, ?, 1, ?)""",
                (market, channel, address, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
            return cur.lastrowid

    async def set_enabled(self, recipient_id: int, enabled: bool) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE notification_recipients SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, recipient_id),
            )
            await db.commit()

    async def delete_recipient(self, recipient_id: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM notification_recipients WHERE id = ?", (recipient_id,),
            )
            await db.commit()

    # ---------- symbol config ----------

    async def list_symbol_configs(self) -> list[SymbolConfig]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT symbol, intervals_json FROM symbol_notification_config ORDER BY symbol",
            )
            rows = await cur.fetchall()
        return [
            SymbolConfig(symbol=r["symbol"], intervals=json.loads(r["intervals_json"]))
            for r in rows
        ]

    async def get_symbol_config(self, symbol: str) -> SymbolConfig | None:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT symbol, intervals_json FROM symbol_notification_config WHERE symbol = ?",
                (symbol,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return SymbolConfig(symbol=row["symbol"], intervals=json.loads(row["intervals_json"]))

    async def upsert_symbol_config(self, symbol: str, intervals: list[str]) -> None:
        # 服务端强制注入 1d, 防客户端传错
        merged = list(dict.fromkeys(["1d", *intervals]))
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO symbol_notification_config (symbol, intervals_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET
                     intervals_json = excluded.intervals_json,
                     updated_at = excluded.updated_at""",
                (symbol, json.dumps(merged), datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def delete_symbol_config(self, symbol: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM symbol_notification_config WHERE symbol = ?", (symbol,),
            )
            await db.commit()

    # ---------- audit ----------

    async def last_audit_hash(self, market: str) -> str | None:
        """最近一次记录的 snapshot hash, 无记录返回 None。"""
        async with self._connect() as db:
            cur = await db.execute(
                """SELECT snapshot_hash FROM notification_audit
                   WHERE market = ? ORDER BY id DESC LIMIT 1""",
                (market,),
            )
            row = await cur.fetchone()
        return row["snapshot_hash"] if row else None

    async def record_audit(
        self, market: str, snapshot_hash: str, *,
        sent: bool, recipients_count: int = 0, error: str | None = None,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO notification_audit
                   (market, triggered_at, snapshot_hash, sent, recipients_count, error)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    market,
                    datetime.now(timezone.utc).isoformat(),
                    snapshot_hash,
                    1 if sent else 0,
                    recipients_count,
                    error,
                ),
            )
            await db.commit()
