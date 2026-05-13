from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from core.persistence.sqlite_repo import StateRepo
from core.persistence.symbol_directory_repo import SymbolDirectoryRepo
from core.services.symbol_directory_service import SymbolDirectoryService


@pytest.fixture
async def svc(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return SymbolDirectoryService(SymbolDirectoryRepo(str(tmp_path / "state.db")))


@pytest.mark.asyncio
async def test_bootstrap_seeds_writes_indices(svc):
    await svc.bootstrap_seeds()
    n = await svc.get_name("HSI.HK")
    assert n == "恒生指数"
    sh = await svc.get_name("000001.SH")
    assert sh == "上证指数"


@pytest.mark.asyncio
async def test_refresh_ashare_normalizes_codes(svc):
    df = pd.DataFrame([
        {"代码": "sh600519", "名称": "贵州茅台"},
        {"代码": "sz000858", "名称": "五 粮 液"},
        {"代码": "bj920469", "名称": "富恒新材"},
    ])
    with patch("core.services.symbol_directory_service.ak.stock_zh_a_spot", return_value=df):
        n = await svc.refresh_ashare()
    assert n == 3
    assert await svc.get_name("600519.SH") == "贵州茅台"
    assert await svc.get_name("000858.SZ") == "五 粮 液"
    assert await svc.get_name("920469.BJ") == "富恒新材"


@pytest.mark.asyncio
async def test_search_by_name_or_symbol(svc):
    df = pd.DataFrame([
        {"代码": "sh600519", "名称": "贵州茅台"},
        {"代码": "sz000858", "名称": "五 粮 液"},
        {"代码": "sh603288", "名称": "海天味业"},
    ])
    with patch("core.services.symbol_directory_service.ak.stock_zh_a_spot", return_value=df):
        await svc.refresh_ashare()
    # 搜代码前缀
    results = await svc.search("600", limit=5)
    assert any(r[0] == "600519.SH" for r in results)
    # 搜名字
    results = await svc.search("茅台", limit=5)
    assert any("茅台" in r[1] for r in results)
    # 空查询
    assert await svc.search("", limit=5) == []
