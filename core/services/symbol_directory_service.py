from __future__ import annotations

import asyncio

import akshare as ak
import structlog

from core.persistence.symbol_directory_repo import SymbolDirectoryRepo

log = structlog.get_logger(__name__)


# 硬编码的指数目录,sina 没有直接 API 能列出它们
_INDEX_SEEDS: list[tuple[str, str, str]] = [
    ("000001.SH", "上证指数", "ashare"),
    ("399001.SZ", "深证成指", "ashare"),
    ("000300.SH", "沪深300", "ashare"),
    ("399006.SZ", "创业板指", "ashare"),
    ("HSI.HK", "恒生指数", "hk"),
    ("HSTECH.HK", "恒生科技指数", "hk"),
    ("HSCEI.HK", "恒生中国企业指数", "hk"),
]


def _normalize_ashare(code: str) -> str:
    """600519 → 600519.SH, 000858 → 000858.SZ, 920001 → 920001.BJ."""
    code = str(code).zfill(6)
    if code.startswith(("60", "68", "51", "50", "11", "13")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


class SymbolDirectoryService:
    def __init__(self, repo: SymbolDirectoryRepo) -> None:
        self.repo = repo

    async def bootstrap_seeds(self) -> None:
        """写入指数种子(快,启动时同步跑)."""
        await self.repo.upsert_many(_INDEX_SEEDS)

    async def refresh_ashare(self) -> int:
        """全量 A 股 code+name,走 sina(stock_zh_a_spot)约 17s / ~5500 只。"""
        df = await asyncio.to_thread(ak.stock_zh_a_spot)
        items = [
            (_normalize_ashare(str(row["代码"]).split("sh")[-1].split("sz")[-1].split("bj")[-1]),
             str(row["名称"]).strip(), "ashare")
            for _, row in df.iterrows()
        ]
        # 原 "代码" 列已经带 "sh600519" 前缀,我们要剥离再 normalize
        normalized = []
        for _, row in df.iterrows():
            sina_code = str(row["代码"])
            if sina_code[:2].lower() in {"sh", "sz", "bj"}:
                mkt = sina_code[:2].upper()
                code = sina_code[2:]
                symbol = f"{code}.{mkt}"
            else:
                symbol = _normalize_ashare(sina_code)
            normalized.append((symbol, str(row["名称"]).strip(), "ashare"))
        n = await self.repo.upsert_many(normalized)
        log.info("symbol_directory.refreshed", count=n)
        return n

    async def get_name(self, symbol: str) -> str | None:
        return await self.repo.get_name(symbol)

    async def search(self, query: str, limit: int = 20) -> list[tuple[str, str, str]]:
        return await self.repo.search(query, limit)

    async def count(self) -> int:
        return await self.repo.count()
