"""回填 26 年开盘至今每个交易日的复盘报告 (一次性脚本, collector 侧跑)。

用法 (在项目根, 已激活 venv):
    python -m scripts.backfill_daily_reviews                # 回填 2026 全年至今
    python -m scripts.backfill_daily_reviews --start 2026-01-01 --end 2026-06-16
    python -m scripts.backfill_daily_reviews --sw-only      # 只回填申万行业指数

历史日 (1月~6月中) 无盘中消息流水: 日线层 (走势/板块位置/龙头分层) 照常生成,
消息层标 data_gaps。结果落 SQLite daily_reviews, api/前端可直接翻阅。

雷区6: 本脚本持 BarRepo RW (DuckDB), 必须在 collector 环境单独跑, 不能与
collector-ashare 进程同时持有 RW 连接。建议停 collector-ashare 后跑, 或确保
回填窗口避开盘中采集。
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timezone
from pathlib import Path

import structlog

_BASE = Path(__file__).resolve().parents[1]
_DATA = _BASE / "data"

log = structlog.get_logger(__name__)

# 大盘基准指数 (取其交易日序列作为回填日历)
_BENCH = "000300.SH"


async def _backfill_sw(sw_service) -> None:
    log.info("backfill.sw_industry.start")
    result = await sw_service.backfill_all(only_missing=False)
    log.info("backfill.sw_industry.done", industries=len(result), saved=sum(result.values()))


async def _backfill_reviews(start: date, end: date) -> None:
    from core.persistence.duckdb_repo import BarRepo
    from core.persistence.daily_review_repo import DailyReviewRepo
    from core.persistence.live_message_repo import LiveMessageRepo
    from core.persistence.sw_industry_repo import SwIndustryRepo
    from core.persistence.theme_repo import ThemeRepo
    from core.services.daily_review_builder import DailyReviewBuilder
    from core.services.market_conclusion_service import MarketConclusionService
    from apps.collector.jobs.daily_review import generate_and_store_daily_review

    bar_repo = BarRepo(str(_DATA / "bars_ashare.duckdb"))
    bar_repo.init()
    state_db = str(_DATA / "state.db")
    review_repo = DailyReviewRepo(state_db)
    builder = DailyReviewBuilder(bar_repo, SwIndustryRepo(state_db), ThemeRepo(state_db))
    service = MarketConclusionService(
        LiveMessageRepo(state_db), ThemeRepo(state_db),
        daily_review_repo=review_repo, daily_review_builder=builder,
    )

    # 用基准指数已有收线日作为交易日历 (保证日线数据存在)。
    # 雷区3: 1d ts=UTC(D-1)16:00, 换 BJT 得交易日。
    from zoneinfo import ZoneInfo
    bjt = ZoneInfo("Asia/Shanghai")
    s = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    e = datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc)
    bars = bar_repo.fetch_history("ashare", _BENCH, s, e, "1d", closed_only=True)
    trade_dates = sorted({
        b.ts.replace(tzinfo=timezone.utc).astimezone(bjt).date().isoformat()
        for b in bars
        if start <= b.ts.replace(tzinfo=timezone.utc).astimezone(bjt).date() <= end
    })
    log.info("backfill.reviews.start", days=len(trade_dates),
             first=trade_dates[0] if trade_dates else None,
             last=trade_dates[-1] if trade_dates else None)
    ok = 0
    for td in trade_dates:
        if await generate_and_store_daily_review(service, review_repo, market="ashare", trade_date=td):
            ok += 1
    log.info("backfill.reviews.done", total=len(trade_dates), ok=ok)


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=None, help="起始交易日 YYYY-MM-DD, 缺省=当年年初")
    parser.add_argument("--end", default=None, help="结束交易日 YYYY-MM-DD, 缺省=今天")
    parser.add_argument("--sw-only", action="store_true", help="只回填申万行业指数")
    parser.add_argument("--skip-sw", action="store_true", help="跳过申万行业回填")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).astimezone().date()
    start = date.fromisoformat(args.start) if args.start else date(today.year, 1, 1)
    end = date.fromisoformat(args.end) if args.end else today

    if not args.skip_sw:
        from apps.api.deps import get_sw_industry_service
        await _backfill_sw(get_sw_industry_service())
    if args.sw_only:
        return
    await _backfill_reviews(start, end)


if __name__ == "__main__":
    asyncio.run(_main())
