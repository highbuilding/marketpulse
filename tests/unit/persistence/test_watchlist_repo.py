import pytest

from core.persistence.sqlite_repo import StateRepo
from core.persistence.watchlist_repo import WatchlistRepo


@pytest.fixture
async def repo(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return WatchlistRepo(str(tmp_path / "state.db"))


@pytest.mark.asyncio
async def test_create_and_list_watchlists(repo):
    wl_id = await repo.create_watchlist("我的关注")
    assert wl_id > 0
    items = await repo.list_watchlists(include_archived=False)
    assert len(items) == 1
    assert items[0].name == "我的关注"
    assert items[0].is_archived is False


@pytest.mark.asyncio
async def test_archive_hides_from_default_list(repo):
    a = await repo.create_watchlist("A")
    b = await repo.create_watchlist("B")
    await repo.archive_watchlist(a)
    actives = await repo.list_watchlists(include_archived=False)
    assert {w.id for w in actives} == {b}
    all_ = await repo.list_watchlists(include_archived=True)
    assert {w.id for w in all_} == {a, b}


@pytest.mark.asyncio
async def test_add_and_remove_symbol(repo):
    wl = await repo.create_watchlist("X")
    await repo.add_symbol(wl, "600519.SH")
    await repo.add_symbol(wl, "000858.SZ")
    assert sorted(await repo.list_symbols(wl)) == ["000858.SZ", "600519.SH"]
    await repo.remove_symbol(wl, "600519.SH")
    assert await repo.list_symbols(wl) == ["000858.SZ"]


@pytest.mark.asyncio
async def test_add_symbol_idempotent(repo):
    wl = await repo.create_watchlist("X")
    await repo.add_symbol(wl, "600519.SH")
    await repo.add_symbol(wl, "600519.SH")
    assert await repo.list_symbols(wl) == ["600519.SH"]


@pytest.mark.asyncio
async def test_all_active_symbols_for_scheduler(repo):
    a = await repo.create_watchlist("A")
    b = await repo.create_watchlist("B")
    arc = await repo.create_watchlist("ARC")
    await repo.add_symbol(a, "600519.SH")
    await repo.add_symbol(b, "000858.SZ")
    await repo.add_symbol(arc, "300750.SZ")
    await repo.archive_watchlist(arc)
    syms = await repo.all_active_symbols()
    assert sorted(syms) == ["000858.SZ", "600519.SH"]


@pytest.mark.asyncio
async def test_rename_watchlist(repo):
    wl = await repo.create_watchlist("旧名")
    await repo.rename_watchlist(wl, "新名")
    items = await repo.list_watchlists(include_archived=False)
    assert items[0].name == "新名"
