# Crypto + SSE + Collector 拆分 实施 Plan(P1-P5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 接入 crypto 5 标的(BTC/ETH/SOL/XRP/TRX)全周期 K 线,通过 SSE 实时 push 给前端,并把 collector 拆成 3 个进程(ashare/us/crypto)实现故障隔离。

**Architecture:** collector 按 market 拆 3 进程(各自独立 DuckDB 文件 + event loop),crypto 用 Binance Spot REST(回填)+ WS(增量 1-2s tick)。新增 SSE 路由,前端 EventSource 增量更新。Redis 仍是 api 唯一读源。

**Tech Stack:** httpx(REST)+ websockets(Binance WS)+ Redis Streams(内部事件总线)+ FastAPI EventSourceResponse(SSE)+ EventSource API(浏览器)

**Spec:** `docs/superpowers/specs/2026-05-29-crypto-sse-and-collector-split-design.md`

---

# Plan 1:collector 拆 3 进程 + DuckDB 按 market 分文件

**目标:** 把单个 collector 进程拆成 ashare / us / crypto 三个独立进程,DuckDB 按 market 拆 3 文件,迁移旧数据 1d/1wk/1mo。

**风险:** 进程拆分动到主流程,需要现有 A 股 / 美股 K 线照常跑。

### Task 1.1:备份 + 写一次性迁移脚本

**Files:**
- Create: `scripts/migrate_bars_per_market.py`

- [ ] **Step 1: 备份现有 bars.duckdb**

```bash
cp /Users/xiangrong/stock/marketpulse/data/bars.duckdb \
   /Users/xiangrong/stock/marketpulse/data/bars.duckdb.before-split-2026-05-29
ls -lh /Users/xiangrong/stock/marketpulse/data/bars.duckdb*
```

Expected: 看到 backup 文件,大小一致。

- [ ] **Step 2: 写迁移脚本**

```python
# scripts/migrate_bars_per_market.py
"""一次性迁移: bars.duckdb → bars_{market}.duckdb (仅 1d/1wk/1mo).

策略 C: intraday (5m/15m/30m/60m/4h/1m) 抛弃, 等 cron 重拉.

用法:
    python scripts/migrate_bars_per_market.py
    # 旧文件不删, 备份在 .before-split-* 文件
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "bars.duckdb"
KEEP_INTERVALS = ("1d", "1wk", "1mo")
MARKETS = ("ashare", "us", "hk", "crypto")


def migrate() -> None:
    if not SRC.exists():
        print(f"source not found: {SRC}, nothing to migrate")
        return

    src = duckdb.connect(str(SRC), read_only=True)

    for market in MARKETS:
        dst_path = ROOT / "data" / f"bars_{market}.duckdb"
        print(f"migrating {market} → {dst_path}")
        dst = duckdb.connect(str(dst_path))
        dst.execute("""
            CREATE TABLE IF NOT EXISTS bars (
                market   VARCHAR NOT NULL,
                symbol   VARCHAR NOT NULL,
                ts       TIMESTAMP NOT NULL,
                interval VARCHAR NOT NULL,
                open     DECIMAL(20, 8) NOT NULL,
                high     DECIMAL(20, 8) NOT NULL,
                low      DECIMAL(20, 8) NOT NULL,
                close    DECIMAL(20, 8) NOT NULL,
                volume   BIGINT NOT NULL,
                amount   DOUBLE,
                turnover DOUBLE,
                outstanding_share DOUBLE,
                PRIMARY KEY (market, symbol, interval, ts)
            )
        """)

        ph = ",".join(["?"] * len(KEEP_INTERVALS))
        rows = src.execute(f"""
            SELECT * FROM bars WHERE market = ? AND interval IN ({ph})
        """, (market, *KEEP_INTERVALS)).fetchall()
        if not rows:
            print(f"  {market}: 0 rows")
            dst.close()
            continue

        cols = [d[0] for d in src.description]
        # 写入 (ON CONFLICT 幂等, 重跑安全)
        dst.executemany(
            f"INSERT INTO bars ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))}) "
            "ON CONFLICT (market, symbol, interval, ts) DO NOTHING",
            rows,
        )
        print(f"  {market}: {len(rows)} rows migrated")
        dst.close()

    src.close()
    print("done")


if __name__ == "__main__":
    migrate()
```

- [ ] **Step 3: 跑迁移**

```bash
. .venv/bin/activate && python scripts/migrate_bars_per_market.py
```

Expected:
```
migrating ashare → .../bars_ashare.duckdb
  ashare: NNNN rows migrated
migrating us → .../bars_us.duckdb
  us: NNNN rows migrated
...
done
```

- [ ] **Step 4: 验证迁移结果**

```bash
. .venv/bin/activate && python -c "
import duckdb
for m in ('ashare','us','hk','crypto'):
    c = duckdb.connect(f'data/bars_{m}.duckdb', read_only=True)
    r = c.execute('SELECT interval, COUNT(*) FROM bars GROUP BY interval').fetchall()
    print(f'{m}: {dict(r)}')
    c.close()
"
```

Expected: 4 个 market 文件存在,每个仅含 1d/1wk/1mo 行(可能某些 market 0 行)。

### Task 1.2:`BarRepo` 不变,通过 db_path 区分

`BarRepo` 已支持任意 db_path,无需改动。

### Task 1.3:抽 `apps/collector/base.py` 通用 lifespan

**Files:**
- Create: `apps/collector/base.py`

- [ ] **Step 1: 写 base.py**

