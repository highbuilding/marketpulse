"""邮件 / 通知文本模板。

render_summary(market, cells, name_map, now) -> (subject, text, html)
- text: 等宽对齐表格 (邮件客户端不支持 HTML 时降级)
- html: inline CSS 暗黑卡片 + 抄底红 / 卖出绿
"""
from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from core.domain.market_sessions import bucket_grid
from core.persistence.signal_repo import TodaySignalCell

# 渲染顺序: 富途口径常用周期顺序
INTERVAL_ORDER = ("15m", "30m", "60m", "4h", "1d")
INTERVAL_LABEL = {"15m": "15m", "30m": "30m", "60m": "1h", "4h": "4h", "1d": "1d"}

MARKET_LABEL = {"ashare": "A股", "us": "美股", "hk": "港股", "crypto": "Crypto"}
MARKET_TZ = {
    "ashare": "Asia/Shanghai",
    "us": "America/New_York",
    "hk": "Asia/Hong_Kong",
    "crypto": "UTC",
}
MARKET_TZ_LABEL = {"ashare": "北京时间", "us": "美东时间", "hk": "香港时间", "crypto": "UTC"}

# 价格货币符号(粗略, 仅用于显示)
MARKET_CURRENCY = {"ashare": "¥", "us": "$", "hk": "HK$", "crypto": ""}

# 颜色主题(中国习惯: 抄底=红, 卖出=绿)
COLOR_BUY = "#dc2626"
COLOR_SELL = "#16a34a"

# 暗色主题色板
BG = "#0a0a0a"
BG_HEADER = "#1f1f1f"
BORDER = "#262626"
TEXT = "#e5e5e5"
TEXT_DIM = "#a3a3a3"
TEXT_MUTED = "#737373"


def render_summary(
    market: str,
    cells: dict[tuple[str, str, str], TodaySignalCell],
    name_map: dict[str, str] | None = None,
    *, now: datetime | None = None,
) -> tuple[str, str, str]:
    """返回 (subject, text, html)。"""
    name_map = name_map or {}
    tz = ZoneInfo(MARKET_TZ.get(market, "UTC"))
    now = (now or datetime.now(tz)).astimezone(tz)
    market_name = MARKET_LABEL.get(market, market)
    tz_label = MARKET_TZ_LABEL.get(market, "UTC")
    currency = MARKET_CURRENCY.get(market, "")

    bjt = ZoneInfo("Asia/Shanghai")
    now_bjt = now.astimezone(bjt)

    subject = f"[MarketPulse] {market_name} CD 信号汇总  {now_bjt.strftime('%Y-%m-%d %H:%M')} 北京时间"

    text_body = _render_text(market_name, cells, now, now_bjt, tz_label)
    html_body = _render_html(
        market_name, cells, name_map,
        now=now, now_bjt=now_bjt, tz_label=tz_label, currency=currency,
        market_tz=tz, market=market,
    )
    return subject, text_body, html_body


# ---------- text fallback ----------

def _render_text(
    market_name: str,
    cells: dict[tuple[str, str, str], TodaySignalCell],
    now: datetime, now_bjt: datetime, tz_label: str,
) -> str:
    counts = {k: c.count for k, c in cells.items()}
    buy_table = _render_table(counts, "buy")
    sell_table = _render_table(counts, "sell")

    same_tz = now.utcoffset() == now_bjt.utcoffset()
    market_local_time = f"{now.strftime('%Y-%m-%d %H:%M')} {tz_label}"

    parts: list[str] = [
        f"[MarketPulse] {market_name} CD 信号汇总  发送于 {now_bjt.strftime('%Y-%m-%d %H:%M')} 北京时间",
        "",
    ]
    if buy_table:
        suffix = "" if same_tz else f"   ({market_local_time})"
        parts.append(f"抄底信号{suffix}")
        parts.append(buy_table)
        parts.append("")
    if sell_table:
        suffix = "" if same_tz else f"   ({market_local_time})"
        parts.append(f"卖出信号{suffix}")
        parts.append(sell_table)
        parts.append("")
    if not buy_table and not sell_table:
        parts.append("(本日无新信号)")
    parts.append("(本日累计, 仅展示出现 ≥1 次的标的)")
    return "\n".join(parts)


def _render_table(counts: dict[tuple[str, str, str], int], signal_type: str) -> str:
    rows: dict[str, dict[str, int]] = {}
    for (sym, iv, st), n in counts.items():
        if st != signal_type or n <= 0:
            continue
        rows.setdefault(sym, {})[iv] = n
    if not rows:
        return ""
    header = f"{'标的':<14}" + "".join(f"{INTERVAL_LABEL[iv]:>6}" for iv in INTERVAL_ORDER)
    lines = [header]
    for sym in sorted(rows.keys()):
        cells = []
        for iv in INTERVAL_ORDER:
            n = rows[sym].get(iv, 0)
            cells.append(f"{n}次" if n > 0 else "-")
        line = f"{sym:<14}" + "".join(f"{c:>6}" for c in cells)
        lines.append(line)
    return "\n".join(lines)


