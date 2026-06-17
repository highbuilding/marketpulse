from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import structlog

from core.domain.models import SwIndustryBar, SwIndustryInfo
from core.integrations.akshare import ak_call
from core.persistence.sw_industry_repo import SwIndustryRepo

log = structlog.get_logger(__name__)

# 行业回填节流: 与 startup_reconcile THROTTLE_S 同量级, 摊平 burst。
_THROTTLE_S = 1.5


class SwIndustryService:
    """申万一级行业指数采集与查询 (collector 持有, 唯一 ak_call 入口)。

    行业指数 OHLCV 写 SQLite; api/结论层只读 repo, 不触发 ak_call。
    """

    def __init__(self, repo: SwIndustryRepo) -> None:
        self.repo = repo

    async def refresh_info(self) -> int:
        """拉取 31 个一级行业列表 + 估值元信息, upsert。返回行业数。"""
        try:
            df = await ak_call("sw_index_first_info", caller="sw_industry.info")
        except Exception as e:  # noqa: BLE001
            log.warning("sw_industry.info_failed", error=str(e))
            return 0
        infos = parse_sw_info_df(df)
        return await self.repo.save_info(infos)

    async def list_codes(self) -> list[str]:
        """返回已知行业代码 (优先 info 表, 空则现拉一次)。"""
        infos = await self.repo.list_info()
        if not infos:
            await self.refresh_info()
            infos = await self.repo.list_info()
        return [i.industry_code for i in infos]

    async def backfill_one(self, industry_code: str, industry_name: str | None = None) -> int:
        """全量回填单个行业指数日线 (index_hist_sw, period=day)。"""
        try:
            df = await ak_call(
                "index_hist_sw",
                symbol=industry_code,
                period="day",
                caller=f"sw_industry.hist:{industry_code}",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("sw_industry.hist_failed", industry_code=industry_code, error=str(e))
            return 0
        bars = parse_sw_hist_df(df, industry_name=industry_name)
        return await self.repo.save_bars(bars)

    async def backfill_all(self, *, only_missing: bool = False) -> dict[str, int]:
        """回填全部一级行业。only_missing=True 时跳过已有当日数据的行业。

        逐行业节流, 单行业失败不阻塞整批 (优雅降级)。
        """
        await self.refresh_info()
        infos = await self.repo.list_info()
        last_dates = await self.repo.last_dates() if only_missing else {}
        today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        out: dict[str, int] = {}
        for info in infos:
            if only_missing and last_dates.get(info.industry_code) == today:
                continue
            out[info.industry_code] = await self.backfill_one(
                info.industry_code, info.industry_name)
            await asyncio.sleep(_THROTTLE_S * random.uniform(0.8, 1.3))
        log.info("sw_industry.backfill_done", industries=len(out),
                 saved=sum(out.values()), only_missing=only_missing)
        return out

    async def list_history(
        self,
        industry_code: str,
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> list[SwIndustryBar]:
        return await self.repo.list_history(industry_code, start=start, end=end)


def parse_sw_info_df(df: pd.DataFrame) -> list[SwIndustryInfo]:
    if df is None or df.empty:
        return []
    now = datetime.now(timezone.utc)
    infos: list[SwIndustryInfo] = []
    for _, row in df.iterrows():
        code = _strip_suffix(str(row.get("行业代码") or "").strip())
        name = _str_or_none(row.get("行业名称"))
        if not code or not name:
            continue
        infos.append(
            SwIndustryInfo(
                industry_code=code,
                industry_name=name,
                member_count=_int(row.get("成份个数")),
                pe_static=_num(row.get("静态市盈率")),
                pe_ttm=_num(row.get("TTM(滚动)市盈率")),
                pb=_num(row.get("市净率")),
                dividend_yield=_num(row.get("静态股息率")),
                updated_at=now,
            )
        )
    return infos


def parse_sw_hist_df(
    df: pd.DataFrame,
    *,
    industry_name: str | None = None,
    pulled_at: datetime | None = None,
) -> list[SwIndustryBar]:
    if df is None or df.empty:
        return []
    pulled_at = pulled_at or datetime.now(timezone.utc)
    bars: list[SwIndustryBar] = []
    for _, row in df.iterrows():
        code = _strip_suffix(str(row.get("代码") or "").strip())
        trade_date = _normalize_date(row.get("日期"))
        if not code or not trade_date:
            continue
        bars.append(
            SwIndustryBar(
                industry_code=code,
                industry_name=industry_name,
                trade_date=trade_date,
                open=_num(row.get("开盘")),
                high=_num(row.get("最高")),
                low=_num(row.get("最低")),
                close=_num(row.get("收盘")),
                volume=_num(row.get("成交量")),
                amount=_num(row.get("成交额")),
                pulled_at=pulled_at,
            )
        )
    return bars


def _strip_suffix(code: str) -> str:
    """801010.SI -> 801010 (index_hist_sw 接收不带后缀的代码)。"""
    return code.split(".")[0] if code else code


def _normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text:
        return None
    # akshare 返回 datetime.date / Timestamp / 'YYYY-MM-DD' 字符串
    return text[:10]


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None
