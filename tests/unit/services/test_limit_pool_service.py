from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from core.persistence.limit_pool_repo import LimitPoolRepo
from core.persistence.sqlite_repo import StateRepo
from core.services.limit_pool_service import LimitPoolService, parse_limit_pool_df


def _ak_dispatch(mapping: dict) -> AsyncMock:
    async def _fake(func_name, *args, **kwargs):
        if func_name not in mapping:
            raise AssertionError(f"unexpected ak_call: {func_name}")
        return mapping[func_name]
    return AsyncMock(side_effect=_fake)


@pytest.fixture
async def svc(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return LimitPoolService(LimitPoolRepo(str(tmp_path / "state.db")))


def test_parse_limit_up_pool_df():
    df = pd.DataFrame([
        {
            "代码": "600110", "名称": "诺德股份", "涨跌幅": 10.03, "最新价": 17.22,
            "成交额": 232947614, "流通市值": 2.98e10, "总市值": 2.98e10,
            "换手率": 0.77, "封板资金": 877075369, "首次封板时间": "092500",
            "最后封板时间": "092500", "炸板次数": 0, "连板数": 3, "所属行业": "电池",
        },
    ])

    items = parse_limit_pool_df(
        df,
        trade_date="2026-06-17",
        pool_type="limit_up",
        pulled_at=datetime(2026, 6, 17, 7, 0, tzinfo=timezone.utc),
    )

    assert len(items) == 1
    assert items[0].symbol == "600110.SH"
    assert items[0].pool_type == "limit_up"
    assert items[0].seal_amount == pytest.approx(877075369)
    assert items[0].ladder_count == 3


def test_parse_previous_pool_df():
    """昨日涨停今日表现: 昨日连板数→ladder_count, 昨日封板时间→first_seal_time。"""
    df = pd.DataFrame([
        {
            "代码": "603663", "名称": "三祥新材", "涨跌幅": -4.15, "最新价": 79.54,
            "涨停价": 91.28, "成交额": 2344520544, "昨日封板时间": "112534",
            "昨日连板数": 1, "涨停统计": "2/1", "所属行业": "化学原料",
        },
    ])
    items = parse_limit_pool_df(df, trade_date="2026-06-17", pool_type="previous")
    assert len(items) == 1
    it = items[0]
    assert it.symbol == "603663.SH"
    assert it.pool_type == "previous"
    assert it.change_pct == pytest.approx(-4.15)  # 今日涨跌幅
    assert it.first_seal_time == "112534"          # 昨日封板时间
    assert it.ladder_count == 1                     # 昨日连板数


@pytest.mark.asyncio
async def test_pull_all_saves_all_pools(svc):
    fake = _ak_dispatch({
        "stock_zt_pool_em": pd.DataFrame([{"代码": "600110", "名称": "诺德股份", "连板数": 3}]),
        "stock_zt_pool_zbgc_em": pd.DataFrame([{"代码": "000777", "名称": "中核科技", "炸板次数": 2}]),
        "stock_zt_pool_dtgc_em": pd.DataFrame([{"代码": "603586", "名称": "金麒麟", "连续跌停": 2}]),
        "stock_zt_pool_previous_em": pd.DataFrame([{"代码": "603663", "名称": "三祥新材", "昨日连板数": 1}]),
    })
    with patch("core.services.limit_pool_service.ak_call", fake):
        result = await svc.pull_all("2026-06-17")

    assert result == {"limit_up": 1, "broken_limit": 1, "down_limit": 1, "previous": 1}
    summary = await svc.summary_by_date("2026-06-17")
    assert summary["limit_up_count"] == 1
    assert summary["broken_count"] == 1
    assert summary["down_limit_count"] == 1
