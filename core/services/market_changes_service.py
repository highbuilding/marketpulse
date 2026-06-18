"""个股盘口异动 + 板块异动采集与查询 (stock_changes_em / stock_board_change_em)。

外部 AkShare 调用只在 service 层; route/结论层只读 repo / cache。
雷区: 这两个是东财 EM bulk 接口, 盘中调用要节流 (见 collector job cron)。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import structlog

from core.domain.models import BoardChange, StockChange
from core.integrations.akshare import ak_call
from core.persistence.stock_changes_repo import StockChangesRepo
from core.services.limit_pool_service import (
    _int,
    _normalize_ashare_symbol,
    _num,
    _str_or_none,
)

log = structlog.get_logger(__name__)

# 采集的异动类型 (stock_changes_em 的 symbol 参数)。覆盖用户要的:
# 快速拉升 / 快速跌 / 涨停封板 / 炸板 / 竞价。
CHANGE_TYPES: list[str] = [
    "火箭发射", "快速反弹",      # 快速拉升
    "加速下跌", "高台跳水",      # 快速下跌
    "封涨停板",                  # 涨停封板
    "打开涨停板",                # 炸板
    "竞价上涨", "竞价下跌",      # 竞价异动
]

# 异动类型 → UI 分组 category
CHANGE_CATEGORY: dict[str, str] = {
    "火箭发射": "surge", "快速反弹": "surge",
    "加速下跌": "plunge", "高台跳水": "plunge",
    "封涨停板": "limit_up",
    "打开涨停板": "broken",
    "竞价上涨": "auction_up", "竞价下跌": "auction_down",
}


class MarketChangesService:
    def __init__(self, repo: StockChangesRepo) -> None:
        self.repo = repo

    async def pull_changes(
        self, trade_date: str, *, change_types: list[str] | None = None,
    ) -> dict[str, int]:
        """逐类型拉 stock_changes_em 并落库。每类型失败只 warning (优雅降级)。"""
        types = change_types or CHANGE_TYPES
        out: dict[str, int] = {}
        for ctype in types:
            try:
                df = await ak_call(
                    "stock_changes_em",
                    symbol=ctype,
                    caller=f"market_changes.pull:{ctype}:{trade_date}",
                )
            except Exception as e:  # noqa: BLE001
                log.warning("market_changes.pull_failed", change_type=ctype,
                            trade_date=trade_date, error=str(e))
                out[ctype] = 0
                continue
            items = parse_changes_df(df, trade_date=trade_date)
            out[ctype] = await self.repo.save_changes(items)
        return out

    async def pull_board_changes(self, trade_date: str) -> int:
        """拉 stock_board_change_em (板块异动) 并落库。"""
        try:
            df = await ak_call(
                "stock_board_change_em",
                caller=f"market_changes.board:{trade_date}",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("market_changes.board_failed", trade_date=trade_date, error=str(e))
            return 0
        items = parse_board_change_df(df, trade_date=trade_date)
        return await self.repo.save_board_changes(items)

    async def list_changes(
        self, market: str, trade_date: str, *,
        change_types: list[str] | None = None, limit: int = 200,
    ) -> list[StockChange]:
        return await self.repo.list_changes(
            market, trade_date, change_types=change_types, limit=limit)

    async def count_by_type(self, market: str, trade_date: str) -> dict[str, int]:
        return await self.repo.count_by_type(market, trade_date)

    async def list_board_changes(
        self, market: str, trade_date: str, *, limit: int = 50,
    ) -> list[BoardChange]:
        return await self.repo.list_board_changes(market, trade_date, limit=limit)


def _decode_related(info: str | None) -> tuple[float | None, float | None]:
    """解码 stock_changes_em 的 `相关信息` 列 (逗号分隔, 语义随类型变化)。

    经验规则 (实测各类型样例):
    - 价格 = 第一个 >=1 且带小数的字段 (区别于成交量大整数)。
    - 涨跌幅 = 最后一个 abs<1 的字段 (分数), 转百分数。
    例: "35.890000,184647,35.89000,0.099908" → price=35.89, pct=9.99
    """
    if not info:
        return None, None
    fields: list[float] = []
    for part in str(info).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            fields.append(float(part))
        except ValueError:
            continue
    if not fields:
        return None, None
    price = None
    for f in fields:
        if abs(f) >= 1 and f != int(f):
            price = round(f, 4)
            break
    if price is None:
        for f in fields:
            if abs(f) >= 1:
                price = round(f, 4)
                break
    pct = None
    for f in fields:
        if abs(f) < 1:
            pct = round(f * 100, 4)
    return price, pct


def parse_changes_df(
    df: pd.DataFrame, *, trade_date: str, pulled_at: datetime | None = None,
) -> list[StockChange]:
    if df is None or df.empty:
        return []
    pulled_at = pulled_at or datetime.now(timezone.utc)
    items: list[StockChange] = []
    for _, row in df.iterrows():
        code = str(row.get("代码") or "").strip()
        if not code:
            continue
        info = row.get("相关信息")
        price, pct = _decode_related(info if isinstance(info, str) else str(info or ""))
        items.append(
            StockChange(
                market="ashare",
                trade_date=trade_date,
                change_time=_str_or_none(row.get("时间")) or "",
                symbol=_normalize_ashare_symbol(code),
                change_type=_str_or_none(row.get("板块")) or "",
                name=_str_or_none(row.get("名称")),
                price=price,
                change_pct=pct,
                raw={"相关信息": _str_or_none(info)},
                pulled_at=pulled_at,
            )
        )
    return items


def parse_board_change_df(
    df: pd.DataFrame, *, trade_date: str, pulled_at: datetime | None = None,
) -> list[BoardChange]:
    if df is None or df.empty:
        return []
    pulled_at = pulled_at or datetime.now(timezone.utc)
    code_col = "板块异动最频繁个股及所属类型-股票代码"
    name_col = "板块异动最频繁个股及所属类型-股票名称"
    dir_col = "板块异动最频繁个股及所属类型-买卖方向"
    items: list[BoardChange] = []
    for _, row in df.iterrows():
        board = _str_or_none(row.get("板块名称"))
        if not board:
            continue
        top_code = str(row.get(code_col) or "").strip()
        items.append(
            BoardChange(
                market="ashare",
                trade_date=trade_date,
                board_name=board,
                change_pct=_num(row.get("涨跌幅")),
                main_net_inflow=_num(row.get("主力净流入")),
                change_total=_int(row.get("板块异动总次数")),
                top_symbol=_normalize_ashare_symbol(top_code) if top_code else None,
                top_name=_str_or_none(row.get(name_col)),
                top_direction=_str_or_none(row.get(dir_col)),
                raw={},
                pulled_at=pulled_at,
            )
        )
    return items