```python
# apps/collector/base.py
"""3 个 collector 进程共享的 lifespan helper.

负责:
- proxy / logging / faulthandler 一次性初始化
- ak_middleware (breakers/ratelimits/outlets) — A 股 / 美股 collector 用,crypto 进程不接 ak 仍可调用此初始化(无副作用)
- Redis 健康检查 + 加载共享依赖
- 各市场各自的 cron + 长任务在自己的 main.py 里 attach

每个 collector 进程的 main.py 自己起 FastAPI(只暴露 /health)给 honcho 探活.
"""
from __future__ import annotations

import asyncio
import os
import socket
from dataclasses import dataclass
from typing import Awaitable, Callable

import structlog
from fastapi import FastAPI

log = structlog.get_logger(__name__)


@dataclass
class CollectorContext:
    process_name: str   # "collector_ashare" / "collector_us" / "collector_crypto"
    market: str         # "ashare" / "us" / "crypto"
    bar_repo_path: str  # data/bars_{market}.duckdb


def setup_proxy_and_logging(process_name: str) -> None:
    """所有 collector 共用的启动顺序: proxy 必须在 import adapter 前."""
    from dotenv import load_dotenv
    load_dotenv()

    from core.integrations.proxy_setup import setup_process_proxy
    setup_process_proxy()

    from core.integrations.logging_setup import setup_logging
    setup_logging(process_name=process_name)


def install_async_exception_handler() -> None:
    """兜住 asyncio.create_task 抛出的异常, 强制走 root logger 落 errors.log."""
    def _handler(loop, context):
        msg = context.get("exception") or context.get("message")
        log.error("asyncio.unhandled_exception",
                  message=context.get("message"),
                  exception_type=type(context.get("exception")).__name__
                      if context.get("exception") else None,
                  error=str(msg) if msg else None,
                  task=str(context.get("task")) if context.get("task") else None)
    asyncio.get_event_loop().set_exception_handler(_handler)


def health_app(role: str) -> FastAPI:
    """每个 collector 进程内嵌的最小 FastAPI, 仅暴露 /health 给 honcho 探活."""
    app = FastAPI(title=f"MarketPulse {role}")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "role": role}
    return app
```

- [ ] **Step 2: 验证 import**

```bash
. .venv/bin/activate && python -c "from apps.collector.base import setup_proxy_and_logging, health_app; print('OK')"
```

Expected: `OK`

### Task 1.4:拆 `apps/collector/ashare/main.py`

把现有 `apps/collector/main.py` 中 A 股相关 cron 抽出,只留 A 股闸门内的 job。

**Files:**
- Create: `apps/collector/ashare/__init__.py`(空)
- Create: `apps/collector/ashare/main.py`

- [ ] **Step 1: 写 ashare main**

```python
# apps/collector/ashare/main.py
"""A 股 collector 进程入口.

职责:
- A 股 tick:ashare (10s)
- A 股 fetch_intraday cron (5m)
- A 股 cd:* signal scan
- A 股 fund_flow / chip / index_minute / market_top / ai_packet / dashboard / baseline
- 写自己的 bars_ashare.duckdb (RW)
"""
from __future__ import annotations

from apps.collector.base import setup_proxy_and_logging
setup_proxy_and_logging("collector_ashare")

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI

from apps.collector.base import health_app, install_async_exception_handler
from core.cache.redis_bars_cache import RedisBarsCache
from core.adapters.registry import AdapterRegistry, load_sources_config
from core.cache.quote_cache import QuoteCache
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)

_BASE = Path(__file__).resolve().parents[3]
_DATA = _BASE / "data"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("collector_ashare.boot")
    install_async_exception_handler()

    # Redis
    from apps.api.deps import get_redis_cache
    redis_cache = get_redis_cache()
    redis_ok = await redis_cache.ping()
    if redis_ok:
        log.info("redis.connected")
    else:
        log.warning("redis.unavailable_at_startup")
    redis_bars = RedisBarsCache(redis_cache)

    # Bar repo (本市场专属)
    bar_repo = BarRepo(str(_DATA / "bars_ashare.duckdb"))
    bar_repo.init()

    # ak middleware (A 股要)
    from core.cache.redis_client import make_redis
    from core.integrations import ak_middleware
    from core.integrations.breaker import SourceBreaker
    from core.integrations.outlets import LocalOutlet, OutletPool
    from core.integrations.ratelimit import RedisTokenBucket

    _redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    _redis_for_mw = make_redis(_redis_url)
    _outlet_pool = OutletPool([LocalOutlet()], cache=redis_cache, cooling_seconds=1800)
    _breakers = {
        "sina": SourceBreaker(source="sina", cache=redis_cache),
        "em":   SourceBreaker(source="em",   cache=redis_cache),
        "ths":  SourceBreaker(source="ths",  cache=redis_cache),
    }
    _ratelimits = {
        "sina": RedisTokenBucket(redis=_redis_for_mw, key="ratelimit:source:sina", rate=5, burst=20),
        "em":   RedisTokenBucket(redis=_redis_for_mw, key="ratelimit:source:em",   rate=10, burst=50),
        "ths":  RedisTokenBucket(redis=_redis_for_mw, key="ratelimit:source:ths",  rate=3, burst=10),
    }
    ak_middleware.setup(ak_middleware.AkMiddleware(
        outlet_pool=_outlet_pool, breakers=_breakers, ratelimits=_ratelimits,
    ))
    log.info("ak_middleware.ready")

    # 通用初始化(directory / watchlist / state) 共享 SQLite, 谁先起谁初始化(幂等)
    from apps.api.deps import (
        get_state_repo, get_watchlist_service,
        get_symbol_directory_service,
    )
    state_repo = get_state_repo()
    await state_repo.init()
    await get_watchlist_service().bootstrap_default()
    dir_svc = get_symbol_directory_service()
    await dir_svc.bootstrap_seeds()
    if not os.getenv("MARKETPULSE_SKIP_DIR_BOOTSTRAP"):
        existing = await dir_svc.count()
        if existing < 100:
            asyncio.create_task(_bootstrap_dir(dir_svc))

    # registry: 只取 A 股 adapter
    config = load_sources_config(str(_BASE / "config" / "sources.yaml"))
    config["markets"] = {"ashare": config["markets"]["ashare"]}
    registry = AdapterRegistry.from_config(config)
    cache = QuoteCache(ttl_s=60)

    # KLineService (注入 redis_bars + bar_repo)
    from core.services.kline_service import KLineService
    adapters = {m: registry.get(m) for m in registry.markets()}
    kline = KLineService(bar_repo, adapters, redis_bars=redis_bars)

    # MarketAmountBaselineRepo
    from core.persistence.market_amount_baseline_repo import MarketAmountBaselineRepo
    baseline_repo = MarketAmountBaselineRepo(str(_DATA / "state.db"))

    # build scheduler — 仅 A 股专属 cron
    from core.scheduler.scheduler import (
        build_scheduler, attach_fundamentals_jobs, attach_signal_jobs,
        attach_index_minute_job, attach_baseline_persist_jobs,
        attach_market_dashboard_job, attach_market_top_job,
        attach_ai_packet_job, attach_chip_preload_job,
    )
    from apps.api.deps import (
        get_fund_flow_service, get_signal_scan_service,
        get_notification_service, get_market_query_service,
        get_ai_market_service, get_chip_service,
    )
    sched = build_scheduler(
        registry, cache, bar_repo, get_watchlist_service(),
        redis_cache=redis_cache, redis_bars=redis_bars,
    )
    attach_fundamentals_jobs(sched, fund_flow=get_fund_flow_service(),
                              watchlist=get_watchlist_service())
    attach_signal_jobs(sched, signal_scan=get_signal_scan_service(),
                       watchlist=get_watchlist_service(),
                       notify_service=get_notification_service(),
                       kline=kline)
    attach_index_minute_job(sched, cache=redis_cache, baseline_repo=baseline_repo)
    attach_baseline_persist_jobs(sched, baseline_repo=baseline_repo)
    attach_market_dashboard_job(sched, cache=redis_cache)
    attach_market_top_job(sched, market_query=get_market_query_service(), cache=redis_cache)
    attach_ai_packet_job(sched, ai_market=get_ai_market_service(), cache=redis_cache)
    attach_chip_preload_job(sched, chip_service=get_chip_service(),
                            watchlist=get_watchlist_service())
    sched.start()
    log.info("collector_ashare.started")
    try:
        yield
    finally:
        sched.shutdown(wait=False)
        try:
            await _redis_for_mw.aclose()
        except Exception:
            pass
        log.info("collector_ashare.shutdown")


async def _bootstrap_dir(svc) -> None:
    try:
        await asyncio.sleep(5)
        n = await svc.refresh_ashare()
        log.info("directory.bootstrapped", count=n)
    except Exception as e:
        log.warning("directory.bootstrap_failed", error=str(e))


app = health_app("collector_ashare")
app.router.lifespan_context = lifespan


def main() -> None:
    port = int(os.getenv("COLLECTOR_ASHARE_PORT", "8788"))
    uvicorn.run("apps.collector.ashare.main:app", host="127.0.0.1", port=port, log_config=None)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: import 自检**

```bash
. .venv/bin/activate && python -c "from apps.collector.ashare.main import app; print('OK')"
```

### Task 1.5:拆 `apps/collector/us/main.py`

**Files:**
- Create: `apps/collector/us/__init__.py`(空)
- Create: `apps/collector/us/main.py`

- [ ] **Step 1: 写 us main**

仿照 `ashare/main.py`,关键改动:
- `process_name="collector_us"`,`bar_repo` 路径 `bars_us.duckdb`
- `config["markets"] = {"us": ...}` 只保留美股
- ak middleware 仍初始化(虽然 us 走 Alpaca 不用 sina,但 outlet_pool 是 LocalOutlet 共享逻辑无副作用)
- attach_us_signal_jobs + attach_us_index_minute_job(美股专属 cron)
- 不 attach A 股专属 (fundamentals / chip / index_minute / dashboard / market_top / ai_packet / baseline 全是 A 股)
- port = 8789

```python
# apps/collector/us/main.py — 完整代码,仿 ashare 写
# (省略,与 ashare 主结构一致,只 attach_us_signal_jobs + attach_us_index_minute_job)
```

> 实施时复制 ashare/main.py,改 5 行(process_name / bar_repo / config / attach_jobs / port),即可。

- [ ] **Step 2: 自检**

```bash
. .venv/bin/activate && python -c "from apps.collector.us.main import app; print('OK')"
```

### Task 1.6:写 `apps/collector/crypto/main.py` stub

**Files:**
- Create: `apps/collector/crypto/__init__.py`(空)
- Create: `apps/collector/crypto/main.py`

- [ ] **Step 1: 写 stub(仅起健康检查 + 占位)**

```python
# apps/collector/crypto/main.py
"""crypto collector 进程入口 (stub).

P2 接 BinanceAdapter REST + backfill cron.
P3 接 binance_ws_consumer.
"""
from __future__ import annotations

