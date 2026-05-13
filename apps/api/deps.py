from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from core.adapters.registry import AdapterRegistry, load_sources_config
from core.cache.quote_cache import QuoteCache
from core.persistence.duckdb_repo import BarRepo
from core.persistence.sqlite_repo import StateRepo


_BASE = Path(__file__).resolve().parents[2]
_CONFIG = _BASE / "config" / "sources.yaml"
_DATA = Path(os.getenv("APP_DATA_DIR", str(_BASE / "data")))


@lru_cache(maxsize=1)
def get_registry() -> AdapterRegistry:
    return AdapterRegistry.from_config(load_sources_config(str(_CONFIG)))


@lru_cache(maxsize=1)
def get_quote_cache() -> QuoteCache:
    return QuoteCache(ttl_s=60)


@lru_cache(maxsize=1)
def get_bar_repo() -> BarRepo:
    repo = BarRepo(str(_DATA / "bars.duckdb"))
    repo.init()
    return repo


@lru_cache(maxsize=1)
def get_state_repo() -> StateRepo:
    return StateRepo(str(_DATA / "state.db"))
