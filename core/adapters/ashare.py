from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import structlog

from core.adapters.base import AdapterError, CircuitBreaker
from core.domain.models import Bar, HealthStatus, Quote
from core.integrations.akshare import ak_call

log = structlog.get_logger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")


_SINA_BASE = "https://hq.sinajs.cn/list="


def _normalize_symbol(code: str) -> str:
    if "." in code:
        return code
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _denormalize(symbol: str) -> str:
    return symbol.split(".")[0]


def _to_sina_code(symbol: str) -> str:
    if "." not in symbol:
        return _to_sina_code(_normalize_symbol(symbol))
    code, mkt = symbol.split(".")
    return f"{mkt.lower()}{code}"


def _classify(symbol: str) -> str:
    """归类 'etf' / 'index' / 'stock'。symbol 形如 600519.SH / 510300.SH / 000001.SH / 000001.SZ。"""
    code, mkt = symbol.split(".")
    mkt = mkt.upper()
    # 指数:SH 段的 000xxx 是上证指数族;SZ 段的 399xxx 是深证指数族
    if mkt == "SH" and code.startswith("000"):
        return "index"
    if mkt == "SZ" and code.startswith("399"):
        return "index"
    # ETF
    if mkt == "SH" and code.startswith(("5", "11")):
        return "etf"
    if mkt == "SZ" and code.startswith("159"):
        return "etf"
    return "stock"


def _num(row, *names: str) -> float | None:
    for name in names:
        if name not in row:
            continue
        v = row[name]
        if pd.isna(v):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _date_key(value) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


