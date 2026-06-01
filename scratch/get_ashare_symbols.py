"""获取 A 股回填标的清单: 沪深 300 + 创业板指 + 科创 50 → 去重.

不依赖 Redis; ak_call 在 middleware=None 时跳过中间件直接走子进程.
输出: data/ashare_backfill_symbols.txt (每行一个 symbol)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from core.integrations.proxy_setup import setup_process_proxy

setup_process_proxy()

from core.integrations.logging_setup import setup_logging

setup_logging(process_name="ashare_symbols")

import structlog
from core.integrations.akshare import ak_call

log = structlog.get_logger(__name__)

# 三大指数
INDICES = {
    "沪深300": "000300",
    "创业板指": "399006",
    "科创50": "000688",
}

DATA = Path(__file__).resolve().parents[1] / "data"


async def get_constituents(index_code: str, index_name: str) -> list[str]:
    """获取指数成分股列表, 返回 normalize 后的 symbol 列表."""
    try:
        result = await ak_call(
            "index_stock_cons",
            symbol=index_code,
            caller=f"ashare_symbols:{index_name}",
        )
    except Exception as e:
        log.warning("constituents_failed", index=index_name, error=str(e))
        return []

    if result is None:
        log.warning("constituents_empty", index=index_name)
        return []

    # akshare 返回 DataFrame, 列名: 品种代码, 品种名称, ...
    import pandas as pd

    if isinstance(result, pd.DataFrame):
        code_col = None
        for col in result.columns:
            if "代码" in str(col):
                code_col = col
                break
        if code_col is None:
            code_col = result.columns[0]

        symbols = []
        for code in result[code_col]:
            code = str(code).strip()
            # normalize: 6 开头 → .SH, 0/3 开头 → .SZ
            if "." in code:
                symbols.append(code)
            elif code.startswith(("6", "9")):
                symbols.append(f"{code}.SH")
            elif code.startswith(("0", "3", "4", "8")):
                symbols.append(f"{code}.SZ")
            else:
                symbols.append(code)
        return symbols

    return []


async def main() -> None:
    all_symbols: dict[str, set[str]] = {}  # index_name → symbols

    for name, code in INDICES.items():
        symbols = await get_constituents(code, name)
        all_symbols[name] = set(symbols)
        log.info("constituents_fetched", index=name, count=len(symbols))
        print(f"{name}: {len(symbols)} 只")

    # 去重合并
    merged: set[str] = set()
    for name, syms in all_symbols.items():
        merged.update(syms)

    print(f"\n去重后总计: {len(merged)} 只")
    for name, syms in all_symbols.items():
        print(f"  {name}: {len(syms)} 只 (独立: {len(syms - merged | syms)})")

    # 保存到文件
    output = DATA / "ashare_backfill_symbols.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        for sym in sorted(merged):
            f.write(f"{sym}\n")
    print(f"\n已保存到 {output}")


if __name__ == "__main__":
    asyncio.run(main())
