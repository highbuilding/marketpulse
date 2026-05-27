"""Redis key 命名空间集中定义。

所有 Redis key 必须经过这里的构造函数,直接拼字符串视为违规。
key 必须至少 2 段 (namespace:scope),namespace 限定为
{cache, state, bus, ratelimit} 四个之一。

参考: docs/superpowers/specs/2026-05-27-stable-data-collection-and-fast-read-design.md §2.1
"""
from __future__ import annotations

# === 命名空间 ===
NS_CACHE = "cache"
NS_STATE = "state"
NS_BUS = "bus"
NS_RATELIMIT = "ratelimit"
_VALID_NS = {NS_CACHE, NS_STATE, NS_BUS, NS_RATELIMIT}

# === bus topic 常量(Streams 名) ===
BUS_QUOTE_TICK = "bus:quote.tick"
BUS_BARS_UPDATED = "bus:bars.updated"
BUS_SIGNAL_NEW = "bus:signal.new"
BUS_SOURCE_STATUS = "bus:source.status"
BUS_BARS_REFILL_REQUEST = "bus:bars.refill_request"


# === cache: 热缓存层 ===
def cache_quote(market: str, symbol: str) -> str:
    return f"cache:quote:{market}:{symbol}"


def cache_index_minute(symbol: str, *, days: int) -> str:
    return f"cache:index:{symbol}:minute:{days}"


def cache_market_dashboard(market: str) -> str:
    return f"cache:market:{market}:dashboard"


def cache_bars_tail(market: str, symbol: str, interval: str) -> str:
    return f"cache:bars:{market}:{symbol}:{interval}:tail"


def cache_bars_full(market: str, symbol: str, interval: str, fingerprint: str) -> str:
    return f"cache:bars:{market}:{symbol}:{interval}:full:{fingerprint}"


def cache_fundflow(symbol: str, *, days: int) -> str:
    return f"cache:fundflow:{symbol}:{days}d"


# === state: 状态/锁 ===
def state_leader_collector() -> str:
    return "state:leader:collector"


def state_source(name: str) -> str:
    return f"state:source:{name}"


def state_outlet(outlet_id: str) -> str:
    return f"state:outlet:{outlet_id}"


def state_inflight(key: str) -> str:
    return f"state:inflight:{key}"


# === ratelimit: 限速器 ===
def ratelimit_source(source: str) -> str:
    return f"ratelimit:source:{source}"


def ratelimit_outlet(outlet_id: str) -> str:
    return f"ratelimit:outlet:{outlet_id}"


# === 校验 ===
def validate(key: str) -> None:
    """校验 key 命名规范。违规 raise ValueError。

    用于 redis_client 写入前的 assertion,catch 散点拼字符串。
    """
    parts = key.split(":")
    if len(parts) < 2:
        raise ValueError(f"key must be at least 2 segments: {key!r}")
    ns = parts[0]
    if ns not in _VALID_NS:
        raise ValueError(f"unknown namespace {ns!r} in key {key!r}; "
                         f"allowed: {sorted(_VALID_NS)}")
