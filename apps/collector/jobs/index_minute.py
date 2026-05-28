"""8 大 A 股指数 5min 序列预拉取 — Plan 2 Stage 4 引入。

替代 apps/api/routes/indices.py 的"路由内 ak_call",前端读 cache 不打 ak。
交易时段每 30s 一次, 非交易时段每 5min 一次 (cron 设置在 attach 函数里)。

2026-05-28 扩展: 注入 market_extras (amount + fund_inflow + amount_ratio)。
amount 从 sina spot 第 [9] 列直接拿 (零增量 ak_call), fund_inflow 走 akshare
stock_hsgt_fund_flow_summary_em (沪股通+深股通求和), amount_ratio 查 SQLite
baseline 算 = today_cum / prev_day_cum_at_offset - 1。

参考: docs/superpowers/specs/2026-05-28-market-index-extended-design.md
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
import structlog

from core.cache import keys
from core.cache.redis_client import RedisCache
from core.integrations.akshare import ak_call
from core.persistence.market_amount_baseline_repo import MarketAmountBaselineRepo

log = structlog.get_logger(__name__)

INDEX_SYMBOLS = [
    "000001.SH", "399001.SZ", "000300.SH", "399006.SZ",
    "000905.SH", "000852.SH", "000688.SH", "000016.SH",
]

_CN_TZ = ZoneInfo("Asia/Shanghai")
_CACHE_TTL_S = 90  # 30s 写 + 90s TTL = 充足覆盖
_SINA_SPOT_BASE = "https://hq.sinajs.cn/list="
_SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}

# A 股开盘 9:30 BJT 第 0 个 5m 桶, 收盘 15:00 (午休 11:30-13:00 不产生新桶)
# 上午: 9:30-11:30 = 24 桶 (offset 0..23)
# 下午: 13:00-15:00 = 24 桶 (offset 24..47)
_ASHARE_OPEN_BJT = time(9, 30)
_ASHARE_AM_CLOSE = time(11, 30)
_ASHARE_PM_OPEN = time(13, 0)
_ASHARE_PM_CLOSE = time(15, 0)


def _ashare_5m_offset(now_bjt: datetime) -> int | None:
    """计算 BJT 时刻对应的 5m 桶 offset (0-based)。

    收盘前最后一桶 = 47 (14:55-15:00)。盘前 / 午休 / 盘后返回 None。
    用于查 baseline 同时段累计。
    """
    t = now_bjt.time()
    if t < _ASHARE_OPEN_BJT or t > _ASHARE_PM_CLOSE:
        return None
    if _ASHARE_AM_CLOSE < t < _ASHARE_PM_OPEN:
        return None
    if t <= _ASHARE_AM_CLOSE:
        # 上午段
        delta_min = (now_bjt.replace(tzinfo=None) - now_bjt.replace(
            hour=9, minute=30, second=0, microsecond=0, tzinfo=None)
        ).total_seconds() / 60
        return min(int(delta_min // 5), 23)
    # 下午段
    delta_min = (now_bjt.replace(tzinfo=None) - now_bjt.replace(
        hour=13, minute=0, second=0, microsecond=0, tzinfo=None)
    ).total_seconds() / 60
    return min(24 + int(delta_min // 5), 47)


@dataclass
class SpotInfo:
    prev_close: float | None
    amount_yuan: float | None  # 当日累计成交额(元), sina spot [9]


def _fetch_spot_info_map_sync() -> dict[str, SpotInfo]:
    """批量拉 8 个指数 spot, 返回 {symbol: SpotInfo}。任何失败返回空 dict。

    sina hq_str_sh000001 字段索引 (实测 2026-05-28):
      [2] prev_close
      [9] 当日累计成交额(元) - 指数成分股加权
    """
    codes = ",".join(_to_sina_a(s) for s in INDEX_SYMBOLS)
    r = requests.get(_SINA_SPOT_BASE + codes, headers=_SINA_HEADERS, timeout=5)
    r.encoding = "gbk"
    r.raise_for_status()
    out: dict[str, SpotInfo] = {}
    for line in r.text.splitlines():
        if "hq_str_" not in line or '="' not in line:
            continue
        sina_code = line.split("hq_str_")[1].split("=")[0]
        payload = line.split('="', 1)[1].rstrip('";\n')
        parts = payload.split(",")
        if len(parts) < 10:
            continue
        symbol = f"{sina_code[2:]}.{sina_code[:2].upper()}"
        info = SpotInfo(prev_close=None, amount_yuan=None)
        try:
            info.prev_close = float(parts[2])
        except (ValueError, IndexError):
            pass
        try:
            info.amount_yuan = float(parts[9])
        except (ValueError, IndexError):
            pass
        out[symbol] = info
    return out


async def _fetch_spot_info_map() -> dict[str, SpotInfo]:
    try:
        return await asyncio.to_thread(_fetch_spot_info_map_sync)
    except Exception as e:  # noqa: BLE001
        log.warning("index_minute.spot_info_fetch_failed", error=str(e))
        return {}


def _to_sina_a(symbol: str) -> str:
    code, mkt = symbol.split(".")
    return f"{mkt.lower()}{code}"


async def _fetch_north_fund_inflow_yiyuan() -> float | None:
    """北向资金净流入(亿元), 沪股通 + 深股通求和。失败返 None。

    数据源: akshare stock_hsgt_fund_flow_summary_em
    columns: ['交易日', '类型', '板块', '资金方向', '交易状态', '成交净买额',
              '资金净流入', '当日资金余额', '上涨数', '持平数', '下跌数',
              '相关指数', '指数涨跌幅']
    """
    try:
        df = await ak_call(
            "stock_hsgt_fund_flow_summary_em",
            caller="index_minute.fund_inflow",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("index_minute.fund_inflow_failed", error=str(e))
        return None
    try:
        north = df[df["板块"].isin(["沪股通", "深股通"])]
        return float(north["资金净流入"].sum())
    except Exception as e:  # noqa: BLE001
        log.warning("index_minute.fund_inflow_parse_failed", error=str(e))
        return None


async def refresh_one_index(
    symbol: str, *, cache: RedisCache, prev_close: float | None = None,
    market_extras: dict | None = None,
) -> None:
    """拉一个指数当日 5m 数据, 写 cache。单条失败仅 warning, 不抛。

    prev_close: 昨收。前端用 (now - prev_close)/prev_close 算今日涨跌幅,
    不能用序列首点 (那是 9:35 盘中价, 会把跳空缺口算进涨跌幅)。
    market_extras: 8 个 A 股指数共享同一份 (市场级字段)。
    """
    sina_code = _to_sina_a(symbol)
    try:
        df = await ak_call(
            "stock_zh_a_minute", symbol=sina_code, period="5", adjust="",
            caller=f"index_minute.refresh:{symbol}",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("index_minute.fetch_failed", symbol=symbol, error=str(e))
        return

    cutoff_utc = datetime.now(timezone.utc) - timedelta(days=2)
    points = []
    for _, row in df.iterrows():
        try:
            naive = datetime.fromisoformat(str(row["day"]).replace(" ", "T"))
            ts = naive.replace(tzinfo=_CN_TZ).astimezone(timezone.utc)
            if ts < cutoff_utc:
                continue
            points.append({
                "ts": ts.isoformat(),
                "close": float(row["close"]),
                "volume": int(float(row["volume"])),
            })
        except Exception:  # noqa: BLE001
            continue
    # 取最近一个交易日 (按北京日期)
    if points:
        last_date_cn = datetime.fromisoformat(points[-1]["ts"]).astimezone(_CN_TZ).date()
        points = [p for p in points
                  if datetime.fromisoformat(p["ts"]).astimezone(_CN_TZ).date() == last_date_cn]
    payload = {
        "symbol": symbol,
        "granularity": "5m",
        "prev_close": prev_close,
        "points": points,
        "market_extras": market_extras or {},
        "meta": {"fresh_at": datetime.now(timezone.utc).isoformat(),
                 "stale": False, "source": "sina"},
    }
    await cache.set_msgpack(keys.cache_index_minute(symbol, days=1), payload, ttl_s=_CACHE_TTL_S)
    log.info("index_minute.cached", symbol=symbol, points=len(points), prev_close=prev_close)


async def refresh_all_indices(
    cache: RedisCache,
    baseline_repo: MarketAmountBaselineRepo | None = None,
) -> None:
    """循环刷新 8 个指数。单条失败不影响后续。

    非 A 股交易日(周末/法定节假日)直接跳过, 交易日只在 BJT 09:00-16:00 内跑。
    避免无意义 sina 调用。

    baseline_repo: 传入则计算 amount_ratio (查上一日同 offset);否则 ratio=None。
    """
    from core.domain.market_calendar import is_trading_day

    now_bjt = datetime.now(_CN_TZ)
    if not is_trading_day("ashare", now_bjt):
        log.debug("index_minute.skip_non_trading_day", date=str(now_bjt.date()))
        return
    if not (9 <= now_bjt.hour < 16):
        log.debug("index_minute.skip_off_hours", hour=now_bjt.hour)
        return

    spot_map = await _fetch_spot_info_map()
    fund_inflow = await _fetch_north_fund_inflow_yiyuan()

    # 上证指数代表"市场总成交额" (sina 第 [9] 列, 单位元)
    # 8 个指数 amount 都接近一致(都是大盘加权), 取上证作市场口径
    sse_amount_yuan = spot_map.get("000001.SH", SpotInfo(None, None)).amount_yuan
    amount_yiyuan = sse_amount_yuan / 1e8 if sse_amount_yuan else None

    # amount_ratio: 查 SQLite baseline 同 offset
    amount_ratio: float | None = None
    if baseline_repo is not None and amount_yiyuan is not None:
        offset = _ashare_5m_offset(now_bjt)
        if offset is not None:
            today_str = now_bjt.date().isoformat()
            try:
                prev = await baseline_repo.query_prev_day_at_offset(
                    "ashare", today_str, offset)
                if prev and prev > 0:
                    amount_ratio = sse_amount_yuan / prev - 1
            except Exception as e:  # noqa: BLE001
                log.warning("index_minute.amount_ratio_failed", error=str(e))

    market_extras = {
        "fund_inflow": fund_inflow,
        "fund_inflow_label": "北向" if fund_inflow is not None else None,
        "amount": amount_yiyuan,
        "amount_unit": "亿元" if amount_yiyuan is not None else None,
        "amount_ratio": amount_ratio,
    }
    log.info("index_minute.market_extras", **market_extras)

    for symbol in INDEX_SYMBOLS:
        info = spot_map.get(symbol, SpotInfo(None, None))
        await refresh_one_index(
            symbol, cache=cache,
            prev_close=info.prev_close,
            market_extras=market_extras,
        )