class AShareAdapter:
    market = "ashare"
    name = "ashare"

    def __init__(self) -> None:
        self.primary_cb = CircuitBreaker(fail_threshold=3, reset_after_s=300)
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.proxies = {}
        self._session.headers.update({
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        })

    async def fetch_snapshot(self, symbols: list[str]) -> list[Quote]:
        if self.primary_cb.can_execute():
            try:
                quotes = await asyncio.to_thread(self._fetch_snapshot_sina, symbols)
                self.primary_cb.record_success()
                return quotes
            except Exception as e:
                self.primary_cb.record_failure()
                log.warning("ashare.primary_failed", error=str(e))
        wanted = {_denormalize(s) for s in symbols}
        try:
            return await asyncio.to_thread(self._fetch_snapshot_mootdx, wanted)
        except Exception as e:
            raise AdapterError(f"both primary and backup failed: {e}", source="ashare") from e

    def _fetch_snapshot_sina(self, symbols: list[str]) -> list[Quote]:
        codes = ",".join(_to_sina_code(s) for s in symbols)
        url = _SINA_BASE + codes
        r = self._session.get(url, timeout=5)
        r.encoding = "gbk"
        r.raise_for_status()
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for line in r.text.splitlines():
            # var hq_str_sh600519="贵州茅台,open,prev_close,now,high,low,...";
            if 'hq_str_' not in line or '="' not in line:
                continue
            sina_code = line.split('hq_str_')[1].split('=')[0]  # e.g. "sh600519"
            payload = line.split('="', 1)[1].rstrip('";\n')
            parts = payload.split(",")
            if len(parts) < 6:
                continue
            symbol = f"{sina_code[2:]}.{sina_code[:2].upper()}"
            try:
                prev_close = float(parts[2])
                price = float(parts[3])
                volume = int(float(parts[8])) if len(parts) > 8 else 0
            except (ValueError, IndexError):
                continue
            if price == 0:
                continue
            change_pct = (price - prev_close) / prev_close * 100 if prev_close else 0.0
            out.append(Quote(
                market="ashare",
                symbol=symbol,
                ts=now,
                price=Decimal(f"{price:.4f}"),
                change_pct=change_pct,
                volume=volume,
                source="sina",
            ))
        return out

    def _fetch_snapshot_mootdx(self, wanted: set[str]) -> list[Quote]:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market="std")
        now = datetime.now(timezone.utc)
        out: list[Quote] = []
        for code in wanted:
            try:
                df = client.quotes(symbol=[code])
                if df is None or df.empty:
                    continue
                row = df.iloc[0]
                out.append(Quote(
                    market="ashare",
                    symbol=_normalize_symbol(code),
                    ts=now,
                    price=Decimal(str(row.get("price", 0))),
                    change_pct=float(row.get("rate", 0.0)),
                    volume=int(row.get("vol", 0)),
                    source="mootdx",
                ))
            except Exception as e:  # noqa: BLE001
                log.warning("ashare.mootdx_symbol_failed", symbol=code, error=str(e))
        return out

    async def subscribe(self, symbols: list[str], on_bar: Callable[[Bar], None]) -> None:
        raise NotImplementedError("use scheduler polling for ashare")

    async def fetch_history(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        sina_code = _to_sina_code(symbol)
        sd = start.strftime("%Y%m%d")
        ed = end.strftime("%Y%m%d")

        kind = _classify(symbol)
        # akshare 这几个接口内部用 mini_racer 解 sina JS, ak_call 统一加锁
        if kind == "etf":
            df = await ak_call("fund_etf_hist_sina", symbol=sina_code,
                               caller=f"ashare.fetch_history:{symbol}:etf")
            df = df[(df["date"] >= start.date()) & (df["date"] <= end.date())]
        elif kind == "index":
            df = await ak_call("stock_zh_index_daily", symbol=sina_code,
                               caller=f"ashare.fetch_history:{symbol}:index")
            df = df[(df["date"] >= start.date()) & (df["date"] <= end.date())]
        else:
            df = await ak_call(
                "stock_zh_a_daily",
                symbol=sina_code, start_date=sd, end_date=ed, adjust="qfq",
                caller=f"ashare.fetch_history:{symbol}:stock",
            )
            df = await self._supplement_stock_daily_metrics(symbol, sd, ed, df)

        out: list[Bar] = []
        for _, row in df.iterrows():
            ts = datetime.combine(row["date"], datetime.min.time(), tzinfo=_CN_TZ).astimezone(timezone.utc)
            out.append(Bar(
                market="ashare",
                symbol=symbol,
                ts=ts,
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=int(row["volume"]),
                interval="1d",
                amount=_num(row, "amount", "成交额"),
                turnover=_num(row, "turnover", "换手率"),
                outstanding_share=_num(row, "outstanding_share", "流通股本"),
            ))
        # 追溯日志: A 股 daily 类问题 grep "ashare.daily.fetched" data/logs/api.log
        latest_ts = out[-1].ts.isoformat() if out else None
        latest_close = float(out[-1].close) if out else None
        log.info(
            "ashare.daily.fetched", symbol=symbol, kind=kind,
            req_start=start.isoformat(), req_end=end.isoformat(),
            bars=len(out), latest_ts=latest_ts, latest_close=latest_close,
        )
        return out

    async def _supplement_stock_daily_metrics(
        self, symbol: str, start_date: str, end_date: str, daily_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """用 stock_zh_a_hist 补 A 股日线成交额/换手率。

        stock_zh_a_daily 稳定但字段通常只有 OHLCV；stock_zh_a_hist 有成交额和换手率。
        这里仅在缺字段时低频补充，失败不影响 K 线返回。
        """
        has_amount = "amount" in daily_df.columns or "成交额" in daily_df.columns
        has_turnover = "turnover" in daily_df.columns or "换手率" in daily_df.columns
        if has_amount and has_turnover:
            return daily_df
        try:
            hist = await ak_call(
                "stock_zh_a_hist",
                symbol=_denormalize(symbol),
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
                caller=f"ashare.fetch_history:{symbol}:stock_metrics",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("ashare.daily_metrics_failed", symbol=symbol, error=str(e))
            return daily_df
        if hist is None or hist.empty or "日期" not in hist.columns:
            return daily_df

        metrics = {
            _date_key(row["日期"]): row
            for _, row in hist.iterrows()
        }
        rows = []
        for _, row in daily_df.iterrows():
            merged = row.to_dict()
            metric_row = metrics.get(_date_key(row["date"]))
            if metric_row is not None:
                if "amount" not in merged and "成交额" in metric_row:
                    merged["amount"] = metric_row["成交额"]
                if "turnover" not in merged and "换手率" in metric_row:
                    merged["turnover"] = metric_row["换手率"]
            rows.append(merged)
        return pd.DataFrame(rows)

    async def fetch_intraday(self, symbol: str, freq: str = "5") -> list[Bar]:
        """freq: '1'/'5'/'15'/'30'/'60' min。

        sina 当日复权因子盘中可能未入表,导致 adjust='qfq' 当日 OHLC 全部 NaN。
        T 日 qfq 数学上 = 不复权价(因子=1),所以 NaN 行用 adjust='' 当日数据兜底。

        ts 语义: sina `day` 字段实际是 bar **CLOSE** 时刻 (实测 5m 首根 = 09:35
        即 09:30-09:35 的 close, 末根 = 15:00 即 14:55-15:00 的 close)。
        雷区 3 要求 intraday bar.ts = close → UTC, 与 sina 语义天然吻合, **不需要 +interval**。
        1m 同样直通(详情图用)。
        """
        sina_code = _to_sina_code(symbol)
        df = await ak_call(
            "stock_zh_a_minute",
            symbol=sina_code, period=freq, adjust="qfq",
            caller=f"ashare.fetch_intraday:{symbol}:{freq}m",
        )
        nan_mask = df["open"].isna() | df["high"].isna() | df["low"].isna() | df["close"].isna()
        if nan_mask.any():
            df = await self._patch_today_nan(df, sina_code, freq, symbol)
        out: list[Bar] = []
        interval = f"{freq}m"
        for _, row in df.iterrows():
            if pd.isna(row["open"]) or pd.isna(row["high"]) or pd.isna(row["low"]) or pd.isna(row["close"]):
                continue
            # sina `day` 已是 close 时刻 (北京时间 wall-clock),标 +08:00 后转 UTC
            naive = datetime.fromisoformat(str(row["day"]).replace(" ", "T"))
            ts = naive.replace(tzinfo=_CN_TZ).astimezone(timezone.utc)
            out.append(Bar(
                market="ashare", symbol=symbol,
                ts=ts,
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=int(float(row["volume"])),
                interval=interval,
            ))
        return out

    async def _patch_today_nan(
        self, qfq_df: pd.DataFrame, sina_code: str, freq: str, symbol: str,
    ) -> pd.DataFrame:
        """sina qfq 当日因子未入表 → NaN 行用 adjust='' 兜底(T 日两者等价)。"""
        try:
            raw_df = await ak_call(
                "stock_zh_a_minute",
                symbol=sina_code, period=freq, adjust="",
                caller=f"ashare.fetch_intraday_raw:{symbol}:{freq}m",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("ashare.qfq_patch_failed", symbol=symbol, freq=freq, error=str(e))
            return qfq_df
        # sina HTTP 返回 OHLCV 字符串/float 不固定, qfq_df 列 dtype 也可能漂移。
        # pandas 2.x 不再隐式 cast → 显式把两边 OHLCV 都强转 float64 避免 TypeError。
        ohlcv_cols = ["open", "high", "low", "close", "volume"]
        for col in ohlcv_cols:
            if col in qfq_df.columns:
                qfq_df[col] = pd.to_numeric(qfq_df[col], errors="coerce")
            if col in raw_df.columns:
                raw_df[col] = pd.to_numeric(raw_df[col], errors="coerce")
        raw_by_day = {str(r["day"]): r for _, r in raw_df.iterrows()}
        patched = 0
        for idx, row in qfq_df.iterrows():
            if pd.isna(row["close"]) and str(row["day"]) in raw_by_day:
                src = raw_by_day[str(row["day"])]
                if not pd.isna(src["close"]):
                    qfq_df.at[idx, "open"] = src["open"]
                    qfq_df.at[idx, "high"] = src["high"]
                    qfq_df.at[idx, "low"] = src["low"]
                    qfq_df.at[idx, "close"] = src["close"]
                    qfq_df.at[idx, "volume"] = src["volume"]
                    patched += 1
        if patched:
            log.info("ashare.qfq_patched", symbol=symbol, freq=freq, patched=patched)
        return qfq_df

    async def health(self) -> HealthStatus:
        if not self.primary_cb.can_execute():
            return HealthStatus(name="ashare", state="degraded", detail="primary circuit open")
        try:
            r = await asyncio.to_thread(self._session.get, _SINA_BASE + "sh000001", timeout=3)
            r.raise_for_status()
            return HealthStatus(name="ashare", state="ok")
        except Exception as e:
            return HealthStatus(name="ashare", state="down", detail=str(e))
