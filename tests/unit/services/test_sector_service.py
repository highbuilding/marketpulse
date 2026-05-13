from unittest.mock import patch

import pandas as pd
import pytest

from core.persistence.sector_repo import SectorRepo
from core.persistence.sqlite_repo import StateRepo
from core.services.sector_service import SectorService


@pytest.fixture
async def svc(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return SectorService(SectorRepo(str(tmp_path / "state.db")))


_SECTOR_DETAIL_DF = pd.DataFrame([
    {"代码": "sh600660", "名称": "福耀玻璃"},
    {"代码": "sh601636", "名称": "旗滨集团"},
    {"代码": "sz000012", "名称": "南玻A"},
])

_NEW_SINA_INDUSTRY_DF = pd.DataFrame([
    {"label": "new_blhy", "板块": "玻璃行业", "公司家数": 3,
     "平均价格": 22.0, "涨跌幅": 1.5, "股票名称": "福耀玻璃",
     "个股-涨跌幅": 5.0, "个股-当前价": 35.0, "个股-涨跌额": 1.5,
     "总成交量": 1000, "总成交额": 5000, "上涨家数": 2, "下跌家数": 1},
])


@pytest.mark.asyncio
async def test_refresh_sector_writes_constituents(svc):
    with patch("core.services.sector_service.ak.stock_sector_detail",
               return_value=_SECTOR_DETAIL_DF):
        n = await svc.refresh_sector("new_blhy", display_name="玻璃行业")
    assert n == 3
    syms = await svc.list_constituents("玻璃行业")
    assert sorted(syms) == ["000012.SZ", "600660.SH", "601636.SH"]


@pytest.mark.asyncio
async def test_refresh_all_iterates_known_labels(svc):
    with patch("core.services.sector_service.ak.stock_sector_spot",
               return_value=_NEW_SINA_INDUSTRY_DF), \
         patch("core.services.sector_service.ak.stock_sector_detail",
               return_value=_SECTOR_DETAIL_DF):
        total = await svc.refresh_all_sina()
    assert total == 3
    sectors = await svc.list_sectors()
    assert any(s.name == "玻璃行业" for s in sectors)
