"""所有 akshare 调用的统一入口。

为什么必须收口:
- akshare 的 sina 系接口(stock_zh_a_*, stock_sector_*, fund_etf_*sina, stock_zh_index_*
  等)内部用 py_mini_racer 解 JS, V8 实例进程级单例, 并发初始化即 SIGABRT。
- 即便不并发, py_mini_racer 0.6.0(已停更最终版)在 macOS arm64 上有析构 race,
  概率性崩溃。我们靠全局 asyncio.Lock 串行化 + 收口减少入口面来缓解。

约束:
- 项目里不再允许 `import akshare` + 直接 `await asyncio.to_thread(ak.xxx, ...)`。
- 所有调用都要走 `ak_call("xxx", ...)`, 锁和日志会自动加上。
- caller 字符串便于诊断: `ak_call("stock_zh_a_minute", ..., caller="indices.5min:000001.SH")`。
"""
from __future__ import annotations

import asyncio
from typing import Any

import akshare as ak

from core.services._locks import acquire as _racer_acquire


async def ak_call(
    func_name: str,
    *args: Any,
    caller: str | None = None,
    **kwargs: Any,
) -> Any:
    """串行化执行 akshare 接口。

    func_name: ak 模块下的函数名, 用字符串避免调用方再 `import akshare`。
    caller: 诊断字符串(默认用 func_name), 出现在 racer.* 日志里。
    """
    func = getattr(ak, func_name, None)
    if func is None or not callable(func):
        raise AttributeError(f"akshare has no callable '{func_name}'")
    label = caller or func_name
    async with _racer_acquire(f"ak:{label}"):
        return await asyncio.to_thread(func, *args, **kwargs)
