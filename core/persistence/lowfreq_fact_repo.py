from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))


class LowFreqFactRepo:
    """盘后低频事实表:龙虎榜、公告、同花顺资金流。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _connect(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def save_lhb_rows(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        async with self._connect() as db:
            before = db.total_changes
            await db.executemany(
                """INSERT INTO lhb_daily (
                     market, trade_date, symbol, name, reason, net_buy, buy_amount,
                     sell_amount, turnover_rate, total_amount, raw_json, pulled_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, trade_date, symbol, reason) DO UPDATE SET
                     name=excluded.name,
                     net_buy=excluded.net_buy,
                     buy_amount=excluded.buy_amount,
                     sell_amount=excluded.sell_amount,
                     turnover_rate=excluded.turnover_rate,
                     total_amount=excluded.total_amount,
                     raw_json=excluded.raw_json,
                     pulled_at=excluded.pulled_at""",
                [
                    (
                        r["market"], r["trade_date"], r["symbol"], r.get("name"),
                        r.get("reason") or "", r.get("net_buy"), r.get("buy_amount"),
                        r.get("sell_amount"), r.get("turnover_rate"),
                        r.get("total_amount"), _json(r.get("raw")), _iso(r["pulled_at"]),
                    )
                    for r in rows
                ],
            )
            await db.commit()
            return db.total_changes - before

    async def save_notice_rows(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        async with self._connect() as db:
            before = db.total_changes
            await db.executemany(
                """INSERT INTO stock_notices_daily (
                     market, trade_date, symbol, name, title, notice_type, raw_json, pulled_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, trade_date, symbol, title) DO UPDATE SET
                     name=excluded.name,
                     notice_type=excluded.notice_type,
                     raw_json=excluded.raw_json,
                     pulled_at=excluded.pulled_at""",
                [
                    (
                        r["market"], r["trade_date"], r["symbol"], r.get("name"),
                        r["title"], r.get("notice_type"), _json(r.get("raw")),
                        _iso(r["pulled_at"]),
                    )
                    for r in rows
                ],
            )
            await db.commit()
            return db.total_changes - before

    async def save_fund_flow_rows(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        async with self._connect() as db:
            before = db.total_changes
            await db.executemany(
                """INSERT INTO lowfreq_fund_flow_daily (
                     market, trade_date, flow_type, subject, change_pct,
                     net_inflow, raw_json, pulled_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(market, trade_date, flow_type, subject) DO UPDATE SET
                     change_pct=excluded.change_pct,
                     net_inflow=excluded.net_inflow,
                     raw_json=excluded.raw_json,
                     pulled_at=excluded.pulled_at""",
                [
                    (
                        r["market"], r["trade_date"], r["flow_type"], r["subject"],
                        r.get("change_pct"), r.get("net_inflow"), _json(r.get("raw")),
                        _iso(r["pulled_at"]),
                    )
                    for r in rows
                ],
            )
            await db.commit()
            return db.total_changes - before

    async def summary_by_date(self, market: str, trade_date: str) -> dict[str, Any]:
        async with self._connect() as db:
            lhb_cur = await db.execute(
                """SELECT symbol, name, reason, net_buy FROM lhb_daily
                   WHERE market = ? AND trade_date = ?
                   ORDER BY net_buy DESC LIMIT 10""",
                (market, trade_date),
            )
            lhb = [dict(r) for r in await lhb_cur.fetchall()]
            notice_cur = await db.execute(
                """SELECT symbol, name, title, notice_type FROM stock_notices_daily
                   WHERE market = ? AND trade_date = ?
                   ORDER BY symbol ASC LIMIT 20""",
                (market, trade_date),
            )
            notices = [dict(r) for r in await notice_cur.fetchall()]
            flow_cur = await db.execute(
                """SELECT flow_type, subject, change_pct, net_inflow
                   FROM lowfreq_fund_flow_daily
                   WHERE market = ? AND trade_date = ?
                   ORDER BY net_inflow DESC LIMIT 20""",
                (market, trade_date),
            )
            flows = [dict(r) for r in await flow_cur.fetchall()]
        return {
            "lhb_count": len(lhb),
            "notice_count": len(notices),
            "fund_flow_count": len(flows),
            "top_lhb": lhb,
            "top_notices": notices,
            "top_fund_flows": flows,
        }
