"""盘后回放聚合路由。

只读 SQLite (live_messages + theme_snapshots), 不碰 DuckDB (雷区 6):
指数 5m 曲线由前端复用现有 /api/symbols/{symbol}/bars/history 链路, 不在此路由取。

按 BJT 自然日构造全天时间窗 (覆盖盘前/盘中/盘后, 纳入收盘结算类消息),
查当日全部实盘消息 + 题材快照序列, 按 theme_code 分组成时间序列供前端重建状态变迁曲线。
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from apps.api.deps import get_live_message_repo, get_theme_repo
from core.domain.market_sessions import MARKET_TZ
from core.persistence.live_message_repo import LiveMessageRepo
from core.persistence.theme_repo import ThemeRepo

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/replay", tags=["replay"])


class ReplayMessageDTO(BaseModel):
    id: str
    ts: str
    level: str
    category: str
    title: str
    body: str
    theme_code: str | None
    symbol: str | None
    symbols: list[str]
    payload: dict[str, Any]
    rule_version: str


class ThemeSeriesPointDTO(BaseModel):
    ts: str
    pct_change: float | None
    pct_change_5m: float | None
    up_ratio: float | None
    amount: float | None
    limit_up_count: int | None
    divergence_score: float | None
    support_score: float | None


class ThemeSeriesDTO(BaseModel):
    theme_code: str
    theme_name: str
    classification: str
    points: list[ThemeSeriesPointDTO]


class ReplayResp(BaseModel):
    market: str
    date: str
    start: str
    end: str
    messages: list[ReplayMessageDTO]
    message_total: int
    message_offset: int
    message_limit: int
    message_has_more: bool
    theme_series: list[ThemeSeriesDTO]
    degraded: list[str]


def _ensure_ashare(market: str) -> None:
    if market != "ashare":
        raise HTTPException(400, "replay currently supports ashare only")


def _day_window(date_str: str | None, market: str) -> tuple[str, datetime, datetime]:
    """把 BJT 自然日转成 [00:00, 23:59:59] 的 UTC 时间窗。date 缺省=今天(BJT)。"""
    tz = ZoneInfo(MARKET_TZ[market])
    if date_str:
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError as e:
            raise HTTPException(400, f"invalid date, expect YYYY-MM-DD: {e}") from e
    else:
        day = datetime.now(tz).date()
    start = datetime.combine(day, time(0, 0, 0), tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(day, time(23, 59, 59), tzinfo=tz).astimezone(timezone.utc)
    return day.isoformat(), start, end


@router.get("", response_model=ReplayResp)
async def replay(
    market: str = Query("ashare"),
    date: str | None = Query(None, description="BJT 自然日 YYYY-MM-DD, 缺省=今天"),
    category: str | None = Query(None, description="消息分类, 缺省=全部"),
    msg_limit: int = Query(200, ge=1, le=500),
    msg_offset: int = Query(0, ge=0),
    snapshot_limit: int = Query(2000, ge=1, le=5000),
    msg_repo: LiveMessageRepo = Depends(get_live_message_repo),
    theme_repo: ThemeRepo = Depends(get_theme_repo),
) -> ReplayResp:
    _ensure_ashare(market)
    day_str, start, end = _day_window(date, market)
    degraded: list[str] = []

    messages: list[ReplayMessageDTO] = []
    message_total = 0
    try:
        message_total = await msg_repo.count_window(
            market, start=start, end=end, category=category,
        )
        rows = await msg_repo.list_window_asc(
            market, start=start, end=end, category=category, limit=msg_limit, offset=msg_offset,
        )
        for m in rows:
            messages.append(
                ReplayMessageDTO(
                    id=m.id,
                    ts=m.ts.isoformat(),
                    level=m.level,
                    category=m.category,
                    title=m.title,
                    body=m.body,
                    theme_code=m.theme_code,
                    symbol=m.symbol,
                    symbols=m.symbols or [],
                    payload=m.payload or {},
                    rule_version=m.rule_version,
                )
            )
    except Exception as e:  # noqa: BLE001
        degraded.append("实盘消息读取失败")
        log.warning("replay.messages_failed", error=str(e))

    theme_series: list[ThemeSeriesDTO] = []
    try:
        snapshots = await theme_repo.list_snapshots_window(
            market, start=start, end=end, limit=snapshot_limit,
        )
        # 按 theme_code 分组, snapshots 已按 ts 升序
        grouped: dict[str, ThemeSeriesDTO] = {}
        for s in snapshots:
            series = grouped.get(s.theme_code)
            if series is None:
                series = ThemeSeriesDTO(
                    theme_code=s.theme_code,
                    theme_name=s.theme_name,
                    classification=s.classification,
                    points=[],
                )
                grouped[s.theme_code] = series
            series.points.append(
                ThemeSeriesPointDTO(
                    ts=s.ts.isoformat(),
                    pct_change=s.pct_change,
                    pct_change_5m=s.pct_change_5m,
                    up_ratio=s.up_ratio,
                    amount=s.amount,
                    limit_up_count=s.limit_up_count,
                    divergence_score=s.divergence_score,
                    support_score=s.support_score,
                )
            )
        # 题材排序: 点数多(活跃)在前
        theme_series = sorted(grouped.values(), key=lambda t: len(t.points), reverse=True)
    except Exception as e:  # noqa: BLE001
        degraded.append("题材快照读取失败")
        log.warning("replay.snapshots_failed", error=str(e))

    return ReplayResp(
        market=market,
        date=day_str,
        start=start.isoformat(),
        end=end.isoformat(),
        messages=messages,
        message_total=message_total,
        message_offset=msg_offset,
        message_limit=msg_limit,
        message_has_more=msg_offset + len(messages) < message_total,
        theme_series=theme_series,
        degraded=degraded,
    )
