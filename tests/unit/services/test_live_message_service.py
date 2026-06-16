from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.domain.models import Bar, FundFlowSnapshot, ThemeConstituent, ThemeDefinition
from core.persistence.fund_flow_repo import FundFlowRepo
from core.persistence.sqlite_repo import StateRepo
from core.persistence.theme_repo import ThemeRepo
from core.services.live_message_service import LiveMessageService


class _FakeBarRepo:
    def __init__(self, rows: dict[str, list[Bar]]) -> None:
        self.rows = rows

    def fetch_history_paged(
        self,
        market: str,
        symbol: str,
        interval: str,
        *,
        before: datetime | None,
        limit: int,
        closed_only: bool = False,
    ) -> list[Bar]:
        assert market == "ashare"
        assert interval == "1d"
        assert closed_only is True
        rows = self.rows.get(symbol, [])
        if before is not None:
            rows = [row for row in rows if row.ts < before]
        return rows[-limit:]


def _daily_bar(symbol: str, ts: datetime, close: float) -> Bar:
    value = Decimal(str(close))
    return Bar(
        market="ashare",
        symbol=symbol,
        ts=ts,
        interval="1d",
        open=value,
        high=value,
        low=value,
        close=value,
        volume=100,
        final=True,
    )


async def _service(tmp_path: Path, *, watch_symbols: list[str] | None = None) -> LiveMessageService:
    svc, _ = await _service_with_repo(tmp_path, watch_symbols=watch_symbols)
    return svc


async def _service_with_repo(
    tmp_path: Path,
    *,
    watch_symbols: list[str] | None = None,
) -> tuple[LiveMessageService, ThemeRepo]:
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    theme_repo = ThemeRepo(str(db))
    await theme_repo.seed_definitions(
        [
            ThemeDefinition(
                market="ashare",
                theme_code="theme:test",
                theme_name="测试题材",
                classification="theme",
                priority="P0",
                source="seed",
            ),
        ],
        [
            ThemeConstituent("ashare", "theme:test", "000001.SZ", "核心A", "leader", 10, source="seed"),
            ThemeConstituent("ashare", "theme:test", "000002.SZ", "核心B", "core", 9, source="seed"),
            ThemeConstituent("ashare", "theme:test", "000003.SZ", "跟随C", "follower", 5, source="seed"),
            ThemeConstituent("ashare", "theme:test", "000004.SZ", "跟随D", "follower", 4, source="seed"),
            ThemeConstituent("ashare", "theme:test", "000005.SZ", "跟随E", "watch", 3, source="seed"),
        ],
    )
    watchlist = AsyncMock()
    watchlist.dynamic_universe = AsyncMock(return_value=watch_symbols or [])
    return LiveMessageService(theme_repo, watchlist), theme_repo


async def _service_with_fund_flow(tmp_path: Path) -> tuple[LiveMessageService, FundFlowRepo]:
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    theme_repo = ThemeRepo(str(db))
    await theme_repo.seed_definitions(
        [
            ThemeDefinition(
                market="ashare",
                theme_code="theme:test",
                theme_name="测试题材",
                classification="theme",
                priority="P0",
                source="seed",
            ),
        ],
        [
            ThemeConstituent("ashare", "theme:test", "000001.SZ", "核心A", "leader", 10, source="seed"),
            ThemeConstituent("ashare", "theme:test", "000002.SZ", "核心B", "core", 9, source="seed"),
            ThemeConstituent("ashare", "theme:test", "000003.SZ", "跟随C", "follower", 5, source="seed"),
        ],
    )
    fund_repo = FundFlowRepo(str(db))
    watchlist = AsyncMock()
    watchlist.dynamic_universe = AsyncMock(return_value=[])
    return LiveMessageService(theme_repo, watchlist, fund_repo), fund_repo


