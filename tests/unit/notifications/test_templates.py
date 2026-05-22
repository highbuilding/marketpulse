from datetime import datetime, timezone

from core.notifications.templates import render_summary
from core.persistence.signal_repo import TodaySignalCell


def _cell(count=1, price=100.0, ts=None):
    return TodaySignalCell(
        count=count,
        latest_price=price,
        latest_bar_ts=ts or datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc),
    )


def test_returns_three_part_tuple():
    subject, text, html = render_summary("ashare", {})
    assert isinstance(subject, str)
    assert isinstance(text, str)
    assert isinstance(html, str)
    assert "<html" in html.lower() or "<body" in html.lower() or "<div" in html.lower()


def test_renders_buy_only():
    cells = {
        ("600519.SH", "1d", "buy"): _cell(count=1, price=1800.50),
        ("600036.SH", "60m", "buy"): _cell(count=2, price=42.30),
    }
    subject, text, html = render_summary("ashare", cells)
    assert "A股" in subject
    assert "CD 信号汇总" in subject
    # text fallback
    assert "抄底信号" in text
    assert "卖出信号" not in text
    assert "600519.SH" in text
    assert "1次" in text
    assert "2次" in text
    # html
    assert "抄底信号" in html
    assert "卖出信号" not in html
    assert "1800.50" in html or "1800.5" in html  # 价格含两位小数
    assert "#dc2626" in html  # 红色主题


def test_renders_both_sections_html():
    cells = {
        ("600519.SH", "1d", "buy"): _cell(count=1),
        ("000001.SZ", "30m", "sell"): _cell(count=1),
    }
    _, _, html = render_summary("ashare", cells)
    assert "抄底信号" in html
    assert "卖出信号" in html
    assert "#dc2626" in html  # 红
    assert "#16a34a" in html  # 绿


def test_renders_chinese_name_in_html():
    cells = {("600519.SH", "1d", "buy"): _cell()}
    name_map = {"600519.SH": "贵州茅台"}
    _, _, html = render_summary("ashare", cells, name_map)
    assert "贵州茅台" in html
    # text 不需要包含中文名(只用对齐表格)
    # 但也不能崩


def test_renders_empty_shows_no_signal():
    _, text, html = render_summary("ashare", {})
    assert "本日无新信号" in text
    assert "本日无新信号" in html


def test_renders_zero_count_cells_omitted():
    cells = {
        ("600519.SH", "1d", "buy"): _cell(count=0),
        ("600036.SH", "60m", "buy"): _cell(count=1),
    }
    _, text, html = render_summary("ashare", cells)
    assert "600036.SH" in text
    assert "600519.SH" not in text
    assert "600036.SH" in html
    assert "600519.SH" not in html


def test_us_market_label():
    subject, _, html = render_summary("us", {("QQQ", "1d", "buy"): _cell(price=497.50)})
    assert "美股" in subject
    assert "美股" in html
    assert "$497.50" in html  # 美股用 $ 前缀


def test_html_includes_local_timezone_label():
    """美股邮件: 顶部时间是北京时间, 信号 section 右边显示美东时间。"""
    cells = {("QQQ", "1d", "buy"): _cell()}
    _, _, html = render_summary("us", cells)
    assert "北京时间" in html
    assert "美东时间" in html
    # section header 内含美东时间 badge
    assert "🔴 抄底信号" in html


def test_html_ashare_only_bjt_no_local_badge():
    """A 股本地就是北京时间, 信号 section 右边不显示重复 badge。"""
    cells = {("600519.SH", "1d", "buy"): _cell()}
    _, _, html = render_summary("ashare", cells)
    # 顶部时间显示一次(发送于 ... 北京时间)
    assert "发送于" in html
    assert html.count("北京时间") == 1
    # section header 不含美东时间
    assert "美东时间" not in html


def test_html_includes_full_date():
    cells = {("QQQ", "1d", "buy"): _cell()}
    _, _, html = render_summary("us", cells)
    import re
    assert re.search(r"\d{4}-\d{2}-\d{2}", html)


