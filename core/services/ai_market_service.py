from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import structlog

from core.adapters.registry import AdapterRegistry
from core.cache.quote_cache import QuoteCache
from core.domain.markets import infer_market
from core.domain.models import Quote
from core.market_data.providers.akshare import AKShareMarketDataProvider
from core.market_metrics.index_strength import IndexStrengthMetrics, compute_index_strength
from core.market_metrics.market_width import MarketBreadthMetrics, compute_market_width
from core.market_metrics.sector_breadth import compute_sector_breadth
from core.market_rules.events import MarketRuleEvent
from core.market_rules.index_style_rules import evaluate_index_style
from core.market_rules.market_width_rules import evaluate_market_width
from core.market_rules.sector_diffusion_rules import (
    evaluate_sector_strength,
    evaluate_sector_weakness,
    sector_diffusion_label,
)
from core.market_rules.stock_mover_rules import evaluate_limit_moves, evaluate_watchlist_moves
from core.services.market_query import MarketQueryService, RankRow, SectorRow
from core.services.symbol_directory_service import SymbolDirectoryService
from core.services.watchlist_service import WatchlistService

log = structlog.get_logger(__name__)


MarketBreadth = MarketBreadthMetrics
AIPacketEvent = MarketRuleEvent


@dataclass(frozen=True, slots=True)
class AIPacketSymbol:
    symbol: str
    name: str | None
    price: float | None
    change_pct: float | None
    volume: int | None
    sectors: list[str]


@dataclass(frozen=True, slots=True)
class AIPacketSector:
    code: str
    name: str
    change_pct: float
    company_count: int
    leader_name: str
    leader_change_pct: float
    leader_symbol: str | None = None
    main_net: float | None = None
    constituents: list[AIPacketSymbol] | None = None
    up_count: int | None = None
    down_count: int | None = None
    up_ratio: float | None = None
    avg_change_pct: float | None = None
    leader_dominance_pct: float | None = None
    breadth_label: str = "成分缺失"


@dataclass(frozen=True, slots=True)
class AIPacket:
    generated_at: datetime
    market: str
    indices: list[AIPacketSymbol]
    breadth: MarketBreadth
    top_gainers: list[RankRow]
    top_losers: list[RankRow]
    hot_sectors: list[AIPacketSector]
    weak_sectors: list[AIPacketSector]
    watchlist: list[AIPacketSymbol]
    index_strength: IndexStrengthMetrics
    events: list[AIPacketEvent]
    ai_brief: dict
    degraded: list[str]