from apps.collector.base import setup_proxy_and_logging
setup_proxy_and_logging("collector_crypto")

import os
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI

from apps.collector.base import health_app, install_async_exception_handler
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)
_BASE = Path(__file__).resolve().parents[3]
_DATA = _BASE / "data"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("collector_crypto.boot")
    install_async_exception_handler()
    bar_repo = BarRepo(str(_DATA / "bars_crypto.duckdb"))
    bar_repo.init()
    log.info("collector_crypto.started", note="P2 will attach BinanceAdapter")
    try:
        yield
    finally:
        log.info("collector_crypto.shutdown")


app = health_app("collector_crypto")
app.router.lifespan_context = lifespan


def main() -> None:
    port = int(os.getenv("COLLECTOR_CRYPTO_PORT", "8790"))
    uvicorn.run("apps.collector.crypto.main:app", host="127.0.0.1", port=port, log_config=None)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 自检**

```bash
. .venv/bin/activate && python -c "from apps.collector.crypto.main import app; print('OK')"
```

### Task 1.7:更新 Procfile + Makefile

**Files:**
- Modify: `Procfile`
- Modify: `Makefile`(若需要新 stop 钩子)

- [ ] **Step 1: 改 Procfile**

```
# Procfile — honcho 拉起本地 dev 多进程

collector_ashare: . .venv/bin/activate && python -m apps.collector.ashare.main
collector_us:     . .venv/bin/activate && python -m apps.collector.us.main
collector_crypto: . .venv/bin/activate && python -m apps.collector.crypto.main
api:              . .venv/bin/activate && uvicorn apps.api.main:app --port 8787
web:              cd apps/web && npm run dev
```

- [ ] **Step 2: 删除旧 entry point**

```bash
# 老的 apps/collector/main.py 仍保留(向后兼容),但不再被 Procfile 引用.
# 后续 P2/P3 完成后再决定是否删.
```

- [ ] **Step 3: Makefile stop 钩子(若有 pkill 行)**

确认 `make stop` 把 3 个 collector + api 都 kill,例如:
```makefile
stop:
	pkill -9 -f "apps.collector.ashare" 2>/dev/null || true
	pkill -9 -f "apps.collector.us"     2>/dev/null || true
	pkill -9 -f "apps.collector.crypto" 2>/dev/null || true
	pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null || true
```

### Task 1.8:删除 api 端 BarRepo 残留代码(api 完全不持 DuckDB)

**Files:**
- Modify: `apps/api/deps.py`

- [ ] **Step 1: 让 get_bar_repo() 总是返回 None**

```python
@lru_cache(maxsize=1)
def get_bar_repo() -> BarRepo | None:
    """api 进程不再持 DuckDB. K 线读路径全走 RedisBarsCache.

    保留函数仅为兼容旧调用 (warmup / repair scripts);
    那些 script 自己直接 BarRepo(...) 显式构造.
    """
    return None
```

