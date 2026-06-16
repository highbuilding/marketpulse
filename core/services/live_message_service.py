from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from core.domain.models import LiveMessage, ThemeConstituent, ThemeDefinition, ThemeSnapshot, ThemeState
from core.persistence.theme_repo import ThemeRepo
from core.services.watchlist_service import WatchlistService

log = structlog.get_logger(__name__)

RULE_VERSION = "v3"
_CTX_TTL = timedelta(seconds=60)
_DEDUP_TTL = timedelta(minutes=5)
_VOLUME_HISTORY_MAX = 24
_VOLUME_SPIKE_MIN_HISTORY = 4
_VOLUME_SPIKE_RATIO = 2.5
_THEME_ROTATION_LOOKBACK = timedelta(minutes=30)
_THEME_ACTIVE_STATES = {"launch", "diffusion"}
_THEME_WEAK_STATES = {"fade", "weakness"}
_BREADTH_MIN_SYMBOLS = 20
_BREADTH_STRONG_RATIO = 0.68
_BREADTH_WEAK_RATIO = 0.68
_INDEX_NAMES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "000016.SH": "上证50",
    "000688.SH": "科创50",
}
_LARGE_CAP_INDEX = "000016.SH"
_SMALL_CAP_INDEX = "000852.SH"
_GROWTH_INDEX = "399006.SZ"


@dataclass
class _QuoteState:
    symbol: str
    price: float
    change_pct: float
    volume: int | None
    amount: float | None
    ts: datetime


@dataclass
class _ThemeContext:
    definition: ThemeDefinition
    constituents: list[ThemeConstituent]
    symbols: set[str] = field(default_factory=set)


@dataclass
class _ThemeEval:
    ctx: _ThemeContext
    known: list[_QuoteState]
    up: list[_QuoteState]
    down: list[_QuoteState]
    core: list[_QuoteState]
    core_up: list[_QuoteState]
    follower_up: list[_QuoteState]
    leader: _QuoteState
    laggard: _QuoteState
    second: _QuoteState | None
    avg_change_pct: float


@dataclass
class _ThemeRuntimeState:
    theme_name: str | None = None
    leader: str | None = None
    leader_change_pct: float | None = None
    up_count: int = 0
    peak_up_count: int = 0
    known_count: int = 0
    peak_up_ratio: float = 0.0
    core_up_count: int = 0
    score: float | None = None
    state: str = "neutral"
    updated_at: datetime | None = None


@dataclass
class _MarketPulseRuntimeState:
    breadth_state: str = "neutral"
    universe_width_state: str = "neutral"
    style_state: str = "neutral"
    updated_at: datetime | None = None


