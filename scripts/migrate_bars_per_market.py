"""一次性迁移: bars.duckdb → bars_{market}.duckdb (仅 1d/1wk/1mo).

策略 C: intraday (5m/15m/30m/60m/4h/1m) 抛弃, 等 cron 重拉.

用法:
    python scripts/migrate_bars_per_market.py
    # 旧文件不删, 备份在 .before-split-* 文件
"""
from __future__ import annotations

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