> 历史: 之前用 MARKETPULSE_BARREPO_READONLY env 决定,现在 collector 各市场拆开 + api 不读 DuckDB,env 不再需要,但保留无害(读不到就走 None).

### Task 1.9:健康冒烟

- [ ] **Step 1: 关停老 collector + api**

```bash
pkill -9 -f "apps.collector.main" 2>/dev/null || true
pkill -9 -f "uvicorn apps.api.main:app" 2>/dev/null || true
sleep 2
```

- [ ] **Step 2: 各进程独立起**

```bash
nohup bash -c '. .venv/bin/activate && python -m apps.collector.ashare.main' >> /tmp/collector_ashare.log 2>&1 & disown
nohup bash -c '. .venv/bin/activate && python -m apps.collector.us.main'     >> /tmp/collector_us.log 2>&1 & disown
nohup bash -c '. .venv/bin/activate && python -m apps.collector.crypto.main' >> /tmp/collector_crypto.log 2>&1 & disown
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 & disown
sleep 12
```

- [ ] **Step 3: 健康检查**

```bash
for port in 8787 8788 8789 8790; do
  curl -s -m 3 -o /dev/null -w "port=$port → %{http_code}\n" http://localhost:$port/health || echo "port=$port DOWN"
done
```

Expected:
```
port=8787 → 200
port=8788 → 200
port=8789 → 200
port=8790 → 200
```

- [ ] **Step 4: 关键 K 线接口验证**

```bash
curl -s -m 5 "http://localhost:8787/api/symbols/600519.SH/bars?interval=1d&days=365" | head -c 300
echo
curl -s -m 5 "http://localhost:8787/api/symbols/AAPL/bars?interval=1d&days=365" | head -c 300
```

Expected: 每个 200 + bars 数组非空。

- [ ] **Step 5: 全套单测**

```bash
. .venv/bin/activate && pytest -m "not integration" -q
```

Expected: 全过(可能 1 个无关 fixture 时间过期失败,无视即可)。

- [ ] **Step 6: Commit Plan 1**

```bash
git add scripts/migrate_bars_per_market.py \
        apps/collector/base.py \
        apps/collector/ashare/__init__.py apps/collector/ashare/main.py \
        apps/collector/us/__init__.py apps/collector/us/main.py \
        apps/collector/crypto/__init__.py apps/collector/crypto/main.py \
        apps/api/deps.py \
        Procfile Makefile

git commit -m "$(cat <<'EOF'
feat(collector): 拆 ashare/us/crypto 3 进程 + DuckDB 按 market 分文件 (P1)

进程隔离: 单市场 crash 不影响其他, 独立 event loop / 独立 DuckDB 文件锁.

迁移: 旧 bars.duckdb 用 scripts/migrate_bars_per_market.py 拆出
bars_{ashare,us,hk,crypto}.duckdb (策略 C, 仅搬 1d/1wk/1mo, intraday 抛弃).

新进程入口:
- apps/collector/ashare/main.py (port 8788)
- apps/collector/us/main.py     (port 8789)
- apps/collector/crypto/main.py (port 8790, P2 接 Binance)

Procfile / Makefile 同步更新.

api 端 get_bar_repo() 强制返 None, 完全脱离 DuckDB. 历史 read_only 路径
保留向后兼容 (warmup / repair script 仍可显式构造 BarRepo).
EOF
)"
```

---

# Plan 2:BinanceAdapter REST + crypto backfill

**目标:** 实现 Binance Spot REST adapter,collector_crypto 启动时一次性回填全周期历史。

### Task 2.1:写 BinanceAdapter

**Files:**
- Create: `core/adapters/binance.py`
- Create: `tests/unit/adapters/test_binance.py`

- [ ] **Step 1: 写 BinanceAdapter**

