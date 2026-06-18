from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
import structlog

from core.integrations.akshare import ak_call
from core.persistence.lowfreq_fact_repo import LowFreqFactRepo
from core.services.limit_pool_service import _normalize_ashare_symbol, _num, _str_or_none

log = structlog.get_logger(__name__)


class LowFreqFactService:
    """盘后低频事实采集。

    这些源只增强复盘, 不参与盘中硬链路; 单源失败只 warning。
    """

    def __init__(self, repo: LowFreqFactRepo) -> None:
        self.repo = repo

    async def pull_all(self, trade_date: str) -> dict[str, int]:
        out = {
            "lhb": await self.pull_lhb(trade_date),
            "notices": await self.pull_notices(trade_date),
            "fund_flow_individual": await self.pull_ths_fund_flow(
                trade_date, "individual"),
            "fund_flow_concept": await self.pull_ths_fund_flow(trade_date, "concept"),
            "fund_flow_industry": await self.pull_ths_fund_flow(trade_date, "industry"),
        }
        return out

    async def pull_lhb(self, trade_date: str) -> int:
        date_arg = trade_date.replace("-", "")
        try:
            df = await ak_call(
                "stock_lhb_detail_em",
                start_date=date_arg,
                end_date=date_arg,
                caller=f"lowfreq.lhb:{date_arg}",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("lowfreq.lhb_failed", trade_date=trade_date, error=str(e))
            return 0
        return await self.repo.save_lhb_rows(parse_lhb_df(df, trade_date=trade_date))

    async def pull_notices(self, trade_date: str) -> int:
        date_arg = trade_date.replace("-", "")
        try:
            df = await ak_call(
                "stock_notice_report",
                symbol="全部",
                date=date_arg,
                caller=f"lowfreq.notices:{date_arg}",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("lowfreq.notices_failed", trade_date=trade_date, error=str(e))
            return 0
        return await self.repo.save_notice_rows(parse_notice_df(df, trade_date=trade_date))

    async def pull_ths_fund_flow(self, trade_date: str, flow_type: str) -> int:
        func = {
            "individual": "stock_fund_flow_individual",
            "concept": "stock_fund_flow_concept",
            "industry": "stock_fund_flow_industry",
        }[flow_type]
        try:
            df = await ak_call(
                func,
                symbol="即时",
                caller=f"lowfreq.ths_flow:{flow_type}:{trade_date}",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("lowfreq.ths_flow_failed", flow_type=flow_type,
                        trade_date=trade_date, error=str(e))
            return 0
        return await self.repo.save_fund_flow_rows(
            parse_ths_fund_flow_df(df, trade_date=trade_date, flow_type=flow_type))

    async def summary_by_date(self, market: str, trade_date: str) -> dict[str, Any]:
        return await self.repo.summary_by_date(market, trade_date)


def parse_lhb_df(df: pd.DataFrame, *, trade_date: str) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        code = _first(row, "代码", "股票代码", "证券代码")
        code = str(code or "").strip()
        if not code:
            continue
        raw = {str(k): _raw(v) for k, v in row.to_dict().items()}
        rows.append({
            "market": "ashare",
            "trade_date": trade_date,
            "symbol": _normalize_ashare_symbol(code),
            "name": _str_or_none(_first(row, "名称", "股票简称", "证券简称")),
            "reason": _str_or_none(_first(row, "上榜原因", "解读", "类型")),
            "net_buy": _num(_first(row, "龙虎榜净买额", "净买额", "净买入额")),
            "buy_amount": _num(_first(row, "龙虎榜买入额", "买入额")),
            "sell_amount": _num(_first(row, "龙虎榜卖出额", "卖出额")),
            "turnover_rate": _num(_first(row, "换手率")),
            "total_amount": _num(_first(row, "龙虎榜成交额", "总成交额", "成交额")),
            "raw": raw,
            "pulled_at": now,
        })
    return rows


def parse_notice_df(df: pd.DataFrame, *, trade_date: str) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        code = str(_first(row, "代码", "股票代码", "证券代码") or "").strip()
        title = _str_or_none(_first(row, "公告标题", "标题", "公告名称"))
        if not code or not title:
            continue
        rows.append({
            "market": "ashare",
            "trade_date": trade_date,
            "symbol": _normalize_ashare_symbol(code),
            "name": _str_or_none(_first(row, "名称", "股票简称", "证券简称")),
            "title": title,
            "notice_type": _str_or_none(_first(row, "公告类型", "类型")),
            "raw": {str(k): _raw(v) for k, v in row.to_dict().items()},
            "pulled_at": now,
        })
    return rows


def parse_ths_fund_flow_df(
    df: pd.DataFrame,
    *,
    trade_date: str,
    flow_type: str,
) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        subject = _str_or_none(_first(row, "行业", "概念", "名称", "股票简称", "代码"))
        if not subject:
            continue
        rows.append({
            "market": "ashare",
            "trade_date": trade_date,
            "flow_type": flow_type,
            "subject": subject,
            "change_pct": _num(_first(row, "涨跌幅", "涨跌幅(%)")),
            "net_inflow": _num(_first(
                row, "净额", "净流入", "主力净流入", "今日主力净流入-净额")),
            "raw": {str(k): _raw(v) for k, v in row.to_dict().items()},
            "pulled_at": now,
        })
    return rows


def _first(row: Any, *names: str) -> Any:
    for name in names:
        if name in row:
            value = row.get(name)
            try:
                if pd.isna(value):
                    continue
            except (TypeError, ValueError):
                pass
            return value
    return None


def _raw(value: Any) -> Any:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:  # noqa: BLE001
            return str(value)
    return value
