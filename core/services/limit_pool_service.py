from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

import pandas as pd
import structlog

from core.domain.models import LimitPoolItem
from core.integrations.akshare import ak_call
from core.persistence.limit_pool_repo import LimitPoolRepo

log = structlog.get_logger(__name__)

PoolType = Literal["limit_up", "broken_limit", "down_limit", "previous"]

_AK_FUNC_BY_POOL: dict[PoolType, str] = {
    "limit_up": "stock_zt_pool_em",
    "broken_limit": "stock_zt_pool_zbgc_em",
    "down_limit": "stock_zt_pool_dtgc_em",
    # previous: 昨日涨停今日表现 (涨跌幅/最新价=今日, 连板数/封板时间=昨日)
    "previous": "stock_zt_pool_previous_em",
}


class LimitPoolService:
    """涨停/炸板/跌停池采集与查询。

    外部 AkShare 调用只在 service 层发生; route/结论层只读 repo。
    """

    def __init__(self, repo: LimitPoolRepo) -> None:
        self.repo = repo

    async def pull_pool(self, trade_date: str, pool_type: PoolType) -> int:
        date_arg = trade_date.replace("-", "")
        func = _AK_FUNC_BY_POOL[pool_type]
        try:
            df = await ak_call(
                func,
                date=date_arg,
                caller=f"limit_pool.pull:{pool_type}:{date_arg}",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("limit_pool.pull_failed", pool_type=pool_type, trade_date=trade_date, error=str(e))
            return 0
        items = parse_limit_pool_df(df, trade_date=trade_date, pool_type=pool_type)
        return await self.repo.save_items(items)

    async def pull_all(self, trade_date: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for pool_type in ("limit_up", "broken_limit", "down_limit", "previous"):
            out[pool_type] = await self.pull_pool(trade_date, pool_type)  # type: ignore[arg-type]
        return out

    async def list_by_date(
        self,
        trade_date: str,
        *,
        pool_type: str | None = None,
        market: str = "ashare",
    ) -> list[LimitPoolItem]:
        return await self.repo.list_by_date(market, trade_date, pool_type=pool_type)

    async def summary_by_date(self, trade_date: str, *, market: str = "ashare") -> dict[str, Any]:
        return await self.repo.summary_by_date(market, trade_date)


def parse_limit_pool_df(
    df: pd.DataFrame,
    *,
    trade_date: str,
    pool_type: PoolType,
    pulled_at: datetime | None = None,
) -> list[LimitPoolItem]:
    if df is None or df.empty:
        return []
    pulled_at = pulled_at or datetime.now(timezone.utc)
    items: list[LimitPoolItem] = []
    for _, row in df.iterrows():
        code = str(row.get("代码") or "").strip()
        if not code:
            continue
        raw = {str(k): _raw_value(v) for k, v in row.to_dict().items()}
        items.append(
            LimitPoolItem(
                market="ashare",
                trade_date=trade_date,
                pool_type=pool_type,
                symbol=_normalize_ashare_symbol(code),
                name=_str_or_none(row.get("名称")),
                change_pct=_num(row.get("涨跌幅")),
                price=_num(row.get("最新价")),
                limit_price=_num(row.get("涨停价")),
                amount=_num(row.get("成交额")),
                free_float_cap=_num(row.get("流通市值")),
                total_cap=_num(row.get("总市值")),
                turnover_rate=_num(row.get("换手率")),
                seal_amount=_num(row.get("封板资金")) or _num(row.get("封单资金")),
                first_seal_time=_str_or_none(row.get("首次封板时间"))
                or _str_or_none(row.get("昨日封板时间")),
                last_seal_time=_str_or_none(row.get("最后封板时间")),
                break_count=_int(row.get("炸板次数")) or _int(row.get("开板次数")),
                ladder_count=_int(row.get("连板数")) or _int(row.get("连续跌停"))
                or _int(row.get("昨日连板数")),
                industry=_str_or_none(row.get("所属行业")),
                amplitude=_num(row.get("振幅")),
                raw=raw,
                pulled_at=pulled_at,
            )
        )
    return items


def _normalize_ashare_symbol(code: str) -> str:
    if code.endswith((".SH", ".SZ", ".BJ")):
        return code
    # 北交所(920xxx / 8xx / 4xx)优先, 避免 920 被下面的 "9" 误判为上交所
    if code.startswith(("4", "8", "920")):
        return f"{code}.BJ"
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    return code


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _num(value)
    if number is None:
        return None
    return int(number)


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    text = str(value).strip()
    return text or None


def _raw_value(value: Any) -> Any:
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:  # noqa: BLE001
            return str(value)
    return value
