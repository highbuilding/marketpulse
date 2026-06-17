"""每日复盘日线分析引擎 (collector 进程跑, 持 BarRepo RW)。

只读日线 (全年都在库) + 申万行业指数 (SQLite) + 题材成分, 产出三个结论 section:
- market_trend   大盘走势解读 (年内涨跌/位置/最大回撤/走势分段)
- sector_position 板块位置榜 (哪些低位/哪些核心, 申万31行业 + 15题材)
- sector_leaders  核心板块个股分层 (龙头/中军/杂毛, 日线量价反推)

雷区6: 日线分析必须在 collector 进程 (持 DuckDB RW 连接) 运行, 结果落 SQLite
daily_reviews; api 进程只读 daily_reviews, 不直连 DuckDB。
全年每个交易日都可算 (传哪天算哪天), 1月~6月中无消息流水时这三层照常输出。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import structlog

from core.persistence.duckdb_repo import BarRepo
from core.persistence.sw_industry_repo import SwIndustryRepo
from core.persistence.theme_repo import ThemeRepo

log = structlog.get_logger(__name__)

TREND_FORMULA_VERSION = "daily-trend-v1"

# 大盘解读用的核心指数 (DuckDB symbol -> 展示名)
_INDEX_NAMES = {
    "000001.SH": "上证指数",
    "000300.SH": "沪深300",
    "399006.SZ": "创业板指",
    "000852.SH": "中证1000",
}
# 低位阈值: 年内区间位置 < 0.3 视为低位
_LOW_POSITION = 0.3
# 核心阈值: 年内位置 >= 0.6 且近20日动量 > 0
_CORE_POSITION = 0.6


@dataclass(frozen=True, slots=True)
class TrendSection:
    key: str
    title: str
    label: str
    score: float
    summary: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "label": self.label,
            "score": self.score,
            "summary": self.summary,
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class DailyTrendResult:
    """日线层三 section + 数据缺口。供 MarketConclusionService 合并。"""
    sections: list[TrendSection] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    headline: str = ""  # 一句话走势, 进 daily summary


class DailyReviewBuilder:
    def __init__(
        self,
        bar_repo: BarRepo,
        sw_industry: SwIndustryRepo,
        themes: ThemeRepo,
        *,
        market: str = "ashare",
    ) -> None:
        self.bar_repo = bar_repo
        self.sw_industry = sw_industry
        self.themes = themes
        self.market = market

    async def build(self, trade_date: str) -> DailyTrendResult:
        """对交易日 trade_date 生成日线三 section (年内对照基准)。"""
        result = DailyTrendResult()
        day = date.fromisoformat(trade_date)
        ytd_start = date(day.year, 1, 1)

        trend = self._market_trend_section(day, ytd_start, result)
        sectors, theme_ranked = await self._sector_position_section(day, ytd_start, result)
        leaders = await self._sector_leaders_section(day, ytd_start, theme_ranked, result)

        result.sections = [s for s in (trend, sectors, leaders) if s is not None]
        return result

    # ---- ① 大盘走势 ----

    def _market_trend_section(
        self, day: date, ytd_start: date, result: DailyTrendResult,
    ) -> TrendSection | None:
        end = _utc_end(day)
        start = _utc_start(ytd_start)
        index_rows: list[dict[str, Any]] = []
        for symbol, name in _INDEX_NAMES.items():
            bars = self.bar_repo.fetch_history(
                self.market, symbol, start, end, "1d", closed_only=True)
            closes = [(b.ts, float(b.close)) for b in bars if b.close is not None]
            if len(closes) < 2:
                continue
            ytd_first = closes[0][1]
            cur = closes[-1][1]
            prev = closes[-2][1]
            highs = [float(b.high) for b in bars if b.high is not None]
            lows = [float(b.low) for b in bars if b.low is not None]
            year_high = max(highs) if highs else cur
            year_low = min(lows) if lows else cur
            day_change = (cur / prev - 1) * 100 if prev else 0.0
            ytd_change = (cur / ytd_first - 1) * 100 if ytd_first else 0.0
            pos = _position_ratio(cur, year_low, year_high)
            max_dd = _max_drawdown([c for _, c in closes])
            index_rows.append({
                "symbol": symbol,
                "name": name,
                "close": round(cur, 2),
                "day_change_pct": round(day_change, 2),
                "ytd_change_pct": round(ytd_change, 2),
                "year_high": round(year_high, 2),
                "year_low": round(year_low, 2),
                "position_ratio": round(pos, 3) if pos is not None else None,
                "from_high_pct": round((cur / year_high - 1) * 100, 2) if year_high else None,
                "max_drawdown_pct": round(max_dd * 100, 2),
                "segments": _trend_segments([c for _, c in closes]),
            })
        if not index_rows:
            result.data_gaps.append("大盘指数日线不足, 无法生成走势解读")
            return None

        bench = next((r for r in index_rows if r["symbol"] == "000300.SH"), index_rows[0])
        ytd = bench["ytd_change_pct"]
        pos = bench["position_ratio"]
        if ytd >= 8 and (pos or 0) >= 0.7:
            label = "年内强势高位"
        elif ytd >= 0:
            label = "年内震荡偏强" if (pos or 0) >= 0.5 else "年内回升中继"
        elif ytd >= -10:
            label = "年内震荡偏弱"
        else:
            label = "年内弱势低位"
        headline = (
            f"{bench['name']} 今日{_signed(bench['day_change_pct'])}% 收{bench['close']};"
            f" 年内{_signed(ytd)}%, 距年内高 {bench['from_high_pct']}%,"
            f" 年内最大回撤 {bench['max_drawdown_pct']}%。"
        )
        result.headline = headline
        return TrendSection(
            key="market_trend",
            title="大盘走势(年内对照)",
            label=label,
            score=round(ytd, 2),
            summary=headline,
            evidence={
                "indices": index_rows,
                "benchmark": bench["symbol"],
                "ytd_start": ytd_start.isoformat(),
                "formula": "ytd_change=close/年初首收-1; position_ratio=(close-年内低)/(年内高-年内低); max_drawdown=年内收盘序列峰值回撤",
            },
        )

    # ---- ② 板块位置榜 ----

    async def _sector_position_section(
        self, day: date, ytd_start: date, result: DailyTrendResult,
    ) -> tuple[TrendSection | None, list[dict[str, Any]]]:
        sectors: list[dict[str, Any]] = []
        # 申万一级行业 (用行业指数本身的 OHLCV, 不依赖个股堆叠)
        infos = await self.sw_industry.list_info()
        for info in infos:
            bars = await self.sw_industry.list_history(
                info.industry_code, start=ytd_start.isoformat(), end=day.isoformat())
            row = _sector_row_from_bars(
                key=info.industry_code, name=info.industry_name, kind="sw_industry",
                closes=[b.close for b in bars if b.close is not None],
                highs=[b.high for b in bars if b.high is not None],
                lows=[b.low for b in bars if b.low is not None],
            )
            if row:
                sectors.append(row)
        if not infos:
            result.data_gaps.append("申万行业指数尚未采集, 板块位置榜缺申万维度")

        # 15 个题材 (用成分股等权平均涨幅近似题材指数)
        theme_ranked = await self._theme_position_rows(day, ytd_start)
        sectors.extend(theme_ranked)

        if not sectors:
            result.data_gaps.append("板块数据不足, 无法生成板块位置榜")
            return None, theme_ranked

        low = sorted(
            [s for s in sectors if s["position_ratio"] is not None and s["position_ratio"] < _LOW_POSITION],
            key=lambda s: s["position_ratio"],
        )
        core = sorted(
            [s for s in sectors
             if (s["position_ratio"] or 0) >= _CORE_POSITION and (s["momentum_20d_pct"] or 0) > 0],
            key=lambda s: (s["momentum_20d_pct"] or 0), reverse=True,
        )
        core_names = "、".join(s["name"] for s in core[:5]) or "暂无"
        low_names = "、".join(s["name"] for s in low[:5]) or "暂无"
        label = f"核心 {len(core)} / 低位 {len(low)}"
        summary = f"当前核心板块:{core_names};低位板块:{low_names}。"
        return (
            TrendSection(
                key="sector_position",
                title="板块位置榜(低位/核心)",
                label=label,
                score=float(len(core)),
                summary=summary,
                evidence={
                    "core_sectors": core[:10],
                    "low_sectors": low[:10],
                    "all_sectors": sorted(
                        sectors, key=lambda s: (s["position_ratio"] or 0), reverse=True),
                    "low_position_threshold": _LOW_POSITION,
                    "core_position_threshold": _CORE_POSITION,
                    "formula": "position_ratio年内区间位置; momentum_20d=近20日涨幅; 低位<0.3; 核心>=0.6且动量>0",
                },
            ),
            theme_ranked,
        )

    async def _theme_position_rows(
        self, day: date, ytd_start: date,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        defs = await self.themes.list_definitions(self.market, include_disabled=False)
        end = _utc_end(day)
        start = _utc_start(ytd_start)
        for d in defs:
            cons = await self.themes.list_static_constituents(
                self.market, d.theme_code, include_disabled=False)
            # 等权平均个股归一化收盘序列 → 题材合成指数
            series = self._equal_weight_series(
                [c.symbol for c in cons], start, end)
            if len(series) < 20:
                continue
            row = _sector_row_from_bars(
                key=d.theme_code, name=d.theme_name, kind="theme",
                closes=series, highs=series, lows=series,
            )
            if row:
                row["member_count"] = len(cons)
                rows.append(row)
        return rows

    def _equal_weight_series(
        self, symbols: list[str], start: datetime, end: datetime,
    ) -> list[float]:
        """成分股等权归一化合成指数 (各股首日=1.0, 等权平均)。"""
        per_symbol: list[list[float]] = []
        for sym in symbols:
            bars = self.bar_repo.fetch_history(
                self.market, sym, start, end, "1d", closed_only=True)
            closes = [float(b.close) for b in bars if b.close is not None]
            if len(closes) >= 20 and closes[0] > 0:
                per_symbol.append([c / closes[0] for c in closes])
        if not per_symbol:
            return []
        # 以最短长度对齐 (个股上市时点/停牌不同, 取尾部对齐)
        n = min(len(s) for s in per_symbol)
        aligned = [s[-n:] for s in per_symbol]
        return [sum(col) / len(col) for col in zip(*aligned)]

    # ---- ③ 核心板块个股分层 (龙头/中军/杂毛) ----

    async def _sector_leaders_section(
        self, day: date, ytd_start: date,
        theme_ranked: list[dict[str, Any]],
        result: DailyTrendResult,
    ) -> TrendSection | None:
        # 取年内位置/动量靠前的题材作"核心板块", 在其内部分层个股
        cores = sorted(
            [t for t in theme_ranked if (t["momentum_20d_pct"] or 0) > 0],
            key=lambda t: (t["momentum_20d_pct"] or 0), reverse=True,
        )[:3]
        if not cores:
            # 没有明显走强题材时, 取动量最高的前 2 个兜底
            cores = sorted(
                theme_ranked, key=lambda t: (t["momentum_20d_pct"] or 0), reverse=True)[:2]
        if not cores:
            result.data_gaps.append("无核心题材, 跳过个股分层")
            return None

        end = _utc_end(day)
        start = _utc_start(ytd_start)
        groups: list[dict[str, Any]] = []
        for theme in cores:
            cons = await self.themes.list_static_constituents(
                self.market, theme["key"], include_disabled=False)
            scored = self._score_constituents(
                [(c.symbol, c.name) for c in cons], start, end)
            if not scored:
                continue
            tiers = _split_tiers(scored)
            groups.append({
                "theme_code": theme["key"],
                "theme_name": theme["name"],
                "leaders": tiers["leaders"],
                "mid": tiers["mid"],
                "laggards": tiers["laggards"],
            })
        if not groups:
            result.data_gaps.append("核心题材成分日线不足, 无法分层")
            return None

        parts = []
        for g in groups:
            lead = "·".join(x["name"] or x["symbol"] for x in g["leaders"][:2]) or "—"
            parts.append(f"{g['theme_name']}:龙头 {lead}")
        summary = ";".join(parts) + "。"
        return TrendSection(
            key="sector_leaders",
            title="核心板块个股分层(龙头/中军/杂毛)",
            label=f"覆盖 {len(groups)} 个核心板块",
            score=float(len(groups)),
            summary=summary,
            evidence={
                "groups": groups,
                "formula": "综合分=年内涨幅+近20日动量+成交额占板块比+距年内高位; Top→龙头, 中段→中军, 尾部滞涨/缩量→杂毛",
            },
        )

    def _score_constituents(
        self, symbols: list[tuple[str, str | None]], start: datetime, end: datetime,
    ) -> list[dict[str, Any]]:
        raw: list[dict[str, Any]] = []
        for sym, name in symbols:
            bars = self.bar_repo.fetch_history(
                self.market, sym, start, end, "1d", closed_only=True)
            closes = [float(b.close) for b in bars if b.close is not None]
            if len(closes) < 20:
                continue
            amounts = [float(b.amount) for b in bars[-20:] if b.amount is not None]
            highs = [float(b.high) for b in bars if b.high is not None]
            cur = closes[-1]
            ytd_change = (cur / closes[0] - 1) * 100 if closes[0] else 0.0
            mom_20 = (cur / closes[-20] - 1) * 100 if closes[-20] else 0.0
            year_high = max(highs) if highs else cur
            from_high = (cur / year_high - 1) * 100 if year_high else 0.0
            avg_amount = sum(amounts) / len(amounts) if amounts else 0.0
            raw.append({
                "symbol": sym,
                "name": name,
                "ytd_change_pct": round(ytd_change, 2),
                "momentum_20d_pct": round(mom_20, 2),
                "from_high_pct": round(from_high, 2),
                "avg_amount_20d": avg_amount,
            })
        if not raw:
            return []
        total_amount = sum(r["avg_amount_20d"] for r in raw) or 1.0
        # 归一化各维度到可比尺度后加权: 年内地位(龙头主导) + 资金认可 + 近期动量。
        ytd_vals = [r["ytd_change_pct"] for r in raw]
        ytd_max = max(abs(v) for v in ytd_vals) or 1.0
        for r in raw:
            r["amount_share"] = round(r["avg_amount_20d"] / total_amount, 4)
            # 综合分(满分~100): 年内涨幅 50% (龙头看全年累计地位, 非短期),
            # 成交额占比 30% (资金认可/中军以上才有量), 近20日动量 20% (近期强弱微调)。
            r["score"] = round(
                50 * _clamp(r["ytd_change_pct"] / ytd_max, -1, 1)
                + 30 * r["amount_share"]
                + 20 * _clamp(r["momentum_20d_pct"] / 30, -1, 1),
                2,
            )
        return sorted(raw, key=lambda r: r["score"], reverse=True)


def _split_tiers(scored: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按综合分把成分分龙头/中军/杂毛三层。"""
    n = len(scored)
    if n <= 3:
        return {"leaders": scored[:1], "mid": scored[1:], "laggards": []}
    lead_n = max(1, round(n * 0.2))
    lag_n = max(1, round(n * 0.3))
    return {
        "leaders": scored[:lead_n],
        "mid": scored[lead_n:n - lag_n],
        "laggards": scored[n - lag_n:],
    }


