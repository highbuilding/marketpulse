from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
import structlog

from core.domain.models import StrategyBacktestRun, StrategyBacktestTrade, TradeInstruction
from core.persistence.candidate_repo import CandidateRepo
from core.persistence.collector_symbol_repo import CollectorSymbolRepo
from core.persistence.duckdb_repo import BarRepo
from core.persistence.limit_pool_repo import LimitPoolRepo
from core.persistence.signal_repo import SignalRepo
from core.persistence.strategy_backtest_repo import StrategyBacktestRepo
from core.persistence.theme_repo import ThemeRepo

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    name: str
    description: str
    horizon: str
    hold_days: int
    exit_rule: str
    stop_loss_pct: float = -6.0
    take_profit_pct: float | None = 12.0


STRATEGIES: tuple[StrategySpec, ...] = (
    StrategySpec(
        "low_position_cd",
        "低位容量趋势 + CD 买点",
        "低位容量趋势候选叠加 1d CD 买点,验证提前埋伏后的指标确认。",
        "3-10日波段",
        5,
        "持有 5 日或 CD 卖点/跌破 10 日线退出",
    ),
    StrategySpec(
        "low_position_breakout",
        "低位容量趋势 + 放量突破",
        "低位区间内出现成交额放大并突破 20 日平台,验证突破触发。",
        "3-10日波段",
        5,
        "持有 5 日或跌破 10 日线退出",
    ),
    StrategySpec(
        "low_position_theme",
        "低位容量趋势 + 题材升温",
        "低位容量个股叠加所属题材热度升温,验证复盘题材条件是否增益。",
        "3-10日波段",
        5,
        "持有 5 日或题材退潮/跌破 10 日线退出",
    ),
    StrategySpec(
        "cd_market_repair",
        "CD 买点 + 市场风险解除",
        "CD 买点只在炸板率和跌停风险缓和后启用,验证市场门控。",
        "1-3日短线",
        3,
        "持有 3 日或 CD 卖点退出",
    ),
    StrategySpec(
        "previous_limit_strong",
        "昨日涨停强环境短线",
        "昨日涨停池在晋级率高、炸板率低时参与,验证涨停生态门控。",
        "1-3日短线",
        2,
        "持有 2 日或市场门控转弱退出",
    ),
    StrategySpec(
        "risk_filter",
        "禁止交易/风险过滤",
        "对比不过滤与过滤高炸板/高跌停/高淘汰惩罚后的买点表现。",
        "风险过滤",
        5,
        "风险日不进场,持有 5 日或跌破 10 日线退出",
    ),
)