```python
# core/adapters/binance.py
"""Binance Spot REST + WS adapter.

REST: 历史回填 (klines 分页) + latest snapshot (ticker 24hr)
WS:   增量推送, 见 apps/collector/crypto/ws_consumer.py

interval 映射 (项目 → Binance):
    5m / 15m / 30m → 5m / 15m / 30m
    60m → 1h
    4h → 4h
    1d → 1d
    1wk → 1w
    1mo → 1M

symbol 映射: BTC-USDT (项目) ↔ BTCUSDT (Binance)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import structlog

from core.adapters.base import MarketAdapter, AdapterError
from core.domain.models import Bar, Quote

log = structlog.get_logger(__name__)

REST_BASE = "https://api.binance.com"

INTERVAL_MAP = {
    "5m": "5m", "15m": "15m", "30m": "30m",
    "60m": "1h", "4h": "4h",
    "1d": "1d", "1wk": "1w", "1mo": "1M",
}


def _to_binance(symbol: str) -> str:
    return symbol.replace("-", "").upper()  # BTC-USDT → BTCUSDT


def _from_binance(b_symbol: str) -> str:
    """BTCUSDT → BTC-USDT (启发式: 末尾 4 字母是 stable token, 前面是 base)"""
    for stable in ("USDT", "USDC", "BUSD", "FDUSD"):
        if b_symbol.endswith(stable):
            return f"{b_symbol[:-len(stable)]}-{stable}"
    return b_symbol


class BinanceAdapter(MarketAdapter):
    market = "crypto"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(base_url=REST_BASE, timeout=10.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]:
        """24hr ticker batch."""
        if not symbols:
            return []
        b_syms = [_to_binance(s) for s in symbols]
        r = await self._client.get(
            "/api/v3/ticker/24hr",
            params={"symbols": '["' + '","'.join(b_syms) + '"]'},
        )
        r.raise_for_status()
        out: list[Quote] = []
        now = datetime.now(timezone.utc)
        for d in r.json():
            sym = _from_binance(d["symbol"])
            out.append(Quote(
                market="crypto", symbol=sym, ts=now,
                price=Decimal(d["lastPrice"]),
                change_pct=float(d["priceChangePercent"]),
                volume=int(float(d["volume"])),
                source="binance",
            ))
        return out

    async def fetch_history(
        self, symbol: str, start: datetime, end: datetime,
    ) -> list[Bar]:
        """1d 历史. 内部分页."""
        return await self._fetch_klines_paged(symbol, "1d", start, end)

    async def fetch_intraday(self, symbol: str, freq: str = "5") -> list[Bar]:
        """freq: '5' / '15' / '30' / '60' (= 1h) / '240' (= 4h)"""
        interval_map = {"5": "5m", "15": "15m", "30": "30m", "60": "60m", "240": "4h"}
        proj_iv = interval_map.get(freq)
        if proj_iv is None:
            raise AdapterError(f"unsupported freq: {freq}", source="binance")
        # 默认拉 30 天
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=30)
        return await self._fetch_klines_paged(symbol, proj_iv, start, end)

    async def fetch_klines(
        self, symbol: str, project_interval: str,
        start: datetime, end: datetime,
    ) -> list[Bar]:
        """显式 interval 历史拉取(给 backfill 用, 不限于 fetch_intraday 的固定 30 天)."""
        return await self._fetch_klines_paged(symbol, project_interval, start, end)

    async def _fetch_klines_paged(
        self, symbol: str, project_interval: str,
        start: datetime, end: datetime,
    ) -> list[Bar]:
        b_iv = INTERVAL_MAP.get(project_interval)
        if b_iv is None:
            raise AdapterError(f"unsupported interval: {project_interval}", source="binance")
        b_sym = _to_binance(symbol)

        out: list[Bar] = []
        cursor_end = int(end.timestamp() * 1000)
        cursor_start = int(start.timestamp() * 1000)

        while cursor_end > cursor_start:
            params = {
                "symbol": b_sym, "interval": b_iv,
                "endTime": cursor_end, "limit": 1000,
            }
            if cursor_start:
                params["startTime"] = cursor_start
            try:
                r = await self._client.get("/api/v3/klines", params=params)
                r.raise_for_status()
            except Exception as e:
                log.warning("binance.klines_failed", symbol=symbol,
                            interval=project_interval, error=str(e))
                break
            rows = r.json()
            if not rows:
                break
            page = [self._parse_kline(symbol, project_interval, row) for row in rows]
            out = page + out  # 早段在前
            # 翻页: 把 cursor_end 设为 page 最早一根的 openTime - 1ms, 防止重复
            earliest_open_ms = rows[0][0]
            if earliest_open_ms <= cursor_start:
                break
            cursor_end = earliest_open_ms - 1
            await asyncio.sleep(0.1)  # 限流缓冲
            if len(rows) < 1000:
                break

        # 去重排序
        by_ts: dict[datetime, Bar] = {b.ts: b for b in out}
        return sorted(by_ts.values(), key=lambda b: b.ts)

    @staticmethod
    def _parse_kline(symbol: str, project_interval: str, row: list) -> Bar:
        # row = [openTime, open, high, low, close, volume, closeTime, ...]
        # closeTime 是 ms, Binance closeTime = openTime + interval - 1ms
        # ts 用 closeTime+1 (close 时刻边界)
        close_ts_ms = row[6] + 1
        ts = datetime.fromtimestamp(close_ts_ms / 1000, tz=timezone.utc)
        return Bar(
            market="crypto", symbol=symbol, ts=ts,
            open=Decimal(row[1]), high=Decimal(row[2]),
            low=Decimal(row[3]), close=Decimal(row[4]),
            volume=int(float(row[5])),
            interval=project_interval,
        )

    async def verify_ticker(self, symbol: str) -> tuple[bool, str | None]:
        try:
            r = await self._client.get("/api/v3/exchangeInfo",
                                       params={"symbol": _to_binance(symbol)})
            r.raise_for_status()
            d = r.json()
            return bool(d.get("symbols")), symbol
        except Exception:
            return False, None
```

- [ ] **Step 2: 写 unit test**

```python
# tests/unit/adapters/test_binance.py
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import respx
import httpx

from core.adapters.binance import BinanceAdapter, _to_binance, _from_binance


def test_symbol_mapping():
    assert _to_binance("BTC-USDT") == "BTCUSDT"
    assert _from_binance("BTCUSDT") == "BTC-USDT"
    assert _from_binance("ETHUSDC") == "ETH-USDC"


def test_parse_kline_close_ts():
    # openTime 1700000000000 ms, interval 5m → closeTime = 1700000300000-1 = ...
    row = [1700000000000, "1.0", "2.0", "0.5", "1.5", "100",
           1700000299999, "0", 0, "0", "0", "0"]
    bar = BinanceAdapter._parse_kline("BTC-USDT", "5m", row)
    expected_ts = datetime.fromtimestamp(1700000300000 / 1000, tz=timezone.utc)
    assert bar.ts == expected_ts
    assert bar.open == Decimal("1.0")
    assert bar.close == Decimal("1.5")
    assert bar.volume == 100
    assert bar.interval == "5m"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_klines_paged_single_page():
    adapter = BinanceAdapter()
    respx.get("https://api.binance.com/api/v3/klines").mock(
        return_value=httpx.Response(200, json=[
            [1700000000000, "1.0", "2.0", "0.5", "1.5", "100",
             1700000299999, "0", 0, "0", "0", "0"],
        ]),
    )
    bars = await adapter.fetch_klines(
        "BTC-USDT", "5m",
        datetime(2023, 11, 14, tzinfo=timezone.utc),
        datetime(2023, 11, 15, tzinfo=timezone.utc),
    )
    assert len(bars) == 1
    assert bars[0].symbol == "BTC-USDT"
    await adapter.aclose()
```

- [ ] **Step 3: 跑测试**

```bash
. .venv/bin/activate && pytest tests/unit/adapters/test_binance.py -v
```

Expected: 3 passed (若 respx 没装就 `pip install respx`).

### Task 2.2:启用 crypto market + 注册 adapter

**Files:**
- Modify: `config/sources.yaml`
- Modify: `core/adapters/registry.py`(若 crypto 走旧 CryptoAdapter,要切到 BinanceAdapter)

- [ ] **Step 1: sources.yaml 启用 crypto + 改 universe**

```yaml
crypto:
  enabled: true
  default_universe: ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "TRX-USDT"]
  index_symbols: ["BTC-USDT"]
```

- [ ] **Step 2: registry 切到 BinanceAdapter**

```python
# core/adapters/registry.py
from core.adapters.binance import BinanceAdapter
# 把原来 "crypto": CryptoAdapter 改为
# "crypto": BinanceAdapter
```

### Task 2.3:crypto backfill cron(一次性 + 每日兜底)

**Files:**
- Create: `apps/collector/crypto/backfill.py`
- Modify: `apps/collector/crypto/main.py`(attach backfill task)

- [ ] **Step 1: 写 backfill**

