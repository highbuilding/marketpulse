from __future__ import annotations

from pathlib import Path

import pytest

from core.domain.models import CollectorSymbol
from core.persistence.collector_symbol_repo import CollectorSymbolRepo
from core.persistence.sqlite_repo import StateRepo


@pytest.mark.asyncio
async def test_collector_symbol_repo_seed_upsert_and_active(tmp_path: Path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    repo = CollectorSymbolRepo(str(db))

    inserted = await repo.seed_symbols([
        CollectorSymbol("ashare", "600519.SH", "贵州茅台", source="core"),
        CollectorSymbol("ashare", "300001.SZ", "测试", source="seed", collect_signals=False),
    ])
    assert inserted == 2
    assert await repo.seed_symbols([
        CollectorSymbol("ashare", "600519.SH", "不会覆盖", source="core"),
    ]) == 0

    rows = await repo.list("ashare")
    assert [r.symbol for r in rows] == ["600519.SH", "300001.SZ"]
    assert await repo.active_symbols("ashare", capability="snapshot") == ["300001.SZ", "600519.SH"]
    assert await repo.active_symbols("ashare", capability="signals") == ["600519.SH"]

    await repo.upsert(CollectorSymbol(
        market="ashare",
        symbol="300001.SZ",
        name="测试改名",
        enabled=False,
        source="seed",
        collect_snapshot=True,
        collect_5m=True,
        collect_signals=True,
    ))
    row = await repo.get("ashare", "300001.SZ")
    assert row is not None
    assert row.name == "测试改名"
    assert row.enabled is False
    assert await repo.active_symbols("ashare", capability="5m") == ["600519.SH"]


@pytest.mark.asyncio
async def test_collector_symbol_repo_remove_manual_deletes_seed_disables(tmp_path: Path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    repo = CollectorSymbolRepo(str(db))
    await repo.seed_symbols([
        CollectorSymbol("ashare", "600519.SH", source="core"),
    ])
    await repo.upsert(CollectorSymbol("ashare", "002415.SZ", source="manual"))

    await repo.remove("ashare", "002415.SZ")
    assert await repo.get("ashare", "002415.SZ") is None

    await repo.remove("ashare", "600519.SH")
    row = await repo.get("ashare", "600519.SH")
    assert row is not None
    assert row.enabled is False
