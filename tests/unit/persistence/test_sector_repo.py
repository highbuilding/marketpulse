import pytest

from core.persistence.sector_repo import SectorRepo
from core.persistence.sqlite_repo import StateRepo


@pytest.fixture
async def repo(tmp_path):
    state = StateRepo(str(tmp_path / "state.db"))
    await state.init()
    return SectorRepo(str(tmp_path / "state.db"))


@pytest.mark.asyncio
async def test_upsert_sector_with_constituents(repo):
    await repo.upsert_sector("玻璃行业", "sina", ["600660.SH", "601636.SH"])
    sectors = await repo.list_sectors()
    assert "玻璃行业" in {s.name for s in sectors}
    syms = await repo.list_constituents("玻璃行业")
    assert sorted(syms) == ["600660.SH", "601636.SH"]


@pytest.mark.asyncio
async def test_upsert_replaces_constituents(repo):
    await repo.upsert_sector("X", "sina", ["A.SZ", "B.SZ"])
    await repo.upsert_sector("X", "sina", ["A.SZ", "C.SZ"])
    syms = await repo.list_constituents("X")
    assert sorted(syms) == ["A.SZ", "C.SZ"]


@pytest.mark.asyncio
async def test_sectors_of_symbol(repo):
    await repo.upsert_sector("玻璃", "sina", ["600660.SH"])
    await repo.upsert_sector("建材", "sina", ["600660.SH", "601636.SH"])
    sectors = await repo.sectors_of_symbol("600660.SH")
    assert sorted(sectors) == ["建材", "玻璃"]