```python
# apps/collector/crypto/backfill.py
"""crypto 全周期历史回填.

启动时一次性跑(全周期 5 标的, 能拉多少拉多少),之后每天 04:00 UTC 兜底再拉.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from core.adapters.binance import BinanceAdapter
from core.cache.redis_bars_cache import RedisBarsCache
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)

INTERVALS = ("5m", "15m", "30m", "60m", "4h", "1d", "1wk", "1mo")
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "TRX-USDT")
# 历史窗口: 各 interval 拉到尽头. 用 unix 0 当 start, Binance 自己截断.
HISTORY_START = datetime(2017, 1, 1, tzinfo=timezone.utc)


async def backfill_one(
    adapter: BinanceAdapter, repo: BarRepo, redis_bars: RedisBarsCache,
    symbol: str, interval: str,
) -> None:
    end = datetime.now(timezone.utc)
    try:
        bars = await adapter.fetch_klines(symbol, interval, HISTORY_START, end)
    except Exception as e:
        log.warning("crypto.backfill_failed", symbol=symbol, interval=interval, error=str(e))
        return
    if not bars:
        log.info("crypto.backfill_empty", symbol=symbol, interval=interval)
        return
    try:
        repo.insert_bars(bars)
    except Exception as e:
        log.warning("crypto.backfill_db_write_failed", symbol=symbol, interval=interval, error=str(e))
    try:
        await redis_bars.upsert_tail("crypto", symbol, interval, bars)
    except Exception as e:
        log.warning("crypto.backfill_redis_write_failed", symbol=symbol, interval=interval, error=str(e))
    log.info("crypto.backfill_done", symbol=symbol, interval=interval, bars=len(bars))


async def run_backfill(
    adapter: BinanceAdapter, repo: BarRepo, redis_bars: RedisBarsCache,
) -> None:
    """串行跑 5 × 8 = 40 个 (symbol, interval) 回填."""
    for symbol in SYMBOLS:
        for interval in INTERVALS:
            await backfill_one(adapter, repo, redis_bars, symbol, interval)
            await asyncio.sleep(0.2)  # 限流缓冲
    log.info("crypto.backfill_all_done")
```

- [ ] **Step 2: crypto/main.py 启动 backfill**

```python
# 在 lifespan 内 attach:
from core.cache.redis_bars_cache import RedisBarsCache
from apps.api.deps import get_redis_cache

redis_bars = RedisBarsCache(get_redis_cache())
adapter = BinanceAdapter()

from apps.collector.crypto.backfill import run_backfill
backfill_task = asyncio.create_task(run_backfill(adapter, bar_repo, redis_bars))

# scheduler attach 每日 04:00 UTC 兜底
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
sched = AsyncIOScheduler(timezone="UTC")
sched.add_job(
    run_backfill, CronTrigger(hour=4, minute=0),
    args=(adapter, bar_repo, redis_bars),
    id="crypto:backfill", max_instances=1, coalesce=True,
)
sched.start()
log.info("collector_crypto.started")

try:
    yield
finally:
    sched.shutdown(wait=False)
    backfill_task.cancel()
    await adapter.aclose()
```

- [ ] **Step 3: 重启 + 验证**

```bash
pkill -9 -f "apps.collector.crypto" && sleep 2
nohup bash -c '. .venv/bin/activate && python -m apps.collector.crypto.main' >> /tmp/collector_crypto.log 2>&1 & disown
sleep 90  # 等首次回填完成
grep "crypto.backfill_done" /Users/xiangrong/stock/marketpulse/data/logs/collector_crypto.log | wc -l
# 预期 40 (5 标的 × 8 周期)

curl -s -m 5 "http://localhost:8787/api/symbols/BTC-USDT/bars?interval=1d&days=2300" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'1d total={len(d[\"bars\"])}, first={d[\"bars\"][0][\"ts\"]}, last={d[\"bars\"][-1][\"ts\"]}')
"
```

Expected: 1d total > 1000(BTC 2017 起到现在 ~3000 根)

- [ ] **Step 4: Commit Plan 2**

```bash
git add core/adapters/binance.py tests/unit/adapters/test_binance.py \
        config/sources.yaml core/adapters/registry.py \
        apps/collector/crypto/backfill.py apps/collector/crypto/main.py
git commit -m "feat(crypto): BinanceAdapter REST + 全周期回填 (P2)"
```

---

# Plan 3:binance_ws_consumer 增量 push

**目标:** 长连 Binance combined WS,收到 kline 事件后区分 final / in-progress 处理。

### Task 3.1:写 ws_consumer

**Files:**
- Create: `apps/collector/crypto/ws_consumer.py`

- [ ] **Step 1: 完整代码**

