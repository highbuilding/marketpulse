from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.notifications.channel import NotificationError
from core.persistence.notification_repo import Recipient, SymbolConfig
from core.persistence.signal_repo import TodaySignalCell
from core.services.notification_service import NotificationService


def _cell(count=1, price=100.0, ts=None):
    return TodaySignalCell(
        count=count,
        latest_price=price,
        latest_bar_ts=ts or datetime(2026, 5, 21, 14, 0, tzinfo=timezone.utc),
    )


def _make_service(*, recipients, configs, cells, last_hash=None):
    notif_repo = MagicMock()
    notif_repo.list_recipients = AsyncMock(return_value=recipients)
    notif_repo.list_symbol_configs = AsyncMock(return_value=configs)
    notif_repo.last_audit_hash = AsyncMock(return_value=last_hash)
    notif_repo.record_audit = AsyncMock()

    signal_repo = MagicMock()
    signal_repo.latest_signals_today = AsyncMock(return_value=cells)

    email_channel = MagicMock()
    email_channel.name = "email"
    email_channel.enabled = True
    email_channel.send = AsyncMock()

    directory = MagicMock()
    directory.get_names = AsyncMock(return_value={})

    svc = NotificationService(
        notif_repo=notif_repo, signal_repo=signal_repo,
        channels={"email": email_channel},
        directory_service=directory,
    )
    return svc, notif_repo, signal_repo, email_channel, directory


@pytest.mark.asyncio
async def test_first_time_with_signals_sends_email():
    svc, notif_repo, _, ch, _ = _make_service(
        recipients=[Recipient(1, "ashare", "email", "a@x.com", True)],
        configs=[SymbolConfig("600519.SH", ["1d", "60m"])],
        cells={("600519.SH", "1d", "buy"): _cell()},
        last_hash=None,
    )
    sent = await svc.maybe_send_summary("ashare")
    assert sent is True
    ch.send.assert_called_once()
    args = ch.send.call_args.args
    kwargs = ch.send.call_args.kwargs
    # signature: send(address, subject, body, html=...)
    assert args[0] == "a@x.com"
    assert "CD 信号汇总" in args[1]
    # html 参数应被传入
    assert kwargs.get("html") is not None
    assert "<table" in kwargs["html"]
    notif_repo.record_audit.assert_called_once()


@pytest.mark.asyncio
async def test_same_hash_skips_send():
    cells = {("600519.SH", "1d", "buy"): _cell(count=1)}
    first_hash = NotificationService._hash(cells)
    svc, _, _, ch, _ = _make_service(
        recipients=[Recipient(1, "ashare", "email", "a@x.com", True)],
        configs=[SymbolConfig("600519.SH", ["1d"])],
        cells=cells,
        last_hash=first_hash,
    )
    sent = await svc.maybe_send_summary("ashare")
    assert sent is False
    ch.send.assert_not_called()


@pytest.mark.asyncio
async def test_empty_cells_skips_send():
    svc, _, _, ch, _ = _make_service(
        recipients=[Recipient(1, "ashare", "email", "a@x.com", True)],
        configs=[SymbolConfig("600519.SH", ["1d"])],
        cells={},
    )
    sent = await svc.maybe_send_summary("ashare")
    assert sent is False
    ch.send.assert_not_called()


@pytest.mark.asyncio
async def test_config_filters_unconfigured_intervals():
    svc, _, _, ch, _ = _make_service(
        recipients=[Recipient(1, "ashare", "email", "a@x.com", True)],
        configs=[SymbolConfig("600519.SH", ["1d"])],     # 没勾 60m
        cells={("600519.SH", "60m", "buy"): _cell()},   # 但有 60m 信号
    )
    sent = await svc.maybe_send_summary("ashare")
    assert sent is False  # 过滤后为空 → 不发
    ch.send.assert_not_called()


@pytest.mark.asyncio
async def test_market_filter_excludes_other_markets():
    svc, _, signal_repo, _, _ = _make_service(
        recipients=[Recipient(1, "ashare", "email", "a@x.com", True)],
        configs=[
            SymbolConfig("600519.SH", ["1d"]),
            SymbolConfig("QQQ", ["1d"]),
        ],
        cells={},
    )
    await svc.maybe_send_summary("ashare")
    args, _ = signal_repo.latest_signals_today.call_args
    symbols = args[0]
    assert "600519.SH" in symbols
    assert "QQQ" not in symbols