async def _service_with_bar_repo(tmp_path: Path, bar_repo: _FakeBarRepo) -> LiveMessageService:
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    theme_repo = ThemeRepo(str(db))
    await theme_repo.seed_definitions(
        [
            ThemeDefinition(
                market="ashare",
                theme_code="theme:test",
                theme_name="测试题材",
                classification="theme",
                priority="P0",
                source="seed",
            ),
        ],
        [
            ThemeConstituent("ashare", "theme:test", "000001.SZ", "核心A", "leader", 10, source="seed"),
            ThemeConstituent("ashare", "theme:test", "000002.SZ", "核心B", "core", 9, source="seed"),
            ThemeConstituent("ashare", "theme:test", "000003.SZ", "跟随C", "follower", 5, source="seed"),
            ThemeConstituent("ashare", "theme:test", "000004.SZ", "跟随D", "follower", 4, source="seed"),
        ],
    )
    watchlist = AsyncMock()
    watchlist.dynamic_universe = AsyncMock(return_value=[])
    return LiveMessageService(theme_repo, watchlist, None, bar_repo)  # type: ignore[arg-type]


async def _tick(
    svc: LiveMessageService,
    symbol: str,
    change_pct: float,
    ts: datetime,
    *,
    amount: float | None = None,
) -> list:
    payload = {
        "market": "ashare",
        "symbol": symbol,
        "ts": ts.isoformat(),
        "price": 10,
        "change_pct": change_pct,
        "volume": 100,
    }
    if amount is not None:
        payload["amount"] = amount
    return await svc.handle_quote_tick(payload)


async def _bar(
    svc: LiveMessageService,
    symbol: str,
    volume: int,
    ts: datetime,
    *,
    open_price: float = 10,
    close_price: float = 10.5,
) -> list:
    return await svc.handle_bar_updated({
        "market": "ashare",
        "symbol": symbol,
        "interval": "5m",
        "ts": ts.isoformat(),
        "open": open_price,
        "high": max(open_price, close_price),
        "low": min(open_price, close_price),
        "close": close_price,
        "volume": volume,
        "final": True,
    })


