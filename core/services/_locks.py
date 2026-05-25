"""mini_racer (akshare 内部 JS 解析) 在并发调用时会 crash 进程,
所有需要 mini_racer 的 akshare 接口共用此 Lock 串行化。

涉及接口:stock_zh_a_spot / fund_etf_category_sina /
        stock_sector_spot / stock_sector_detail 等。

诊断: 每次进入/退出锁打日志, 配合 ak 实际 call 的耗时, 能从日志精确
还原 V8 崩溃前的最后一次调用是谁、是否与其他 call 并发。

雷区 1 hang 变种(2026-05-25 观察到): 持锁方在 mini_racer 内部 V8 race
hang 死(不抛 SIGABRT, worker 不死, 只是该协程的工作线程永久卡在 V8 调用),
锁被一直占着, 后续所有 ak_call 排队卡死。watchdog 在持锁 > 60s 时用
faulthandler.dump_traceback 把所有线程栈直写到 fault.log, 拿到下次发作的
工作线程 frame, 才能定位卡在哪行 sina JS / V8 调用。
"""
import asyncio
import faulthandler
import threading
import time
from contextlib import asynccontextmanager

import structlog

from core.integrations.logging_setup import get_fault_log_file

_log = structlog.get_logger("mini_racer_lock")
_lock = asyncio.Lock()
_seq = 0

# 持锁超过这个秒数 → dump 所有线程栈到 fault.log
_HANG_DUMP_SECONDS = 60


def _dump_threads_on_hang(sid: int, caller: str) -> None:
    """watchdog 触发: 持锁过久, 把所有线程的 Python frame 直写到 fault.log。
    不打断也不重试, 留证据给下次诊断。stderr 也回声一份方便实时观察。
    """
    fp = get_fault_log_file()
    _log.warning("racer.hang_detected", id=sid, caller=caller,
                 threshold_s=_HANG_DUMP_SECONDS,
                 hint="see fault.log for thread dump")
    if fp is None:
        # setup_logging 还没跑(测试 / import 期), 跳过 dump
        return
    try:
        # 写 banner 让 fault.log 里多次 dump 区分得开
        banner = (
            f"\n=== racer.hang_detected ts={time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"sid={sid} caller={caller} held>={_HANG_DUMP_SECONDS}s ===\n"
        ).encode()
        fp.write(banner)
        fp.flush()
        faulthandler.dump_traceback(file=fp, all_threads=True)
        fp.flush()
    except Exception as e:  # noqa: BLE001
        _log.warning("racer.hang_dump_failed", id=sid, caller=caller, error=str(e))


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
        # 单次性 watchdog: 60s 后还没 release 就 dump 所有线程栈
        watchdog = threading.Timer(
            _HANG_DUMP_SECONDS, _dump_threads_on_hang, args=(sid, caller),
        )
        watchdog.daemon = True  # 主进程退出时跟着退, 不阻塞 shutdown
        watchdog.start()
        try:
            yield
        finally:
            watchdog.cancel()
            held_ms = (time.monotonic() - held_since) * 1000
            _log.info("racer.exit", id=sid, caller=caller,
                      held_ms=round(held_ms, 1))


# 兼容老用法 `async with mini_racer_lock:`
mini_racer_lock = _lock