@pytest.mark.asyncio
async def test_no_recipients_skips_and_records():
    svc, notif_repo, _, ch, _ = _make_service(
        recipients=[],
        configs=[SymbolConfig("600519.SH", ["1d"])],
        cells={("600519.SH", "1d", "buy"): _cell()},
    )
    sent = await svc.maybe_send_summary("ashare")
    assert sent is False
    ch.send.assert_not_called()
    notif_repo.record_audit.assert_called_once()
    kwargs = notif_repo.record_audit.call_args.kwargs
    assert kwargs["sent"] is False
    assert "no recipients" in (kwargs.get("error") or "")


@pytest.mark.asyncio
async def test_channel_failure_records_error():
    svc, notif_repo, _, ch, _ = _make_service(
        recipients=[Recipient(1, "ashare", "email", "a@x.com", True)],
        configs=[SymbolConfig("600519.SH", ["1d"])],
        cells={("600519.SH", "1d", "buy"): _cell()},
    )
    ch.send = AsyncMock(side_effect=NotificationError("smtp down"))
    sent = await svc.maybe_send_summary("ashare")
    assert sent is False
    kwargs = notif_repo.record_audit.call_args.kwargs
    assert kwargs["sent"] is False
    assert "smtp down" in (kwargs.get("error") or "")


@pytest.mark.asyncio
async def test_directory_service_called_with_symbols():
    svc, _, _, ch, directory = _make_service(
        recipients=[Recipient(1, "ashare", "email", "a@x.com", True)],
        configs=[SymbolConfig("600519.SH", ["1d"])],
        cells={("600519.SH", "1d", "buy"): _cell()},
    )
    directory.get_names = AsyncMock(return_value={"600519.SH": "贵州茅台"})
    await svc.maybe_send_summary("ashare")
    directory.get_names.assert_awaited_once()
    # html 中应包含中文名
    kwargs = ch.send.call_args.kwargs
    assert "贵州茅台" in (kwargs.get("html") or "")


@pytest.mark.asyncio
async def test_directory_lookup_failure_does_not_block_send():
    svc, _, _, ch, directory = _make_service(
        recipients=[Recipient(1, "ashare", "email", "a@x.com", True)],
        configs=[SymbolConfig("600519.SH", ["1d"])],
        cells={("600519.SH", "1d", "buy"): _cell()},
    )
    directory.get_names = AsyncMock(side_effect=RuntimeError("db locked"))
    sent = await svc.maybe_send_summary("ashare")
    assert sent is True  # 名字拿不到也要发
    ch.send.assert_called_once()


def test_hash_stable_regardless_of_dict_order():
    a = NotificationService._hash({
        ("A", "1d", "buy"): _cell(count=1),
        ("B", "60m", "sell"): _cell(count=2),
    })
    b = NotificationService._hash({
        ("B", "60m", "sell"): _cell(count=2),
        ("A", "1d", "buy"): _cell(count=1),
    })
    assert a == b


def test_hash_changes_with_count():
    a = NotificationService._hash({("A", "1d", "buy"): _cell(count=1)})
    b = NotificationService._hash({("A", "1d", "buy"): _cell(count=2)})
    assert a != b


def test_hash_ignores_price_change():
    """价格波动不应触发 hash 变化(避免同根 bar 收盘前后微变误发)。"""
    a = NotificationService._hash({("A", "1d", "buy"): _cell(count=1, price=100.0)})
    b = NotificationService._hash({("A", "1d", "buy"): _cell(count=1, price=105.0)})
    assert a == b


def test_hash_compatible_with_int_values():
    """旧风格(value=int)和新风格(TodaySignalCell)应产生相同 hash, 便于平滑迁移。"""
    a = NotificationService._hash({("A", "1d", "buy"): 3})
    b = NotificationService._hash({("A", "1d", "buy"): _cell(count=3)})
    assert a == b


def test_today_start_utc_returns_natural_day_midnight():
    from zoneinfo import ZoneInfo

    bjt = ZoneInfo("Asia/Shanghai")
    start = NotificationService._today_start_utc("ashare")
    start_bjt = start.astimezone(bjt)
    assert start_bjt.hour == 0
    assert start_bjt.minute == 0
    assert start_bjt.second == 0


def test_today_start_utc_us_natural_day():
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    start = NotificationService._today_start_utc("us")
    start_et = start.astimezone(et)
    assert start_et.hour == 0
    assert start_et.minute == 0