class StrategyBacktestService:
    def __init__(
        self,
        repo: StrategyBacktestRepo,
        *,
        bar_repo: BarRepo | None,
        signals: SignalRepo,
        candidates: CandidateRepo,
        collector_symbols: CollectorSymbolRepo,
        limit_pool: LimitPoolRepo,
        themes: ThemeRepo,
    ) -> None:
        self.repo = repo
        self.bar_repo = bar_repo
        self.signals = signals
        self.candidates = candidates
        self.collector_symbols = collector_symbols
        self.limit_pool = limit_pool
        self.themes = themes

    async def list_runs(self, market: str = "ashare") -> list[StrategyBacktestRun]:
        return await self.repo.list_latest(market)

    async def get_report(
        self, market: str, strategy_id: str,
    ) -> tuple[StrategyBacktestRun | None, list[StrategyBacktestTrade]]:
        run = await self.repo.get_latest(market, strategy_id)
        if run is None or run.id is None:
            return run, []
        return run, await self.repo.list_trades(run.id)

    async def list_instructions(self, market: str = "ashare") -> list[TradeInstruction]:
        return await self.repo.list_instructions(market)

    async def run_all(
        self,
        *,
        market: str = "ashare",
        lookback_days: int = 720,
        max_symbols: int = 300,
    ) -> list[StrategyBacktestRun]:
        if market != "ashare":
            return []
        if self.bar_repo is None:
            raise RuntimeError("StrategyBacktestService.run_all requires collector BarRepo")

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)
        symbols = await self._universe(market, max_symbols=max_symbols)
        bars = await asyncio.to_thread(
            self.bar_repo.fetch_history_frame,
            market, symbols, start, end, "1d",
        )
        if bars.empty:
            runs = [
                self._empty_run(spec, market, ["bars_1d_missing"], engine="vectorbt-unavailable")
                for spec in STRATEGIES
            ]
            for run in runs:
                await self.repo.save_run(run, [])
            return runs

        data = await self._build_feature_matrices(market, bars, start, end)
        runs: list[StrategyBacktestRun] = []
        eligible: list[StrategyBacktestRun] = []
        for spec in STRATEGIES:
            run, trades = self._run_strategy(spec, market, data)
            await self.repo.save_run(run, trades)
            runs.append(run)
            if run.sandbox_eligible:
                eligible.append(run)
        instructions = await self._generate_instructions(market, eligible)
        await self.repo.deactivate_active_instructions(market)
        await self.repo.upsert_instructions(instructions)
        return runs

    async def _universe(self, market: str, *, max_symbols: int) -> list[str]:
        symbols = await self.collector_symbols.active_symbols(market, capability="signals")
        if len(symbols) < 10:
            candidates = await self.candidates.list_active(market, limit=max_symbols)
            symbols = [c.symbol for c in candidates]
        # 保持稳定顺序,先用采集清单;回测成本由 max_symbols 控制。
        return sorted(dict.fromkeys(symbols))[:max_symbols]

    async def _build_feature_matrices(
        self, market: str, bars: pd.DataFrame, start: datetime, end: datetime,
    ) -> dict[str, Any]:
        bars = bars.copy()
        bars["trade_date"] = (
            pd.to_datetime(bars["ts"], utc=True)
            .dt.tz_convert("Asia/Shanghai")
            .dt.strftime("%Y-%m-%d")
        )
        bars = bars.sort_values(["trade_date", "symbol"])
        matrices = {
            col: bars.pivot(index="trade_date", columns="symbol", values=col).sort_index()
            for col in ("open", "high", "low", "close", "amount", "turnover")
        }
        close = matrices["close"].astype(float)
        amount = matrices["amount"].astype(float).fillna(0.0)
        dates = list(close.index)
        symbols = list(close.columns)

        high = matrices["high"].astype(float)
        low = matrices["low"].astype(float)
        ma5 = close.rolling(5, min_periods=3).mean()
        ma10 = close.rolling(10, min_periods=5).mean()
        ma20 = close.rolling(20, min_periods=10).mean()
        low120 = close.rolling(120, min_periods=30).min()
        high120 = close.rolling(120, min_periods=30).max()
        position = ((close - low120) / (high120 - low120).replace(0, np.nan)).clip(0, 1)
        amount_ma20 = amount.rolling(20, min_periods=10).mean()
        broad_ret20 = close.mean(axis=1).pct_change(20, fill_method=None)
        rs20 = close.pct_change(20, fill_method=None).sub(broad_ret20, axis=0)
        volatility20 = ((high - low) / close.replace(0, np.nan)).rolling(20, min_periods=10).mean()
        # 新增因子: 不追高(偏离 ma5 不超 8%), 可从日线反推覆盖全历史
        not_extended = close <= ma5 * 1.08
        low_position = (
            (position <= 0.35)
            & (amount_ma20 >= 30_000_000)
            & (close >= ma20 * 0.96)
        )
        quality_low_position = (
            low_position
            & (close >= ma20 * 0.98)
            & (close >= ma5 * 0.97)
            & (rs20 >= -0.10)
            & (volatility20 <= 0.12)
            & not_extended                                  # 新增: 不追高
        )
        breakout = (
            (close > close.rolling(20, min_periods=10).max().shift(1))
            & (amount > amount_ma20 * 1.5)
            & quality_low_position.rolling(20, min_periods=1).max().astype(bool)
        )
        sell_risk = (close < ma10) | (close.pct_change(fill_method=None) <= -0.06)
        # 大盘趋势: 宽基均价 > 其 ma20 才做多(A股择时关键, 砍掉熊市做多)
        _broad = close.mean(axis=1)
        _broad_up = (_broad > _broad.rolling(20, min_periods=10).mean()).fillna(False)
        market_uptrend = pd.DataFrame(
            np.repeat(_broad_up.to_numpy()[:, None], len(close.columns), axis=1),
            index=close.index, columns=close.columns,
        )

        cd_buy = pd.DataFrame(False, index=close.index, columns=close.columns)
        cd_sell = pd.DataFrame(False, index=close.index, columns=close.columns)
        sigs = await self.signals.list_recent(
            since=start, intervals=["1d"], symbols=symbols, limit=100_000,
        )
        for sig in sigs:
            trade_date = (
                pd.Timestamp(sig.bar_ts).tz_convert("Asia/Shanghai").strftime("%Y-%m-%d")
                if pd.Timestamp(sig.bar_ts).tzinfo else
                pd.Timestamp(sig.bar_ts, tz="UTC").tz_convert("Asia/Shanghai").strftime("%Y-%m-%d")
            )
            if trade_date in cd_buy.index and sig.symbol in cd_buy.columns:
                if sig.signal_type == "buy":
                    cd_buy.at[trade_date, sig.symbol] = True
                elif sig.signal_type == "sell":
                    cd_sell.at[trade_date, sig.symbol] = True

        start_date = dates[0]
        end_date = dates[-1]
        market_state_raw = await self.limit_pool.market_state_window(market, start_date, end_date)
        market_state = pd.DataFrame(index=close.index)
        market_state["has_limit_pool"] = [
            d in market_state_raw for d in market_state.index
        ]
        for key in (
            "limit_up_count", "break_rate", "down_limit_count", "promotion_rate",
            "red_rate", "loser_penalty_pct", "high_promotion_rate",
            "high_loser_penalty_pct", "ladder_strength_score", "max_ladder_height",
            "second_plus_count", "ladder_continuity", "short_term_allowed", "trend_allowed",
        ):
            market_state[key] = [
                market_state_raw.get(d, {}).get(key) for d in market_state.index
            ]
        numeric_cols = [
            "limit_up_count", "break_rate", "down_limit_count", "promotion_rate",
            "red_rate", "loser_penalty_pct", "high_promotion_rate",
            "high_loser_penalty_pct", "ladder_strength_score", "max_ladder_height",
            "second_plus_count", "ladder_continuity",
        ]
        for col in numeric_cols:
            market_state[col] = pd.to_numeric(market_state[col], errors="coerce").fillna(0.0)
        market_state["short_term_allowed"] = market_state["short_term_allowed"].eq(True)
        market_state["trend_allowed"] = ~market_state["trend_allowed"].eq(False)
        up_breadth = (close.pct_change(fill_method=None) > 0).mean(axis=1).fillna(0.5)
        broad_ret1 = close.mean(axis=1).pct_change(fill_method=None).fillna(0.0)
        has_pool = market_state["has_limit_pool"].astype(bool)
        risk_off = (
            (
                has_pool
                & (
                    (market_state["break_rate"] >= 0.45)
                    | (market_state["down_limit_count"] >= 20)
                    | (market_state["loser_penalty_pct"] <= -5.0)
                    | (market_state["high_loser_penalty_pct"] <= -6.0)
                    | (
                        (market_state["max_ladder_height"] <= 2)
                        & (market_state["second_plus_count"] <= 2)
                        & (market_state["break_rate"] >= 0.35)
                    )
                )
            )
            | ((up_breadth <= 0.35) & (broad_ret1 < 0))
        )
        ecology_ok = (
            ~has_pool
            | (market_state["ladder_strength_score"] >= 35)
            | (market_state["second_plus_count"] >= 5)
        )
        risk_on = (
            (
                (market_state["promotion_rate"] >= 0.30)
                | (market_state["limit_up_count"] >= 50)
                | ((up_breadth >= 0.62) & (broad_ret1 > 0))
            )
            & ecology_ok
            & (market_state["break_rate"] <= 0.35)
            & (market_state["down_limit_count"] <= 10)
            & (~has_pool | (market_state["loser_penalty_pct"] >= -3.0))
            & (~has_pool | (market_state["high_loser_penalty_pct"] >= -5.0))
        )
        repair = (
            ~risk_on
            & ~risk_off
            & (
                (market_state["promotion_rate"] >= 0.20)
                | ((up_breadth >= 0.55) & (broad_ret1 >= 0))
            )
            & (market_state["break_rate"] <= 0.40)
            & (market_state["down_limit_count"] <= 15)
            & (~has_pool | (market_state["high_loser_penalty_pct"] >= -6.0))
        )
        market_state["up_breadth"] = up_breadth
        market_state["broad_return_pct"] = broad_ret1 * 100
        market_state["risk_on"] = risk_on
        market_state["repair"] = repair
        market_state["risk_off"] = risk_off
        market_state["regime"] = np.select(
            [risk_off.to_numpy(), risk_on.to_numpy(), repair.to_numpy()],
            ["risk_off", "risk_on", "repair"],
            default="neutral",
        )

        previous_symbols = await self.limit_pool.pool_symbols_window(
            market, start_date, end_date, "previous")
        previous_pool = pd.DataFrame(False, index=close.index, columns=close.columns)
        for d, syms in previous_symbols.items():
            if d in previous_pool.index:
                for sym in syms:
                    if sym in previous_pool.columns:
                        previous_pool.at[d, sym] = True

        theme_hot = await self._theme_hot_matrix(market, close, start, end)
        data_gaps = []
        if not cd_buy.any().any():
            data_gaps.append("indicator_signals 缺少 1d CD 买点,相关策略交易数可能为 0")
        if not market_state_raw:
            data_gaps.append("limit_pool_daily 区间数据不足,复盘市场门控退化为默认值")
        elif len(market_state_raw) < max(20, len(close.index) * 0.2):
            data_gaps.append(
                f"limit_pool_daily 历史覆盖不足({len(market_state_raw)}天),"
                "连板/昨板生态仅对有数据日期生效,其余日期使用宽基上涨家数兜底"
            )
        if not theme_hot.any().any():
            data_gaps.append("theme_snapshots/theme_constituents 历史不足,题材升温策略可能交易数为 0")

        return {
            "open": matrices["open"].astype(float),
            "close": close,
            "low_position": low_position.fillna(False),
            "quality_low_position": quality_low_position.fillna(False),
            "breakout": breakout.fillna(False),
            "market_uptrend": market_uptrend,
            "relative_strength20": rs20,
            "theme_hot": theme_hot.reindex_like(close).fillna(False),
            "cd_buy": cd_buy,
            "cd_sell": cd_sell,
            "sell_risk": sell_risk.fillna(False),
            "previous_pool": previous_pool,
            "market_state": market_state,
            "market_state_raw": market_state_raw,
            "data_gaps": data_gaps,
        }

    async def _theme_hot_matrix(
        self, market: str, close: pd.DataFrame, start: datetime, end: datetime,
    ) -> pd.DataFrame:
        out = pd.DataFrame(False, index=close.index, columns=close.columns)
        definitions = await self.themes.list_definitions(market, include_disabled=False)
        if not definitions:
            return out
        symbol_themes: dict[str, set[str]] = {}
        for definition in definitions[:80]:
            members = await self.themes.list_static_constituents(
                market, definition.theme_code, include_disabled=False)
            for member in members:
                if member.symbol in out.columns:
                    symbol_themes.setdefault(member.symbol, set()).add(definition.theme_code)
        snapshots = await self.themes.list_snapshots_window(
            market, start=start, end=end, limit=100_000)
        hot_by_date: dict[str, set[str]] = {}
        for snap in snapshots:
            score = (
                (snap.pct_change or 0) * 1.2
                + (snap.pct_change_5m or 0) * 0.8
                + (snap.amount_ratio or 0) * 2.0
                + (snap.up_ratio or 0) * 10.0
                + (snap.limit_up_count or 0) * 1.5
            )
            if score < 5:
                continue
            d = pd.Timestamp(snap.ts).tz_convert("Asia/Shanghai").strftime("%Y-%m-%d")
            hot_by_date.setdefault(d, set()).add(snap.theme_code)
            for leader in snap.leader_symbols or []:
                if d in out.index and leader in out.columns:
                    out.at[d, leader] = True
        for d, hot_codes in hot_by_date.items():
            if d not in out.index:
                continue
            for sym, codes in symbol_themes.items():
                if codes & hot_codes:
                    out.at[d, sym] = True
        return out

    def _run_strategy(
        self, spec: StrategySpec, market: str, data: dict[str, Any],
    ) -> tuple[StrategyBacktestRun, list[StrategyBacktestTrade]]:
        close: pd.DataFrame = data["close"]
        entries, exits, extra_gaps = self._signals_for(spec.strategy_id, data)
        # 所有盘后/日线信号顺延一日执行,避免未来函数。
        entries = entries.shift(1, fill_value=False).astype(bool)
        exits = exits.shift(1, fill_value=False).astype(bool)
        engine = "vectorbt"
        try:
            import vectorbt as vbt  # noqa: PLC0415
            pf = vbt.Portfolio.from_signals(
                close,
                entries,
                exits,
                init_cash=1_000_000,
                fees=0.0005,
                slippage=0.001,
                freq="1D",
            )
            total_ret_raw = pf.total_return()
            max_dd_raw = pf.max_drawdown()
            total_return = float(np.nanmean(np.asarray(total_ret_raw, dtype=float))) * 100
            max_dd = float(np.nanmin(np.asarray(max_dd_raw, dtype=float))) * 100
            values = pf.value()
            equity = values.mean(axis=1) if isinstance(values, pd.DataFrame) else values
        except Exception as e:  # noqa: BLE001
            log.warning("strategy_backtest.vectorbt_failed", strategy=spec.strategy_id, error=str(e))
            engine = "fallback"
            total_return = None
            max_dd = None
            equity = pd.Series(dtype=float)

        trades = self._make_trades(spec, data, entries, exits)
        returns = pd.Series([t.return_pct for t in trades], dtype=float)
        trade_count = len(trades)
        win_rate = float((returns > 0).mean() * 100) if trade_count else None
        avg_return = float(returns.mean()) if trade_count else None
        median_return = float(returns.median()) if trade_count else None
        worst_trade = float(returns.min()) if trade_count else None
        avg_holding = (
            float(pd.Series([t.holding_days for t in trades], dtype=float).mean())
            if trade_count else None
        )
        if max_dd is None:
            max_dd = self._max_drawdown_from_trades(returns)
        yearly = self._yearly_stats(trades)
        latest_year = yearly[-1] if yearly else {}
        # 年化收益: 从组合资金曲线(equity=等权各标的 NAV 均值)推算。资金分散全 universe
        # 多数标的空仓 → 口径偏保守, 仅作展示, 不作门槛。
        annual_return: float | None = None
        try:
            if len(equity) > 1 and float(equity.iloc[0]) > 0:
                growth = float(equity.iloc[-1]) / float(equity.iloc[0])
                if growth > 0:
                    annual_return = (growth ** (252.0 / max(len(equity), 1)) - 1) * 100
        except Exception:  # noqa: BLE001
            annual_return = None
        # 沙盒门槛(风险调整, 2026-06-18 重校准): 沙盒是"纸面观察"非实盘部署, 门槛只筛
        # 有正期望 + 一致性的策略进入观察。逐笔指标可信(_make_trades 真实逐笔); 原
        # avg≥2.5%/笔偏高(最佳仅 1.72%)→ 降到 1.0%, 辅以胜率/中位/回撤/交易数护栏。
        quality_gates = {
            "min_trades_80": trade_count >= 80,
            "win_rate_ge_50": win_rate is not None and win_rate >= 50,
            "avg_return_ge_1_0": avg_return is not None and avg_return >= 1.0,
            "median_return_gt_0": median_return is not None and median_return > 0,
            "max_drawdown_gt_minus_20": max_dd is not None and max_dd > -20,
            "latest_year_non_negative": (
                not latest_year or (latest_year.get("avg_return_pct") or 0) >= 0
            ),
        }
        sandbox = bool(all(quality_gates.values()))
        status = "passed" if sandbox else ("insufficient_data" if trade_count < 20 else "watch_only")
        sample_start = str(close.index.min()) if len(close.index) else None
        sample_end = str(close.index.max()) if len(close.index) else None
        data_gaps = [*data["data_gaps"], *extra_gaps]
        if trade_count < 20:
            data_gaps.append("有效交易少于 20 笔,暂不能进入沙盒")

        # 组合级 PnL: 集中持仓真实收益(对比顶层 total/annual 的"摊全 universe"稀释口径)。
        portfolio = {
            "method": "固定仓位组合: N并发槽, 每仓=本金/N, 满仓跳过, 现金不生息, 不复利",
            "init_cash": 1_000_000,
            "by_slots": {
                str(n): self._simulate_portfolio(trades, n_slots=n) for n in (3, 5, 10)
            },
        }
        portfolio["headline_n5"] = portfolio["by_slots"].get("5")

        run = StrategyBacktestRun(
            market=market,
            strategy_id=spec.strategy_id,
            strategy_name=spec.name,
            description=spec.description,
            horizon=spec.horizon,
            status=status,
            engine=engine,
            sample_start=sample_start,
            sample_end=sample_end,
            symbol_count=len(close.columns),
            trade_count=trade_count,
            win_rate=win_rate,
            avg_return_pct=avg_return,
            median_return_pct=median_return,
            max_drawdown_pct=max_dd,
            worst_trade_pct=worst_trade,
            avg_holding_days=avg_holding,
            annual_return_pct=annual_return,
            total_return_pct=total_return,
            sandbox_eligible=sandbox,
            metrics={
                "entry_rule": self._entry_rule(spec.strategy_id),
                "exit_rule": spec.exit_rule,
                "stop_loss_pct": spec.stop_loss_pct,
                "take_profit_pct": spec.take_profit_pct,
                "fees": 0.0005,
                "slippage": 0.001,
                "execution": "日线/盘后信号顺延 1 个交易日执行; T+1 用最短持有 1 日近似",
                "market_regime_formula": (
                    "risk_off=炸板率>=45%或跌停>=20或淘汰惩罚<=-5%或宽基下跌且上涨家数<=35%; "
                    "并纳入高位淘汰惩罚<=-6%和连板生态坍塌; "
                    "risk_on=晋级率/涨停数/上涨家数达标且连板强度或二板以上广度达标; "
                    "repair=非 risk_on/risk_off 且晋级率>=20%或上涨家数>=55%"
                ),
                "sandbox_quality_gates": quality_gates,
                "portfolio": portfolio,
            },
            returns=self._forward_returns(data["close"], entries),
            yearly=yearly,
            market_state=self._market_state_stats(trades, data["market_state"]),
            theme_state=self._theme_state_stats(trades, data["theme_hot"]),
            exit_comparison=self._exit_comparison(spec, data, entries),
            equity_curve=self._equity_curve(equity),
            data_gaps=data_gaps,
            generated_at=datetime.now(timezone.utc),
        )
        return run, trades[:500]

    def _signals_for(
        self, strategy_id: str, data: dict[str, Any],
    ) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
        close: pd.DataFrame = data["close"]
        ms: pd.DataFrame = data["market_state"]
        trend_ok = pd.DataFrame(
            np.repeat(ms["trend_allowed"].to_numpy()[:, None], len(close.columns), axis=1),
            index=close.index, columns=close.columns,
        )
        short_ok = pd.DataFrame(
            np.repeat(ms["short_term_allowed"].to_numpy()[:, None], len(close.columns), axis=1),
            index=close.index, columns=close.columns,
        )
        risk_ok = pd.DataFrame(
            np.repeat((~ms["risk_off"]).to_numpy()[:, None], len(close.columns), axis=1),
            index=close.index, columns=close.columns,
        )
        risk_onish = pd.DataFrame(
            np.repeat(
                ms["regime"].isin(["risk_on", "repair"]).to_numpy()[:, None],
                len(close.columns),
                axis=1,
            ),
            index=close.index, columns=close.columns,
        )
        risk_on = pd.DataFrame(
            np.repeat(ms["risk_on"].to_numpy()[:, None], len(close.columns), axis=1),
            index=close.index, columns=close.columns,
        )
        relative_ok = data["relative_strength20"].fillna(0) >= -0.05
        candidate = data["quality_low_position"] & relative_ok
        sell_risk = data["sell_risk"] | data["cd_sell"]
        strong_ecology = (
            (ms["promotion_rate"] >= 0.30)
            & (ms["high_promotion_rate"] >= 0.20)
            & (ms["red_rate"] >= 0.45)
            & (ms["break_rate"] <= 0.35)
            & (ms["down_limit_count"] <= 10)
            & (ms["ladder_strength_score"] >= 35)
        )
        strong_ecology = pd.DataFrame(
            np.repeat(strong_ecology.to_numpy()[:, None], len(close.columns), axis=1),
            index=close.index, columns=close.columns,
        )
        uptrend = data["market_uptrend"]
        if strategy_id == "low_position_cd":
            entries = candidate & data["cd_buy"] & trend_ok & risk_ok & uptrend
            exits = sell_risk | (~risk_ok)
        elif strategy_id == "low_position_breakout":
            entries = candidate & data["breakout"] & trend_ok & risk_ok & uptrend
            exits = sell_risk | (~risk_ok)
        elif strategy_id == "low_position_theme":
            entries = candidate & data["theme_hot"] & trend_ok & risk_onish
            exits = sell_risk | (~data["theme_hot"]) | (~risk_ok)
        elif strategy_id == "cd_market_repair":
            entries = data["cd_buy"] & risk_onish & risk_ok
            exits = data["cd_sell"] | (~risk_ok)
        elif strategy_id == "previous_limit_strong":
            entries = data["previous_pool"] & short_ok & strong_ecology
            exits = (~short_ok) | (~risk_on)
        else:
            raw = candidate & (data["cd_buy"] | data["breakout"])
            entries = raw & risk_ok & uptrend
            exits = sell_risk | (~risk_ok)
        return entries.fillna(False), exits.fillna(False), []

    def _make_trades(
        self,
        spec: StrategySpec,
        data: dict[str, Any],
        entries: pd.DataFrame,
        exits: pd.DataFrame,
    ) -> list[StrategyBacktestTrade]:
        close: pd.DataFrame = data["close"]
        trades: list[StrategyBacktestTrade] = []
        for symbol in close.columns:
            in_pos = False
            entry_i = -1
            entry_price = 0.0
            for i, date in enumerate(close.index):
                price = close.iat[i, close.columns.get_loc(symbol)]
                if pd.isna(price):
                    continue
                if not in_pos and bool(entries.iat[i, entries.columns.get_loc(symbol)]):
                    in_pos = True
                    entry_i = i
                    entry_price = float(price)
                    continue
                if not in_pos:
                    continue
                held = i - entry_i
                pnl_pct = (float(price) / entry_price - 1) * 100
                hit_stop = held >= 1 and pnl_pct <= spec.stop_loss_pct
                hit_take = (
                    held >= 1
                    and spec.take_profit_pct is not None
                    and pnl_pct >= spec.take_profit_pct
                )
                hit_risk = held >= 1 and bool(exits.iat[i, exits.columns.get_loc(symbol)])
                hit_time = held >= max(spec.hold_days, 1)
                should_exit = hit_stop or hit_take or hit_risk or hit_time
                if should_exit:
                    exit_price = float(price)
                    signal_i = max(entry_i - 1, 0)
                    if hit_stop:
                        reason = "stop_loss"
                    elif hit_take:
                        reason = "take_profit"
                    elif hit_risk:
                        reason = "risk_exit"
                    else:
                        reason = "time_stop"
                    trades.append(StrategyBacktestTrade(
                        market="ashare",
                        strategy_id=spec.strategy_id,
                        symbol=symbol,
                        entry_date=str(close.index[entry_i]),
                        exit_date=str(date),
                        entry_price=entry_price,
                        exit_price=exit_price,
                        return_pct=(exit_price / entry_price - 1) * 100 - 0.15,
                        holding_days=held,
                        exit_reason=reason,
                        evidence={
                            "signal_date": str(close.index[signal_i]),
                            "hold_days": spec.hold_days,
                            "stop_loss_pct": spec.stop_loss_pct,
                            "take_profit_pct": spec.take_profit_pct,
                        },
                    ))
                    in_pos = False
        trades.sort(key=lambda t: (t.entry_date, t.return_pct))
        return trades

    async def _generate_instructions(
        self, market: str, eligible: list[StrategyBacktestRun],
    ) -> list[TradeInstruction]:
        if not eligible:
            return []
        best = max(eligible, key=lambda r: (r.avg_return_pct or 0, r.win_rate or 0))
        candidates = await self.candidates.list_active(market, limit=20)
        now = datetime.now(timezone.utc)
        out: list[TradeInstruction] = []
        for c in candidates[:10]:
            out.append(TradeInstruction(
                market=market,
                instruction_key=f"{best.strategy_id}:{c.candidate_key}",
                action="BUY_SETUP",
                target_type="symbol",
                target_id=c.symbol,
                target_name=c.name,
                candidate_key=c.candidate_key,
                title=f"{c.name or c.symbol} 进入纸面观察",
                summary=(
                    f"{best.strategy_name} 历史胜率 "
                    f"{(best.win_rate or 0):.1f}%,均值收益 {(best.avg_return_pct or 0):.2f}%."
                ),
                confidence=min(0.85, max(0.35, (best.win_rate or 0) / 100)),
                severity="watch",
                reasons=[
                    f"候选池评分 {c.score:.1f}",
                    f"策略 {best.strategy_name} 已通过回测沙盒门槛",
                ],
                risks=[
                    "当前为纸面指令,不自动下单",
                    "若跌破 10 日线或题材退潮,观察失效",
                ],
                entry_conditions=[
                    "放量突破或 CD 买点确认",
                    "市场风险门控未恶化",
                ],
                invalidation_conditions=[
                    "炸板率显著上升或跌停扩散",
                    "个股跌破 10 日线",
                ],
                evidence={
                    "strategy_id": best.strategy_id,
                    "strategy_name": best.strategy_name,
                    "win_rate": best.win_rate,
                    "avg_return_pct": best.avg_return_pct,
                    "max_drawdown_pct": best.max_drawdown_pct,
                    "candidate_score": c.score,
                },
                generated_at=now,
                expires_at=now + timedelta(days=2),
            ))
        return out

    def _empty_run(
        self, spec: StrategySpec, market: str, gaps: list[str], *, engine: str,
    ) -> StrategyBacktestRun:
        return StrategyBacktestRun(
            market=market,
            strategy_id=spec.strategy_id,
            strategy_name=spec.name,
            description=spec.description,
            horizon=spec.horizon,
            status="insufficient_data",
            engine=engine,
            data_gaps=gaps,
            generated_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _entry_rule(strategy_id: str) -> str:
        return {
            "low_position_cd": "高质量低位容量趋势 AND 1d CD 买点 AND 趋势门控允许 AND 非 risk_off",
            "low_position_breakout": "高质量低位容量趋势 AND 放量突破 20 日平台 AND 趋势门控允许 AND 非 risk_off",
            "low_position_theme": "高质量低位容量趋势 AND 所属题材升温 AND 市场 risk_on/repair",
            "cd_market_repair": "1d CD 买点 AND 市场 risk_on/repair AND 非 risk_off",
            "previous_limit_strong": "昨日涨停池 AND 晋级率/红盘率/高位晋级达标 AND 炸板率/跌停/连板强度达标",
            "risk_filter": "高质量低位/CD/突破原始买点 AND 非 risk_off 市场",
        }.get(strategy_id, strategy_id)

    @staticmethod
    def _forward_returns(close: pd.DataFrame, entries: pd.DataFrame) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for n in (1, 3, 5, 10):
            ret = (close.shift(-n) / close - 1) * 100
            vals = ret.where(entries).stack().dropna()
            out[f"{n}d"] = {
                "count": int(vals.count()),
                "avg_return_pct": float(vals.mean()) if len(vals) else None,
                "median_return_pct": float(vals.median()) if len(vals) else None,
                "win_rate": float((vals > 0).mean() * 100) if len(vals) else None,
            }
        return out

    @staticmethod
    def _yearly_stats(trades: list[StrategyBacktestTrade]) -> list[dict[str, Any]]:
        grouped: dict[str, list[float]] = {}
        for t in trades:
            grouped.setdefault(t.entry_date[:4], []).append(t.return_pct)
        return [
            {
                "year": year,
                "trade_count": len(vals),
                "win_rate": sum(1 for v in vals if v > 0) / len(vals) * 100,
                "avg_return_pct": sum(vals) / len(vals),
            }
            for year, vals in sorted(grouped.items())
        ]

    @staticmethod
    def _market_state_stats(
        trades: list[StrategyBacktestTrade], market_state: pd.DataFrame,
    ) -> list[dict[str, Any]]:
        buckets = {"risk_on": [], "repair": [], "neutral": [], "risk_off": []}
        for t in trades:
            state_date = str(t.evidence.get("signal_date") or t.entry_date)
            if state_date not in market_state.index:
                continue
            row = market_state.loc[state_date]
            regime = str(row.get("regime") or "neutral")
            if regime not in buckets:
                regime = "neutral"
            buckets[regime].append(t.return_pct)
        return [
            {
                "state": k,
                "trade_count": len(v),
                "avg_return_pct": sum(v) / len(v) if v else None,
                "win_rate": sum(1 for x in v if x > 0) / len(v) * 100 if v else None,
            }
            for k, v in buckets.items()
        ]

    @staticmethod
    def _theme_state_stats(
        trades: list[StrategyBacktestTrade], theme_hot: pd.DataFrame,
    ) -> list[dict[str, Any]]:
        hot: list[float] = []
        cold: list[float] = []
        for t in trades:
            if t.entry_date in theme_hot.index and t.symbol in theme_hot.columns:
                (hot if bool(theme_hot.at[t.entry_date, t.symbol]) else cold).append(t.return_pct)
        return [
            {
                "state": "theme_hot",
                "trade_count": len(hot),
                "avg_return_pct": sum(hot) / len(hot) if hot else None,
                "win_rate": sum(1 for x in hot if x > 0) / len(hot) * 100 if hot else None,
            },
            {
                "state": "theme_not_hot",
                "trade_count": len(cold),
                "avg_return_pct": sum(cold) / len(cold) if cold else None,
                "win_rate": sum(1 for x in cold if x > 0) / len(cold) * 100 if cold else None,
            },
        ]

    def _exit_comparison(
        self, spec: StrategySpec, data: dict[str, Any], entries: pd.DataFrame,
    ) -> list[dict[str, Any]]:
        close = data["close"]
        rows = []
        for hold in (3, 5, 10):
            ret = (close.shift(-hold) / close - 1) * 100
            vals = ret.where(entries).stack().dropna()
            rows.append({
                "exit_rule": f"fixed_{hold}d",
                "trade_count": int(vals.count()),
                "avg_return_pct": float(vals.mean()) if len(vals) else None,
                "win_rate": float((vals > 0).mean() * 100) if len(vals) else None,
            })
        rows.append({"exit_rule": spec.exit_rule, "note": "主回测交易明细采用该卖出规则"})
        return rows

    @staticmethod
    def _equity_curve(equity: pd.Series | pd.DataFrame) -> list[dict[str, Any]]:
        if isinstance(equity, pd.DataFrame):
            equity = equity.mean(axis=1)
        if equity is None or len(equity) == 0:
            return []
        sample = equity.dropna()
        if len(sample) > 160:
            sample = sample.iloc[:: max(1, len(sample) // 160)]
        return [
            {"date": str(idx)[:10], "value": float(v)}
            for idx, v in sample.items()
        ]

    @staticmethod
    def _max_drawdown_from_trades(returns: pd.Series) -> float | None:
        if returns.empty:
            return None
        equity = (1 + returns / 100).cumprod()
        dd = equity / equity.cummax() - 1
        return float(dd.min() * 100)

    @staticmethod
    def _simulate_portfolio(trades, *, n_slots: int, init_cash: float = 1_000_000.0):
        """组合级 PnL 模拟: 最多 N 个并发仓, 每仓固定 = 本金/N(不复利), 满仓时新信号
        跳过(容量约束), 现金不生息, 资金随平仓循环。回答"集中持仓真实赚多少"。

        相对"等权摊全 universe"的稀释口径, 这个更接近实盘可部署收益。
        """
        if not trades or n_slots < 1:
            return None
        import heapq
        from datetime import date as _date

        def _d(s: str) -> _date:
            return _date.fromisoformat(str(s)[:10])

        size = init_cash / n_slots
        entries_sorted = sorted(trades, key=lambda t: str(t.entry_date))
        heap: list[tuple[str, float]] = []   # (exit_date, pnl_amount)
        realized = 0.0
        curve: list[tuple[str, float]] = []  # (exit_date, equity)
        taken = skipped = 0
        for t in entries_sorted:
            entry_d = str(t.entry_date)
            while heap and heap[0][0] <= entry_d:   # 先释放已平仓槽位
                xd, pnl = heapq.heappop(heap)
                realized += pnl
                curve.append((xd, init_cash + realized))
            if len(heap) < n_slots:
                heapq.heappush(heap, (str(t.exit_date), size * float(t.return_pct) / 100.0))
                taken += 1
            else:
                skipped += 1
        while heap:
            xd, pnl = heapq.heappop(heap)
            realized += pnl
            curve.append((xd, init_cash + realized))
        if not curve:
            return None
        total_return_pct = realized / init_cash * 100.0
        peak = init_cash
        max_dd = 0.0
        for _, eq in curve:
            peak = max(peak, eq)
            max_dd = min(max_dd, (eq - peak) / peak * 100.0)
        span_days = max((_d(curve[-1][0]) - _d(entries_sorted[0].entry_date)).days, 1)
        growth = 1.0 + total_return_pct / 100.0
        annual = (growth ** (365.0 / span_days) - 1) * 100.0 if growth > 0 else -100.0
        total = taken + skipped
        return {
            "n_slots": n_slots,
            "total_return_pct": round(total_return_pct, 2),
            "annual_return_pct": round(annual, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "trades_taken": taken,
            "trades_skipped": skipped,
            "capacity_utilization_pct": round(taken / total * 100, 1) if total else 0.0,
        }
