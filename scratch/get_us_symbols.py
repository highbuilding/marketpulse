"""获取美股回填标的清单: S&P 500 + 现有 US seeds 补充(中概股/ETF).

从 Wikipedia 拉 S&P 500 成分表, 与 US seeds 合并去重.
输出: data/us_backfill_symbols.txt
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import structlog

log = structlog.get_logger(__name__)

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
DATA = Path(__file__).resolve().parents[1] / "data"


async def fetch_sp500_symbols() -> list[str]:
    """从 Wikipedia 解析 S&P 500 成分股列表."""
    headers = {"User-Agent": "MarketPulse/1.0 (local data collection; zhonghuai)"}
    async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
        r = await client.get(WIKI_URL)
        r.raise_for_status()

    # Wikipedia 页面第一个 wikitable 就是成分股表
    # 简单解析: 找第一个 wikitable, 提取第一列 (ticker)
    html = r.text

    # 用正则匹配 wikitable 中的第一列 ticker
    # 格式: <tr>...<td><a...>AAPL</a></td>...
    # 更稳健的做法: 找到第一个 <table class="wikitable, 提取所有 <tr> 中第一个 <td> 的文本

    # Simple approach: find the table, get first column of each row
    table_match = re.search(r'<table[^>]*class="wikitable[^"]*"[^>]*>(.*?)</table>', html, re.DOTALL)
    if not table_match:
        log.error("sp500_table_not_found")
        return []

    table = table_match.group(1)
    symbols = []
    for row in re.finditer(r'<tr>(.*?)</tr>', table, re.DOTALL):
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row.group(1), re.DOTALL)
        if not cells:
            continue
        # 第一列: ticker 链接
        first_cell = cells[0]
        ticker_match = re.search(r'<a[^>]*>([A-Z]{1,5})</a>', first_cell)
        if ticker_match:
            symbols.append(ticker_match.group(1))

    return symbols


def get_us_seeds_symbols() -> set[str]:
    """从 _us_seeds 提取纯股票 symbol (去掉 ETF)."""
    from core.services._us_seeds import US_SEEDS

    stocks = set()
    for symbol, name, market in US_SEEDS:
        if market == "us":
            stocks.add(symbol)
    return stocks


async def main() -> None:
    print("拉取 S&P 500 成分股...")
    sp500 = await fetch_sp500_symbols()
    print(f"S&P 500: {len(sp500)} 只")
    if sp500:
        print(f"  示例: {sp500[:10]}")

    sp500_set = set(sp500)
    seeds = get_us_seeds_symbols()
    print(f"US seeds (股票): {len(seeds)} 只")

    # 合并
    merged = sp500_set | seeds
    extra = seeds - sp500_set
    print(f"\n合并后总计: {len(merged)} 只")
    print(f"S&P 500: {len(sp500_set)} 只")
    print(f"US seeds 补充 (不在 S&P 500): {len(extra)} 只")
    if extra:
        print(f"  补充标的: {sorted(extra)[:30]}...")

    output = DATA / "us_backfill_symbols.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        for sym in sorted(merged):
            f.write(f"{sym}\n")
    print(f"\n已保存到 {output}")


if __name__ == "__main__":
    asyncio.run(main())
