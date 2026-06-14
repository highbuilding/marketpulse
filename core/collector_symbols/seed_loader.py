from __future__ import annotations

import structlog

from core.domain.core_symbols import core_symbols
from core.domain.markets import infer_market
from core.domain.models import CollectorSymbol
from core.persistence.collector_symbol_repo import CollectorSymbolRepo
from core.persistence.theme_repo import ThemeRepo
from core.services.symbol_directory_service import SymbolDirectoryService

log = structlog.get_logger(__name__)


async def bootstrap_ashare_collector_symbols(
    repo: CollectorSymbolRepo,
    *,
    theme_repo: ThemeRepo | None = None,
    directory: SymbolDirectoryService | None = None,
) -> int:
    rows: list[CollectorSymbol] = []
    names: dict[str, str] = {}
    symbols = set(core_symbols("ashare"))
    seed_symbols: set[str] = set()
    if theme_repo is not None:
        try:
            definitions = await theme_repo.list_definitions("ashare", include_disabled=False)
            for d in definitions:
                for c in await theme_repo.list_static_constituents(
                    "ashare", d.theme_code, include_disabled=False,
                ):
                    if infer_market(c.symbol) == "ashare":
                        seed_symbols.add(c.symbol)
                        if c.name:
                            names[c.symbol] = c.name
            symbols |= seed_symbols
        except Exception as e:  # noqa: BLE001
            log.warning("collector_symbols.seed_theme_failed", error=str(e))
    if directory is not None:
        try:
            names |= await directory.get_names(sorted(symbols))
        except Exception as e:  # noqa: BLE001
            log.warning("collector_symbols.seed_names_failed", error=str(e))
    for symbol in sorted(symbols):
        rows.append(CollectorSymbol(
            market="ashare",
            symbol=symbol,
            name=names.get(symbol),
            source="core" if symbol in set(core_symbols("ashare")) else "seed",
            enabled=True,
            collect_snapshot=True,
            collect_5m=True,
            collect_signals=True,
        ))
    inserted = await repo.seed_symbols(rows)
    log.info("collector_symbols.seed_done", market="ashare", inserted=inserted, total=len(rows))
    return inserted
