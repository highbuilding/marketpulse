from __future__ import annotations

from pathlib import Path

import pytest

from core.domain.models import ThemeConstituent, ThemeDefinition
from core.persistence.sqlite_repo import StateRepo
from core.persistence.theme_repo import ThemeRepo
from core.themes.seed_loader import load_ashare_seed


@pytest.mark.asyncio
async def test_seed_definitions_are_idempotent_and_do_not_override_manual_changes(tmp_path: Path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    repo = ThemeRepo(str(db))
    definitions = [
        ThemeDefinition(
            market="ashare",
            theme_code="theme:robotics",
            theme_name="机器人",
            classification="theme",
            priority="P1",
            source="seed",
            seed_version="test",
        ),
    ]
    constituents = [
        ThemeConstituent(
            market="ashare",
            theme_code="theme:robotics",
            symbol="300024.SZ",
            name="机器人",
            role_hint="core",
            source="seed",
            seed_version="test",
        ),
    ]

    assert await repo.seed_definitions(definitions, constituents) == (1, 1)
    assert await repo.seed_definitions(definitions, constituents) == (0, 0)

    await repo.upsert_definition(
        ThemeDefinition(
            market="ashare",
            theme_code="theme:robotics",
            theme_name="机器人",
            classification="theme",
            priority="P3",
            enabled=False,
            source="seed",
            seed_version="test",
            note="手工停用",
        ),
    )
    assert await repo.seed_definitions(definitions, constituents) == (0, 0)
    row = await repo.get_definition("ashare", "theme:robotics")
    assert row is not None
    assert row.priority == "P3"
    assert row.enabled is False
    assert row.note == "手工停用"


@pytest.mark.asyncio
async def test_delete_seed_definition_disables_but_manual_definition_deletes(tmp_path: Path):
    db = tmp_path / "state.db"
    await StateRepo(str(db)).init()
    repo = ThemeRepo(str(db))

    await repo.seed_definitions([
        ThemeDefinition(
            market="ashare", theme_code="theme:seed", theme_name="内置",
            classification="theme", priority="P1", source="seed",
        ),
    ], [])
    await repo.upsert_definition(
        ThemeDefinition(
            market="ashare", theme_code="theme:manual", theme_name="手工",
            classification="theme", priority="P2", source="manual",
        ),
    )

    await repo.delete_definition("ashare", "theme:seed")
    await repo.delete_definition("ashare", "theme:manual")

    seed = await repo.get_definition("ashare", "theme:seed")
    manual = await repo.get_definition("ashare", "theme:manual")
    assert seed is not None
    assert seed.enabled is False
    assert manual is None


def test_load_ashare_seed_has_bounded_initial_universe():
    definitions, constituents = load_ashare_seed()
    assert 1 <= len(definitions) <= 45
    assert all(d.market == "ashare" for d in definitions)
    assert all(d.source == "seed" for d in definitions)
    assert all(c.source == "seed" for c in constituents)
    assert {d.priority for d in definitions} <= {"P0", "P1", "P2", "P3"}
    assert all(c.symbol == c.symbol.upper() for c in constituents)
