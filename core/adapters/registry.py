from __future__ import annotations

from typing import Any

import yaml

from core.adapters.ashare import AShareAdapter
from core.adapters.base import MarketAdapter
from core.adapters.crypto import CryptoAdapter
from core.adapters.hk import HKAdapter
from core.adapters.us import USAdapter


_BUILDERS = {
    "ashare": AShareAdapter,
    "hk": HKAdapter,
    "us": USAdapter,
    "crypto": CryptoAdapter,
}


def load_sources_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class AdapterRegistry:
    def __init__(self, adapters: dict[str, MarketAdapter], universes: dict[str, list[str]],
                 indices: dict[str, list[str]]) -> None:
        self._adapters = adapters
        self._universes = universes
        self._indices = indices

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "AdapterRegistry":
        adapters: dict[str, MarketAdapter] = {}
        universes: dict[str, list[str]] = {}
        indices: dict[str, list[str]] = {}
        for market, m in (cfg.get("markets") or {}).items():
            if not m.get("enabled"):
                continue
            if market not in _BUILDERS:
                continue
            adapters[market] = _BUILDERS[market]()
            universes[market] = list(m.get("default_universe") or [])
            indices[market] = list(m.get("index_symbols") or [])
        return cls(adapters, universes, indices)

    def markets(self) -> list[str]:
        return list(self._adapters.keys())

    def get(self, market: str) -> MarketAdapter:
        return self._adapters[market]

    def universe(self, market: str) -> list[str]:
        return self._universes.get(market, [])

    def index_symbols(self, market: str) -> list[str]:
        return self._indices.get(market, [])
