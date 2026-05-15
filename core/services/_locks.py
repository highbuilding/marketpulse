"""mini_racer (akshare 内部 JS 解析) 在并发调用时会 crash 进程,
所有需要 mini_racer 的 akshare 接口共用此 Lock 串行化。

涉及接口:stock_zh_a_spot / fund_etf_category_sina /
        stock_sector_spot / stock_sector_detail 等。

诊断: 每次进入/退出锁打日志, 配合 ak 实际 call 的耗时, 能从日志精确
还原 V8 崩溃前的最后一次调用是谁、是否与其他 call 并发。
"""
import asyncio
import time
from contextlib import asynccontextmanager

import structlog

_log = structlog.get_logger("mini_racer_lock")
_lock = asyncio.Lock()
_seq = 0


@asynccontextmanager
async def acquire(caller: str):
    """用法: async with acquire("ashare.fetch_intraday:600519.SH:60m"):"""
    global _seq
    _seq += 1
    sid = _seq
    waiting_since = time.monotonic()
    _log.info("racer.wait", id=sid, caller=caller,
              locked=_lock.locked())
    async with _lock:
        waited_ms = (time.monotonic() - waiting_since) * 1000
        held_since = time.monotonic()
        _log.info("racer.enter", id=sid, caller=caller,
                  waited_ms=round(waited_ms, 1))
        try:
            yield
        finally:
            held_ms = (time.monotonic() - held_since) * 1000
            _log.info("racer.exit", id=sid, caller=caller,
                      held_ms=round(held_ms, 1))


# 兼容老用法 `async with mini_racer_lock:`
mini_racer_lock = _lock