# ---------- HTML ----------

def _render_html(
    market_name: str,
    cells: dict[tuple[str, str, str], TodaySignalCell],
    name_map: dict[str, str],
    *, now: datetime, now_bjt: datetime, tz_label: str, currency: str,
    market_tz: ZoneInfo, market: str,
) -> str:
    same_tz = now.utcoffset() == now_bjt.utcoffset()
    # 信号 section 右侧的市场本地时间(同时区时不重复显示)
    local_time_badge = ""
    if not same_tz:
        local_time_badge = (
            f'<span style="color:{TEXT_MUTED};font-size:12px;font-weight:normal;'
            f'margin-left:12px">'
            f'{escape(tz_label)} {now.strftime("%Y-%m-%d %H:%M")}'
            f'</span>'
        )

    buy_section = _render_html_section(
        cells, "buy", "抄底信号", "🔴", COLOR_BUY, name_map, currency,
        local_time_badge=local_time_badge, market_tz=market_tz, market=market,
    )
    sell_section = _render_html_section(
        cells, "sell", "卖出信号", "🟢", COLOR_SELL, name_map, currency,
        local_time_badge=local_time_badge, market_tz=market_tz, market=market,
    )

    if not buy_section and not sell_section:
        body_inner = (
            f'<p style="color:{TEXT_DIM};text-align:center;padding:24px 0">'
            f'(本日无新信号)</p>'
        )
    else:
        body_inner = (buy_section or "") + (sell_section or "")

    # 邮件发送时间统一用北京时间
    time_line = (
        f'<span style="color:{TEXT_MUTED}">发送于</span>'
        f'<span style="color:{TEXT};margin-left:6px">'
        f'{now_bjt.strftime("%Y-%m-%d %H:%M")}</span>'
        f'<span style="color:{TEXT_MUTED};margin-left:6px">北京时间</span>'
    )

    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:{BG}">
<div style="font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',Arial,sans-serif;background:{BG};color:{TEXT};padding:24px;max-width:680px;margin:0 auto">
  <h2 style="margin:0 0 4px 0;font-size:20px;color:{TEXT};border-bottom:1px solid {BORDER};padding-bottom:10px">
    MarketPulse <span style="color:{TEXT_MUTED};font-weight:normal">·</span> {escape(market_name)} CD 信号汇总
  </h2>
  <p style="color:{TEXT_DIM};font-size:13px;margin:8px 0 20px 0">
    {time_line}
  </p>
  {body_inner}
  <p style="color:{TEXT_MUTED};font-size:11px;margin-top:28px;padding-top:12px;border-top:1px solid {BORDER}">
    次数 = 本日(本市场自然日)新生成 bar 上扫到的信号数 · 价格 = 当日最新触发 bar 的收盘价
  </p>
</div>
</body></html>"""
    return html


def _render_html_section(
    cells: dict[tuple[str, str, str], TodaySignalCell],
    signal_type: str, title: str, icon: str, color: str,
    name_map: dict[str, str], currency: str,
    *, local_time_badge: str = "",
    market_tz: ZoneInfo | None = None,
    market: str = "",
) -> str:
    # 收集本类型下出现过的 symbol -> {interval: cell}
    rows: dict[str, dict[str, TodaySignalCell]] = {}
    for (sym, iv, st), cell in cells.items():
        if st != signal_type or cell.count <= 0:
            continue
        rows.setdefault(sym, {})[iv] = cell
    if not rows:
        return ""

    header_html = (
        f'<tr style="background:{BG_HEADER}">'
        f'<th align="left" style="padding:8px 10px;font-size:12px;color:{TEXT_DIM};font-weight:600">标的</th>'
        f'<th align="left" style="padding:8px 10px;font-size:12px;color:{TEXT_DIM};font-weight:600">名称</th>'
        + "".join(
            f'<th align="center" style="padding:8px 6px;font-size:12px;color:{TEXT_DIM};font-weight:600;width:72px">{INTERVAL_LABEL[iv]}</th>'
            for iv in INTERVAL_ORDER
        )
        + '</tr>'
    )

    body_rows: list[str] = []
    for sym in sorted(rows.keys()):
        per_iv = rows[sym]
        price_cell: TodaySignalCell | None = None
        for iv in reversed(INTERVAL_ORDER):  # 1d 优先
            if iv in per_iv:
                price_cell = per_iv[iv]
                break
        price_html = ""
        if price_cell is not None:
            price_html = (
                f'<br><span style="color:{TEXT_MUTED};font-size:11px">'
                f'@ {currency}{price_cell.latest_price:.2f}</span>'
            )
        name = escape(name_map.get(sym, "—"))

        cells_html = []
        for iv in INTERVAL_ORDER:
            cell = per_iv.get(iv)
            if cell is None:
                cells_html.append(
                    f'<td align="center" style="padding:8px 6px;color:{TEXT_MUTED};font-size:13px">-</td>'
                )
            else:
                times_html = _format_trigger_times(
                    cell.trigger_times, iv, market_tz, market,
                )
                times_block = (
                    f'<br><span style="color:{TEXT_MUTED};font-size:10px;line-height:1.3">'
                    f'{times_html}</span>'
                ) if times_html else ""
                cells_html.append(
                    f'<td align="center" style="padding:8px 6px;color:{color};font-size:13px;font-weight:600">'
                    f'{cell.count}次{times_block}</td>'
                )

        body_rows.append(
            f'<tr style="border-top:1px solid {BORDER}">'
            f'<td style="padding:8px 10px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;color:{TEXT};vertical-align:top">'
            f'<b>{escape(sym)}</b>{price_html}</td>'
            f'<td style="padding:8px 10px;color:{TEXT_DIM};font-size:13px;vertical-align:top">{name}</td>'
            + "".join(cells_html)
            + '</tr>'
        )

    return f"""
  <h3 style="color:{color};border-left:4px solid {color};padding:6px 0 6px 12px;margin:20px 0 6px 0;font-size:15px">
    {icon} {title}{local_time_badge}
  </h3>
  <table style="width:100%;border-collapse:collapse;background:{BG};border:1px solid {BORDER};border-radius:6px;overflow:hidden">
    <thead>{header_html}</thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table>