class AIMarketService:
    """生成投喂 AI 的 A 股盘面结构化数据包。

    第一版只做按需实时聚合，不落库。触发判断由程序侧完成，AI 只负责
    对结构化事实做解释、归纳和提醒。
    """

    def __init__(
        self,
        *,
        registry: AdapterRegistry,
        cache: QuoteCache,
        watchlist: WatchlistService,
        directory: SymbolDirectoryService,
        market_query: MarketQueryService,
        ttl_s: float = 45.0,
    ) -> None:
        self.registry = registry
        self.cache = cache
        self.watchlist = watchlist
        self.directory = directory
        self.market_query = market_query
        self.provider = AKShareMarketDataProvider(market_query)
        self.ttl_s = ttl_s
        self._cached: tuple[float, AIPacket] | None = None
        self._lock = asyncio.Lock()
        self._last_breadth: MarketBreadthMetrics | None = None

    async def build_ashare_packet(self) -> AIPacket:
        now = time.monotonic()
        if self._cached and self._cached[0] > now:
            return self._cached[1]
        async with self._lock:
            now = time.monotonic()
            if self._cached and self._cached[0] > now:
                return self._cached[1]
            packet = await self._build_ashare_packet_uncached()
            ttl = 5.0 if packet.degraded else self.ttl_s
            self._cached = (time.monotonic() + ttl, packet)
            return packet

    async def _build_ashare_packet_uncached(self) -> AIPacket:
        degraded: list[str] = []
        rank_rows: list[RankRow] = []
        sector_rows: list[SectorRow] = []
        watch_symbols: list[str] = []

        try:
            rank_rows = await self.provider.fetch_all_stocks(limit=5000)
        except Exception as e:  # noqa: BLE001
            degraded.append("全 A 涨跌幅/成交额快照获取失败")
            log.warning("ai_market.rank_failed", error=str(e))

        try:
            sector_rows = await self.provider.fetch_sectors()
        except Exception as e:  # noqa: BLE001
            degraded.append("行业板块实时快照获取失败")
            log.warning("ai_market.sectors_failed", error=str(e))

        try:
            watch_symbols = [
                s for s in await self.watchlist.dynamic_universe()
                if infer_market(s) == "ashare"
            ]
        except Exception as e:  # noqa: BLE001
            degraded.append("关注列表读取失败")
            log.warning("ai_market.watchlist_failed", error=str(e))

        names = await self._safe_names(watch_symbols)
        index_symbols = [
            "000001.SH",
            "399001.SZ",
            "399006.SZ",
            "000300.SH",
            "000905.SH",
            "000852.SH",
            "000688.SH",
            "000016.SH",
        ]
        index_names = await self._safe_names(index_symbols)
        quotes = await self._safe_quotes(index_symbols + watch_symbols, degraded)

        indices = [
            self._symbol_row(sym, index_names.get(sym), quotes.get(sym), [])
            for sym in index_symbols
        ]
        watch_rows = []
        for sym in watch_symbols:
            watch_rows.append(self._symbol_row(sym, names.get(sym), quotes.get(sym), []))

        hot_sectors = await self._sector_rows(sector_rows[:8])
        weak_sectors = await self._sector_rows(sorted(sector_rows, key=lambda s: s.change_pct)[:8])
        top_gainers = rank_rows[:10]
        top_losers = sorted(rank_rows, key=lambda r: r.change_pct)[:10]
        breadth = compute_market_width(rank_rows)
        index_strength = compute_index_strength(indices)
        events = self._events(
            breadth=breadth,
            previous_breadth=self._last_breadth,
            index_strength=index_strength,
            hot_sectors=hot_sectors,
            weak_sectors=weak_sectors,
            watchlist=watch_rows,
            top_gainers=top_gainers,
            top_losers=top_losers,
        )
        self._last_breadth = breadth
        ai_brief = self._ai_brief(
            breadth=breadth,
            indices=indices,
            index_strength=index_strength,
            hot_sectors=hot_sectors,
            weak_sectors=weak_sectors,
            watchlist=watch_rows,
            events=events,
            degraded=degraded,
        )
        return AIPacket(
            generated_at=datetime.now(timezone.utc),
            market="ashare",
            indices=indices,
            breadth=breadth,
            top_gainers=top_gainers,
            top_losers=top_losers,
            hot_sectors=hot_sectors,
            weak_sectors=weak_sectors,
            watchlist=watch_rows,
            index_strength=index_strength,
            events=events,
            ai_brief=ai_brief,
            degraded=degraded,
        )

    async def _safe_names(self, symbols: list[str]) -> dict[str, str]:
        if not symbols:
            return {}
        try:
            return await self.directory.get_names(symbols)
        except Exception as e:  # noqa: BLE001
            log.warning("ai_market.names_failed", error=str(e))
            return {}

    async def _safe_quotes(
        self,
        symbols: list[str],
        degraded: list[str],
    ) -> dict[str, Quote]:
        unique = list(dict.fromkeys(symbols))
        out = {
            sym: q for sym in unique
            if (q := self.cache.get("ashare", sym)) is not None
        }
        missing = [s for s in unique if s not in out]
        if not missing:
            return out
        try:
            adapter = self.registry.get("ashare")
            fetched = await adapter.fetch_snapshot(missing)
            for quote in fetched:
                self.cache.put(quote)
                out[quote.symbol] = quote
        except Exception as e:  # noqa: BLE001
            degraded.append("指数/关注股实时行情部分缺失")
            log.warning("ai_market.quotes_failed", error=str(e))
        return out

    async def _sector_rows(self, rows: list[SectorRow]) -> list[AIPacketSector]:
        out: list[AIPacketSector] = []
        for row in rows:
            constituents: list[AIPacketSymbol] = []
            rank_rows: list[RankRow] = []
            try:
                rank_rows = await self.provider.fetch_sector_constituents(row.code, limit=8)
                constituents = [
                    AIPacketSymbol(
                        symbol=r.symbol,
                        name=r.name,
                        price=r.price,
                        change_pct=r.change_pct,
                        volume=r.volume,
                        sectors=[row.name],
                    )
                    for r in rank_rows
                ]
            except Exception as e:  # noqa: BLE001
                log.warning("ai_market.constituents_failed", sector=row.name, error=str(e))
            if not constituents and row.leader_symbol:
                constituents = [AIPacketSymbol(
                    symbol=row.leader_symbol,
                    name=row.leader_name,
                    price=None,
                    change_pct=row.leader_change_pct,
                    volume=None,
                    sectors=[row.name],
                )]
            breadth = compute_sector_breadth(rank_rows)
            label = sector_diffusion_label(
                up_ratio=breadth.up_ratio if breadth.total else None,
                leader_dominance_pct=breadth.leader_dominance_pct,
            )
            out.append(AIPacketSector(
                code=row.code,
                name=row.name,
                change_pct=row.change_pct,
                company_count=row.company_count,
                leader_name=row.leader_name,
                leader_change_pct=row.leader_change_pct,
                leader_symbol=row.leader_symbol,
                constituents=constituents,
                up_count=breadth.up_count if breadth.total else None,
                down_count=breadth.down_count if breadth.total else None,
                up_ratio=breadth.up_ratio if breadth.total else None,
                avg_change_pct=breadth.avg_change_pct,
                leader_dominance_pct=breadth.leader_dominance_pct,
                breadth_label=label,
            ))
        return out

    @staticmethod
    def _symbol_row(
        symbol: str,
        name: str | None,
        quote: Quote | None,
        sectors: list[str],
    ) -> AIPacketSymbol:
        return AIPacketSymbol(
            symbol=symbol,
            name=name,
            price=float(quote.price) if quote else None,
            change_pct=quote.change_pct if quote else None,
            volume=quote.volume if quote else None,
            sectors=sectors,
        )

    @staticmethod
    def _events(
        *,
        breadth: MarketBreadth,
        previous_breadth: MarketBreadth | None,
        index_strength: IndexStrengthMetrics,
        hot_sectors: list[AIPacketSector],
        weak_sectors: list[AIPacketSector],
        watchlist: list[AIPacketSymbol],
        top_gainers: list[RankRow],
        top_losers: list[RankRow],
    ) -> list[AIPacketEvent]:
        events: list[AIPacketEvent] = []
        events.extend(evaluate_market_width(breadth, previous=previous_breadth))
        events.extend(evaluate_index_style(index_strength))
        events.extend(evaluate_sector_strength(hot_sectors))
        events.extend(evaluate_sector_weakness(weak_sectors))
        events.extend(evaluate_watchlist_moves(watchlist))
        events.extend(evaluate_limit_moves(
            breadth=breadth,
            top_gainers=top_gainers,
            top_losers=top_losers,
        ))
        return sorted(events, key=lambda e: e.score, reverse=True)[:12]

    @staticmethod
    def _ai_brief(
        *,
        breadth: MarketBreadth,
        indices: list[AIPacketSymbol],
        index_strength: IndexStrengthMetrics,
        hot_sectors: list[AIPacketSector],
        weak_sectors: list[AIPacketSector],
        watchlist: list[AIPacketSymbol],
        events: list[AIPacketEvent],
        degraded: list[str],
    ) -> dict:
        return {
            "task": "请基于 A 股盘面数据，输出 5 分钟级别市场总结、主线判断、风险点和需要继续盯盘的标的；不要给交易指令。",
            "market": "ashare",
            "breadth": asdict(breadth),
            "indices": [asdict(i) for i in indices],
            "index_strength": asdict(index_strength),
            "hot_sectors": [asdict(s) for s in hot_sectors[:5]],
            "weak_sectors": [asdict(s) for s in weak_sectors[:5]],
            "watchlist": [asdict(w) for w in watchlist],
            "events": [asdict(e) for e in events],
            "data_gaps": degraded,
        }
