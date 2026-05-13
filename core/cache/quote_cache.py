from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock

from core.domain.models import Quote


@dataclass
class _Entry:
    quote: Quote
    expires_at: float


class QuoteCache:
    def __init__(self, ttl_s: float = 60.0) -> None:
        self.ttl_s = ttl_s
        self._store: dict[tuple[str, str], _Entry] = {}
        self._lock = RLock()

    def put(self, quote: Quote) -> None:
        with self._lock:
            self._store[(quote.market, quote.symbol)] = _Entry(
                quote=quote, expires_at=time.monotonic() + self.ttl_s,
            )

    def get(self, market: str, symbol: str) -> Quote | None:
        with self._lock:
            entry = self._store.get((market, symbol))
            if entry is None:
                return None
            if entry.expires_at < time.monotonic():
                del self._store[(market, symbol)]
                return None
            return entry.quote

    def snapshot(self, market: str) -> list[Quote]:
        now = time.monotonic()
        with self._lock:
            stale = [k for k, v in self._store.items() if v.expires_at < now]
            for k in stale:
                del self._store[k]
            return [v.quote for k, v in self._store.items() if k[0] == market]