"""


def _format_trigger_times(
    trigger_times: tuple[datetime, ...], interval: str,
    market_tz: ZoneInfo | None, market: str = "",
) -> str:
    """把触发时间序列格式化成窗口区间字符串。

    - 1d:        'MM-DD' (1d 一天最多 1 次)
    - 60m / 4h:  '开始-结束' wall-clock; 用 bucket_grid 反查 open
                 完整 '10:30-11:30' / 半棒 '09:00-09:30'
    - 15m / 30m: '开始-结束' wall-clock; ts 是 bar START, 直接 +interval 算 close
    - 多次用 ' / ' 分隔
    """
    if not trigger_times or market_tz is None:
        return ""
    if interval == "1d":
        seen = set()
        formatted: list[str] = []
        for t in trigger_times:
            s = t.astimezone(market_tz).strftime("%m-%d")
            if s not in seen:
                seen.add(s)
                formatted.append(s)
        return " / ".join(formatted)

    parts: list[str] = []
    for t in trigger_times:
        open_local, close_local = _bar_window(t, interval, market_tz, market)
        if open_local is None or close_local is None:
            # 万一对不上 bucket(数据异常)— 退回单一 close 时刻
            parts.append(t.astimezone(market_tz).strftime("%H:%M"))
        else:
            parts.append(
                f"{open_local.strftime('%H:%M')}-{close_local.strftime('%H:%M')}"
            )
    return " / ".join(parts)


def _bar_window(
    ts_utc: datetime, interval: str, market_tz: ZoneInfo, market: str,
) -> tuple[datetime | None, datetime | None]:
    """根据 interval 反推 bar (open, close) 的本地时刻。

    所有 intraday bar.ts = bar close 时刻 (雷区 3, 1m 除外):
    - 5m / 15m / 30m: open = ts - interval, close = ts
    - 60m / 4h: 用 bucket_grid 反查 (open, close), 因为半棒 bar 不能简单减 60min/4h
    """
    if interval in ("5m", "15m", "30m"):
        delta = timedelta(minutes=int(interval[:-1]))
        close_local = ts_utc.astimezone(market_tz)
        open_local = (ts_utc - delta).astimezone(market_tz)
        return open_local, close_local

    if interval in ("60m", "4h") and market in ("ashare", "us", "hk", "crypto"):
        interval_minutes = 60 if interval == "60m" else 240
        # bar 的 close 时刻是 ts_utc;反查 bucket_grid 找匹配的 (open, close)
        local_date = ts_utc.astimezone(market_tz).date()
        try:
            grid = bucket_grid(market, local_date, interval_minutes)  # type: ignore[arg-type]
        except Exception:
            return None, None
        for open_utc, close_utc in grid:
            if close_utc == ts_utc:
                return open_utc.astimezone(market_tz), close_utc.astimezone(market_tz)
        # ts 跨日(理论上不会, 防御): 看前一天
        try:
            grid_prev = bucket_grid(market, local_date - timedelta(days=1), interval_minutes)  # type: ignore[arg-type]
        except Exception:
            return None, None
        for open_utc, close_utc in grid_prev:
            if close_utc == ts_utc:
                return open_utc.astimezone(market_tz), close_utc.astimezone(market_tz)
        return None, None

    return None, None
