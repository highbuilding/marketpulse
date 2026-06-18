from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import structlog

from core.domain.models import Bar, TradeCandidate
from core.persistence.candidate_repo import CandidateRepo
from core.persistence.duckdb_repo import BarRepo
from core.persistence.fund_flow_repo import FundFlowRepo
from core.persistence.limit_pool_repo import LimitPoolRepo
from core.persistence.theme_repo import ThemeRepo

log = structlog.get_logger(__name__)

CANDIDATE_TYPE_LOW_POSITION = "low_position_capacity_trend"
FORMULA_VERSION = "low-position-capacity-v1"


@dataclass(frozen=True, slots=True)
class _SymbolScore:
    symbol: str
    name: str | None
    theme_code: str
    theme_name: str
    score: float
    decision: str
    reasons: list[str]
    risks: list[str]
    evidence: dict[str, Any]


class WatchCandidateService:
    """观察池服务。

    生成路径在 collector 进程运行, 需要 BarRepo 读日线; API 进程只调用 list。
    输出是观察候选, 不是交易指令。
    """

    def __init__(
        self,
        repo: CandidateRepo,
        *,
        themes: ThemeRepo | None = None,
        bar_repo: BarRepo | None = None,
        fund_flow: FundFlowRepo | None = None,
        limit_pool: LimitPoolRepo | None = None,
    ) -> None:
        self.repo = repo
        self.themes = themes
        self.bar_repo = bar_repo
        self.fund_flow = fund_flow
        self.limit_pool = limit_pool

    async def list_candidates(
        self,
        market: str,
        *,
        limit: int = 50,
        candidate_type: str | None = CANDIDATE_TYPE_LOW_POSITION,
    ) -> list[TradeCandidate]:
        return await self.repo.list_active(
            market, limit=limit, candidate_type=candidate_type)

    async def generate_low_position_capacity_trend(
        self,
        market: str = "ashare",
        *,
        trade_date: str | None = None,
        limit: int = 50,
    ) -> list[TradeCandidate]:
        if self.bar_repo is None or self.themes is None:
            log.warning("watch_candidates.generate_missing_deps", market=market)
            return []
        day = date.fromisoformat(trade_date) if trade_date else datetime.now().date()
        end = _utc_end(day)
        start = _utc_start(date(day.year, 1, 1))
        theme_heat = await self._theme_heat(market, day)
        limit_risk = await self._limit_risk(market, day.isoformat())

        defs = await self.themes.list_definitions(market, include_disabled=False)
        universe: list[tuple[Any, Any]] = []
        all_symbols: set[str] = set()
        for definition in defs:
            cons = await self.themes.list_static_constituents(
                market, definition.theme_code, include_disabled=False)
            universe.append((definition, cons))
            all_symbols.update(c.symbol for c in cons)
        fund_flows = await self._fund_flows(market, day, sorted(all_symbols))

        scored: dict[str, _SymbolScore] = {}
        for definition, cons in universe:
            heat = theme_heat.get(definition.theme_code, {})
            for c in cons:
                bars = self.bar_repo.fetch_history(
                    market, c.symbol, start, end, "1d", closed_only=True)
                item = _score_symbol(
                    symbol=c.symbol,
                    name=c.name,
                    theme_code=definition.theme_code,
                    theme_name=definition.theme_name,
                    bars=bars,
                    theme_heat=heat,
                    fund_main_net=(
                        fund_flows.get(c.symbol).main_net
                        if fund_flows.get(c.symbol) is not None else None
                    ),
                    limit_risk=limit_risk,
                )
                if item is None:
                    continue
                old = scored.get(c.symbol)
                if old is None or item.score > old.score:
                    scored[c.symbol] = item

        ranked = sorted(scored.values(), key=lambda x: x.score, reverse=True)[:limit]
        out: list[TradeCandidate] = []
        now = datetime.now(timezone.utc)
        for item in ranked:
            candidate = TradeCandidate(
                market=market,
                candidate_key=(
                    f"{market}:{item.symbol}:{CANDIDATE_TYPE_LOW_POSITION}:"
                    f"{day.isoformat()}"
                ),
                symbol=item.symbol,
                name=item.name,
                theme_code=item.theme_code,
                theme_name=item.theme_name,
                candidate_type=CANDIDATE_TYPE_LOW_POSITION,
                decision=item.decision,
                score=item.score,
                reasons=item.reasons,
                risks=item.risks,
                evidence={
                    **item.evidence,
                    "formula_version": FORMULA_VERSION,
                    "trade_date": day.isoformat(),
                },
                status="active",
                generated_at=now,
            )
            await self.repo.upsert(candidate)
            out.append(candidate)
        log.info("watch_candidates.generated", market=market, trade_date=day.isoformat(),
                 count=len(out))
        return out

    async def _theme_heat(self, market: str, day: date) -> dict[str, dict[str, Any]]:
        if self.themes is None:
            return {}
        start = _utc_start(day)
        end = _utc_end(day)
        rows = await self.themes.list_snapshots_window(
            market, start=start, end=end, limit=5000)
        latest: dict[str, Any] = {}
        for row in rows:
            old = latest.get(row.theme_code)
            if old is None or row.ts > old.ts:
                latest[row.theme_code] = row
        out: dict[str, dict[str, Any]] = {}
        for code, snap in latest.items():
            heat = _theme_score(
                up_ratio=snap.up_ratio,
                pct_change=snap.pct_change,
                pct_change_5m=snap.pct_change_5m,
                amount_ratio=snap.amount_ratio,
                limit_up_count=snap.limit_up_count,
                divergence_score=snap.divergence_score,
            )
            out[code] = {
                "theme_heat_score": heat,
                "up_ratio": snap.up_ratio,
                "amount_ratio": snap.amount_ratio,
                "limit_up_count": snap.limit_up_count,
                "divergence_score": snap.divergence_score,
            }
        return out

    async def _fund_flows(self, market: str, day: date, symbols: list[str]):
        if self.fund_flow is None:
            return {}
        # 只取当天资金流, 避免旧数据污染观察池。
        start = _utc_start(day)
        end = _utc_end(day)
        return await self.fund_flow.latest_symbol_flows(symbols, start=start, end=end)

    async def _limit_risk(self, market: str, trade_date: str) -> dict[str, Any]:
        if self.limit_pool is None:
            return {"available": False}
        summary = await self.limit_pool.summary_by_date(market, trade_date)
        break_rate = float(summary.get("break_rate") or 0.0)
        down_limit_count = int(summary.get("down_limit_count") or 0)
        return {
            "available": True,
            "break_rate": break_rate,
            "down_limit_count": down_limit_count,
            "risk_penalty": 15 if break_rate >= 0.4 or down_limit_count >= 30 else 0,
        }


