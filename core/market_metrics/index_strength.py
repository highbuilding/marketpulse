from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class IndexLike(Protocol):
    symbol: str
    name: str | None
    change_pct: float | None


@dataclass(frozen=True, slots=True)
class IndexStrengthMetrics:
    ranking: list[dict]
    small_vs_large_pct: float | None
    growth_vs_large_pct: float | None


def compute_index_strength(indices: list[IndexLike]) -> IndexStrengthMetrics:
    values = {
        item.symbol: item.change_pct
        for item in indices
        if item.change_pct is not None
    }
    ranking = [
        {
            "symbol": item.symbol,
            "name": item.name,
            "change_pct": item.change_pct,
        }
        for item in sorted(
            indices,
            key=lambda i: i.change_pct if i.change_pct is not None else -999.0,
            reverse=True,
        )
    ]
    small = values.get("000852.SH")
    large = values.get("000016.SH") or values.get("000300.SH")
    growth = values.get("399006.SZ")
    return IndexStrengthMetrics(
        ranking=ranking,
        small_vs_large_pct=(small - large) if small is not None and large is not None else None,
        growth_vs_large_pct=(growth - large) if growth is not None and large is not None else None,
    )

