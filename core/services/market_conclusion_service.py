from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import structlog

from core.domain.models import LiveMessage, ThemeSnapshot
from core.domain.market_sessions import MARKET_TZ
from core.persistence.limit_pool_repo import LimitPoolRepo
from core.persistence.live_message_repo import LiveMessageRepo
from core.persistence.theme_repo import ThemeRepo

if TYPE_CHECKING:
    from core.persistence.daily_review_repo import DailyReviewRepo
    from core.services.lowfreq_fact_service import LowFreqFactService
    from core.services.daily_review_builder import DailyReviewBuilder

log = structlog.get_logger(__name__)

FORMULA_VERSION = "conclusion-v1"

_LEVEL_WEIGHT = {
    "info": 1.0,
    "watch": 2.0,
    "warning": 3.0,
    "critical": 5.0,
}
_CATEGORY_WEIGHT = {
    "index": 1.0,
    "theme": 1.15,
    "watchlist": 1.0,
    "signal": 1.05,
    "risk": 1.25,
    "system": 0.5,
}


@dataclass(frozen=True, slots=True)
class ConclusionSection:
    key: str
    title: str
    label: str
    score: float
    summary: str
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class IntradayConclusion:
    market: str
    generated_at: datetime
    window_minutes: int
    formula_version: str
    sections: list[ConclusionSection]
    data_gaps: list[str]


@dataclass(frozen=True, slots=True)
class DailyReviewConclusion:
    market: str
    trade_date: str
    generated_at: datetime
    formula_version: str
    summary: str
    sections: list[ConclusionSection]
    next_watch: list[str]
    data_gaps: list[str]