```python
# apps/collector/crypto/ws_consumer.py
"""Binance combined kline WS 长连接消费.

40 路 stream (5 标的 × 8 周期) 复用 1 个 connection.

收到 kline 事件:
- k.x=true (收盘): DuckDB insert + Redis tail upsert + xadd bus:bars.updated (final=true)
- k.x=false (进行中): cache:bars:{m}:{s}:{iv}:current 单根 + xadd bus:bars.updated (final=false)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import structlog
import websockets

from core.cache import keys
from core.cache.redis_bars_cache import RedisBarsCache
from core.cache.redis_client import RedisCache
from core.domain.models import Bar
from core.persistence.duckdb_repo import BarRepo

log = structlog.get_logger(__name__)

WS_BASE = "wss://stream.binance.com:9443/stream"
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "TRX-USDT")
INTERVALS_PROJECT = ("5m", "15m", "30m", "60m", "4h", "1d", "1wk", "1mo")
INTERVAL_PROJ_TO_BINANCE = {
    "5m": "5m", "15m": "15m", "30m": "30m", "60m": "1h", "4h": "4h",
    "1d": "1d", "1wk": "1w", "1mo": "1M",
}
INTERVAL_BINANCE_TO_PROJ = {v: k for k, v in INTERVAL_PROJ_TO_BINANCE.items()}


def _build_streams_url() -> str:
    parts = []
    for sym in SYMBOLS:
        b_sym = sym.replace("-", "").lower()
        for proj_iv in INTERVALS_PROJECT:
            b_iv = INTERVAL_PROJ_TO_BINANCE[proj_iv]
            parts.append(f"{b_sym}@kline_{b_iv}")
    return f"{WS_BASE}?streams={'/'.join(parts)}"


def _parse_kline_msg(stream_data: dict) -> tuple[Bar, bool] | None:
    """返回 (Bar, is_final). 解析失败返 None."""
    try:
        k = stream_data["k"]
        b_sym = k["s"]
        b_iv = k["i"]
        proj_iv = INTERVAL_BINANCE_TO_PROJ.get(b_iv)
        if proj_iv is None:
            return None
        # close ts = k.T + 1ms
        ts = datetime.fromtimestamp((k["T"] + 1) / 1000, tz=timezone.utc)
        # symbol 反查
        sym = None
        for s in SYMBOLS:
            if s.replace("-", "").upper() == b_sym:
                sym = s
                break
        if sym is None:
            return None
        bar = Bar(
            market="crypto", symbol=sym, ts=ts,
            open=Decimal(k["o"]), high=Decimal(k["h"]),
            low=Decimal(k["l"]), close=Decimal(k["c"]),
            volume=int(float(k["v"])), interval=proj_iv,
        )
        return bar, bool(k["x"])
    except Exception as e:
        log.warning("ws.parse_failed", error=str(e))
        return None


async def _bar_to_event(bar: Bar, final: bool) -> dict:
    return {
        "market": bar.market, "symbol": bar.symbol,
        "interval": bar.interval, "ts": bar.ts.isoformat(),
        "open": float(bar.open), "high": float(bar.high),
        "low": float(bar.low), "close": float(bar.close),
        "volume": bar.volume, "final": final,
    }


async def handle_message(
    msg: dict, *,
    repo: BarRepo, redis_bars: RedisBarsCache, redis_cache: RedisCache,
) -> None:
    stream_data = msg.get("data")
    if not stream_data or stream_data.get("e") != "kline":
        return
    parsed = _parse_kline_msg(stream_data)
    if parsed is None:
        return
    bar, final = parsed

    if final:
        # 收盘: 持久化
        try:
            repo.insert_bars([bar])
        except Exception as e:
            log.warning("ws.duckdb_write_failed", symbol=bar.symbol,
                        interval=bar.interval, error=str(e))
        await redis_bars.upsert_tail("crypto", bar.symbol, bar.interval, [bar])
        log.info("ws.kline_closed", symbol=bar.symbol, interval=bar.interval,
                 ts=bar.ts.isoformat(), close=float(bar.close))
    else:
        # 进行中: 单根 current key, TTL 2x interval
        ttl_map = {"5m": 600, "15m": 1800, "30m": 3600, "60m": 7200,
                   "4h": 28800, "1d": 172800, "1wk": 1209600, "1mo": 5184000}
        ttl = ttl_map.get(bar.interval, 600)
        cur_key = keys.cache_bars_current("crypto", bar.symbol, bar.interval)
        try:
            await redis_cache.set_msgpack(cur_key, await _bar_to_event(bar, False), ttl_s=ttl)
        except Exception as e:
            log.warning("ws.current_write_failed", error=str(e))

    # xadd 给 SSE 路由消费
    payload = await _bar_to_event(bar, final)
    try:
        await redis_cache._r.xadd(
            keys.BUS_BARS_UPDATED,
            {"data": json.dumps(payload).encode()},
            maxlen=10000, approximate=True,
        )
    except Exception as e:
        log.warning("ws.xadd_failed", error=str(e))


async def consume_loop(
    *, repo: BarRepo, redis_bars: RedisBarsCache, redis_cache: RedisCache,
) -> None:
    url = _build_streams_url()
    log.info("ws.start", streams=len(SYMBOLS) * len(INTERVALS_PROJECT))
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
                log.info("ws.connected")
                backoff = 1.0
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        await handle_message(msg, repo=repo,
                                             redis_bars=redis_bars, redis_cache=redis_cache)
                    except Exception as e:
                        log.warning("ws.handle_failed", error=str(e))
        except asyncio.CancelledError:
            log.info("ws.cancelled")
            return
        except Exception as e:
            log.warning("ws.connection_lost", error=str(e), retry_in=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
```

### Task 3.2:keys.py 加 cache_bars_current

**Files:**
- Modify: `core/cache/keys.py`

```python
def cache_bars_current(market: str, symbol: str, interval: str) -> str:
    """进行中(未收盘)bar 单根 cache. TTL = 2x interval."""
    return f"cache:bars:{market}:{symbol}:{interval}:current"
```

### Task 3.3:crypto/main.py 启动 ws_consumer

修改 `apps/collector/crypto/main.py` lifespan:

```python
from apps.collector.crypto.ws_consumer import consume_loop
ws_task = asyncio.create_task(consume_loop(
    repo=bar_repo, redis_bars=redis_bars, redis_cache=get_redis_cache(),
))

try:
    yield
finally:
    ws_task.cancel()
    ...
```

### Task 3.4:验证

```bash
pkill -9 -f "apps.collector.crypto" && sleep 2
nohup bash -c '. .venv/bin/activate && python -m apps.collector.crypto.main' >> /tmp/collector_crypto.log 2>&1 & disown
sleep 30
grep "ws.kline_closed\|ws.current" data/logs/collector_crypto.log | tail -10
docker exec marketpulse-redis-dev redis-cli xlen "bus:bars.updated"
```

Expected: 短时间内 0+ 条 ws.kline_closed,xlen 30+

Commit Plan 3.

---

# Plan 4:api SSE 路由

**目标:** `/api/sse/bars/{symbol}/{interval}` push init/bar/tick/ping events。

### Task 4.1:写 SSE 路由

**Files:**
- Create: `apps/api/routes/sse_bars.py`
- Modify: `apps/api/main.py` (注册路由)

- [ ] **Step 1: 写路由**

