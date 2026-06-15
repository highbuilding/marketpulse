from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.domain.models import ThemeConstituent, ThemeDefinition
from core.persistence.theme_repo import ThemeRepo


_SEED_DIR = Path(__file__).with_name("seeds")
_SEED_PATH = _SEED_DIR / "ashare_themes.json"
_EXPANSION_PATH = _SEED_DIR / "ashare_themes_expansion.json"


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_ashare_seed(path: Path = _SEED_PATH) -> tuple[list[ThemeDefinition], list[ThemeConstituent]]:
    paths = [path]
    if path == _SEED_PATH:
        paths.append(_EXPANSION_PATH)
    return load_ashare_seeds(paths)


def load_ashare_seeds(paths: list[Path]) -> tuple[list[ThemeDefinition], list[ThemeConstituent]]:
    definitions: dict[tuple[str, str], ThemeDefinition] = {}
    constituents: dict[tuple[str, str, str], ThemeConstituent] = {}
    for path in paths:
        if not path.exists():
            continue
        loaded_definitions, loaded_constituents = _load_ashare_seed_file(path)
        for definition in loaded_definitions:
            definitions.setdefault((definition.market, definition.theme_code), definition)
        for constituent in loaded_constituents:
            constituents.setdefault(
                (constituent.market, constituent.theme_code, constituent.symbol),
                constituent,
            )
    return list(definitions.values()), list(constituents.values())


def _load_ashare_seed_file(path: Path) -> tuple[list[ThemeDefinition], list[ThemeConstituent]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    market = payload.get("market", "ashare")
    version = payload.get("version")
    definitions: list[ThemeDefinition] = []
    constituents: list[ThemeConstituent] = []
    for row in payload.get("themes", []):
        theme_code = str(row["theme_code"]).strip()
        definitions.append(
            ThemeDefinition(
                market=market,
                theme_code=theme_code,
                theme_name=str(row["theme_name"]).strip(),
                classification=str(row.get("classification", "theme")).strip(),
                priority=str(row.get("priority", "P2")).strip(),
                enabled=bool(row.get("enabled", True)),
                source="seed",
                seed_version=version,
                note=_text(row.get("note")),
            ),
        )
        for member in row.get("members", []):
            symbol = str(member["symbol"]).strip().upper()
            constituents.append(
                ThemeConstituent(
                    market=market,
                    theme_code=theme_code,
                    symbol=symbol,
                    name=_text(member.get("name")),
                    role_hint=_text(member.get("role_hint")),
                    weight=member.get("weight"),
                    enabled=bool(member.get("enabled", True)),
                    source="seed",
                    seed_version=version,
                    note=_text(member.get("note")),
                ),
            )
    return definitions, constituents


async def bootstrap_ashare_seed(repo: ThemeRepo, path: Path = _SEED_PATH) -> tuple[int, int]:
    definitions, constituents = load_ashare_seed(path)
    return await repo.seed_definitions(definitions, constituents)