class MarketConclusionService:
    """把事实流水压缩为可解释结论层。

    第一阶段只读 SQLite 中的 live_messages/theme_snapshots, 不触发外部数据源。
    后续涨停池、龙虎榜、观察池等事实表接入后, 继续在这里组合。
    """

    def __init__(
        self,
        live_messages: LiveMessageRepo,
        themes: ThemeRepo,
        limit_pool: LimitPoolRepo | None = None,
        *,
        daily_review_repo: "DailyReviewRepo | None" = None,
        daily_review_builder: "DailyReviewBuilder | None" = None,
        lowfreq: "LowFreqFactService | None" = None,
    ) -> None:
        self.live_messages = live_messages
        self.themes = themes
        self.limit_pool = limit_pool
        # api 进程注入 repo (只读已生成复盘); collector 注入 builder (持 DuckDB 算日线)。
        self.daily_review_repo = daily_review_repo
        self.daily_review_builder = daily_review_builder
        self.lowfreq = lowfreq

    async def intraday(self, market: str, *, minutes: int = 60) -> IntradayConclusion:
        now = datetime.now(timezone.utc)
        return await self.intraday_window(
            market, start=now - timedelta(minutes=minutes), end=now,
            window_minutes=minutes,
        )

    async def intraday_window(
        self, market: str, *, start: datetime, end: datetime,
        window_minutes: int | None = None,
    ) -> IntradayConclusion:
        """对任意 [start, end] 窗口算盘中结论 (读时按窗切片, 无落档)。

        30min 结论轮 (/intraday-rounds) 用本方法对每个 30min 边界现算 —— 不再 cron 落库。
        只读 live_messages/theme_snapshots/涨停池, 不触发外部数据源。
        """
        if window_minutes is None:
            window_minutes = max(1, round((end - start).total_seconds() / 60))
        data_gaps: list[str] = []

        messages: list[LiveMessage] = []
        try:
            messages = await self.live_messages.list_window(
                market, start=start, end=end, limit=300,
            )
        except Exception as e:  # noqa: BLE001
            data_gaps.append(f"实盘消息读取失败:{e}")

        snapshots: list[ThemeSnapshot] = []
        try:
            snapshots = await self.themes.list_snapshots_window(
                market, start=start, end=end, limit=2000,
            )
        except Exception as e:  # noqa: BLE001
            data_gaps.append(f"题材快照读取失败:{e}")

        if not messages:
            data_gaps.append("窗口内暂无实盘消息")
        if not snapshots:
            data_gaps.append("窗口内暂无题材快照")

        trade_date = end.astimezone(
            ZoneInfo(MARKET_TZ.get(market, "Asia/Shanghai"))).date().isoformat()
        limit_summary = await self._limit_summary(market, trade_date, data_gaps)
        previous_summary = await self._previous_limit_summary(market, trade_date, data_gaps)

        sections = [
            self._market_section(messages),
            self._theme_section(snapshots, messages),
            self._limit_section(limit_summary),
            self._previous_limit_section(previous_summary),
            self._risk_section(messages),
            self._signal_section(messages),
        ]
        return IntradayConclusion(
            market=market,
            generated_at=end,
            window_minutes=window_minutes,
            formula_version=FORMULA_VERSION,
            sections=sections,
            data_gaps=data_gaps,
        )

    async def daily_review(
        self,
        market: str,
        *,
        trade_date: str | None = None,
    ) -> DailyReviewConclusion:
        """api 读路径: 优先返回 collector 已生成并落库的复盘 (含日线层);

        无存档 (尚未生成 / 历史日未回填) 时, 退化为纯消息即时计算 —— 缺日线层但
        不报错 (优雅降级)。collector 侧用 generate_daily_review 算全量并落库。
        """
        day, _start, _end = _day_window(market, trade_date)
        if self.daily_review_repo is not None:
            try:
                stored = await self.daily_review_repo.get(market, day.isoformat())
            except Exception as e:  # noqa: BLE001
                log.warning("conclusion.daily_review_read_failed", error=str(e))
                stored = None
            if stored is not None:
                return _conclusion_from_stored(market, stored)
        return await self._compute_daily_review(market, trade_date, day_sections=None)

    async def generate_daily_review(
        self,
        market: str,
        *,
        trade_date: str | None = None,
    ) -> DailyReviewConclusion:
        """collector 写路径: 算日线层 (builder) + 消息层, 合并为完整复盘。

        日线层全年每个交易日都可算; 消息层无数据时按日期标 data_gaps。
        调用方 (job/脚本) 负责把结果落 daily_reviews。
        """
        day_sections: list[ConclusionSection] = []
        day_gaps: list[str] = []
        day_headline = ""
        if self.daily_review_builder is not None:
            day, _s, _e = _day_window(market, trade_date)
            try:
                result = await self.daily_review_builder.build(day.isoformat())
                day_sections = [
                    ConclusionSection(
                        key=s.key, title=s.title, label=s.label,
                        score=s.score, summary=s.summary, evidence=s.evidence,
                    )
                    for s in result.sections
                ]
                day_gaps = result.data_gaps
                day_headline = result.headline
            except Exception as e:  # noqa: BLE001
                log.warning("conclusion.daily_trend_build_failed", error=str(e))
                day_gaps = [f"日线走势分析失败:{e}"]
        return await self._compute_daily_review(
            market, trade_date,
            day_sections=day_sections, day_gaps=day_gaps, day_headline=day_headline,
        )

    async def _compute_daily_review(
        self,
        market: str,
        trade_date: str | None,
        *,
        day_sections: list[ConclusionSection] | None,
        day_gaps: list[str] | None = None,
        day_headline: str = "",
    ) -> DailyReviewConclusion:
        day, start, end = _day_window(market, trade_date)
        now = datetime.now(timezone.utc)
        data_gaps: list[str] = list(day_gaps or [])

        messages: list[LiveMessage] = []
        try:
            messages = await self.live_messages.list_window_asc(
                market, start=start, end=end, limit=5000,
            )
        except Exception as e:  # noqa: BLE001
            data_gaps.append(f"实盘消息读取失败:{e}")

        snapshots: list[ThemeSnapshot] = []
        try:
            snapshots = await self.themes.list_snapshots_window(
                market, start=start, end=end, limit=5000,
            )
        except Exception as e:  # noqa: BLE001
            data_gaps.append(f"题材快照读取失败:{e}")

        if not messages:
            data_gaps.append("当日暂无实盘消息(盘中事实流水)")
        if not snapshots:
            data_gaps.append("当日暂无题材快照")
        limit_summary = await self._limit_summary(market, day.isoformat(), data_gaps)
        previous_summary = await self._previous_limit_summary(
            market, day.isoformat(), data_gaps)
        lowfreq_summary = await self._lowfreq_summary(market, day.isoformat(), data_gaps)

        # 从日线走势 section 取基准指数当日涨跌, 作为市场风险基调的硬约束:
        # 避免"上涨日却报风险主导"(题材盘中瞬时转弱不代表全天风险)。
        bench_day_change = _benchmark_day_change(day_sections)

        market_section = self._daily_market_section(messages, bench_day_change)
        theme_section = self._daily_theme_section(snapshots, messages)
        limit_section = self._limit_section(limit_summary)
        previous_section = self._previous_limit_section(previous_summary)
        lowfreq_section = self._lowfreq_section(lowfreq_summary)
        risk_section = self._daily_risk_section(messages, bench_day_change)
        signal_section = self._signal_section(messages)
        # 日线层 section 排在消息层前 (走势/板块位置/龙头分层是复盘主结论)
        sections = [
            *(day_sections or []),
            market_section, theme_section, limit_section, previous_section,
            lowfreq_section, risk_section, signal_section,
        ]

        summary = self._daily_summary(
            market_section, theme_section, limit_section, previous_section,
            lowfreq_section, risk_section, signal_section,
        )
        if day_headline:
            summary = f"{day_headline} {summary}"
        next_watch = self._next_watch(theme_section, limit_section, risk_section, signal_section)
        return DailyReviewConclusion(
            market=market,
            trade_date=day.isoformat(),
            generated_at=now,
            formula_version=FORMULA_VERSION,
            summary=summary,
            sections=sections,
            next_watch=next_watch,
            data_gaps=data_gaps,
        )

    @staticmethod
    def _risk_intensity(messages: list[LiveMessage]) -> dict[str, Any]:
        """有界风险强度: 只算'实质转弱的题材数 + critical事件数', 不随消息条数膨胀。

        收紧口径 (避免每天误报"风险主导"): 15 个题材几乎每天都会短暂出现
        分歧/异动偏单点/走强质量一般等常态波动, 这些不计入风险。只有:
        - 题材"转弱" (dedupe_key 末尾 :weakness, 真正走弱) 计入风险题材;
        - critical 级事件 (真正高危) 额外加权。
        剔除 CD 信号 (有独立 signal_state)。distinct 转弱题材上界=题材数, 稳定有界。
        """
        # 转弱题材: dedupe_key 形如 theme:{code}:weakness
        weak_themes = {
            m.theme_code for m in messages
            if m.theme_code and m.category != "signal"
            and m.dedupe_key.endswith(":weakness")
        }
        critical = [m for m in messages if m.category != "signal" and m.level == "critical"]
        # 风险事件 (供展示 top_risks): 转弱 + critical, 不含分歧/异动/质量噪音
        risk_events = [
            m for m in messages
            if m.category != "signal"
            and (m.dedupe_key.endswith(":weakness") or m.level == "critical")
        ]
        intensity = len(weak_themes) + 2 * len(critical)
        return {
            "intensity": intensity,
            "risk_theme_count": len(weak_themes),
            "critical_count": len(critical),
            "risk_event_count": len(risk_events),
            "top_risks": [m.title for m in risk_events[:6]],
        }

    @staticmethod
    def _message_pressure(messages: list[LiveMessage]) -> dict[str, float]:
        pressure: dict[str, float] = {}
        for msg in messages:
            level_weight = _LEVEL_WEIGHT.get(msg.level, 1.0)
            category_weight = _CATEGORY_WEIGHT.get(msg.category, 1.0)
            pressure[msg.category] = pressure.get(msg.category, 0.0) + level_weight * category_weight
        return pressure

    def _market_section(self, messages: list[LiveMessage]) -> ConclusionSection:
        pressure = self._message_pressure(messages)
        index_pressure = pressure.get("index", 0.0)
        theme_pressure = pressure.get("theme", 0.0)
        intensity = self._risk_intensity(messages)["intensity"]
        score = round(index_pressure + 0.6 * theme_pressure - 5 * intensity, 2)
        if intensity >= 8:
            label = "风险压制"
            summary = "多个题材处于风险状态,盘中结论以防守和降噪为主。"
        elif index_pressure + theme_pressure >= 12:
            label = "盘面活跃"
            summary = "指数或题材消息密度较高,需要结合题材扩散质量判断持续性。"
        elif messages:
            label = "盘面平稳"
            summary = "窗口内有事件,但未形成强方向结论。"
        else:
            label = "等待数据"
            summary = "窗口内暂无足够事实消息。"
        return ConclusionSection(
            key="market_state",
            title="市场状态",
            label=label,
            score=score,
            summary=summary,
            evidence={
                "message_count": len(messages),
                "pressure_by_category": pressure,
                "risk_intensity": intensity,
                "formula": "index_pressure + 0.6*theme_pressure - 5*risk_intensity",
            },
        )

    @staticmethod
    def _limit_section(summary: dict[str, Any] | None) -> ConclusionSection:
        if summary is None:
            return ConclusionSection(
                key="limit_structure",
                title="涨停结构",
                label="数据缺失",
                score=0.0,
                summary="真实涨停/炸板/跌停池暂无数据,暂用采集清单代理判断。",
                evidence={
                    "formula": "limit_mood_score = 25*limit_up/80 - 30*break_rate/0.45 - 20*down_limit/30 + 25*ladder_strength/100",
                },
            )

        limit_up_count = int(summary.get("limit_up_count") or 0)
        broken_count = int(summary.get("broken_count") or 0)
        down_limit_count = int(summary.get("down_limit_count") or 0)
        break_rate = float(summary.get("break_rate") or 0.0)
        max_ladder = int(summary.get("max_ladder_height") or 0)
        second_plus = int(summary.get("second_plus_count") or 0)
        ladder_strength = float(summary.get("ladder_strength_score") or 0.0)
        ladder_continuity = float(summary.get("ladder_continuity") or 0.0)
        score = round(
            25 * _clamp(limit_up_count / 80, 0, 1)
            - 30 * _clamp(break_rate / 0.45, 0, 1)
            - 20 * _clamp(down_limit_count / 30, 0, 1)
            + 25 * _clamp(ladder_strength / 100, 0, 1),
            2,
        )
        if break_rate >= 0.45 or down_limit_count >= 30:
            label = "退潮风险"
        elif ladder_strength >= 65 and break_rate < 0.25:
            label = "连板生态较强"
        elif max_ladder >= 5 and second_plus >= 8 and ladder_continuity >= 0.75:
            label = "连板梯队完整"
        elif limit_up_count >= 50 and break_rate < 0.35:
            label = "涨停结构活跃"
        elif limit_up_count > 0:
            label = "涨停结构一般"
        else:
            label = "涨停结构偏弱"
        return ConclusionSection(
            key="limit_structure",
            title="涨停结构",
            label=label,
            score=score,
            summary=(
                f"涨停 {limit_up_count} 只,炸板 {broken_count} 只,"
                f"跌停 {down_limit_count} 只,炸板率 {break_rate:.1%},"
                f"最高 {max_ladder} 连板,二板以上 {second_plus} 只,"
                f"连板强度 {ladder_strength:.1f}。"
            ),
            evidence={
                **summary,
                "formula": "25*limit_up/80 - 30*break_rate/0.45 - 20*down_limit/30 + 25*ladder_strength/100",
            },
        )

    @staticmethod
    def _previous_limit_section(summary: dict[str, Any] | None) -> ConclusionSection:
        if summary is None:
            return ConclusionSection(
                key="previous_limit_performance",
                title="昨日涨停表现",
                label="数据缺失",
                score=0.0,
                summary="昨日涨停今日表现暂无数据,无法计算晋级率和淘汰惩罚。",
                evidence={
                    "formula": (
                        "promotion_rate=count(previous∩today_limit)/count(previous); "
                        "current_edge=mean(previous.change_pct); "
                        "loser_penalty=mean(change_pct of previous not promoted)"
                    ),
                },
            )

        count = int(summary.get("previous_count") or 0)
        promotion = float(summary.get("promotion_rate") or 0.0)
        current_edge = summary.get("current_edge_pct")
        loser_penalty = summary.get("loser_penalty_pct")
        red_rate = summary.get("red_rate")
        high_promotion = summary.get("high_promotion_rate")
        high_loser_penalty = summary.get("high_loser_penalty_pct")
        edge = float(current_edge) if current_edge is not None else 0.0
        penalty = float(loser_penalty) if loser_penalty is not None else 0.0
        red = float(red_rate) if red_rate is not None else 0.0
        high = float(high_promotion) if high_promotion is not None else promotion
        high_penalty = float(high_loser_penalty) if high_loser_penalty is not None else penalty
        score = round(40 * promotion + 25 * red + 20 * high + 2 * edge + 1.2 * penalty + 1.2 * high_penalty, 2)
        if count == 0:
            label = "数据缺失"
        elif promotion >= 0.30 and high >= 0.25 and edge >= 2:
            label = "晋级溢价强"
        elif high_penalty <= -6:
            label = "高位淘汰重"
        elif edge >= 0:
            label = "溢价尚可"
        elif penalty <= -5:
            label = "淘汰惩罚重"
        else:
            label = "溢价偏弱"
        summary_text = (
            f"昨日涨停 {count} 只,今日晋级 {int(summary.get('promoted_count') or 0)} 只,"
            f"晋级率 {promotion:.1%},平均表现 {edge:+.2f}%,"
            f"红盘率 {red:.1%},淘汰惩罚 {penalty:+.2f}%,"
            f"高位晋级 {high:.1%}。"
        )
        return ConclusionSection(
            key="previous_limit_performance",
            title="昨日涨停表现",
            label=label,
            score=score,
            summary=summary_text,
            evidence={
                **summary,
                "formula": (
                    "score=40*promotion_rate + 25*red_rate + 20*high_promotion_rate + "
                    "2*current_edge_pct + 1.2*loser_penalty_pct + 1.2*high_loser_penalty_pct"
                ),
            },
        )

    @staticmethod
    def _lowfreq_section(summary: dict[str, Any] | None) -> ConclusionSection:
        if summary is None:
            return ConclusionSection(
                key="lowfreq_facts",
                title="低频资金与公告",
                label="数据缺失",
                score=0.0,
                summary="龙虎榜、公告、同花顺低频资金流暂无入库数据。",
                evidence={
                    "formula": "lowfreq_score = min(lhb_count,10)*2 + min(fund_flow_count,20)*0.5",
                },
            )
        lhb_count = int(summary.get("lhb_count") or 0)
        notice_count = int(summary.get("notice_count") or 0)
        fund_flow_count = int(summary.get("fund_flow_count") or 0)
        score = round(min(lhb_count, 10) * 2 + min(fund_flow_count, 20) * 0.5, 2)
        if lhb_count >= 20 or fund_flow_count >= 20:
            label = "低频确认充分"
        elif lhb_count > 0 or notice_count > 0 or fund_flow_count > 0:
            label = "低频事实可用"
        else:
            label = "数据缺失"
        summary_text = (
            f"龙虎榜 {lhb_count} 条,公告 {notice_count} 条,"
            f"低频资金流 {fund_flow_count} 条。"
        )
        return ConclusionSection(
            key="lowfreq_facts",
            title="低频资金与公告",
            label=label,
            score=score,
            summary=summary_text,
            evidence={
                **summary,
                "formula": "min(lhb_count,10)*2 + min(fund_flow_count,20)*0.5",
            },
        )

    def _daily_market_section(
        self, messages: list[LiveMessage], bench_day_change: float | None = None,
    ) -> ConclusionSection:
        pressure = self._message_pressure(messages)
        intensity = self._risk_intensity(messages)["intensity"]
        activity_score = round(sum(pressure.values()), 2)
        index_pressure = pressure.get("index", 0.0)
        theme_pressure = pressure.get("theme", 0.0)
        score = round(activity_score - 5 * intensity, 2)
        # 风险主导硬约束: 必须 (大盘当日下跌 或 无走势数据) 且多题材转弱才算。
        # 上涨日 (bench_day_change > 0) 即便盘中瞬时转弱也不判风险主导。
        market_down = bench_day_change is None or bench_day_change < 0
        risk_dominant = intensity >= 8 and market_down
        if theme_pressure >= index_pressure and theme_pressure >= 12 and not risk_dominant:
            label = "题材驱动"
            summary = "全天题材消息权重高于指数消息,复盘重点看主线扩散质量。"
        elif index_pressure >= 10 and not risk_dominant:
            label = "指数驱动"
            summary = "全天指数消息较多,复盘重点看指数风格与个股宽度是否一致。"
        elif risk_dominant:
            label = "全天风险主导"
            summary = f"全天 {self._risk_intensity(messages)['risk_theme_count']} 个题材转弱且大盘走弱,复盘重点放在退潮原因和风险条件。"
        elif messages:
            label = "结构平淡"
            summary = "全天事实消息未形成高强度主线。"
        else:
            label = "数据不足"
            summary = "当日暂无足够事实消息。"
        return ConclusionSection(
            key="daily_market",
            title="全天市场结构",
            label=label,
            score=score,
            summary=summary,
            evidence={
                "message_count": len(messages),
                "pressure_by_category": pressure,
                "activity_score": activity_score,
                "risk_intensity": intensity,
                "benchmark_day_change_pct": bench_day_change,
                "formula": "风险主导=转弱题材>=8 且 大盘当日下跌; 否则按题材/指数驱动",
            },
        )

    def _theme_section(
        self, snapshots: list[ThemeSnapshot], messages: list[LiveMessage],
    ) -> ConclusionSection:
        ranked = self._rank_themes(snapshots)
        top = ranked[0] if ranked else None
        theme_messages = [m for m in messages if m.category == "theme"]
        if top is None:
            label = "暂无主线"
            score = 0.0
            summary = "窗口内暂无题材快照,只能依赖事实消息做弱判断。"
        else:
            score = round(float(top["score"]), 2)
            momentum = float(top["momentum"])
            if score >= 70 and momentum >= 0:
                label = "主线扩散"
            elif score >= 55:
                label = "题材活跃"
            elif momentum <= -0.2:
                label = "题材回落"
            else:
                label = "题材观察"
            summary = f"{top['theme_name']} 当前最强,热度 {score:.1f}, up_ratio={top['up_ratio']!r}。"
        return ConclusionSection(
            key="theme_state",
            title="题材状态",
            label=label,
            score=score,
            summary=summary,
            evidence={
                "top_themes": ranked[:5],
                "theme_message_count": len(theme_messages),
                "formula": "40*up_ratio + 20*pct_change/5 + 15*pct_change_5m/2 + 15*(amount_ratio-1)/2 + 10*limit_up_count/3 - 15*divergence_score/100",
            },
        )

    def _daily_theme_section(
        self, snapshots: list[ThemeSnapshot], messages: list[LiveMessage],
    ) -> ConclusionSection:
        ranked = self._rank_themes(snapshots)
        top_themes = ranked[:5]
        top = top_themes[0] if top_themes else None
        theme_messages = [m for m in messages if m.category == "theme"]
        if top is None:
            label = "无明确主线"
            score = 0.0
            summary = "当日题材快照不足,无法确认主线。"
        else:
            score = round(float(top["score"]), 2)
            if score >= 70:
                label = "主线明确"
            elif score >= 55:
                label = "题材活跃"
            elif float(top["momentum"]) < -0.2:
                label = "主线回落"
            else:
                label = "轮动观察"
            names = "、".join(str(t["theme_name"]) for t in top_themes[:3])
            summary = f"全天题材强度靠前:{names}。"
        return ConclusionSection(
            key="daily_theme",
            title="全天题材复盘",
            label=label,
            score=score,
            summary=summary,
            evidence={
                "top_themes": top_themes,
                "theme_message_count": len(theme_messages),
                "formula": "按题材热度分排序,取全天窗口内最新快照与窗口初值 momentum",
            },
        )

    def _daily_risk_section(
        self, messages: list[LiveMessage], bench_day_change: float | None = None,
    ) -> ConclusionSection:
        risk = self._risk_section(messages)
        intensity = risk.score  # 有界强度 (转弱题材数 + 2*critical)
        market_down = bench_day_change is None or bench_day_change < 0
        if intensity >= 8 and market_down:
            label = "复盘重点:风险释放"
            summary = "全天多个题材转弱且大盘走弱,次日观察应先看风险是否解除。"
        elif intensity >= 4:
            label = "复盘重点:分歧修复"
            summary = "全天存在明显转弱题材,次日需要看主线是否修复。"
        else:
            label = "复盘重点:机会筛选"
            summary = "全天转弱题材有限,可重点筛选结构清晰的观察池。"
        return ConclusionSection(
            key="daily_risk",
            title="全天风险复盘",
            label=label,
            score=intensity,
            summary=summary,
            evidence=dict(risk.evidence),
        )

    def _risk_section(self, messages: list[LiveMessage]) -> ConclusionSection:
        pressure = self._message_pressure(messages)
        ri = self._risk_intensity(messages)
        intensity = ri["intensity"]
        # 有界阈值 (intensity = 风险题材数 + 2*critical): 剔除CD信号, 不随消息数膨胀。
        # ~8 题材陷入风险=升高; ~4=偏高。
        if intensity >= 8:
            label = "风险升高"
            summary = f"{ri['risk_theme_count']} 个题材处于风险状态,观察池应降权。"
        elif intensity >= 4:
            label = "风险偏高"
            summary = "已有风险信号,需要等待题材扩散或宽度改善确认。"
        else:
            label = "风险可控"
            summary = "风险题材数量有限,未达到警戒强度。"
        return ConclusionSection(
            key="risk_state",
            title="风险状态",
            label=label,
            score=float(intensity),
            summary=summary,
            evidence={
                "risk_message_count": ri["risk_event_count"],
                "risk_theme_count": ri["risk_theme_count"],
                "critical_count": ri["critical_count"],
                "top_risks": ri["top_risks"],
                "pressure_by_category": pressure,
                "formula": "intensity = 风险题材数 + 2*critical事件数 (剔除CD信号, 有界)",
            },
        )

    @staticmethod
    def _signal_section(messages: list[LiveMessage]) -> ConclusionSection:
        signal_messages = [m for m in messages if m.category == "signal"]
        buy_count = sum(1 for m in signal_messages if "买入" in f"{m.title}{m.body}")
        sell_count = sum(1 for m in signal_messages if "卖出" in f"{m.title}{m.body}")
        balance = buy_count - sell_count
        if balance > 0:
            label = "买入信号占优"
            summary = "CD 信号买入数量多于卖出,但仍需结合市场和题材风险。"
        elif balance < 0:
            label = "卖出信号占优"
            summary = "CD 信号卖出数量多于买入,观察池应谨慎。"
        elif signal_messages:
            label = "信号均衡"
            summary = "买卖信号未形成明显方向。"
        else:
            label = "暂无信号"
            summary = "窗口内暂无 CD 信号事件。"
        return ConclusionSection(
            key="signal_state",
            title="技术信号",
            label=label,
            score=float(balance),
            summary=summary,
            evidence={
                "signal_message_count": len(signal_messages),
                "buy_count": buy_count,
                "sell_count": sell_count,
                "formula": "signal_balance = buy_count - sell_count",
            },
        )

    @staticmethod
    def _daily_summary(*sections: ConclusionSection) -> str:
        by_key = {s.key: s for s in sections}
        market = by_key.get("daily_market")
        theme = by_key.get("daily_theme")
        risk = by_key.get("daily_risk")
        signal = by_key.get("signal_state")
        parts = []
        if market:
            parts.append(f"市场:{market.label}")
        if theme:
            parts.append(f"题材:{theme.label}")
        limit = by_key.get("limit_structure")
        if limit:
            parts.append(f"涨停:{limit.label}")
        previous = by_key.get("previous_limit_performance")
        if previous:
            parts.append(f"昨板:{previous.label}")
        lowfreq = by_key.get("lowfreq_facts")
        if lowfreq:
            parts.append(f"低频:{lowfreq.label}")
        if risk:
            parts.append(f"风险:{risk.label}")
        if signal:
            parts.append(f"信号:{signal.label}")
        return "；".join(parts) if parts else "当日数据不足,无法生成复盘。"

    @staticmethod
    def _next_watch(
        theme: ConclusionSection,
        limit: ConclusionSection,
        risk: ConclusionSection,
        signal: ConclusionSection,
    ) -> list[str]:
        items: list[str] = []
        break_rate = 0.0
        if limit.evidence.get("break_rate") is not None:
            break_rate = float(limit.evidence["break_rate"])
        if risk.score >= 8 or break_rate >= 0.45:
            items.append("次日先观察风险是否解除,暂不提升观察池权重。")
        elif risk.score >= 4:
            items.append("次日重点观察分歧后的修复强度和炸板风险变化。")
        else:
            items.append("次日可优先筛选低位容量趋势观察池。")

        if theme.evidence.get("top_themes"):
            names = [str(t["theme_name"]) for t in theme.evidence["top_themes"][:3]]
            items.append(f"跟踪题材:{'、'.join(names)}。")
        if signal.score > 0:
            items.append("CD 买入信号占优,但需要市场状态和题材确认。")
        elif signal.score < 0:
            items.append("CD 卖出信号占优,观察池降权。")
        return items

    async def _limit_summary(
        self,
        market: str,
        trade_date: str,
        data_gaps: list[str],
    ) -> dict[str, Any] | None:
        if self.limit_pool is None:
            data_gaps.append("真实涨停/连板/炸板池尚未接入结论层")
            return None
        try:
            summary = await self.limit_pool.summary_by_date(market, trade_date)
        except Exception as e:  # noqa: BLE001
            data_gaps.append(f"涨停池读取失败:{e}")
            return None
        if (
            int(summary.get("limit_up_count") or 0) == 0
            and int(summary.get("broken_count") or 0) == 0
            and int(summary.get("down_limit_count") or 0) == 0
        ):
            data_gaps.append("当日暂无真实涨停/炸板/跌停池数据")
            return None
        return summary

    async def _previous_limit_summary(
        self,
        market: str,
        trade_date: str,
        data_gaps: list[str],
    ) -> dict[str, Any] | None:
        if self.limit_pool is None:
            data_gaps.append("昨日涨停表现池尚未接入结论层")
            return None
        try:
            summary = await self.limit_pool.previous_performance_by_date(market, trade_date)
        except Exception as e:  # noqa: BLE001
            data_gaps.append(f"昨日涨停表现读取失败:{e}")
            return None
        if summary is None:
            data_gaps.append("当日暂无昨日涨停今日表现数据")
            return None
        if summary.get("open_edge_pct") is None:
            data_gaps.append("昨日涨停开盘溢价缺少开盘价源,暂仅统计当前表现")
        return summary

    async def _lowfreq_summary(
        self,
        market: str,
        trade_date: str,
        data_gaps: list[str],
    ) -> dict[str, Any] | None:
        if self.lowfreq is None:
            data_gaps.append("龙虎榜/公告/低频资金性质尚未接入每日复盘")
            return None
        try:
            summary = await self.lowfreq.summary_by_date(market, trade_date)
        except Exception as e:  # noqa: BLE001
            data_gaps.append(f"低频事实读取失败:{e}")
            return None
        if (
            int(summary.get("lhb_count") or 0) == 0
            and int(summary.get("notice_count") or 0) == 0
            and int(summary.get("fund_flow_count") or 0) == 0
        ):
            data_gaps.append("当日暂无龙虎榜/公告/低频资金流入库数据")
            return None
        return summary

    @staticmethod
    def _rank_themes(snapshots: list[ThemeSnapshot]) -> list[dict[str, Any]]:
        grouped: dict[str, list[ThemeSnapshot]] = {}
        for item in snapshots:
            grouped.setdefault(item.theme_code, []).append(item)

        ranked: list[dict[str, Any]] = []
        for theme_code, rows in grouped.items():
            ordered = sorted(rows, key=lambda x: x.ts)
            first = ordered[0]
            latest = ordered[-1]
            up_ratio = latest.up_ratio or 0.0
            pct_change = latest.pct_change or 0.0
            pct_change_5m = latest.pct_change_5m or 0.0
            amount_ratio = latest.amount_ratio if latest.amount_ratio is not None else 1.0
            limit_up_count = latest.limit_up_count or 0
            divergence_score = latest.divergence_score or 0.0
            momentum = up_ratio - (first.up_ratio or 0.0)
            score = (
                40 * _clamp(up_ratio, 0, 1)
                + 20 * _clamp(pct_change / 5, -1, 1)
                + 15 * _clamp(pct_change_5m / 2, -1, 1)
                + 15 * _clamp(amount_ratio - 1, 0, 2) / 2
                + 10 * _clamp(limit_up_count / 3, 0, 1)
                - 15 * _clamp(divergence_score / 100, 0, 1)
            )
            ranked.append({
                "theme_code": theme_code,
                "theme_name": latest.theme_name,
                "score": round(score, 2),
                "momentum": round(momentum, 4),
                "up_ratio": latest.up_ratio,
                "pct_change": latest.pct_change,
                "pct_change_5m": latest.pct_change_5m,
                "amount_ratio": latest.amount_ratio,
                "limit_up_count": latest.limit_up_count,
                "divergence_score": latest.divergence_score,
                "latest_ts": latest.ts.isoformat(),
            })
        return sorted(ranked, key=lambda x: float(x["score"]), reverse=True)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _benchmark_day_change(day_sections: list[ConclusionSection] | None) -> float | None:
    """从日线走势 section (market_trend) 取基准指数当日涨跌幅 (%)。无则 None。"""
    if not day_sections:
        return None
    trend = next((s for s in day_sections if s.key == "market_trend"), None)
    if trend is None:
        return None
    indices = trend.evidence.get("indices") or []
    bench = trend.evidence.get("benchmark")
    row = next((r for r in indices if r.get("symbol") == bench), None)
    if row is None and indices:
        row = indices[0]
    if row is None:
        return None
    val = row.get("day_change_pct")
    return float(val) if val is not None else None


