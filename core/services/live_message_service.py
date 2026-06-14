from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from core.domain.models import LiveMessage, ThemeConstituent, ThemeDefinition
from core.persistence.theme_repo import ThemeRepo
from core.services.watchlist_service import WatchlistService

log = structlog.get_logger(__name__)

RULE_VERSION = "v1"
_CTX_TTL = timedelta(seconds=60)
_DEDUP_TTL = timedelta(minutes=5)


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
        messages.extend(self._watchlist_messages(quote, prev, source_event_id))
        messages.extend(self._theme_messages(market, quote, source_event_id))
        return self._dedupe(messages, now=ts)

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
        _payload: dict[str, Any],
        *,
        source_event_id: str | None = None,
    ) -> list[LiveMessage]:
        # 第一版保留扩展点: CD 信号由 bus:signal.new 负责,bar.updated 不重复产消息。
        _ = source_event_id
        return []

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

    def _theme_messages(
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
            states = [self._quotes.get(s) for s in ctx.symbols]
            known = [s for s in states if s is not None]
            if len(known) < min(3, len(ctx.symbols)):
                continue
            up = [s for s in known if s.change_pct > 0]
            down = [s for s in known if s.change_pct < 0]
            leader = max(known, key=lambda s: s.change_pct)
            laggard = min(known, key=lambda s: s.change_pct)
            if len(up) >= 3 and leader.change_pct >= 1.0:
                messages.append(self._message(
                    market=market,
                    ts=quote.ts,
                    level="watch",
                    category="theme",
                    title=f"{ctx.definition.theme_name}走强",
                    body=(
                        f"{len(ctx.symbols)}只成分股中{len(up)}只上涨,"
                        f"{leader.symbol} 领涨 {leader.change_pct:.2f}%。"
                    ),
                    source_event="bus:quote.tick",
                    source_event_id=source_event_id,
                    dedupe_key=f"theme:{theme_code}:strength",
                    theme_code=theme_code,
                    symbol=leader.symbol,
                    symbols=[s.symbol for s in sorted(known, key=lambda x: x.change_pct, reverse=True)[:5]],
                    payload=_theme_payload(ctx, known, leader, laggard),
                ))
            if len(down) >= 3 and laggard.change_pct <= -1.0:
                messages.append(self._message(
                    market=market,
                    ts=quote.ts,
                    level="warning",
                    category="theme",
                    title=f"{ctx.definition.theme_name}转弱",
                    body=(
                        f"{len(ctx.symbols)}只成分股中{len(down)}只下跌,"
                        f"{laggard.symbol} 跌幅 {laggard.change_pct:.2f}%。"
                    ),
                    source_event="bus:quote.tick",
                    source_event_id=source_event_id,
                    dedupe_key=f"theme:{theme_code}:weakness",
                    theme_code=theme_code,
                    symbol=laggard.symbol,
                    symbols=[s.symbol for s in sorted(known, key=lambda x: x.change_pct)[:5]],
                    payload=_theme_payload(ctx, known, leader, laggard),
                ))
        return messages

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


def _theme_payload(
    ctx: _ThemeContext,
    states: list[_QuoteState],
    leader: _QuoteState,
    laggard: _QuoteState,
) -> dict[str, Any]:
    return {
        "theme_code": ctx.definition.theme_code,
        "theme_name": ctx.definition.theme_name,
        "member_count": len(ctx.symbols),
        "known_count": len(states),
        "up_count": sum(1 for s in states if s.change_pct > 0),
        "down_count": sum(1 for s in states if s.change_pct < 0),
        "leader": {"symbol": leader.symbol, "change_pct": leader.change_pct},
        "laggard": {"symbol": laggard.symbol, "change_pct": laggard.change_pct},
        "members": [
            {"symbol": s.symbol, "change_pct": s.change_pct, "price": s.price}
            for s in sorted(states, key=lambda x: x.change_pct, reverse=True)
        ],
    }