def _score_symbol(
    *,
    symbol: str,
    name: str | None,
    theme_code: str,
    theme_name: str,
    bars: list[Bar],
    theme_heat: dict[str, Any],
    fund_main_net: float | None,
    limit_risk: dict[str, Any],
) -> _SymbolScore | None:
    if len(bars) < 30:
        return None
    closes = [float(b.close) for b in bars if b.close is not None]
    if len(closes) < 30 or closes[-1] <= 0:
        return None
    highs = [float(b.high) for b in bars if b.high is not None]
    lows = [float(b.low) for b in bars if b.low is not None]
    amounts = [float(b.amount) for b in bars[-20:] if b.amount is not None]
    cur = closes[-1]
    lo20 = min(lows[-20:] if len(lows) >= 20 else lows)
    hi20 = max(highs[-20:] if len(highs) >= 20 else highs)
    position_ratio = _position_ratio(cur, lo20, hi20)
    ma20 = sum(closes[-20:]) / 20
    ma20_fit = cur / ma20 if ma20 else None
    mom20 = (cur / closes[-20] - 1) * 100 if closes[-20] else 0.0
    avg_amount_20d = sum(amounts) / len(amounts) if amounts else None

    capacity_score = _capacity_score(avg_amount_20d)
    low_position_score = _low_position_score(position_ratio, ma20_fit)
    theme_score = _theme_confirm_score(theme_heat)
    fund_score = 5 if fund_main_net is not None and fund_main_net > 0 else 0
    risk_penalty = float(limit_risk.get("risk_penalty") or 0)
    trend_score = 8 if mom20 > 0 else 0
    score = round(
        capacity_score + low_position_score + theme_score + fund_score
        + trend_score - risk_penalty,
        2,
    )
    if score >= 70 and risk_penalty == 0:
        decision = "observe"
    elif risk_penalty > 0:
        decision = "risk_hold"
    elif score >= 55:
        decision = "wait_confirm"
    else:
        decision = "exclude"
    if decision == "exclude":
        return None

    reasons: list[str] = []
    risks: list[str] = []
    if capacity_score >= 20:
        reasons.append("20日均额满足容量趋势观察")
    if low_position_score >= 20:
        reasons.append("20日区间位置偏低且站近20日均线")
    if theme_score >= 15:
        reasons.append("所属题材热度具备确认")
    if trend_score > 0:
        reasons.append("近20日动量转正")
    if fund_main_net is None:
        risks.append("当日个股低频资金流缺失")
    elif fund_main_net <= 0:
        risks.append("当日主力净流入未确认")
    if risk_penalty > 0:
        risks.append("涨停/炸板结构存在退潮风险")
    return _SymbolScore(
        symbol=symbol,
        name=name,
        theme_code=theme_code,
        theme_name=theme_name,
        score=score,
        decision=decision,
        reasons=reasons,
        risks=risks,
        evidence={
            "position_ratio_20d": position_ratio,
            "ma20_fit": round(ma20_fit, 4) if ma20_fit is not None else None,
            "momentum_20d_pct": round(mom20, 2),
            "avg_amount_20d": avg_amount_20d,
            "capacity_score": capacity_score,
            "low_position_score": low_position_score,
            "theme_confirm_score": theme_score,
            "fund_score": fund_score,
            "limit_risk": limit_risk,
            "formula": (
                "candidate_score=capacity_score+low_position_score+"
                "theme_confirm_score+trend_score+fund_score-risk_penalty"
            ),
        },
    )