def test_subject_uses_bjt():
    """Subject 一律用北京时间, 与本市场无关。"""
    cells = {("QQQ", "1d", "buy"): _cell()}
    subject, _, _ = render_summary("us", cells)
    assert "北京时间" in subject
    # 美股 subject 不带美东时间 (避免歧义)
    assert "美东时间" not in subject

    subject_a, _, _ = render_summary("ashare", {("X.SH", "1d", "buy"): _cell()})
    assert "北京时间" in subject_a


def test_html_escapes_user_input():
    """name_map 来自外部数据, 必须 HTML 转义防注入。"""
    cells = {("X.SH", "1d", "buy"): _cell()}
    name_map = {"X.SH": "<script>alert(1)</script>"}
    _, _, html = render_summary("ashare", cells, name_map)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_html_shows_trigger_times_60m():
    """60m cell 下方应显示 bar 窗口区间 'open-close' (本市场时区)。

    QQQ 美股 60m 09:30 ET 这根 bar 实际是 09:00-09:30 ET 半棒。
    """
    ts_close_0930 = datetime(2026, 5, 21, 13, 30, tzinfo=timezone.utc)  # 09:30 ET
    ts_close_1030 = datetime(2026, 5, 21, 14, 30, tzinfo=timezone.utc)  # 10:30 ET
    cell = TodaySignalCell(
        count=2, latest_price=100.0, latest_bar_ts=ts_close_1030,
        trigger_times=(ts_close_0930, ts_close_1030),
    )
    cells = {("QQQ", "60m", "buy"): cell}
    _, _, html = render_summary("us", cells)
    # 09:00-09:30 (盘前半棒, RTH 边界硬断)
    assert "09:00-09:30" in html
    # 10:30 这根是完整 1h: 09:30-10:30
    assert "09:30-10:30" in html


def test_html_shows_trigger_times_4h_half_bar():
    """4h 美股 16:00 这根 bar 是 13:30-16:00 半棒(2.5h, RTH 收盘断)。"""
    ts_close_1600 = datetime(2026, 5, 21, 20, 0, tzinfo=timezone.utc)  # 16:00 ET
    cell = TodaySignalCell(
        count=1, latest_price=100.0, latest_bar_ts=ts_close_1600,
        trigger_times=(ts_close_1600,),
    )
    cells = {("QQQ", "4h", "sell"): cell}
    _, _, html = render_summary("us", cells)
    assert "13:30-16:00" in html


def test_html_shows_trigger_times_15m_uses_close_ts():
    """15m bar.ts = bar CLOSE (雷区 3 改造后), bar window = (ts - 15min, ts)。"""
    ts_close = datetime(2026, 5, 21, 14, 15, tzinfo=timezone.utc)  # 10:15 ET CLOSE
    cell = TodaySignalCell(
        count=1, latest_price=100.0, latest_bar_ts=ts_close,
        trigger_times=(ts_close,),
    )
    cells = {("QQQ", "15m", "sell"): cell}
    _, _, html = render_summary("us", cells)
    # 10:00-10:15 ET (close - 15m → 10:00 open)
    assert "10:00-10:15" in html


def test_html_shows_trigger_times_1d_uses_date():
    """1d cell 显示 MM-DD 而非时分。"""
    bar_ts = datetime(2026, 5, 21, 4, 0, tzinfo=timezone.utc)  # ET 00:00
    cell = TodaySignalCell(
        count=1, latest_price=100.0, latest_bar_ts=bar_ts,
        trigger_times=(bar_ts,),
    )
    cells = {("QQQ", "1d", "buy"): cell}
    _, _, html = render_summary("us", cells)
    assert "05-21" in html


def test_html_no_trigger_times_when_empty():
    cell = TodaySignalCell(
        count=1, latest_price=100.0,
        latest_bar_ts=datetime(2026, 5, 21, tzinfo=timezone.utc),
        trigger_times=(),
    )
    cells = {("X.SH", "1d", "buy"): cell}
    _, _, html = render_summary("ashare", cells)
    assert "1次" in html