def _sector_row_from_bars(
    *, key: str, name: str, kind: str,
    closes: list[float], highs: list[float], lows: list[float],
) -> dict[str, Any] | None:
    closes = [c for c in closes if c is not None]
    if len(closes) < 2:
        return None
    cur = closes[-1]
    year_high = max(h for h in highs if h is not None) if highs else max(closes)
    year_low = min(l for l in lows if l is not None) if lows else min(closes)
    ytd_change = (cur / closes[0] - 1) * 100 if closes[0] else 0.0
    day_change = (cur / closes[-2] - 1) * 100 if closes[-2] else 0.0
    mom_20 = (cur / closes[-20] - 1) * 100 if len(closes) >= 20 and closes[-20] else None
    pos = _position_ratio(cur, year_low, year_high)
    return {
        "key": key,
        "name": name,
        "kind": kind,
        "day_change_pct": round(day_change, 2),
        "ytd_change_pct": round(ytd_change, 2),
        "momentum_20d_pct": round(mom_20, 2) if mom_20 is not None else None,
        "position_ratio": round(pos, 3) if pos is not None else None,
        "from_high_pct": round((cur / year_high - 1) * 100, 2) if year_high else None,
    }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _position_ratio(cur: float, low: float, high: float) -> float | None:
    span = high - low
    if span <= 0:
        return None
    return max(0.0, min(1.0, (cur - low) / span))