def _capacity_score(avg_amount: float | None) -> float:
    if avg_amount is None:
        return 0.0
    if 500_000_000 <= avg_amount <= 5_000_000_000:
        return 25.0
    if 200_000_000 <= avg_amount < 500_000_000:
        return 15.0
    if avg_amount > 5_000_000_000:
        return 12.0
    return 0.0


def _low_position_score(position_ratio: float | None, ma20_fit: float | None) -> float:
    score = 0.0
    if position_ratio is not None:
        if 0.10 <= position_ratio <= 0.45:
            score += 22
        elif position_ratio < 0.60:
            score += 12
    if ma20_fit is not None:
        if 1.00 <= ma20_fit <= 1.08:
            score += 18
        elif 0.97 <= ma20_fit < 1.00:
            score += 8
    return score


def _theme_confirm_score(theme_heat: dict[str, Any]) -> float:
    heat = float(theme_heat.get("theme_heat_score") or 0.0)
    amount_ratio = theme_heat.get("amount_ratio")
    score = 0.0
    if heat >= 70:
        score += 25
    elif heat >= 55:
        score += 15
    if amount_ratio is not None and float(amount_ratio) >= 1.2:
        score += 8
    return score


def _theme_score(
    *,
    up_ratio: float | None,
    pct_change: float | None,
    pct_change_5m: float | None,
    amount_ratio: float | None,
    limit_up_count: int | None,
    divergence_score: float | None,
) -> float:
    return (
        40 * _clamp(up_ratio or 0.0, 0, 1)
        + 20 * _clamp((pct_change or 0.0) / 5, -1, 1)
        + 15 * _clamp((pct_change_5m or 0.0) / 2, -1, 1)
        + 15 * _clamp((amount_ratio or 1.0) - 1, 0, 2) / 2
        + 10 * _clamp((limit_up_count or 0) / 3, 0, 1)
        - 15 * _clamp((divergence_score or 0.0) / 100, 0, 1)
    )


def _position_ratio(cur: float, low: float, high: float) -> float | None:
    span = high - low
    if span <= 0:
        return None
    return max(0.0, min(1.0, (cur - low) / span))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _utc_start(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=timezone.utc) - timedelta(days=1)


def _utc_end(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59), tzinfo=timezone.utc)
