"""MarketPulse 数据初始化 — 新机器部署 Step 1 (执行一次).

按市场补齐全量 K 线数据。所有 interval 覆盖到数据源允许的最大范围。
**幂等**: 检测已有数据, 只补缺口。重复执行秒级完成。

用法:
    make dev-stop              # 先停 collector (DuckDB 单写)
    make init-data              # 补全 (幂等, 可重复执行)
    make dev                    # 启动服务

    # 或单独市场 / 强制全量:
    python scratch/init_data.py --market us
    python scratch/init_data.py --force  # 忽略已有数据, 全量重拉

周期覆盖策略: 见 CLAUDE.md §数据流核心路径

日常增量由 collector 负责:
  - Crypto: Binance WS (1m-1mo) + 每日 04:00 UTC cron 兜底
  - A 股: bar_poller 按需轮询 (10s) + cron CD 扫描
  - 美股: Alpaca WS (1m) + cron intraday 扫描
  - 聚合周期 (60m/4h/1wk/1mo) 首次读取时 KLineService 懒生成并缓存
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from core.integrations.proxy_setup import setup_process_proxy

setup_process_proxy()

from core.integrations.logging_setup import setup_logging

setup_logging(process_name="init_data")

import structlog

log = structlog.get_logger(__name__)

DATA = Path(__file__).resolve().parents[1] / "data"

# 基础周期 (从数据源直接拉取)
CRYPTO_INTERVALS = ("5m", "15m", "30m", "60m", "4h", "1d", "1wk", "1mo")
STOCK_BASE_INTERVALS = ("1d", "5m", "15m", "30m")

# 日线历史窗口
DAILY_START = datetime(2010, 1, 1, tzinfo=timezone.utc)
CRYPTO_GENESIS = datetime(2017, 7, 1, tzinfo=timezone.utc)

CRYPTO_SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "TRX-USDT")

# 各周期缺口容忍度: 超过此间隔认为数据不连续, 需回填
_GAP_TOLERANCE = {
    "1m": timedelta(minutes=3),
    "5m": timedelta(minutes=10),
    "15m": timedelta(minutes=30),
    "30m": timedelta(hours=1),
    "60m": timedelta(hours=2),
    "4h": timedelta(hours=8),
    "1d": timedelta(days=2),
    "1wk": timedelta(days=10),
    "1mo": timedelta(days=35),
}


# ---------------------------------------------------------------------------
# 增量检测
# ---------------------------------------------------------------------------

def _get_symbol_last_ts(db_path: str, market: str, symbol: str, interval: str) -> datetime | None:
    """查询某个 symbol+interval 已有数据的最后时间."""
    import duckdb
    try:
        c = duckdb.connect(db_path, read_only=True)
        row = c.execute(
            "SELECT MAX(ts) FROM bars WHERE market=? AND symbol=? AND interval=?",
            (market, symbol, interval),
        ).fetchone()
        c.close()
        if row and row[0]:
            return row[0]
    except Exception:  # noqa: BLE001
        pass
    return None


def _find_gap_start(db_path: str, market: str, symbol: str, interval: str,
                     tolerance: timedelta, lookback_days: int = 7) -> datetime | None:
    """检查最近 lookback_days 天的数据连续性, 返回第一个缺口位置.
    无缺口返 None, 有缺口返 gap_start_ts (需补数据的起点).
    """
    import duckdb
    try:
        c = duckdb.connect(db_path, read_only=True)
        rows = c.execute(
            "SELECT ts FROM bars WHERE market=? AND symbol=? AND interval=? "
            "AND ts > (CURRENT_TIMESTAMP - INTERVAL '{} days') ORDER BY ts".format(lookback_days),
            (market, symbol, interval),
        ).fetchall()
        c.close()
        if not rows:
            return None  # 无数据, 调用方需全量拉
        if len(rows) < 2:
            return rows[0][0] + timedelta(seconds=1)  # 只有 1 条, 从它之后补
        # 检查相邻间隔
        last_ts = rows[-1][0]
        now = datetime.now(timezone.utc)
        if now - last_ts > tolerance:
            return rows[-1][0] + timedelta(seconds=1)  # 最新数据太旧
        for i in range(len(rows) - 1, 0, -1):
            gap = rows[i][0] - rows[i - 1][0]
            if gap > tolerance:
                return rows[i - 1][0] + timedelta(seconds=1)
        return None  # 数据连续
    except Exception:  # noqa: BLE001
        return None


def _count_existing(db_path: str, market: str, interval: str) -> int:
    import duckdb
    try:
        c = duckdb.connect(db_path, read_only=True)
        cnt = c.execute(
            "SELECT COUNT(*) FROM bars WHERE market=? AND interval=?",
            (market, interval),
        ).fetchone()[0]
        c.close()
        return cnt
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# Crypto
# ---------------------------------------------------------------------------

async def init_crypto(force: bool = False) -> int:
    from core.adapters.binance import BinanceAdapter
    from core.persistence.duckdb_repo import BarRepo

    repo = BarRepo(str(DATA / "bars_crypto.duckdb"))
    repo.init()
    adapter = BinanceAdapter()
    db_path = str(DATA / "bars_crypto.duckdb")
    total = 0

    print(f"\n{'='*60}")
    print(f"Crypto: {len(CRYPTO_SYMBOLS)} symbols × {len(CRYPTO_INTERVALS)} intervals")
    print(f"{'='*60}")

    for sym in CRYPTO_SYMBOLS:
        for iv in CRYPTO_INTERVALS:
            if not force:
                cnt = _count_existing(db_path, "crypto", iv)
                if cnt > 0:
                    print(f"  {sym:12s} {iv:4s} → 已有 {cnt:>8,} bars, 跳过")
                    continue

            try:
                bars = await adapter.fetch_klines(sym, iv, CRYPTO_GENESIS, datetime.now(timezone.utc))
                if bars:
                    repo.insert_bars(bars)
                    total += len(bars)
                    print(f"  {sym:12s} {iv:4s} → {len(bars):>8,} bars")
            except Exception as e:
                log.warning("init.crypto_failed", symbol=sym, interval=iv, error=str(e))
                print(f"  {sym:12s} {iv:4s} → FAILED: {e}")
            await asyncio.sleep(0.15)

    await adapter.aclose()
    print(f"  Crypto: {total:,} bars 新增")
    return total


# ---------------------------------------------------------------------------
# A 股
# ---------------------------------------------------------------------------

# 市场指数 — 始终包含，不受 symbols 文件影响
_MARKET_INDICES: dict[str, list[str]] = {
    "ashare": [
        "000001.SH", "399001.SZ", "000300.SH", "399006.SZ",
        "000905.SH", "000852.SH", "000688.SH", "000016.SH",
    ],
    "us": ["SPY", "QQQ", "DIA", "IWM"],
    "crypto": ["BTC-USDT"],
}

def _load_ashare_symbols() -> list[str]:
    syms = set(_MARKET_INDICES.get("ashare", []))
    f = DATA / "ashare_backfill_symbols.txt"
    if f.exists():
        syms.update(l.strip() for l in open(f) if l.strip() and not l.startswith("#"))
    return sorted(syms)


async def init_ashare(force: bool = False) -> int:
    from core.adapters.ashare import AShareAdapter
    from core.persistence.duckdb_repo import BarRepo

    repo = BarRepo(str(DATA / "bars_ashare.duckdb"))
    repo.init()
    adapter = AShareAdapter()
    symbols = _load_ashare_symbols()
    db_path = str(DATA / "bars_ashare.duckdb")
    now = datetime.now(timezone.utc)
    total = 0

    print(f"\n{'='*60}")
    print(f"A 股: {len(symbols)} symbols")
    print(f"{'='*60}")

    for i, sym in enumerate(symbols, 1):
        n_sym = 0

        # 全周期: 缺口连续性检测 + 补缺口
        for iv, freq in [("1d", None), ("5m", "5"), ("15m", "15"), ("30m", "30")]:
            tol = _GAP_TOLERANCE.get(iv, timedelta(days=2))
            if not force:
                gap_start = _find_gap_start(db_path, "ashare", sym, iv, tol)
                if gap_start is None:
                    continue  # 数据连续
                fill_start = gap_start
            else:
                fill_start = DAILY_START if iv == "1d" else None

            if iv == "1d" and fill_start is not None:
                try:
                    bars = await adapter.fetch_history(sym, fill_start, now)
                    if bars: repo.insert_bars(bars); n_sym += len(bars)
                except Exception as e:
                    log.warning("init.ashare_1d_failed", symbol=sym, error=str(e))
            elif iv != "1d":
                try:
                    bars = await adapter.fetch_intraday(sym, freq=freq)
                    if bars: repo.insert_bars(bars); n_sym += len(bars)
                except Exception as e:
                    log.warning("init.ashare_intraday_failed", symbol=sym, freq=freq, error=str(e))
                await asyncio.sleep(0.05)

        total += n_sym
        if i % 100 == 0 or i == 1:
            print(f"  [{i}/{len(symbols)}] {sym}: +{n_sym:,} bars  (累计 {total:,})")
        await asyncio.sleep(0.1)

    print(f"  A 股: {total:,} bars 新增")

    # 预生成派生周期 (60m/4h ← 5m 聚合, 1wk/1mo ← 1d resample)
    await _pre_aggregate(repo, adapter, symbols, db_path, "ashare")

    return total


# ---------------------------------------------------------------------------
# 美股
# ---------------------------------------------------------------------------

def _load_us_symbols() -> list[str]:
    syms = set(_MARKET_INDICES.get("us", []))
    f = DATA / "us_backfill_symbols.txt"
    if f.exists():
        syms.update(l.strip() for l in open(f) if l.strip() and not l.startswith("#"))
    return sorted(syms)


async def init_us(force: bool = False) -> int:
    from core.adapters.us import USAdapter
    from core.persistence.duckdb_repo import BarRepo

    repo = BarRepo(str(DATA / "bars_us.duckdb"))
    repo.init()
    adapter = USAdapter()
    symbols = _load_us_symbols()
    db_path = str(DATA / "bars_us.duckdb")
    now = datetime.now(timezone.utc)
    total = 0

    print(f"\n{'='*60}")
    print(f"美股: {len(symbols)} symbols")
    print(f"{'='*60}")

    for i, sym in enumerate(symbols, 1):
        n_sym = 0

        # 全周期: 缺口连续性检测 + 补缺口
        for iv, freq in [("1d", None), ("5m", "5"), ("15m", "15"), ("30m", "30")]:
            tol = _GAP_TOLERANCE.get(iv, timedelta(days=2))
            if not force:
                gap_start = _find_gap_start(db_path, "us", sym, iv, tol)
                if gap_start is None:
                    continue  # 数据连续
                fill_start = gap_start
            else:
                fill_start = DAILY_START if iv == "1d" else None

            if iv == "1d" and fill_start is not None:
                try:
                    bars = await adapter.fetch_history(sym, fill_start, now)
                    if bars: repo.insert_bars(bars); n_sym += len(bars)
                except Exception as e:
                    log.warning("init.us_1d_failed", symbol=sym, error=str(e))
            elif iv != "1d":
                try:
                    bars = await adapter.fetch_intraday(sym, freq=freq)
                    if bars: repo.insert_bars(bars); n_sym += len(bars)
                except Exception as e:
                    log.warning("init.us_intraday_failed", symbol=sym, freq=freq, error=str(e))
                await asyncio.sleep(0.1)

        total += n_sym
        if i % 100 == 0 or i == 1:
            print(f"  [{i}/{len(symbols)}] {sym}: +{n_sym:,} bars  (累计 {total:,})")
        await asyncio.sleep(0.2)

    # 预生成派生周期
    await _pre_aggregate(repo, adapter, symbols, db_path, "us")

    await adapter.aclose()
    print(f"  美股: {total:,} bars 新增")
    return total


# ---------------------------------------------------------------------------
# 预生成派生周期
# ---------------------------------------------------------------------------

async def _pre_aggregate(repo, adapter, symbols: list[str], db_path: str,
                          market: str) -> None:
    """60m/4h 从 5m 聚合, 1wk/1mo 从 1d resample.

    只在首次初始化时执行 (DB 里这些周期为空时生成).
    后续 collector 不会新增派生周期 — 现在一次生成完.
    """
    from core.services.intraday_aggregator import aggregate_intraday
    from core.domain.markets import Market

    now = datetime.now(timezone.utc)

    # 只生成空周期 (已有数据则跳过)
    for target_iv, source_iv, agg_minutes in [
        ("60m", "5m", 60),
        ("4h", "5m", 240),
    ]:
        existing = _count_existing(db_path, market, target_iv)
        if existing > 0:
            print(f"  {market} {target_iv}: 已有 {existing:,} bars, 跳过聚合")
            continue

        print(f"  {market} {target_iv}: 从 {source_iv} 聚合中...")
        n_generated = 0
        for sym in symbols:
            last = _get_symbol_last_ts(db_path, market, sym, source_iv)
            if last is None:
                continue
            # 拉 5m raw bars 的时间窗口
            start = last - timedelta(days=60 if market == "us" else 90)
            raw = repo.fetch_history(market, sym, start, now, interval=source_iv)
            if not raw:
                continue
            try:
                agg = aggregate_intraday(raw, Market(market), agg_minutes)
                if agg:
                    repo.insert_bars(agg)
                    n_generated += len(agg)
            except Exception as e:
                log.warning("init.pre_aggregate_failed",
                            market=market, symbol=sym, target=target_iv, error=str(e))
        print(f"    → {n_generated:,} bars 生成")

    # 1wk/1mo: pandas resample 从 1d
    for target_iv, freq in [("1wk", "W"), ("1mo", "ME")]:
        existing = _count_existing(db_path, market, target_iv)
        if existing > 0:
            print(f"  {market} {target_iv}: 已有 {existing:,} bars, 跳过 resample")
            continue

        print(f"  {market} {target_iv}: 从 1d resample 中...")
        n_generated = 0
        import pandas as pd
        for sym in symbols:
            last = _get_symbol_last_ts(db_path, market, sym, "1d")
            if last is None:
                continue
            start = last - timedelta(days=365 * 15)  # 覆盖全量历史
            daily = repo.fetch_history(market, sym, start, now, interval="1d")
            if not daily:
                continue
            try:
                df = pd.DataFrame([{
                    "ts": b.ts, "open": float(b.open), "high": float(b.high),
                    "low": float(b.low), "close": float(b.close),
                    "volume": b.volume,
                } for b in daily])
                df["ts"] = pd.to_datetime(df["ts"])
                df = df.set_index("ts").sort_index()
                resampled = df.resample(freq).agg({
                    "open": "first", "high": "max", "low": "min",
                    "close": "last", "volume": "sum",
                }).dropna()
                from core.domain.models import Bar
                bars = [
                    Bar(market=market, symbol=sym, ts=idx.to_pydatetime().replace(tzinfo=timezone.utc),
                        open=Decimal(str(r.open)), high=Decimal(str(r.high)),
                        low=Decimal(str(r.low)), close=Decimal(str(r.close)),
                        volume=int(r.volume), interval=target_iv)
                    for idx, r in resampled.iterrows()
                ]
                if bars:
                    repo.insert_bars(bars)
                    n_generated += len(bars)
            except Exception as e:
                log.warning("init.pre_resample_failed",
                            market=market, symbol=sym, target=target_iv, error=str(e))
        print(f"    → {n_generated:,} bars 生成")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main() -> None:
    p = argparse.ArgumentParser(description="MarketPulse 数据初始化 (幂等, 只补缺口)")
    p.add_argument("--market", choices=["crypto", "ashare", "us", "all"], default="all")
    p.add_argument("--force", action="store_true",
                   help="强制全量重拉 (忽略已有数据)")
    args = p.parse_args()

    print("=" * 60)
    print("MarketPulse 数据初始化" + (" (force 模式)" if args.force else " (增量模式)"))
    print(f"时间: {datetime.now(timezone.utc).isoformat()}")
    print(f"注意: 运行前请停 collector (make dev-stop)")
    if not args.force:
        print(f"已有数据自动跳过, 只补缺口。加 --force 强制全量。")
    print("=" * 60)

    grand_total = 0

    if args.market in ("crypto", "all"):
        grand_total += await init_crypto(args.force)
    if args.market in ("ashare", "all"):
        grand_total += await init_ashare(args.force)
    if args.market in ("us", "all"):
        grand_total += await init_us(args.force)

    print(f"\n{'='*60}")
    print(f"完成! 新增 {grand_total:,} bars")
    if grand_total == 0:
        print(f"数据已是最新, 无需更新。")
    print(f"make dev 启动服务")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