def _conclusion_from_stored(market: str, stored: dict[str, Any]) -> DailyReviewConclusion:
    """把 daily_reviews 存档行还原成 DailyReviewConclusion (api 读路径)。"""
    sections = [
        ConclusionSection(
            key=s.get("key", ""),
            title=s.get("title", ""),
            label=s.get("label", ""),
            score=float(s.get("score", 0.0)),
            summary=s.get("summary", ""),
            evidence=s.get("evidence", {}) or {},
        )
        for s in stored.get("sections", [])
    ]
    generated_at = stored.get("generated_at") or datetime.now(timezone.utc)
    return DailyReviewConclusion(
        market=market,
        trade_date=stored["trade_date"],
        generated_at=generated_at,
        formula_version=stored.get("formula_version", FORMULA_VERSION),
        summary=stored.get("summary", ""),
        sections=sections,
        next_watch=stored.get("next_watch", []),
        data_gaps=stored.get("data_gaps", []),
    )


def _day_window(market: str, trade_date: str | None) -> tuple[date, datetime, datetime]:
    tz = ZoneInfo(MARKET_TZ.get(market, "Asia/Shanghai"))
    if trade_date:
        day = date.fromisoformat(trade_date)
    else:
        day = datetime.now(tz).date()
    start = datetime.combine(day, time(0, 0, 0), tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(day, time(23, 59, 59), tzinfo=tz).astimezone(timezone.utc)
    return day, start, end
