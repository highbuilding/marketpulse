from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from apps.api.deps import get_theme_repo
from core.domain.models import ThemeConstituent, ThemeDefinition
from core.persistence.theme_repo import ThemeRepo


router = APIRouter(prefix="/api/themes", tags=["themes"])

_SUPPORTED_MARKETS = {"ashare"}
_PRIORITIES = {"P0", "P1", "P2", "P3"}
_CLASSIFICATIONS = {"index_weight", "industry", "concept", "theme", "watch"}


class ThemeDTO(BaseModel):
    market: str
    theme_code: str
    theme_name: str
    classification: str
    priority: str
    enabled: bool
    source: str
    seed_version: str | None
    note: str | None
    member_count: int
    created_at: str | None
    updated_at: str | None


class ThemeConstituentDTO(BaseModel):
    market: str
    theme_code: str
    symbol: str
    name: str | None
    role_hint: str | None
    weight: float | None
    enabled: bool
    source: str
    seed_version: str | None
    note: str | None
    created_at: str | None
    updated_at: str | None


class ThemesResp(BaseModel):
    themes: list[ThemeDTO]


class ThemeDetailResp(BaseModel):
    theme: ThemeDTO
    constituents: list[ThemeConstituentDTO]


class ThemeIdResp(BaseModel):
    theme_code: str


class ThemeBody(BaseModel):
    market: str = "ashare"
    theme_code: str | None = None
    theme_name: str = Field(..., min_length=1)
    classification: str = "theme"
    priority: str = "P2"
    enabled: bool = True
    note: str | None = None


class PatchThemeBody(BaseModel):
    theme_name: str | None = None
    classification: str | None = None
    priority: str | None = None
    enabled: bool | None = None
    note: str | None = None


class ConstituentIdResp(BaseModel):
    symbol: str


class ConstituentBody(BaseModel):
    symbol: str = Field(..., min_length=1)
    name: str | None = None
    role_hint: str | None = None
    weight: float | None = None
    enabled: bool = True
    note: str | None = None


def _ensure_market(market: str) -> None:
    if market not in _SUPPORTED_MARKETS:
        raise HTTPException(400, f"themes unsupported for market: {market}")


def _validate_priority(priority: str) -> str:
    priority = priority.strip().upper()
    if priority not in _PRIORITIES:
        raise HTTPException(400, "invalid priority")
    return priority


def _validate_classification(classification: str) -> str:
    classification = classification.strip()
    if classification not in _CLASSIFICATIONS:
        raise HTTPException(400, "invalid classification")
    return classification


def _theme_code(classification: str, name: str, code: str | None) -> str:
    if code and code.strip():
        return code.strip()
    return f"{classification}:{name.strip()}"