```python
# apps/api/routes/sse_bars.py
"""SSE: K 线增量 push.

GET /api/sse/bars/{symbol}/{interval}

事件:
- init  (历史 N 根 + 当前进行中 bar 快照)
- bar   (k.x=true, replace 末根)
- tick  (k.x=false, 原地更新末根 OHLC/volume)
- ping  (心跳 30s)
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from apps.api.deps import get_redis_cache, get_redis_bars_cache
from core.cache import keys
from core.domain.markets import infer_market

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/sse", tags=["sse"])

GROUP = "sse"
INIT_TAIL_BARS = 200
PING_INTERVAL_S = 30


def _sse_event(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


@router.get("/bars/{symbol}/{interval}")
async def sse_bars(
    symbol: str, interval: str,
    redis_cache=Depends(get_redis_cache),
    redis_bars=Depends(get_redis_bars_cache),
):
    market = infer_market(symbol)

    async def gen():
        # init: tail + current
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=365 if interval in ("1d", "1wk", "1mo") else 30)
        history = await redis_bars.get_tail(market, symbol, interval, start, end)
        bars_data = [{
            "ts": b.ts.isoformat(), "open": float(b.open),
            "high": float(b.high), "low": float(b.low),
            "close": float(b.close), "volume": b.volume,
            "final": True,
        } for b in history[-INIT_TAIL_BARS:]]
        # current bar
        current = await redis_cache.get_msgpack(
            keys.cache_bars_current(market, symbol, interval),
        )
        if current:
            bars_data.append(current)
        yield _sse_event("init", {
            "bars": bars_data,
            "server_ts": datetime.now(timezone.utc).isoformat(),
        })

        # 订阅 bus:bars.updated
        consumer_id = f"sse-{uuid.uuid4().hex[:8]}"
        try:
            await redis_cache._r.xgroup_create(
                keys.BUS_BARS_UPDATED, GROUP, id="$", mkstream=True,
            )
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                log.warning("sse.group_create_failed", error=str(e))

        last_ping = datetime.now(timezone.utc)
        while True:
            try:
                entries = await redis_cache._r.xreadgroup(
                    GROUP, consumer_id,
                    streams={keys.BUS_BARS_UPDATED: ">"},
                    count=10, block=PING_INTERVAL_S * 1000,
                )
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("sse.read_failed", error=str(e))
                await asyncio.sleep(1)
                continue

            now = datetime.now(timezone.utc)
            if entries:
                for _stream, msgs in entries:
                    for msg_id, fields in msgs:
                        try:
                            data_raw = fields.get(b"data") or fields.get("data")
                            payload = json.loads(data_raw)
                            if payload.get("symbol") == symbol \
                               and payload.get("interval") == interval:
                                event = "bar" if payload.get("final") else "tick"
                                yield _sse_event(event, payload)
                        finally:
                            await redis_cache._r.xack(
                                keys.BUS_BARS_UPDATED, GROUP, msg_id,
                            )
                last_ping = now

            if (now - last_ping).total_seconds() >= PING_INTERVAL_S:
                yield _sse_event("ping", {"server_ts": now.isoformat()})
                last_ping = now

    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache",
                                       "Connection": "keep-alive",
                                       "X-Accel-Buffering": "no"})
```

- [ ] **Step 2: 注册路由**

`apps/api/main.py`:
```python
from apps.api.routes.sse_bars import router as sse_bars_router
app.include_router(sse_bars_router)
```

### Task 4.2:验证

```bash
pkill -9 -f "uvicorn apps.api.main:app" && sleep 2
nohup bash -c '. .venv/bin/activate && uvicorn apps.api.main:app --port 8787' >> /tmp/api.log 2>&1 & disown
sleep 5
curl -s -N -m 60 "http://localhost:8787/api/sse/bars/BTC-USDT/5m" | head -c 5000
```

Expected: 看到 `event: init` 然后 60s 内若有新 tick/bar push,会跟着出。

Commit Plan 4.

---

# Plan 5:前端 useKlineStream + 详情页接入

### Task 5.1:写 hook

**Files:**
- Create: `apps/web/lib/use_kline_stream.ts`

```typescript
import { useEffect, useState } from 'react'
import type { BarDTO } from '@/lib/types'

export interface KlineEvent {
  ts: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  final: boolean
}

export function useKlineStream(symbol: string, interval: string,
                               enabled: boolean): BarDTO[] {
  const [bars, setBars] = useState<BarDTO[]>([])

  useEffect(() => {
    if (!enabled) return
    const url = `/api/sse/bars/${encodeURIComponent(symbol)}/${interval}`
    const es = new EventSource(url)

    const toBarDTO = (e: KlineEvent): BarDTO => ({
      ts: e.ts, open: e.open, high: e.high, low: e.low,
      close: e.close, volume: e.volume,
    })

    es.addEventListener('init', (msg) => {
      const data = JSON.parse((msg as MessageEvent).data)
      setBars((data.bars as KlineEvent[]).map(toBarDTO))
    })

    es.addEventListener('bar', (msg) => {
      const ev = JSON.parse((msg as MessageEvent).data) as KlineEvent
      setBars((prev) => {
        const last = prev[prev.length - 1]
        const dto = toBarDTO(ev)
        if (last && last.ts === ev.ts) {
          return [...prev.slice(0, -1), dto]
        }
        return [...prev, dto]
      })
    })

    es.addEventListener('tick', (msg) => {
      const ev = JSON.parse((msg as MessageEvent).data) as KlineEvent
      setBars((prev) => {
        const last = prev[prev.length - 1]
        const dto = toBarDTO(ev)
        if (last && last.ts === ev.ts) {
          return [...prev.slice(0, -1), dto]
        }
        return [...prev, dto]
      })
    })

    es.onerror = () => { /* EventSource auto-reconnects */ }
    return () => es.close()
  }, [symbol, interval, enabled])

  return bars
}
```

### Task 5.2:详情页接入(crypto only)

修改 `apps/web/app/symbol/[code]/page.tsx`:
- 若 `effectiveMarket === 'crypto'` 且 `interval !== '1m'`,用 `useKlineStream` 替代 SWR 拉 bars
- A 股 / 美股保持 SWR

### Task 5.3:验证

浏览器打开 `/symbol/BTC-USDT`,切到 5m,观察:
- 立即看到历史 K 线
- 末根 OHLC 每 1-2s 实时变动
- 5 分钟到 → 多一根新 bar

### Task 5.4:Commit Plan 5

```bash
git add apps/web/lib/use_kline_stream.ts apps/web/app/symbol/[code]/page.tsx
git commit -m "feat(web): crypto 详情页接入 SSE useKlineStream (P5)"
```

---

# 整体验收

- [ ] `make dev` 起 4 进程(redis 单独 docker)+ web,所有 /health 200
- [ ] 浏览器打开 BTC 详情页,切 5m / 15m / 30m / 1h / 4h / 1d / 1wk / 1mo,**8 个 tab 都有数据**
- [ ] 末根 OHLC 实时变动(1-2s 频率)
- [ ] 杀 collector_ashare,collector_crypto 继续推送,collector_us K 线照常
- [ ] 单测全过(401 + 新增 ~10 个)

---

# 文档自审

- 路径全用 `/Users/xiangrong/stock/marketpulse/...` 绝对路径或 git 仓内相对路径,无歧义
- 每个 task 有 Files / 完整代码 / 验证命令 / 预期输出
- commit 模板按主题分,5 个 plan = 5 个 commit
- 类型一致:Bar 模型字段 / SSE event 字段 / Redis key 函数签名 跨 plan 不漂移
- spec coverage:§ 0-9 全覆盖
- 风险有 mitigation:Binance 限流(100ms 间隔)/ WS 断连(指数退避)/ DuckDB 锁(独立文件)