def _max_drawdown(closes: list[float]) -> float:
    """收盘序列的最大回撤 (负值, 如 -0.11 表示 -11%)。"""
    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        if c > peak:
            peak = c
        if peak > 0:
            dd = c / peak - 1
            if dd < max_dd:
                max_dd = dd
    return max_dd


def _trend_segments(closes: list[float], *, swing_pct: float = 8.0) -> list[dict[str, Any]]:
    """ZigZag 摆动点检测: 把序列切成 上涨/下跌 段 (反向超 swing_pct 确认枢轴)。

    从首个点起跟踪极值; 当价格自极值反向回撤超过 swing_pct, 在极值处确认一个枢轴,
    记录上一枢轴→该枢轴的段, 再以新极值继续。最后收尾未确认段。
    """
    if len(closes) < 3:
        return []
    segments: list[dict[str, Any]] = []
    pivot_idx = 0          # 上一枢轴位置
    hi_idx = 0             # 自上枢轴以来的最高点
    lo_idx = 0             # 自上枢轴以来的最低点
    direction = 0          # 当前段方向: 1 上, -1 下, 0 未定
    for i in range(1, len(closes)):
        c = closes[i]
        if c >= closes[hi_idx]:
            hi_idx = i
        if c <= closes[lo_idx]:
            lo_idx = i
        if direction >= 0:
            # 看是否自最高点回撤超阈值 → 确认上涨段见顶
            retrace = (c / closes[hi_idx] - 1) * 100 if closes[hi_idx] else 0.0
            if retrace <= -swing_pct and hi_idx > pivot_idx:
                segments.append(_seg(pivot_idx, hi_idx, closes, "上涨"))
                pivot_idx, lo_idx, direction = hi_idx, i, -1
                continue
        if direction <= 0:
            # 看是否自最低点反弹超阈值 → 确认下跌段见底
            rebound = (c / closes[lo_idx] - 1) * 100 if closes[lo_idx] else 0.0
            if rebound >= swing_pct and lo_idx > pivot_idx:
                segments.append(_seg(pivot_idx, lo_idx, closes, "下跌"))
                pivot_idx, hi_idx, direction = lo_idx, i, 1
    # 收尾段: 从最后枢轴到末点
    last_kind = "上涨" if closes[-1] >= closes[pivot_idx] else "下跌"
    if len(closes) - 1 > pivot_idx:
        segments.append(_seg(pivot_idx, len(closes) - 1, closes, last_kind))
    return segments


def _seg(i0: int, i1: int, closes: list[float], kind: str) -> dict[str, Any]:
    c0, c1 = closes[i0], closes[i1]
    return {
        "kind": kind,
        "from_idx": i0,
        "to_idx": i1,
        "change_pct": round((c1 / c0 - 1) * 100, 2) if c0 else 0.0,
        "bars": i1 - i0 + 1,
    }


def _signed(value: float) -> str:
    return f"+{value}" if value >= 0 else f"{value}"


def _utc_start(d: date) -> datetime:
    return datetime.combine(d, time(0, 0, 0), tzinfo=timezone.utc) - timedelta(days=1)


def _utc_end(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59), tzinfo=timezone.utc)