def _theme_dto(row: ThemeDefinition) -> ThemeDTO:
    return ThemeDTO(
        market=row.market,
        theme_code=row.theme_code,
        theme_name=row.theme_name,
        classification=row.classification,
        priority=row.priority,
        enabled=row.enabled,
        source=row.source,
        seed_version=row.seed_version,
        note=row.note,
        member_count=row.member_count,
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


def _constituent_dto(row: ThemeConstituent) -> ThemeConstituentDTO:
    return ThemeConstituentDTO(
        market=row.market,
        theme_code=row.theme_code,
        symbol=row.symbol,
        name=row.name,
        role_hint=row.role_hint,
        weight=row.weight,
        enabled=row.enabled,
        source=row.source,
        seed_version=row.seed_version,
        note=row.note,
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.get("", response_model=ThemesResp)
async def list_themes(
    market: str = Query("ashare"),
    include_disabled: bool = Query(True),
    repo: ThemeRepo = Depends(get_theme_repo),
) -> ThemesResp:
    _ensure_market(market)
    rows = await repo.list_definitions(market, include_disabled=include_disabled)
    return ThemesResp(themes=[_theme_dto(r) for r in rows])


@router.post("", response_model=ThemeIdResp)
async def create_theme(
    body: ThemeBody,
    repo: ThemeRepo = Depends(get_theme_repo),
) -> ThemeIdResp:
    _ensure_market(body.market)
    classification = _validate_classification(body.classification)
    priority = _validate_priority(body.priority)
    name = body.theme_name.strip()
    code = _theme_code(classification, name, body.theme_code)
    await repo.upsert_definition(
        ThemeDefinition(
            market=body.market,
            theme_code=code,
            theme_name=name,
            classification=classification,
            priority=priority,
            enabled=body.enabled,
            source="manual",
            note=body.note.strip() if body.note else None,
        ),
    )
    return ThemeIdResp(theme_code=code)


@router.get("/{theme_code}", response_model=ThemeDetailResp)
async def get_theme(
    theme_code: str,
    market: str = Query("ashare"),
    include_disabled: bool = Query(True),
    repo: ThemeRepo = Depends(get_theme_repo),
) -> ThemeDetailResp:
    _ensure_market(market)
    theme = await repo.get_definition(market, theme_code)
    if theme is None:
        raise HTTPException(404, "theme not found")
    rows = await repo.list_static_constituents(
        market, theme_code, include_disabled=include_disabled)
    return ThemeDetailResp(
        theme=_theme_dto(theme),
        constituents=[_constituent_dto(r) for r in rows],
    )


@router.patch("/{theme_code}", response_model=ThemeIdResp)
async def patch_theme(
    theme_code: str,
    body: PatchThemeBody,
    market: str = Query("ashare"),
    repo: ThemeRepo = Depends(get_theme_repo),
) -> ThemeIdResp:
    _ensure_market(market)
    existing = await repo.get_definition(market, theme_code)
    if existing is None:
        raise HTTPException(404, "theme not found")
    await repo.upsert_definition(
        ThemeDefinition(
            market=existing.market,
            theme_code=existing.theme_code,
            theme_name=body.theme_name.strip() if body.theme_name is not None else existing.theme_name,
            classification=_validate_classification(body.classification)
            if body.classification is not None else existing.classification,
            priority=_validate_priority(body.priority)
            if body.priority is not None else existing.priority,
            enabled=body.enabled if body.enabled is not None else existing.enabled,
            source=existing.source,
            seed_version=existing.seed_version,
            note=body.note if body.note is not None else existing.note,
            created_at=existing.created_at,
        ),
    )
    return ThemeIdResp(theme_code=theme_code)


@router.delete("/{theme_code}", status_code=204)
async def delete_theme(
    theme_code: str,
    market: str = Query("ashare"),
    repo: ThemeRepo = Depends(get_theme_repo),
) -> None:
    _ensure_market(market)
    await repo.delete_definition(market, theme_code)


@router.post("/{theme_code}/constituents", response_model=ConstituentIdResp)
async def upsert_constituent(
    theme_code: str,
    body: ConstituentBody,
    market: str = Query("ashare"),
    repo: ThemeRepo = Depends(get_theme_repo),
) -> ConstituentIdResp:
    _ensure_market(market)
    theme = await repo.get_definition(market, theme_code)
    if theme is None:
        raise HTTPException(404, "theme not found")
    symbol = body.symbol.strip().upper()
    if not symbol:
        raise HTTPException(400, "symbol cannot be empty")
    await repo.upsert_constituent(
        ThemeConstituent(
            market=market,
            theme_code=theme_code,
            symbol=symbol,
            name=body.name.strip() if body.name else None,
            role_hint=body.role_hint.strip() if body.role_hint else None,
            weight=body.weight,
            enabled=body.enabled,
            source="manual",
            note=body.note.strip() if body.note else None,
        ),
    )
    return ConstituentIdResp(symbol=symbol)


@router.delete("/{theme_code}/constituents/{symbol}", status_code=204)
async def delete_constituent(
    theme_code: str,
    symbol: str,
    market: str = Query("ashare"),
    repo: ThemeRepo = Depends(get_theme_repo),
) -> None:
    _ensure_market(market)
    await repo.delete_constituent(market, theme_code, symbol.strip().upper())
