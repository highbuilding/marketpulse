from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import RLock

import duckdb
import pandas as pd
import structlog

from core.domain.models import Bar

log = structlog.get_logger(__name__)


class BarRepo:
    def __init__(self, db_path: str, *, read_only: bool = False) -> None:
        # read_only=True: api 进程使用,避免与 collector 争抢 DuckDB 文件写锁。
        # 雷区: 多进程同时持写锁会触发 IO Error: Conflicting lock is held in PID...
        self.db_path = db_path
        self.read_only = read_only
        self._lock = RLock()

    def _conn(self):
        return duckdb.connect(self.db_path, read_only=self.read_only)

    # 读写锁分离(2026-06-08): self._lock 仅保护写(insert_bars/init), 读方法不持锁。
    # 依据 DuckDB 1.5.2 同进程实测: 多个 RW 连接(含长持写连接 + 短生命周期读连接)
    # 共存安全, 无 Conflicting lock(read_only 连接才冲突, 故读仍用默认 RW _conn)。
    # 收益: 开盘 bar_poller 高频 insert 持锁时, 历史查询(/internal/bars/history)
    # 不再排队等写锁。配合 base.py 的 to_thread(查询移出事件循环), 根治满载慢查询。

    def init(self) -> None:
        if self.read_only:
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._conn() as c:
            c.execute("""
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
            c.execute("ALTER TABLE bars ADD COLUMN IF NOT EXISTS amount DOUBLE")
            c.execute("ALTER TABLE bars ADD COLUMN IF NOT EXISTS turnover DOUBLE")
            c.execute("ALTER TABLE bars ADD COLUMN IF NOT EXISTS outstanding_share DOUBLE")
            # final 标记: 存量行默认 TRUE(全部视为收线, 安全)。仅进行态日K入库写 FALSE,
            # 收线后被权威数据 ON CONFLICT 覆盖翻 TRUE。信号/聚合/缺口检测查 closed_only。
            c.execute("ALTER TABLE bars ADD COLUMN IF NOT EXISTS final BOOLEAN DEFAULT TRUE")

    # 列顺序与 bars 表 schema 严格一致, 供 DataFrame 批量 upsert 使用
    _COLS = (
        "market", "symbol", "ts", "interval", "open", "high", "low", "close",
        "volume", "amount", "turnover", "outstanding_share", "final",
    )

    def insert_bars(self, bars: list[Bar]) -> None:
        with self._lock:
            if not bars:
                return
            # 1m 已废弃 (审计 B4): 永久杜绝任何路径写 1m 伪 bar。
            # 存储边界策略: 1m + 进行中态不存, 5m+ 收线才入库。
            dropped = sum(1 for b in bars if b.interval == "1m")
            if dropped:
                log.warning("bars.insert_dropped_1m", count=dropped,
                            market=bars[0].market, symbol=bars[0].symbol)
                bars = [b for b in bars if b.interval != "1m"]
                if not bars:
                    return
            rows = [(
                b.market, b.symbol, b.ts.astimezone(timezone.utc).replace(tzinfo=None),
                b.interval, b.open, b.high, b.low, b.close, b.volume,
                b.amount, b.turnover, b.outstanding_share, b.final,
            ) for b in bars]
            # 向量化批量 upsert: register DataFrame + INSERT ... SELECT ... ON CONFLICT.
            # 坑: 早期用 executemany 逐行 upsert, 回填全周期 5m (~77 万根/标的) 时
            # 单标的插入耗时 10+ 分钟 (20k 行 ≈ 16s, 线性放大). DataFrame 批量走
            # DuckDB 列式引擎, 77 万行 ~3s. 见 backfill 全历史窗口扩大后的性能回归.
            df = pd.DataFrame(rows, columns=list(self._COLS))
            with self._conn() as c:
                c.register("_incoming_bars", df)
                try:
                    c.execute("""
                        INSERT INTO bars (
                            market, symbol, ts, interval, open, high, low, close,
                            volume, amount, turnover, outstanding_share, final
                        )
                        SELECT
                            market, symbol, ts, interval, open, high, low, close,
                            volume, amount, turnover, outstanding_share, final
                        FROM _incoming_bars
                        ON CONFLICT (market, symbol, interval, ts) DO UPDATE SET
                            open=excluded.open, high=excluded.high, low=excluded.low,
                            close=excluded.close, volume=excluded.volume,
                            amount=excluded.amount, turnover=excluded.turnover,
                            outstanding_share=excluded.outstanding_share,
                            final=excluded.final
                    """)
                finally:
                    c.unregister("_incoming_bars")

    def delete_bars(
        self, market: str, symbol: str, interval: str,
        *, start: datetime | None = None, end: datetime | None = None,
    ) -> int:
        """删除指定标的/周期的 bar (可选时间窗)。数据冲刷用: 先删脏数据再重拉。

        start/end 均 None = 删该标的该周期全部。返回删除行数。
        """
        with self._lock:
            conds = ["market = ?", "symbol = ?", "interval = ?"]
            args: list = [market, symbol, interval]
            if start is not None:
                conds.append("ts >= ?")
                args.append(start.astimezone(timezone.utc).replace(tzinfo=None))
            if end is not None:
                conds.append("ts <= ?")
                args.append(end.astimezone(timezone.utc).replace(tzinfo=None))
            with self._conn() as c:
                before = c.execute(
                    f"SELECT COUNT(*) FROM bars WHERE {' AND '.join(conds)}", args,
                ).fetchone()[0]
                c.execute(f"DELETE FROM bars WHERE {' AND '.join(conds)}", args)
            return int(before)

    def fetch_history(
        self, market: str, symbol: str,
        start: datetime, end: datetime, interval: str = "1d",
        *, closed_only: bool = False,
    ) -> list[Bar]:
        # closed_only=True: 仅收线根(信号/聚合/缺口检测用, 排除进行态日K)。
        # 默认 False: 含进行态(前端历史展示)。
        with self._conn() as c:  # 读不持锁(读写分离)
            cur = c.execute(f"""
                SELECT ts, interval, open, high, low, close, volume,
                       amount, turnover, outstanding_share, final
                FROM bars
                WHERE market=? AND symbol=? AND interval=?
                  AND ts BETWEEN ? AND ?
                  {"AND final = TRUE" if closed_only else ""}
                ORDER BY ts
            """, (market, symbol, interval,
                   start.astimezone(timezone.utc).replace(tzinfo=None),
                   end.astimezone(timezone.utc).replace(tzinfo=None)))
            rows = cur.fetchall()
        return self._rows_to_bars(market, symbol, rows)

    def fetch_history_frame(
        self,
        market: str,
        symbols: list[str],
        start: datetime,
        end: datetime,
        interval: str = "1d",
        *,
        closed_only: bool = True,
    ) -> pd.DataFrame:
        """批量读取历史 bar 为 DataFrame,供回测/统计任务使用。

        collector/offline 进程调用; api 进程不要经此路径直连 DuckDB。
        """
        if not symbols:
            return pd.DataFrame()
        placeholders = ",".join(["?"] * len(symbols))
        args: list = [
            market,
            interval,
            start.astimezone(timezone.utc).replace(tzinfo=None),
            end.astimezone(timezone.utc).replace(tzinfo=None),
            *symbols,
        ]
        with self._conn() as c:
            return c.execute(f"""
                SELECT market, symbol, ts, interval, open, high, low, close,
                       volume, amount, turnover, outstanding_share, final
                FROM bars
                WHERE market = ? AND interval = ?
                  AND ts BETWEEN ? AND ?
                  AND symbol IN ({placeholders})
                  {"AND final = TRUE" if closed_only else ""}
                ORDER BY ts ASC, symbol ASC
            """, args).fetchdf()

    def fetch_history_paged(
        self, market: str, symbol: str, interval: str,
        *, before: datetime | None, limit: int, closed_only: bool = False,
    ) -> list[Bar]:
        """游标分页: 返回严格早于 before 的最近 limit 根, 升序。

        币安/TradingView 反向翻页口径 —— before=None 取最新一页,
        前端拿到首页最老一根的 ts 作为下一页的 before, 一直翻到上市首日。
        返回升序 (前端 lightweight-charts setData 要求升序)。
        closed_only=True 时仅收线根(默认含进行态, 供前端展示)。
        """
        before_naive = (
            before.astimezone(timezone.utc).replace(tzinfo=None)
            if before is not None else None
        )
        with self._conn() as c:  # 读不持锁(读写分离)
            cur = c.execute(f"""
                SELECT ts, interval, open, high, low, close, volume,
                       amount, turnover, outstanding_share, final
                FROM bars
                WHERE market=? AND symbol=? AND interval=?
                  AND (CAST(? AS TIMESTAMP) IS NULL OR ts < CAST(? AS TIMESTAMP))
                  {"AND final = TRUE" if closed_only else ""}
                ORDER BY ts DESC
                LIMIT ?
            """, (market, symbol, interval, before_naive, before_naive, limit))
            rows = cur.fetchall()
        rows.reverse()  # DESC 取最近 limit 根后翻成升序
        return self._rows_to_bars(market, symbol, rows)

    def fetch_last_ts_map(
        self, market: str, interval: str, symbols: list[str],
        *, closed_only: bool = False,
    ) -> dict[str, datetime]:
        """返回 {symbol: max(ts)}，仅返回有数据的 symbol。

        用于 sweep_derived 批量子查询，复用 repo 连接避免 read_only 冲突。
        closed_only=True 时仅按收线根算末点(缺口检测用, 避免进行态根让 reconcile
        误判"已最新"不补)。
        """
        if not symbols:
            return {}
        placeholders = ",".join(["?"] * len(symbols))
        with self._conn() as c:  # 读不持锁(读写分离)
            rows = c.execute(f"""
                SELECT symbol, MAX(ts) FROM bars
                WHERE market=? AND interval=?
                  AND symbol IN ({placeholders})
                  {"AND final = TRUE" if closed_only else ""}
                GROUP BY symbol
            """, [market, interval] + list(symbols)).fetchall()
        return {r[0]: r[1].replace(tzinfo=timezone.utc) for r in rows}

    def fetch_first_ts_map(
        self, market: str, interval: str, symbols: list[str],
        *, closed_only: bool = False,
    ) -> dict[str, datetime]:
        """返回 {symbol: min(ts)}，用于检测派生周期是否漏了早期历史。"""
        if not symbols:
            return {}
        placeholders = ",".join(["?"] * len(symbols))
        with self._conn() as c:  # 读不持锁(读写分离)
            rows = c.execute(f"""
                SELECT symbol, MIN(ts) FROM bars
                WHERE market=? AND interval=?
                  AND symbol IN ({placeholders})
                  {"AND final = TRUE" if closed_only else ""}
                GROUP BY symbol
            """, [market, interval] + list(symbols)).fetchall()
        return {r[0]: r[1].replace(tzinfo=timezone.utc) for r in rows}

    @staticmethod
    def _rows_to_bars(market: str, symbol: str, rows: list) -> list[Bar]:
        out: list[Bar] = []
        for ts, iv, o, h, low, cl, v, amount, turnover, outstanding_share, final in rows:
            out.append(Bar(
                market=market, symbol=symbol,
                ts=ts.replace(tzinfo=timezone.utc),
                open=Decimal(str(o)), high=Decimal(str(h)),
                low=Decimal(str(low)), close=Decimal(str(cl)),
                volume=int(v), interval=iv,
                amount=float(amount) if amount is not None else None,
                turnover=float(turnover) if turnover is not None else None,
                outstanding_share=(
                    float(outstanding_share) if outstanding_share is not None else None
                ),
                final=bool(final) if final is not None else True,
            ))
        return out