class LiveMessageService:
    def __init__(self, theme_repo: ThemeRepo, watchlist: WatchlistService) -> None:
        self.theme_repo = theme_repo
        self.watchlist = watchlist
        self._ctx_loaded_at: datetime | None = None
        self._themes: dict[str, _ThemeContext] = {}
        self._symbol_themes: dict[str, list[str]] = {}
        self._watch_symbols: set[str] = set()
        self._quotes: dict[str, _QuoteState] = {}
        self._last_emit: dict[str, datetime] = {}
        self._theme_states: dict[str, _ThemeRuntimeState] = {}
        self._market_pulse = _MarketPulseRuntimeState()
        self._bar_volumes: dict[tuple[str, str], list[int]] = {}

    async def handle_quote_tick(
        self,
        payload: dict[str, Any],
        *,
        source_event_id: str | None = None,
    ) -> list[LiveMessage]:
        market = str(payload.get("market") or "ashare")
        if market != "ashare":
            return []
        await self._ensure_context(market)
        symbol = str(payload.get("symbol") or "").upper()
        if not symbol:
            return []
        ts = _parse_dt(payload.get("ts"))
        quote = _QuoteState(
            symbol=symbol,
            price=float(payload.get("price") or 0),
            change_pct=float(payload.get("change_pct") or 0),
            volume=_int_or_none(payload.get("volume")),
            amount=_float_or_none(payload.get("amount")),
            ts=ts,
        )
        prev = self._quotes.get(symbol)
        self._quotes[symbol] = quote

        messages: list[LiveMessage] = []
        messages.extend(self._index_pulse_messages(market, quote, source_event_id))
        messages.extend(self._collector_breadth_messages(market, quote, source_event_id))
        messages.extend(self._watchlist_messages(quote, prev, source_event_id))
        messages.extend(await self._theme_messages(market, quote, source_event_id))
        messages.extend(self._watchlist_theme_risk_messages(market, quote, source_event_id))
        return self._dedupe(messages, now=ts)

    def _index_pulse_messages(
        self,
        market: str,
        quote: _QuoteState,
        source_event_id: str | None,
    ) -> list[LiveMessage]:
        if quote.symbol not in _INDEX_NAMES:
            return []
        known = [self._quotes[s] for s in _INDEX_NAMES if s in self._quotes]
        if len(known) < 4:
            return []

        up = [s for s in known if s.change_pct > 0]
        down = [s for s in known if s.change_pct < 0]
        ordered = sorted(known, key=lambda s: s.change_pct, reverse=True)
        leader = ordered[0]
        laggard = ordered[-1]
        avg = sum(s.change_pct for s in known) / len(known)
        amount_values = [s.amount for s in known if s.amount is not None]
        total_amount = sum(amount_values) if amount_values else None
        payload = {
            "known_count": len(known),
            "up_count": len(up),
            "down_count": len(down),
            "avg_change_pct": avg,
            "leader": {"symbol": leader.symbol, "name": _INDEX_NAMES.get(leader.symbol), "change_pct": leader.change_pct},
            "laggard": {"symbol": laggard.symbol, "name": _INDEX_NAMES.get(laggard.symbol), "change_pct": laggard.change_pct},
            "indices": [
                {"symbol": s.symbol, "name": _INDEX_NAMES.get(s.symbol), "change_pct": s.change_pct, "price": s.price}
                for s in ordered
            ],
            "total_amount": total_amount,
            "rule_version": RULE_VERSION,
        }

        messages: list[LiveMessage] = []
        breadth_state = "neutral"
        if len(up) >= 5 and avg >= 0.25 and leader.change_pct >= 0.6:
            breadth_state = "strong"
        elif len(down) >= 5 and avg <= -0.25 and laggard.change_pct <= -0.6:
            breadth_state = "weak"

        if breadth_state == "strong" and self._market_pulse.breadth_state != "strong":
            messages.append(self._message(
                market=market,
                ts=quote.ts,
                level="watch",
                category="index",
                title="大盘脉搏偏强",
                body=f"{len(known)}个核心指数中{len(up)}个上涨,{_INDEX_NAMES.get(leader.symbol, leader.symbol)}领涨 {leader.change_pct:.2f}%。",
                source_event="bus:quote.tick",
                source_event_id=source_event_id,
                dedupe_key="index:pulse:strong",
                symbol=leader.symbol,
                symbols=[s.symbol for s in ordered],
                payload=payload | {"state": breadth_state},
            ))
        elif breadth_state == "weak" and self._market_pulse.breadth_state != "weak":
            messages.append(self._message(
                market=market,
                ts=quote.ts,
                level="warning",
                category="index",
                title="大盘脉搏偏弱",
                body=f"{len(known)}个核心指数中{len(down)}个下跌,{_INDEX_NAMES.get(laggard.symbol, laggard.symbol)}领跌 {laggard.change_pct:.2f}%。",
                source_event="bus:quote.tick",
                source_event_id=source_event_id,
                dedupe_key="index:pulse:weak",
                symbol=laggard.symbol,
                symbols=[s.symbol for s in ordered],
                payload=payload | {"state": breadth_state},
            ))

        core_lineup = {
            "000001.SH": self._quotes.get("000001.SH"),
            "000300.SH": self._quotes.get("000300.SH"),
            _GROWTH_INDEX: self._quotes.get(_GROWTH_INDEX),
            _SMALL_CAP_INDEX: self._quotes.get(_SMALL_CAP_INDEX),
        }
        if all(core_lineup.values()):
            core_values = [s for s in core_lineup.values() if s is not None]
            core_up = [s for s in core_values if s.change_pct >= 0.35]
            core_down = [s for s in core_values if s.change_pct <= -0.35]
            if len(core_up) >= 3 and self._market_pulse.breadth_state != "resonance_up":
                messages.append(self._message(
                    market=market,
                    ts=quote.ts,
                    level="watch",
                    category="index",
                    title="核心指数共振走强",
                    body=f"上证、沪深300、创业板和中证1000中 {len(core_up)} 个涨幅超过 0.35%。",
                    source_event="bus:quote.tick",
                    source_event_id=source_event_id,
                    dedupe_key="index:resonance:up",
                    symbol=leader.symbol,
                    symbols=[s.symbol for s in core_values],
                    payload=payload | {"state": "resonance_up"},
                ))
                breadth_state = "resonance_up"
            elif len(core_down) >= 3 and self._market_pulse.breadth_state != "resonance_down":
                messages.append(self._message(
                    market=market,
                    ts=quote.ts,
                    level="warning",
                    category="index",
                    title="核心指数共振走弱",
                    body=f"上证、沪深300、创业板和中证1000中 {len(core_down)} 个跌幅超过 0.35%。",
                    source_event="bus:quote.tick",
                    source_event_id=source_event_id,
                    dedupe_key="index:resonance:down",
                    symbol=laggard.symbol,
                    symbols=[s.symbol for s in core_values],
                    payload=payload | {"state": "resonance_down"},
                ))
                breadth_state = "resonance_down"

        style_state = "neutral"
        large = self._quotes.get(_LARGE_CAP_INDEX)
        small = self._quotes.get(_SMALL_CAP_INDEX)
        growth = self._quotes.get(_GROWTH_INDEX)
        if large and small:
            small_vs_large = small.change_pct - large.change_pct
            growth_vs_large = growth.change_pct - large.change_pct if growth else None
            style_payload = payload | {
                "large": {"symbol": large.symbol, "change_pct": large.change_pct},
                "small": {"symbol": small.symbol, "change_pct": small.change_pct},
                "growth": {"symbol": growth.symbol, "change_pct": growth.change_pct} if growth else None,
                "small_vs_large_pct": small_vs_large,
                "growth_vs_large_pct": growth_vs_large,
            }
            if large.change_pct >= 0.3 and small.change_pct <= -0.5:
                style_state = "large_defense"
                title = "权重护盘但小票走弱"
                body = f"上证50 {large.change_pct:.2f}%,中证1000 {small.change_pct:.2f}%,风格分化扩大。"
                messages.append(self._message(
                    market=market, ts=quote.ts, level="warning", category="risk",
                    title=title, body=body,
                    source_event="bus:quote.tick", source_event_id=source_event_id,
                    dedupe_key="index:style:large_defense",
                    symbol=small.symbol,
                    symbols=[large.symbol, small.symbol, *([growth.symbol] if growth else [])],
                    payload=style_payload | {"state": style_state},
                ))
            elif small_vs_large >= 0.8 and (growth_vs_large is None or growth_vs_large >= 0.3):
                style_state = "small_growth_stronger"
                messages.append(self._message(
                    market=market, ts=quote.ts, level="watch", category="index",
                    title="小票成长强于权重",
                    body=f"中证1000相对上证50强 {small_vs_large:.2f} 个百分点。",
                    source_event="bus:quote.tick", source_event_id=source_event_id,
                    dedupe_key="index:style:small_growth_stronger",
                    symbol=small.symbol,
                    symbols=[large.symbol, small.symbol, *([growth.symbol] if growth else [])],
                    payload=style_payload | {"state": style_state},
                ))
            elif small_vs_large <= -0.8:
                style_state = "large_stronger"
                messages.append(self._message(
                    market=market, ts=quote.ts, level="watch", category="index",
                    title="权重强于小票",
                    body=f"上证50相对中证1000强 {abs(small_vs_large):.2f} 个百分点。",
                    source_event="bus:quote.tick", source_event_id=source_event_id,
                    dedupe_key="index:style:large_stronger",
                    symbol=large.symbol,
                    symbols=[large.symbol, small.symbol, *([growth.symbol] if growth else [])],
                    payload=style_payload | {"state": style_state},
                ))

        self._market_pulse = _MarketPulseRuntimeState(
            breadth_state=breadth_state,
            universe_width_state=self._market_pulse.universe_width_state,
            style_state=style_state,
            updated_at=quote.ts,
        )
        return messages

    def _collector_breadth_messages(
        self,
        market: str,
        quote: _QuoteState,
        source_event_id: str | None,
    ) -> list[LiveMessage]:
        known = [
            q for symbol, q in self._quotes.items()
            if symbol not in _INDEX_NAMES and (symbol.endswith(".SH") or symbol.endswith(".SZ"))
        ]
        if len(known) < _BREADTH_MIN_SYMBOLS:
            return []
        up = [s for s in known if s.change_pct > 0]
        down = [s for s in known if s.change_pct < 0]
        flat = len(known) - len(up) - len(down)
        avg = sum(s.change_pct for s in known) / len(known)
        up_ratio = len(up) / len(known)
        down_ratio = len(down) / len(known)
        ordered = sorted(known, key=lambda s: s.change_pct, reverse=True)
        leader = ordered[0]
        laggard = ordered[-1]
        width_state = "neutral"
        if up_ratio >= _BREADTH_STRONG_RATIO and avg >= 0.35:
            width_state = "strong"
        elif down_ratio >= _BREADTH_WEAK_RATIO and avg <= -0.35:
            width_state = "weak"
        payload = {
            "sample_source": "collector_symbols",
            "sample_scope": "当前采集清单,非全A",
            "sample_count": len(known),
            "up_count": len(up),
            "down_count": len(down),
            "flat_count": flat,
            "up_ratio": up_ratio,
            "down_ratio": down_ratio,
            "avg_change_pct": avg,
            "leader": {"symbol": leader.symbol, "change_pct": leader.change_pct},
            "laggard": {"symbol": laggard.symbol, "change_pct": laggard.change_pct},
            "rule_version": RULE_VERSION,
        }
        messages: list[LiveMessage] = []
        if width_state == "strong" and self._market_pulse.universe_width_state != "strong":
            messages.append(self._message(
                market=market,
                ts=quote.ts,
                level="watch",
                category="index",
                title="采集样本宽度偏强",
                body=f"采集清单 {len(known)} 个标的中 {len(up)} 个上涨,平均涨跌幅 {avg:.2f}%。",
                source_event="bus:quote.tick",
                source_event_id=source_event_id,
                dedupe_key="collector_breadth:strong",
                symbol=leader.symbol,
                symbols=[s.symbol for s in ordered[:8]],
                payload=payload | {"state": width_state},
            ))
        elif width_state == "weak" and self._market_pulse.universe_width_state != "weak":
            messages.append(self._message(
                market=market,
                ts=quote.ts,
                level="warning",
                category="index",
                title="采集样本宽度偏弱",
                body=f"采集清单 {len(known)} 个标的中 {len(down)} 个下跌,平均涨跌幅 {avg:.2f}%。",
                source_event="bus:quote.tick",
                source_event_id=source_event_id,
                dedupe_key="collector_breadth:weak",
                symbol=laggard.symbol,
                symbols=[s.symbol for s in ordered[-8:]],
                payload=payload | {"state": width_state},
            ))

        if self._market_pulse.breadth_state in {"strong", "resonance_up"} and width_state == "weak":
            messages.append(self._message(
                market=market,
                ts=quote.ts,
                level="warning",
                category="risk",
                title="指数偏强但采集样本背离",
                body=f"核心指数偏强,但采集清单 {len(down)} / {len(known)} 个标的下跌。",
                source_event="bus:quote.tick",
                source_event_id=source_event_id,
                dedupe_key="risk:index_vs_collector_breadth",
                symbol=laggard.symbol,
                symbols=[s.symbol for s in ordered[-8:]],
                payload=payload | {
                    "state": "index_up_width_down",
                    "index_state": self._market_pulse.breadth_state,
                },
            ))

        self._market_pulse = _MarketPulseRuntimeState(
            breadth_state=self._market_pulse.breadth_state,
            universe_width_state=width_state,
            style_state=self._market_pulse.style_state,
            updated_at=quote.ts,
        )
        return messages

    async def handle_signal_new(
        self,
        payload: dict[str, Any],
        *,
        source_event_id: str | None = None,
    ) -> list[LiveMessage]:
        market = str(payload.get("market") or "ashare")
        if market != "ashare":
            return []
        await self._ensure_context(market)
        symbol = str(payload.get("symbol") or "").upper()
        interval = str(payload.get("interval") or "")
        signal_type = str(payload.get("signal_type") or "")
        ts = _parse_dt(payload.get("detected_at") or payload.get("bar_ts"))
        direction = "买入" if signal_type == "buy" else "卖出"
        title = f"{symbol} 触发 {interval} CD {direction}信号"
        body = f"CD {direction}信号出现在 {interval} 周期,价格 {payload.get('price') or '--'}。"
        msg = self._message(
            market=market,
            ts=ts,
            level="watch" if signal_type == "buy" else "warning",
            category="signal",
            title=title,
            body=body,
            source_event="bus:signal.new",
            source_event_id=source_event_id,
            dedupe_key=f"signal:{symbol}:{interval}:{signal_type}",
            symbol=symbol,
            symbols=[symbol],
            payload=payload,
        )
        return self._dedupe([msg], now=ts)

    async def handle_bar_updated(
        self,
        payload: dict[str, Any],
        *,
        source_event_id: str | None = None,
    ) -> list[LiveMessage]:
        market = str(payload.get("market") or "ashare")
        if market != "ashare":
            return []
        interval = str(payload.get("interval") or "")
        if interval != "5m" or not bool(payload.get("final")):
            return []
        symbol = str(payload.get("symbol") or "").upper()
        volume = _int_or_none(payload.get("volume"))
        if not symbol or volume is None or volume <= 0:
            return []
        await self._ensure_context(market)
        ts = _parse_dt(payload.get("ts"))
        key = (symbol, interval)
        history = self._bar_volumes.get(key, [])
        messages: list[LiveMessage] = []
        if len(history) >= _VOLUME_SPIKE_MIN_HISTORY:
            sample = history[-8:]
            baseline = sum(sample) / len(sample) if sample else 0
            ratio = volume / baseline if baseline > 0 else 0
            if ratio >= _VOLUME_SPIKE_RATIO:
                messages.extend(self._volume_spike_messages(
                    market=market,
                    symbol=symbol,
                    interval=interval,
                    ts=ts,
                    payload=payload,
                    volume=volume,
                    baseline=baseline,
                    ratio=ratio,
                    sample_count=len(sample),
                    source_event_id=source_event_id,
                ))
        history = [*history, volume]
        if len(history) > _VOLUME_HISTORY_MAX:
            history = history[-_VOLUME_HISTORY_MAX:]
        self._bar_volumes[key] = history
        return self._dedupe(messages, now=ts)

    async def _ensure_context(self, market: str) -> None:
        now = datetime.now(timezone.utc)
        if self._ctx_loaded_at and now - self._ctx_loaded_at < _CTX_TTL:
            return
        try:
            definitions = await self.theme_repo.list_definitions(market, include_disabled=False)
            themes: dict[str, _ThemeContext] = {}
            symbol_themes: dict[str, list[str]] = {}
            for d in definitions:
                rows = await self.theme_repo.list_static_constituents(
                    market, d.theme_code, include_disabled=False)
                ctx = _ThemeContext(d, rows, {c.symbol for c in rows})
                themes[d.theme_code] = ctx
                for c in rows:
                    symbol_themes.setdefault(c.symbol, []).append(d.theme_code)
            watch_symbols = {
                s for s in await self.watchlist.dynamic_universe()
                if s.endswith(".SH") or s.endswith(".SZ")
            }
            self._themes = themes
            self._symbol_themes = symbol_themes
            self._watch_symbols = watch_symbols
            self._ctx_loaded_at = now
        except Exception as e:  # noqa: BLE001
            log.warning("live_message.context_failed", market=market, error=str(e))

    def _watchlist_messages(
        self,
        quote: _QuoteState,
        prev: _QuoteState | None,
        source_event_id: str | None,
    ) -> list[LiveMessage]:
        if quote.symbol not in self._watch_symbols:
            return []
        messages: list[LiveMessage] = []
        crossed = _crossed_abs(prev.change_pct if prev else None, quote.change_pct, 2.0) \
            or _crossed_abs(prev.change_pct if prev else None, quote.change_pct, 5.0)
        flipped_up = prev is not None and prev.change_pct < 0 <= quote.change_pct
        flipped_down = prev is not None and prev.change_pct > 0 >= quote.change_pct
        if not (crossed or flipped_up or flipped_down):
            return []
        if flipped_up:
            title = f"自选股 {quote.symbol} 翻红"
            level = "watch"
            direction = "up"
        elif flipped_down:
            title = f"自选股 {quote.symbol} 翻绿"
            level = "warning"
            direction = "down"
        else:
            title = f"自选股 {quote.symbol} 波动扩大"
            level = "watch" if quote.change_pct > 0 else "warning"
            direction = "move"
        messages.append(self._message(
            market="ashare",
            ts=quote.ts,
            level=level,
            category="watchlist",
            title=title,
            body=f"当前涨跌幅 {quote.change_pct:.2f}%,最新价 {quote.price:.2f}。",
            source_event="bus:quote.tick",
            source_event_id=source_event_id,
            dedupe_key=f"watchlist:{quote.symbol}:{direction}",
            symbol=quote.symbol,
            symbols=[quote.symbol],
            payload=_quote_payload(quote, prev),
        ))
        return messages

    async def _theme_messages(
        self,
        market: str,
        quote: _QuoteState,
        source_event_id: str | None,
    ) -> list[LiveMessage]:
        messages: list[LiveMessage] = []
        for theme_code in self._symbol_themes.get(quote.symbol, []):
            ctx = self._themes.get(theme_code)
            if not ctx:
                continue
            ev = self._evaluate_theme(ctx)
            if ev is None:
                continue
            prev = self._theme_states.get(theme_code, _ThemeRuntimeState())
            current_state = _theme_state(ev, prev)
            await self._persist_theme_eval(market, quote.ts, ev, current_state, prev)
            if current_state == "launch":
                messages.append(self._message(
                    market=market,
                    ts=quote.ts,
                    level="watch",
                    category="theme",
                    title=f"{ctx.definition.theme_name}启动",
                    body=(
                        f"{len(ev.known)}只已采成分中{len(ev.up)}只上涨,"
                        f"{ev.leader.symbol} 领涨 {ev.leader.change_pct:.2f}%,核心上涨 {len(ev.core_up)} 只。"
                    ),
                    source_event="bus:quote.tick",
                    source_event_id=source_event_id,
                    dedupe_key=f"theme:{theme_code}:launch",
                    theme_code=theme_code,
                    symbol=ev.leader.symbol,
                    symbols=[s.symbol for s in sorted(ev.known, key=lambda x: x.change_pct, reverse=True)[:5]],
                    payload=_theme_payload(ev) | {"state": current_state, "prev_state": prev.state},
                ))
            if current_state == "diffusion":
                messages.append(self._message(
                    market=market,
                    ts=quote.ts,
                    level="watch",
                    category="theme",
                    title=f"{ctx.definition.theme_name}进入扩散",
                    body=(
                        f"{len(ev.known)}只已采成分中{len(ev.up)}只上涨,"
                        f"核心上涨 {len(ev.core_up)} 只,跟随上涨 {len(ev.follower_up)} 只。"
                    ),
                    source_event="bus:quote.tick",
                    source_event_id=source_event_id,
                    dedupe_key=f"theme:{theme_code}:diffusion",
                    theme_code=theme_code,
                    symbol=ev.leader.symbol,
                    symbols=[s.symbol for s in sorted(ev.known, key=lambda x: x.change_pct, reverse=True)[:5]],
                    payload=_theme_payload(ev) | {"state": current_state, "prev_state": prev.state},
                ))
            if current_state == "divergence":
                messages.append(self._message(
                    market=market,
                    ts=quote.ts,
                    level="warning",
                    category="risk",
                    title=f"{ctx.definition.theme_name}进入分歧",
                    body=f"{ev.leader.symbol} 领涨 {ev.leader.change_pct:.2f}%,但扩散不足或龙二断层。",
                    source_event="bus:quote.tick",
                    source_event_id=source_event_id,
                    dedupe_key=f"risk:{theme_code}:divergence",
                    theme_code=theme_code,
                    symbol=ev.leader.symbol,
                    symbols=[s.symbol for s in sorted(ev.known, key=lambda x: x.change_pct, reverse=True)[:5]],
                    payload=_theme_payload(ev) | {"state": current_state, "prev_state": prev.state},
                ))
            if current_state == "weakness":
                messages.append(self._message(
                    market=market,
                    ts=quote.ts,
                    level="warning",
                    category="theme",
                    title=f"{ctx.definition.theme_name}转弱",
                    body=(
                        f"{len(ctx.symbols)}只成分股中{len(ev.down)}只下跌,"
                        f"{ev.laggard.symbol} 跌幅 {ev.laggard.change_pct:.2f}%。"
                    ),
                    source_event="bus:quote.tick",
                    source_event_id=source_event_id,
                    dedupe_key=f"theme:{theme_code}:weakness",
                    theme_code=theme_code,
                    symbol=ev.laggard.symbol,
                    symbols=[s.symbol for s in sorted(ev.known, key=lambda x: x.change_pct)[:5]],
                    payload=_theme_payload(ev) | {"state": current_state, "prev_state": prev.state},
                ))
            if current_state == "fade":
                peak_up_count = max(prev.peak_up_count, prev.up_count)
                messages.append(self._message(
                    market=market,
                    ts=quote.ts,
                    level="warning",
                    category="risk",
                    title=f"{ctx.definition.theme_name}强度回落",
                    body=f"上涨家数从盘中高点 {peak_up_count} 降至 {len(ev.up)},领涨 {ev.leader.symbol} 当前 {ev.leader.change_pct:.2f}%。",
                    source_event="bus:quote.tick",
                    source_event_id=source_event_id,
                    dedupe_key=f"risk:{theme_code}:strength_fade",
                    theme_code=theme_code,
                    symbol=ev.leader.symbol,
                    symbols=[s.symbol for s in sorted(ev.known, key=lambda x: x.change_pct, reverse=True)[:5]],
                    payload=_theme_payload(ev) | {
                        "state": current_state,
                        "prev_state": prev.state,
                        "prev_up_count": prev.up_count,
                        "peak_up_count": peak_up_count,
                    },
                ))
            messages.extend(self._theme_rotation_messages(
                market, quote.ts, theme_code, ev, prev, current_state, source_event_id))
            messages.extend(self._theme_quality_messages(
                market, quote.ts, ev, prev, current_state, source_event_id))
            score = _theme_score(ev, current_state)
            self._theme_states[theme_code] = _ThemeRuntimeState(
                theme_name=ctx.definition.theme_name,
                leader=ev.leader.symbol,
                leader_change_pct=ev.leader.change_pct,
                up_count=len(ev.up),
                peak_up_count=max(prev.peak_up_count, len(ev.up)),
                known_count=len(ev.known),
                peak_up_ratio=max(prev.peak_up_ratio, _up_ratio(ev)),
                core_up_count=len(ev.core_up),
                score=score,
                state=current_state,
                updated_at=quote.ts,
            )
        return messages

    def _theme_rotation_messages(
        self,
        market: str,
        ts: datetime,
        theme_code: str,
        ev: _ThemeEval,
        prev: _ThemeRuntimeState,
        current_state: str,
        source_event_id: str | None,
    ) -> list[LiveMessage]:
        """识别题材间接力: 新题材启动/扩散,同时近窗口内有其他题材转弱/回落。"""
        if current_state not in _THEME_ACTIVE_STATES or prev.state in _THEME_ACTIVE_STATES:
            return []
        cutoff = ts - _THEME_ROTATION_LOOKBACK
        candidates = [
            (code, state)
            for code, state in self._theme_states.items()
            if code != theme_code
            and state.state in _THEME_WEAK_STATES
            and state.updated_at is not None
            and state.updated_at >= cutoff
        ]
        if not candidates:
            return []
        from_code, from_state = max(candidates, key=lambda item: item[1].updated_at or cutoff)
        from_name = from_state.theme_name or from_code
        to_name = ev.ctx.definition.theme_name
        payload = _theme_payload(ev) | {
            "state": "rotation",
            "to_state": current_state,
            "from_theme_code": from_code,
            "from_theme_name": from_name,
            "from_state": from_state.state,
            "from_updated_at": from_state.updated_at.isoformat() if from_state.updated_at else None,
            "from_up_count": from_state.up_count,
            "from_peak_up_count": from_state.peak_up_count,
            "from_peak_up_ratio": from_state.peak_up_ratio,
            "rule_version": RULE_VERSION,
        }
        return [self._message(
            market=market,
            ts=ts,
            level="watch",
            category="theme",
            title=f"题材轮动: {to_name}接力{from_name}",
            body=(
                f"{to_name}进入{_theme_state_label(current_state)},"
                f"{from_name}近30分钟已{_theme_state_label(from_state.state)},"
                f"关注资金是否从旧主线切向新主线。"
            ),
            source_event="bus:quote.tick",
            source_event_id=source_event_id,
            dedupe_key=f"theme_rotation:{from_code}->{theme_code}",
            theme_code=theme_code,
            symbol=ev.leader.symbol,
            symbols=[ev.leader.symbol, *([from_state.leader] if from_state.leader else [])],
            payload=payload,
        )]

    async def _persist_theme_eval(
        self,
        market: str,
        ts: datetime,
        ev: _ThemeEval,
        current_state: str,
        prev: _ThemeRuntimeState,
    ) -> None:
        """持久化题材快照和当前状态,供 UI 展示与盘后复盘。

        这条路径只写 SQLite 小表,失败不影响实时消息生成。
        """
        bucket = _bucket_5m(ts)
        leaders = [s.symbol for s in sorted(ev.known, key=lambda x: x.change_pct, reverse=True)[:5]]
        up_ratio = len(ev.up) / len(ev.known) if ev.known else None
        support_score = len(ev.core_up) / len(ev.core) if ev.core else None
        divergence_score = (
            ev.leader.change_pct - ev.second.change_pct
            if ev.second is not None else None
        )
        amount = sum(s.amount or 0 for s in ev.known)
        payload = _theme_payload(ev) | {
            "state": current_state,
            "bucket_ts": bucket.isoformat(),
            "peak_up_count": max(prev.peak_up_count, len(ev.up)),
            "rule_version": RULE_VERSION,
        }
        score = _theme_score(ev, current_state)
        reason = _theme_reason(ev, current_state)
        try:
            await self.theme_repo.upsert_snapshots([
                ThemeSnapshot(
                    market=market,
                    theme_code=ev.ctx.definition.theme_code,
                    theme_name=ev.ctx.definition.theme_name,
                    classification=ev.ctx.definition.classification,
                    ts=bucket,
                    pct_change=ev.avg_change_pct,
                    amount=amount or None,
                    up_ratio=up_ratio,
                    limit_up_count=_limit_up_count(ev),
                    member_count=len(ev.ctx.symbols),
                    leader_symbols=leaders,
                    divergence_score=divergence_score,
                    support_score=support_score,
                    raw=payload,
                ),
            ])
            await self.theme_repo.upsert_states([
                ThemeState(
                    market=market,
                    theme_code=ev.ctx.definition.theme_code,
                    theme_name=ev.ctx.definition.theme_name,
                    state=current_state,
                    score=score,
                    reason=reason,
                    evidence=payload,
                    updated_at=ts,
                ),
            ])
        except Exception as e:  # noqa: BLE001
            log.warning(
                "live_message.theme_state_persist_failed",
                market=market,
                theme_code=ev.ctx.definition.theme_code,
                error=str(e),
            )

    def _evaluate_theme(self, ctx: _ThemeContext) -> _ThemeEval | None:
        known = [q for q in (self._quotes.get(s) for s in ctx.symbols) if q is not None]
        min_known = min(
            len(ctx.symbols),
            max(3, min(8, math.ceil(len(ctx.symbols) * 0.35))),
        )
        if len(known) < min_known:
            return None
        up = [s for s in known if s.change_pct > 0]
        down = [s for s in known if s.change_pct < 0]
        ordered = sorted(known, key=lambda s: s.change_pct, reverse=True)
        leader = ordered[0]
        laggard = ordered[-1]
        second = ordered[1] if len(ordered) > 1 else None
        core_symbols = {
            c.symbol for c in ctx.constituents
            if (c.role_hint or "") in {"leader", "core", "mid_core"} or (c.weight or 0) >= 8
        }
        follower_symbols = {
            c.symbol for c in ctx.constituents
            if (c.role_hint or "") in {"follower", "laggard", "watch"} and c.symbol not in core_symbols
        }
        core = [s for s in known if s.symbol in core_symbols]
        core_up = [s for s in core if s.change_pct > 0]
        follower_up = [s for s in known if s.symbol in follower_symbols and s.change_pct > 0]
        avg = sum(s.change_pct for s in known) / len(known)
        return _ThemeEval(ctx, known, up, down, core, core_up, follower_up, leader, laggard, second, avg)

    def _theme_quality_messages(
        self,
        market: str,
        ts: datetime,
        ev: _ThemeEval,
        prev: _ThemeRuntimeState,
        current_state: str,
        source_event_id: str | None,
    ) -> list[LiveMessage]:
        messages: list[LiveMessage] = []
        theme_code = ev.ctx.definition.theme_code
        theme_name = ev.ctx.definition.theme_name
        amount_known_ratio = _amount_known_ratio(ev)
        leader_amount_share = _leader_amount_share(ev)
        limit_up_count = _limit_up_count(ev)
        near_limit_count = _near_limit_count(ev)
        momentum_count = _momentum_count(ev)
        leader_core = any(c.symbol == ev.leader.symbol for c in ev.ctx.constituents
                          if (c.role_hint or "") in {"leader", "core", "mid_core"} or (c.weight or 0) >= 8)
        if prev.leader and prev.leader != ev.leader.symbol and leader_core \
                and ev.leader.change_pct >= 2.0 \
                and (prev.leader_change_pct is None or ev.leader.change_pct - prev.leader_change_pct >= 1.0):
            messages.append(self._message(
                market=market, ts=ts, level="watch", category="theme",
                title=f"{theme_name}核心股切换",
                body=f"{ev.leader.symbol} 接过领涨,当前 {ev.leader.change_pct:.2f}%,上一领涨 {prev.leader}。",
                source_event="bus:quote.tick", source_event_id=source_event_id,
                dedupe_key=f"theme:{theme_code}:leader_switch",
                theme_code=theme_code, symbol=ev.leader.symbol,
                symbols=[ev.leader.symbol, prev.leader],
                payload=_theme_payload(ev) | {"prev_leader": prev.leader, "prev_leader_change_pct": prev.leader_change_pct},
            ))
        if len(ev.known) >= 4 and _up_ratio(ev) >= 0.45 and _core_up_ratio(ev) <= 0.50:
            messages.append(self._message(
                market=market, ts=ts, level="warning", category="risk",
                title=f"{theme_name}走强质量一般",
                body=(
                    f"上涨占比 {_up_ratio(ev):.0%},"
                    f"但核心上涨占比仅 {_core_up_ratio(ev):.0%}。"
                ),
                source_event="bus:quote.tick", source_event_id=source_event_id,
                dedupe_key=f"risk:{theme_code}:core_unsynced",
                theme_code=theme_code, symbol=ev.leader.symbol,
                symbols=[s.symbol for s in sorted(ev.known, key=lambda x: x.change_pct, reverse=True)[:5]],
                payload=_theme_payload(ev),
            ))
        if ev.leader.change_pct >= 4.0 and (
            _up_ratio(ev) < 0.45 or _leader_gap(ev) >= 2.0
        ):
            messages.append(self._message(
                market=market, ts=ts, level="warning", category="risk",
                title=f"{theme_name}异动偏单点",
                body=f"{ev.leader.symbol} 领涨 {ev.leader.change_pct:.2f}%,但成分股扩散不足。",
                source_event="bus:quote.tick", source_event_id=source_event_id,
                dedupe_key=f"risk:{theme_code}:single_leader",
                theme_code=theme_code, symbol=ev.leader.symbol,
                symbols=[s.symbol for s in sorted(ev.known, key=lambda x: x.change_pct, reverse=True)[:5]],
                payload=_theme_payload(ev),
            ))
        if current_state in _THEME_ACTIVE_STATES \
                and amount_known_ratio >= 0.65 \
                and leader_amount_share is not None \
                and leader_amount_share <= 0.45 \
                and momentum_count >= max(2, math.ceil(len(ev.known) * 0.20)):
            messages.append(self._message(
                market=market, ts=ts, level="watch", category="theme",
                title=f"{theme_name}扩散有成交确认",
                body=(
                    f"{momentum_count}只成分涨幅超过5%,"
                    f"领涨成交占比 {leader_amount_share:.0%},成交没有明显集中在单一标的。"
                ),
                source_event="bus:quote.tick", source_event_id=source_event_id,
                dedupe_key=f"theme:{theme_code}:amount_confirmed",
                theme_code=theme_code, symbol=ev.leader.symbol,
                symbols=[s.symbol for s in sorted(ev.known, key=lambda x: x.change_pct, reverse=True)[:5]],
                payload=_theme_payload(ev) | {"state": current_state},
            ))
        if current_state in _THEME_ACTIVE_STATES \
                and leader_amount_share is not None \
                and leader_amount_share >= 0.55 \
                and len(ev.known) >= 4:
            messages.append(self._message(
                market=market, ts=ts, level="warning", category="risk",
                title=f"{theme_name}成交集中度偏高",
                body=f"{ev.leader.symbol} 成交额占已采成分 {leader_amount_share:.0%},题材扩散质量需要继续确认。",
                source_event="bus:quote.tick", source_event_id=source_event_id,
                dedupe_key=f"risk:{theme_code}:amount_concentrated",
                theme_code=theme_code, symbol=ev.leader.symbol,
                symbols=[s.symbol for s in sorted(ev.known, key=lambda x: x.change_pct, reverse=True)[:5]],
                payload=_theme_payload(ev) | {"state": current_state},
            ))
        if limit_up_count >= 1 and near_limit_count >= max(2, math.ceil(len(ev.known) * 0.15)):
            messages.append(self._message(
                market=market, ts=ts, level="watch", category="theme",
                title=f"{theme_name}涨停结构增强",
                body=f"已采成分中 {limit_up_count} 只涨停或接近涨停,{near_limit_count} 只涨幅超过7%。",
                source_event="bus:quote.tick", source_event_id=source_event_id,
                dedupe_key=f"theme:{theme_code}:limit_structure",
                theme_code=theme_code, symbol=ev.leader.symbol,
                symbols=[s.symbol for s in sorted(ev.known, key=lambda x: x.change_pct, reverse=True)[:5]],
                payload=_theme_payload(ev) | {"state": current_state},
            ))
        prev_leader_quote = next((s for s in ev.known if s.symbol == prev.leader), None)
        if prev.leader \
                and prev_leader_quote is not None \
                and prev.leader_change_pct is not None \
                and prev.leader_change_pct >= 7.0 \
                and prev.leader_change_pct - prev_leader_quote.change_pct >= 2.0:
            messages.append(self._message(
                market=market, ts=ts, level="warning", category="risk",
                title=f"{theme_name}龙头高位回落",
                body=(
                    f"{prev_leader_quote.symbol} 从 {prev.leader_change_pct:.2f}% "
                    f"回落至 {prev_leader_quote.change_pct:.2f}%,观察是否带动后排退潮。"
                ),
                source_event="bus:quote.tick", source_event_id=source_event_id,
                dedupe_key=f"risk:{theme_code}:leader_pullback",
                theme_code=theme_code, symbol=prev_leader_quote.symbol,
                symbols=[prev_leader_quote.symbol, ev.leader.symbol],
                payload=_theme_payload(ev) | {
                    "state": current_state,
                    "prev_leader_change_pct": prev.leader_change_pct,
                    "prev_leader_current_change_pct": prev_leader_quote.change_pct,
                },
            ))
        return messages

    def _watchlist_theme_risk_messages(
        self,
        market: str,
        quote: _QuoteState,
        source_event_id: str | None,
    ) -> list[LiveMessage]:
        if quote.symbol not in self._watch_symbols or quote.change_pct > -1.0:
            return []
        messages: list[LiveMessage] = []
        for theme_code in self._symbol_themes.get(quote.symbol, []):
            ctx = self._themes.get(theme_code)
            if not ctx:
                continue
            ev = self._evaluate_theme(ctx)
            if ev is None or len(ev.up) < 3:
                continue
            messages.append(self._message(
                market=market, ts=quote.ts, level="warning", category="risk",
                title=f"自选股 {quote.symbol} 逆{ctx.definition.theme_name}走弱",
                body=f"{ctx.definition.theme_name}有 {len(ev.up)} 只上涨,但该自选股 {quote.change_pct:.2f}%。",
                source_event="bus:quote.tick", source_event_id=source_event_id,
                dedupe_key=f"risk:watchlist:{quote.symbol}:against_theme",
                theme_code=theme_code, symbol=quote.symbol, symbols=[quote.symbol],
                payload=_theme_payload(ev) | {"watch_symbol": quote.symbol, "watch_change_pct": quote.change_pct},
            ))
        return messages

    def _volume_spike_messages(
        self,
        *,
        market: str,
        symbol: str,
        interval: str,
        ts: datetime,
        payload: dict[str, Any],
        volume: int,
        baseline: float,
        ratio: float,
        sample_count: int,
        source_event_id: str | None,
    ) -> list[LiveMessage]:
        related_themes = self._symbol_themes.get(symbol, [])
        if symbol not in self._watch_symbols and not related_themes:
            return []
        open_price = _float_or_none(payload.get("open"))
        close_price = _float_or_none(payload.get("close"))
        is_down = open_price is not None and close_price is not None and close_price < open_price
        level = "warning" if is_down else "watch"
        category = "watchlist" if symbol in self._watch_symbols else ("risk" if is_down else "theme")
        theme_code = related_themes[0] if related_themes else None
        theme_name = self._themes[theme_code].definition.theme_name if theme_code and theme_code in self._themes else None
        prefix = f"自选股 {symbol}" if symbol in self._watch_symbols else f"{theme_name or '题材'}成分 {symbol}"
        title = f"{prefix} 5m明显放量"
        direction = "下跌" if is_down else "上涨或持平"
        body = f"当前5m成交量为近{sample_count}根均量 {ratio:.1f} 倍,K线{direction}。"
        msg = self._message(
            market=market,
            ts=ts,
            level=level,
            category=category,
            title=title,
            body=body,
            source_event="bus:bars.updated",
            source_event_id=source_event_id,
            dedupe_key=f"volume_spike:{symbol}:{interval}",
            theme_code=theme_code,
            symbol=symbol,
            symbols=[symbol],
            payload={
                "symbol": symbol,
                "interval": interval,
                "volume": volume,
                "baseline_volume": baseline,
                "volume_ratio": ratio,
                "sample_count": sample_count,
                "open": open_price,
                "close": close_price,
                "theme_code": theme_code,
                "theme_name": theme_name,
                "rule_version": RULE_VERSION,
            },
        )
        return [msg]

    def _dedupe(self, messages: list[LiveMessage], *, now: datetime) -> list[LiveMessage]:
        result: list[LiveMessage] = []
        for msg in messages:
            last = self._last_emit.get(msg.dedupe_key)
            if last is not None and now - last < _DEDUP_TTL:
                continue
            self._last_emit[msg.dedupe_key] = now
            result.append(msg)
        return result

    def _message(
        self,
        *,
        market: str,
        ts: datetime,
        level: str,
        category: str,
        title: str,
        body: str,
        source_event: str,
        dedupe_key: str,
        theme_code: str | None = None,
        symbol: str | None = None,
        symbols: list[str] | None = None,
        source_event_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> LiveMessage:
        bucket = _bucket_5m(ts)
        msg_id = hashlib.sha1(
            f"{market}:{dedupe_key}:{bucket.isoformat()}".encode("utf-8"),
        ).hexdigest()
        return LiveMessage(
            id=msg_id,
            market=market,
            ts=ts,
            level=level,  # type: ignore[arg-type]
            category=category,  # type: ignore[arg-type]
            title=title,
            body=body,
            theme_code=theme_code,
            symbol=symbol,
            symbols=symbols or [],
            source_event=source_event,
            source_event_id=source_event_id,
            dedupe_key=dedupe_key,
            payload=payload or {},
            rule_version=RULE_VERSION,
        )


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if value:
        try:
            return datetime.fromisoformat(str(value)).astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _bucket_5m(ts: datetime) -> datetime:
    ts = ts.astimezone(timezone.utc)
    minute = ts.minute - (ts.minute % 5)
    return ts.replace(minute=minute, second=0, microsecond=0)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _crossed_abs(prev: float | None, current: float, threshold: float) -> bool:
    if prev is None:
        return abs(current) >= threshold
    return abs(prev) < threshold <= abs(current)


def _quote_payload(quote: _QuoteState, prev: _QuoteState | None) -> dict[str, Any]:
    return {
        "symbol": quote.symbol,
        "price": quote.price,
        "change_pct": quote.change_pct,
        "prev_change_pct": prev.change_pct if prev else None,
        "volume": quote.volume,
        "amount": quote.amount,
    }


def _theme_payload(ev: _ThemeEval) -> dict[str, Any]:
    return {
        "theme_code": ev.ctx.definition.theme_code,
        "theme_name": ev.ctx.definition.theme_name,
        "member_count": len(ev.ctx.symbols),
        "known_count": len(ev.known),
        "known_ratio": _known_ratio(ev),
        "up_count": len(ev.up),
        "up_ratio": _up_ratio(ev),
        "down_count": len(ev.down),
        "down_ratio": _down_ratio(ev),
        "core_count": len(ev.core),
        "core_up_count": len(ev.core_up),
        "core_up_ratio": _core_up_ratio(ev),
        "follower_up_count": len(ev.follower_up),
        "follower_up_ratio": _follower_up_ratio(ev),
        "avg_change_pct": ev.avg_change_pct,
        "total_amount": _total_amount(ev),
        "amount_known_count": _amount_known_count(ev),
        "amount_known_ratio": _amount_known_ratio(ev),
        "leader_amount": ev.leader.amount,
        "leader_amount_share": _leader_amount_share(ev),
        "limit_up_count": _limit_up_count(ev),
        "near_limit_count": _near_limit_count(ev),
        "momentum_count": _momentum_count(ev),
        "severe_down_count": _severe_down_count(ev),
        "active_money_confirmed": _active_money_confirmed(ev),
        "leader": {"symbol": ev.leader.symbol, "change_pct": ev.leader.change_pct},
        "laggard": {"symbol": ev.laggard.symbol, "change_pct": ev.laggard.change_pct},
        "second": {"symbol": ev.second.symbol, "change_pct": ev.second.change_pct} if ev.second else None,
        "members": [
            {"symbol": s.symbol, "change_pct": s.change_pct, "price": s.price}
            for s in sorted(ev.known, key=lambda x: x.change_pct, reverse=True)
        ],
    }


def _ratio(part: int, total: int) -> float:
    return part / total if total > 0 else 0.0


def _known_ratio(ev: _ThemeEval) -> float:
    return _ratio(len(ev.known), len(ev.ctx.symbols))


def _up_ratio(ev: _ThemeEval) -> float:
    return _ratio(len(ev.up), len(ev.known))


def _down_ratio(ev: _ThemeEval) -> float:
    return _ratio(len(ev.down), len(ev.known))


def _core_up_ratio(ev: _ThemeEval) -> float:
    return _ratio(len(ev.core_up), len(ev.core))


def _follower_up_ratio(ev: _ThemeEval) -> float:
    follower_known = [
        s for s in ev.known
        if any(c.symbol == s.symbol and (c.role_hint or "") in {"follower", "laggard", "watch"}
               for c in ev.ctx.constituents)
    ]
    return _ratio(len(ev.follower_up), len(follower_known))


def _amount_known_count(ev: _ThemeEval) -> int:
    return sum(1 for s in ev.known if s.amount is not None and s.amount > 0)


def _amount_known_ratio(ev: _ThemeEval) -> float:
    return _ratio(_amount_known_count(ev), len(ev.known))


def _total_amount(ev: _ThemeEval) -> float | None:
    values = [s.amount for s in ev.known if s.amount is not None and s.amount > 0]
    return sum(values) if values else None


def _leader_amount_share(ev: _ThemeEval) -> float | None:
    total = _total_amount(ev)
    if not total or ev.leader.amount is None or ev.leader.amount <= 0:
        return None
    return ev.leader.amount / total


def _limit_up_count(ev: _ThemeEval) -> int:
    return sum(1 for s in ev.known if s.change_pct >= 9.8)


def _near_limit_count(ev: _ThemeEval) -> int:
    return sum(1 for s in ev.known if s.change_pct >= 7.0)


def _momentum_count(ev: _ThemeEval) -> int:
    return sum(1 for s in ev.known if s.change_pct >= 5.0)


def _severe_down_count(ev: _ThemeEval) -> int:
    return sum(1 for s in ev.known if s.change_pct <= -5.0)


def _active_money_confirmed(ev: _ThemeEval) -> bool:
    leader_share = _leader_amount_share(ev)
    return (
        _amount_known_ratio(ev) >= 0.65
        and leader_share is not None
        and leader_share <= 0.45
        and _momentum_count(ev) >= max(2, math.ceil(len(ev.known) * 0.20))
    )


def _leader_gap(ev: _ThemeEval) -> float:
    return (
        ev.leader.change_pct - ev.second.change_pct
        if ev.second is not None else 0.0
    )


def _theme_state(ev: _ThemeEval, prev: _ThemeRuntimeState) -> str:
    peak_up_count = max(prev.peak_up_count, prev.up_count)
    up_ratio = _up_ratio(ev)
    down_ratio = _down_ratio(ev)
    core_up_ratio = _core_up_ratio(ev)
    min_launch_up = max(3, math.ceil(len(ev.known) * 0.45))
    min_diffusion_up = max(4, math.ceil(len(ev.known) * 0.60))

    up_count_drop = peak_up_count - len(ev.up)
    up_ratio_drop = max(prev.peak_up_ratio, _ratio(peak_up_count, prev.known_count)) - up_ratio
    if prev.state in {"launch", "diffusion", "strength"} \
            and up_ratio_drop >= 0.22 \
            and up_count_drop >= max(2, math.ceil(len(ev.known) * 0.15)):
        return "fade"
    if down_ratio >= 0.55 and ev.laggard.change_pct <= -1.0:
        return "weakness"
    if ev.leader.change_pct >= 4.0 and (
        up_ratio < 0.45 or _leader_gap(ev) >= 2.0
    ):
        return "divergence"
    if len(ev.up) >= min_diffusion_up and core_up_ratio >= 0.50 and ev.leader.change_pct >= 1.0:
        return "diffusion"
    if len(ev.up) >= min_launch_up and core_up_ratio >= 0.33 and ev.leader.change_pct >= 1.0:
        return "launch"
    return "neutral"


def _theme_score(ev: _ThemeEval, current_state: str) -> float:
    up_ratio = len(ev.up) / len(ev.known) if ev.known else 0.0
    core_ratio = len(ev.core_up) / len(ev.core) if ev.core else 0.0
    base = ev.avg_change_pct + up_ratio * 4.0 + core_ratio * 3.0
    if current_state == "launch":
        base += 1.5
    elif current_state == "diffusion":
        base += 2.0
    elif current_state == "divergence":
        base -= 0.5
    elif current_state == "fade":
        base -= 1.0
    elif current_state == "weakness":
        base -= 2.0
    if current_state in _THEME_ACTIVE_STATES:
        if _active_money_confirmed(ev):
            base += 0.75
        else:
            leader_share = _leader_amount_share(ev)
            if leader_share is not None and leader_share >= 0.55:
                base -= 0.5
    return round(base, 4)


def _theme_state_label(state: str) -> str:
    return {
        "launch": "启动",
        "diffusion": "扩散",
        "divergence": "分歧",
        "fade": "强度回落",
        "weakness": "转弱",
        "neutral": "中性",
    }.get(state, state)


def _theme_reason(ev: _ThemeEval, current_state: str) -> str:
    if current_state == "launch":
        return (
            f"题材启动:{len(ev.known)}只已采成分中{len(ev.up)}只上涨,"
            f"核心上涨{len(ev.core_up)}只,{ev.leader.symbol}领涨{ev.leader.change_pct:.2f}%"
        )
    if current_state == "diffusion":
        return (
            f"题材扩散:{len(ev.known)}只已采成分中{len(ev.up)}只上涨,"
            f"核心上涨{len(ev.core_up)}只,跟随上涨{len(ev.follower_up)}只"
        )
    if current_state == "divergence":
        return (
            f"题材分歧:{ev.leader.symbol}领涨{ev.leader.change_pct:.2f}%,"
            f"上涨家数{len(ev.up)}只"
        )
    if current_state == "fade":
        return (
            f"题材回落:{len(ev.known)}只已采成分中{len(ev.up)}只上涨,"
            f"均值{ev.avg_change_pct:.2f}%"
        )
    if current_state == "weakness":
        return (
            f"题材转弱:{len(ev.known)}只已采成分中{len(ev.down)}只下跌,"
            f"{ev.laggard.symbol}领跌{ev.laggard.change_pct:.2f}%"
        )
    return (
        f"{len(ev.known)}只已采成分中{len(ev.up)}只上涨,"
        f"均值{ev.avg_change_pct:.2f}%"
    )
