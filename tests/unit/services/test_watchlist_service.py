import pytest

from core.persistence.sqlite_repo import StateRepo
from core.persistence.watchlist_repo import WatchlistRepo
from core.services.watchlist_service import WatchlistService


@pytest.fixture
async def svc(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return WatchlistService(WatchlistRepo(str(tmp_path / "state.db")))


@pytest.mark.asyncio
async def test_bootstrap_creates_default_watchlist(svc):
    await svc.bootstrap_default()
    items = await svc.list_all()
    assert len(items) == 1
    assert items[0].name == "我的关注"


@pytest.mark.asyncio
async def test_bootstrap_idempotent(svc):
    await svc.bootstrap_default()
    await svc.bootstrap_default()
    items = await svc.list_all()
    assert len(items) == 1


@pytest.mark.asyncio
async def test_dynamic_universe_unions_active_lists(svc):
    a = await svc.create("A")
    b = await svc.create("B")
    await svc.add_symbol(a, "600519.SH")
    await svc.add_symbol(b, "000858.SZ")
    syms = await svc.dynamic_universe()
    assert sorted(syms) == ["000858.SZ", "600519.SH"]