@pytest.mark.asyncio
async def test_theme_strength_message_is_deduped(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    assert await _tick(svc, "000001.SZ", 2.5, ts) == []
    assert await _tick(svc, "000002.SZ", 2.0, ts) == []

    first = await _tick(svc, "000003.SZ", 1.8, ts)
    assert len(first) == 1
    assert first[0].category == "theme"
    assert first[0].title == "测试题材启动"
    assert first[0].theme_code == "theme:test"
    assert first[0].payload["up_count"] == 3
    assert first[0].payload["core_up_count"] == 2

    again = await _tick(svc, "000004.SZ", 1.6, ts)
    assert [m.title for m in again] == ["测试题材进入扩散"]


@pytest.mark.asyncio
async def test_theme_eval_persists_snapshot_and_state(tmp_path: Path):
    svc, theme_repo = await _service_with_repo(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    await _tick(svc, "000001.SZ", 2.5, ts)
    await _tick(svc, "000002.SZ", 2.0, ts)
    await _tick(svc, "000003.SZ", 1.8, ts)

    snapshots = await theme_repo.list_recent_snapshots("ashare", limit=5)
    states = await theme_repo.list_states("ashare")

    assert snapshots[0].theme_code == "theme:test"
    assert snapshots[0].ts == ts.replace(minute=35, second=0, microsecond=0)
    assert snapshots[0].up_ratio == 1.0
    assert snapshots[0].leader_symbols == ["000001.SZ", "000002.SZ", "000003.SZ"]
    assert states[0].theme_code == "theme:test"
    assert states[0].state == "launch"
    assert states[0].evidence["up_count"] == 3


@pytest.mark.asyncio
async def test_watchlist_flip_message(tmp_path: Path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    theme_repo = ThemeRepo(str(db))
    watchlist = AsyncMock()
    watchlist.dynamic_universe = AsyncMock(return_value=["600519.SH"])
    svc = LiveMessageService(theme_repo, watchlist)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    base = {"market": "ashare", "ts": ts.isoformat(), "symbol": "600519.SH", "price": 100, "volume": 100}
    assert await svc.handle_quote_tick({**base, "change_pct": -0.2}) == []
    messages = await svc.handle_quote_tick({**base, "change_pct": 0.1})
    assert len(messages) == 1
    assert messages[0].category == "watchlist"
    assert messages[0].title == "自选股 600519.SH 翻红"


@pytest.mark.asyncio
async def test_watchlist_volume_spike_message(tmp_path: Path):
    svc = await _service(tmp_path, watch_symbols=["000001.SZ"])
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    for i in range(4):
        assert await _bar(svc, "000001.SZ", 1000, ts.replace(minute=35 + i * 5)) == []
    messages = await _bar(svc, "000001.SZ", 3000, ts.replace(minute=55))

    assert len(messages) == 1
    assert messages[0].category == "watchlist"
    assert messages[0].title == "自选股 000001.SZ 5m明显放量"
    assert messages[0].payload["volume_ratio"] == 3.0


@pytest.mark.asyncio
async def test_volume_spike_ignores_unrelated_symbol(tmp_path: Path):
    svc = await _service(tmp_path, watch_symbols=[])
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    for i in range(4):
        assert await _bar(svc, "600519.SH", 1000, ts.replace(minute=35 + i * 5)) == []
    assert await _bar(svc, "600519.SH", 4000, ts.replace(minute=55)) == []


@pytest.mark.asyncio
async def test_theme_leader_switch_message(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    assert await _tick(svc, "000001.SZ", 2.5, ts) == []
    assert await _tick(svc, "000002.SZ", 2.0, ts) == []
    assert [m.title for m in await _tick(svc, "000003.SZ", 1.5, ts)] == ["测试题材启动"]

    messages = await _tick(svc, "000002.SZ", 4.0, ts)
    assert [m.title for m in messages] == ["测试题材核心股切换"]
    assert messages[0].payload["prev_leader"] == "000001.SZ"


@pytest.mark.asyncio
async def test_theme_quality_risk_messages(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    assert await _tick(svc, "000001.SZ", 0.2, ts) == []
    assert await _tick(svc, "000002.SZ", -0.1, ts) == []
    assert await _tick(svc, "000003.SZ", 2.5, ts) == []
    messages = await _tick(svc, "000004.SZ", 1.5, ts)

    titles = {m.title for m in messages}
    assert "测试题材启动" in titles
    assert "测试题材走强质量一般" in titles
    risk = next(m for m in messages if m.title == "测试题材走强质量一般")
    assert risk.category == "risk"
    assert risk.payload["core_up_count"] == 1


@pytest.mark.asyncio
async def test_theme_single_leader_risk(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    assert await _tick(svc, "000001.SZ", 4.5, ts) == []
    assert await _tick(svc, "000002.SZ", 1.0, ts) == []
    messages = await _tick(svc, "000003.SZ", -0.2, ts)

    assert {m.title for m in messages} == {"测试题材进入分歧", "测试题材异动偏单点"}
    assert all(m.category == "risk" for m in messages)


@pytest.mark.asyncio
async def test_theme_active_state_adds_amount_confirmation(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    assert await _tick(svc, "000001.SZ", 5.2, ts, amount=300) == []
    assert await _tick(svc, "000002.SZ", 5.0, ts, amount=250) == []
    messages = await _tick(svc, "000003.SZ", 1.8, ts, amount=200)

    titles = {m.title for m in messages}
    assert "测试题材启动" in titles
    assert "测试题材扩散有成交确认" in titles
    confirmed = next(m for m in messages if m.title == "测试题材扩散有成交确认")
    assert confirmed.payload["active_money_confirmed"] is True
    assert confirmed.payload["leader_amount_share"] == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_theme_amount_concentration_risk(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    await _tick(svc, "000001.SZ", 2.5, ts, amount=1_000)
    await _tick(svc, "000002.SZ", 2.0, ts, amount=100)
    await _tick(svc, "000003.SZ", 1.8, ts, amount=100)
    messages = await _tick(svc, "000004.SZ", 1.6, ts, amount=100)

    assert "测试题材成交集中度偏高" in {m.title for m in messages}
    risk = next(m for m in messages if m.title == "测试题材成交集中度偏高")
    assert risk.category == "risk"
    assert risk.payload["leader_amount_share"] == pytest.approx(1_000 / 1_300)


@pytest.mark.asyncio
async def test_theme_active_state_adds_same_day_core_fund_flow_confirmation(tmp_path: Path):
    svc, fund_repo = await _service_with_fund_flow(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)
    await fund_repo.save_symbol_flows([
        FundFlowSnapshot("000001.SZ", "symbol", ts, main_net=10_000_000),
        FundFlowSnapshot("000002.SZ", "symbol", ts, main_net=5_000_000),
    ])

    assert await _tick(svc, "000001.SZ", 2.5, ts) == []
    assert await _tick(svc, "000002.SZ", 2.0, ts) == []
    messages = await _tick(svc, "000003.SZ", 1.8, ts)

    titles = {m.title for m in messages}
    assert "测试题材启动" in titles
    assert "测试题材核心资金流确认" in titles
    confirmed = next(m for m in messages if m.title == "测试题材核心资金流确认")
    assert confirmed.payload["fund_flow_available"] is True
    assert confirmed.payload["core_main_net"] == pytest.approx(15_000_000)
    assert confirmed.payload["core_positive_flow_count"] == 2


@pytest.mark.asyncio
async def test_theme_active_state_warns_same_day_core_fund_flow_divergence(tmp_path: Path):
    svc, fund_repo = await _service_with_fund_flow(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)
    await fund_repo.save_symbol_flows([
        FundFlowSnapshot("000001.SZ", "symbol", ts, main_net=-8_000_000),
        FundFlowSnapshot("000002.SZ", "symbol", ts, main_net=-3_000_000),
    ])

    await _tick(svc, "000001.SZ", 2.5, ts)
    await _tick(svc, "000002.SZ", 2.0, ts)
    messages = await _tick(svc, "000003.SZ", 1.8, ts)

    assert "测试题材上涨但资金流背离" in {m.title for m in messages}
    risk = next(m for m in messages if m.title == "测试题材上涨但资金流背离")
    assert risk.category == "risk"
    assert risk.payload["fund_flow_available"] is True
    assert risk.payload["core_main_net"] == pytest.approx(-11_000_000)


@pytest.mark.asyncio
async def test_theme_fund_flow_evidence_ignores_stale_previous_day_rows(tmp_path: Path):
    svc, fund_repo = await _service_with_fund_flow(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)
    stale = datetime(2026, 6, 13, 16, 0, tzinfo=timezone.utc)
    await fund_repo.save_symbol_flows([
        FundFlowSnapshot("000001.SZ", "symbol", stale, main_net=99_000_000),
    ])

    await _tick(svc, "000001.SZ", 2.5, ts)
    await _tick(svc, "000002.SZ", 2.0, ts)
    messages = await _tick(svc, "000003.SZ", 1.8, ts)

    assert "测试题材核心资金流确认" not in {m.title for m in messages}
    launch = next(m for m in messages if m.title == "测试题材启动")
    assert launch.payload["fund_flow_available"] is False
    assert launch.payload["fund_flow_reason"] == "same_day_core_flow_missing"


@pytest.mark.asyncio
async def test_theme_limit_structure_and_leader_pullback_proxy(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    await _tick(svc, "000001.SZ", 9.9, ts, amount=300)
    await _tick(svc, "000002.SZ", 7.4, ts, amount=200)
    messages = await _tick(svc, "000003.SZ", 7.1, ts, amount=180)

    assert "测试题材涨停结构增强" in {m.title for m in messages}
    structure = next(m for m in messages if m.title == "测试题材涨停结构增强")
    assert structure.payload["limit_up_count"] == 1
    assert structure.payload["near_limit_count"] == 3

    pullback = await _tick(svc, "000001.SZ", 6.8, ts, amount=360)
    assert "测试题材龙头高位回落" in {m.title for m in pullback}


@pytest.mark.asyncio
async def test_theme_limit_continuation_uses_previous_closed_daily_bars(tmp_path: Path):
    ts = datetime(2026, 6, 16, 1, 35, tzinfo=timezone.utc)
    prev_prev = datetime(2026, 6, 13, 16, 0, tzinfo=timezone.utc)
    prev = datetime(2026, 6, 14, 16, 0, tzinfo=timezone.utc)
    bar_repo = _FakeBarRepo({
        "000001.SZ": [
            _daily_bar("000001.SZ", prev_prev, 10),
            _daily_bar("000001.SZ", prev, 11),
        ],
        "000002.SZ": [
            _daily_bar("000002.SZ", prev_prev, 10),
            _daily_bar("000002.SZ", prev, 10.2),
        ],
        "000003.SZ": [
            _daily_bar("000003.SZ", prev_prev, 10),
            _daily_bar("000003.SZ", prev, 10.1),
        ],
    })
    svc = await _service_with_bar_repo(tmp_path, bar_repo)

    await _tick(svc, "000001.SZ", 9.9, ts, amount=300)
    await _tick(svc, "000002.SZ", 7.2, ts, amount=200)
    messages = await _tick(svc, "000003.SZ", 7.1, ts, amount=180)

    assert "测试题材连板结构增强" in {m.title for m in messages}
    continuation = next(m for m in messages if m.title == "测试题材连板结构增强")
    assert continuation.payload["limit_structure_scope"] == "当前采集清单,非全A"
    assert continuation.payload["previous_limit_like_count"] == 1
    assert continuation.payload["continuation_limit_count"] == 1
    assert continuation.payload["continuation_limit_symbols"][0]["symbol"] == "000001.SZ"


@pytest.mark.asyncio
async def test_theme_limit_break_proxy_uses_intraday_peak(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    await _tick(svc, "000001.SZ", 9.9, ts, amount=300)
    await _tick(svc, "000002.SZ", 7.3, ts, amount=220)
    await _tick(svc, "000003.SZ", 7.1, ts, amount=180)
    messages = await _tick(svc, "000001.SZ", 6.9, ts.replace(minute=45), amount=380)

    assert "测试题材炸板风险出现" in {m.title for m in messages}
    broken = next(m for m in messages if m.title == "测试题材炸板风险出现")
    assert broken.category == "risk"
    assert broken.payload["broken_limit_count"] == 1
    assert broken.payload["broken_limit_symbols"][0]["symbol"] == "000001.SZ"
    assert broken.payload["broken_limit_symbols"][0]["peak_change_pct"] == pytest.approx(9.9)


@pytest.mark.asyncio
async def test_theme_strength_fade_risk(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    await _tick(svc, "000001.SZ", 1.8, ts)
    await _tick(svc, "000002.SZ", 1.7, ts)
    await _tick(svc, "000003.SZ", 1.6, ts)
    await _tick(svc, "000004.SZ", 1.5, ts)

    assert await _tick(svc, "000003.SZ", -0.2, ts) == []
    messages = await _tick(svc, "000004.SZ", -0.3, ts)
    assert [m.title for m in messages] == ["测试题材强度回落"]
    assert messages[0].payload["peak_up_count"] == 4


@pytest.mark.asyncio
async def test_watchlist_against_theme_risk(tmp_path: Path):
    svc = await _service(tmp_path, watch_symbols=["000005.SZ"])
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    await _tick(svc, "000001.SZ", 2.0, ts)
    await _tick(svc, "000002.SZ", 1.8, ts)
    await _tick(svc, "000003.SZ", 1.5, ts)
    messages = await _tick(svc, "000005.SZ", -1.2, ts)

    assert [m.title for m in messages] == ["自选股 000005.SZ 逆测试题材走弱"]
    assert messages[0].category == "risk"


@pytest.mark.asyncio
async def test_collector_breadth_weak_message_uses_sample_scope(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    messages = []
    for i in range(20):
        messages = await _tick(svc, f"60{i:04d}.SH", -0.6, ts)

    assert [m.title for m in messages] == ["采集样本宽度偏弱"]
    assert messages[0].category == "index"
    assert messages[0].payload["sample_source"] == "collector_symbols"
    assert messages[0].payload["sample_scope"] == "当前采集清单,非全A"
    assert messages[0].payload["sample_count"] == 20
    assert messages[0].payload["down_count"] == 20


@pytest.mark.asyncio
async def test_collector_breadth_fast_deterioration_uses_5m_bucket_baseline(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    for i in range(20):
        await _tick(svc, f"60{i:04d}.SH", 0.1, ts)

    messages = []
    next_bucket = ts.replace(minute=40)
    for i in range(5):
        messages = await _tick(svc, f"60{i:04d}.SH", -0.8, next_bucket)

    assert "采集样本宽度快速恶化" in {m.title for m in messages}
    risk = next(m for m in messages if m.title == "采集样本宽度快速恶化")
    assert risk.category == "risk"
    assert risk.payload["sample_scope"] == "当前采集清单,非全A"
    assert risk.payload["baseline_down_count"] == 0
    assert risk.payload["down_count"] == 5


@pytest.mark.asyncio
async def test_collector_breadth_limit_structure_and_down_limit_proxy(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    messages = []
    for i in range(17):
        messages = await _tick(svc, f"60{i:04d}.SH", 0.1, ts)
    messages = await _tick(svc, "600017.SH", 9.9, ts)
    messages = await _tick(svc, "600018.SH", 7.5, ts)
    messages = await _tick(svc, "600019.SH", 7.2, ts)

    assert "采集样本涨停结构增强" in {m.title for m in messages}
    limit_msg = next(m for m in messages if m.title == "采集样本涨停结构增强")
    assert limit_msg.payload["near_limit_count"] == 3
    assert limit_msg.payload["up_limit_count"] == 1

    down_ts = ts.replace(minute=40)
    for i in range(17):
        messages = await _tick(svc, f"60{i:04d}.SH", -0.1, down_ts)
    messages = await _tick(svc, "600017.SH", -9.9, down_ts)
    messages = await _tick(svc, "600018.SH", -7.5, down_ts)
    messages = await _tick(svc, "600019.SH", -7.2, down_ts)

    assert "采集样本跌停风险扩散" in {m.title for m in messages}
    risk = next(m for m in messages if m.title == "采集样本跌停风险扩散")
    assert risk.payload["severe_down_count"] == 3
    assert risk.payload["down_limit_count"] == 1


@pytest.mark.asyncio
async def test_index_strong_but_collector_breadth_diverges(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    await _tick(svc, "000001.SH", 0.5, ts)
    await _tick(svc, "000300.SH", 0.6, ts)
    await _tick(svc, "399006.SZ", 0.7, ts)
    await _tick(svc, "000852.SH", 0.8, ts)

    messages = []
    for i in range(20):
        messages = await _tick(svc, f"60{i:04d}.SH", -0.7, ts)

    titles = {m.title for m in messages}
    assert "采集样本宽度偏弱" in titles
    assert "指数偏强但采集样本背离" in titles
    risk = next(m for m in messages if m.title == "指数偏强但采集样本背离")
    assert risk.category == "risk"
    assert risk.payload["index_state"] == "resonance_up"


@pytest.mark.asyncio
async def test_theme_state_machine_persists_diffusion(tmp_path: Path):
    svc, theme_repo = await _service_with_repo(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    await _tick(svc, "000001.SZ", 2.5, ts)
    await _tick(svc, "000002.SZ", 2.0, ts)
    await _tick(svc, "000003.SZ", 1.8, ts)
    messages = await _tick(svc, "000004.SZ", 1.6, ts)
    states = await theme_repo.list_states("ashare")

    assert [m.title for m in messages] == ["测试题材进入扩散"]
    assert states[0].state == "diffusion"
    assert states[0].evidence["state"] == "diffusion"
    assert states[0].evidence["core_up_count"] == 2


@pytest.mark.asyncio
async def test_theme_rotation_message_when_new_theme_replaces_fading_theme(tmp_path: Path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    theme_repo = ThemeRepo(str(db))
    await theme_repo.seed_definitions(
        [
            ThemeDefinition("ashare", "theme:old", "旧题材", "theme", priority="P0", source="seed"),
            ThemeDefinition("ashare", "theme:new", "新题材", "theme", priority="P0", source="seed"),
        ],
        [
            ThemeConstituent("ashare", "theme:old", "000001.SZ", "旧核心A", "leader", 10, source="seed"),
            ThemeConstituent("ashare", "theme:old", "000002.SZ", "旧核心B", "core", 9, source="seed"),
            ThemeConstituent("ashare", "theme:old", "000003.SZ", "旧跟随C", "follower", 5, source="seed"),
            ThemeConstituent("ashare", "theme:old", "000004.SZ", "旧跟随D", "follower", 4, source="seed"),
            ThemeConstituent("ashare", "theme:old", "000005.SZ", "旧跟随E", "watch", 3, source="seed"),
            ThemeConstituent("ashare", "theme:new", "000101.SZ", "新核心A", "leader", 10, source="seed"),
            ThemeConstituent("ashare", "theme:new", "000102.SZ", "新核心B", "core", 9, source="seed"),
            ThemeConstituent("ashare", "theme:new", "000103.SZ", "新跟随C", "follower", 5, source="seed"),
            ThemeConstituent("ashare", "theme:new", "000104.SZ", "新跟随D", "follower", 4, source="seed"),
            ThemeConstituent("ashare", "theme:new", "000105.SZ", "新跟随E", "watch", 3, source="seed"),
        ],
    )
    watchlist = AsyncMock()
    watchlist.dynamic_universe = AsyncMock(return_value=[])
    svc = LiveMessageService(theme_repo, watchlist)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    await _tick(svc, "000001.SZ", 2.0, ts)
    await _tick(svc, "000002.SZ", 1.8, ts)
    await _tick(svc, "000003.SZ", 1.6, ts)
    await _tick(svc, "000004.SZ", 1.5, ts)
    await _tick(svc, "000003.SZ", -0.2, ts)
    assert [m.title for m in await _tick(svc, "000004.SZ", -0.3, ts)] == ["旧题材强度回落"]

    await _tick(svc, "000101.SZ", 2.2, ts)
    await _tick(svc, "000102.SZ", 2.0, ts)
    messages = await _tick(svc, "000103.SZ", 1.8, ts)

    titles = {m.title for m in messages}
    assert "新题材启动" in titles
    assert "题材轮动: 新题材接力旧题材" in titles
    rotation = next(m for m in messages if m.title == "题材轮动: 新题材接力旧题材")
    assert rotation.category == "theme"
    assert rotation.payload["from_theme_code"] == "theme:old"
    assert rotation.payload["from_state"] == "fade"


@pytest.mark.asyncio
async def test_index_pulse_weak_message_from_core_indices(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    assert await _tick(svc, "000001.SH", -0.4, ts) == []
    assert await _tick(svc, "399001.SZ", -0.5, ts) == []
    assert await _tick(svc, "399006.SZ", -0.7, ts) == []
    assert await _tick(svc, "000300.SH", -0.3, ts) == []
    messages = await _tick(svc, "000905.SH", -0.8, ts)

    assert [m.title for m in messages] == ["大盘脉搏偏弱"]
    assert messages[0].category == "index"
    assert messages[0].payload["down_count"] == 5
    assert messages[0].payload["state"] == "weak"


@pytest.mark.asyncio
async def test_index_style_large_defense_risk(tmp_path: Path):
    svc = await _service(tmp_path)
    ts = datetime(2026, 6, 15, 1, 35, tzinfo=timezone.utc)

    assert await _tick(svc, "000001.SH", 0.1, ts) == []
    assert await _tick(svc, "000300.SH", 0.2, ts) == []
    assert await _tick(svc, "399006.SZ", -0.4, ts) == []
    assert await _tick(svc, "000016.SH", 0.5, ts) == []
    messages = await _tick(svc, "000852.SH", -0.7, ts)

    assert [m.title for m in messages] == ["权重护盘但小票走弱"]
    assert messages[0].category == "risk"
    assert messages[0].payload["small_vs_large_pct"] == pytest.approx(-1.2)
