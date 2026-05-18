from __future__ import annotations

import structlog

from core.integrations.akshare import ak_call
from core.persistence.symbol_directory_repo import SymbolDirectoryRepo
from core.services._us_seeds import US_SEEDS

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

    async def bootstrap_us_seeds(self) -> int:
        """美股静态 seeds, 启动时刷一次。纯本地写库, 无外部调用。"""
        n = await self.repo.upsert_many(US_SEEDS)
        log.info("symbol_directory.us_seeds_bootstrapped", count=n)
        return n

    async def upsert_one(self, symbol: str, name: str, market: str) -> None:
        await self.repo.upsert_many([(symbol, name, market)])

    async def refresh_ashare(self) -> int:
        """A 股 + ETF code+name,sina 通道。"""
        df = await ak_call("stock_zh_a_spot",
                            caller="directory.refresh_ashare:stock_zh_a_spot")
        normalized: list[tuple[str, str, str]] = []
        for _, row in df.iterrows():
            sina_code = str(row["代码"])
            if sina_code[:2].lower() in {"sh", "sz", "bj"}:
                mkt = sina_code[:2].upper()
                code = sina_code[2:]
                symbol = f"{code}.{mkt}"
            else:
                symbol = _normalize_ashare(sina_code)
            normalized.append((symbol, str(row["名称"]).strip(), "ashare"))

        # ETF 走 sina;mini_racer 必须串行
        try:
            etf_df = await ak_call(
                "fund_etf_category_sina", symbol="ETF基金",
                caller="directory.refresh_ashare:fund_etf_category_sina",
            )
            for _, row in etf_df.iterrows():
                sina_code = str(row["代码"])
                if sina_code[:2].lower() in {"sh", "sz"}:
                    mkt = sina_code[:2].upper()
                    code = sina_code[2:]
                    normalized.append((f"{code}.{mkt}", str(row["名称"]).strip(), "etf"))
        except Exception as e:  # noqa: BLE001
            log.warning("directory.etf_refresh_failed", error=str(e))

        n = await self.repo.upsert_many(normalized)
        log.info("symbol_directory.refreshed", count=n)
        return n

    async def get_name(self, symbol: str) -> str | None:
        return await self.repo.get_name(symbol)

    async def get_names(self, symbols: list[str]) -> dict[str, str]:
        return await self.repo.get_names(symbols)

    async def search(
        self, query: str, limit: int = 20,
        *, market: str | None = None,
    ) -> list[tuple[str, str, str]]:
        return await self.repo.search(query, limit, market=market)

    async def count(self) -> int:
        return await self.repo.count()
