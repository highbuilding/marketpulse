"""日志持久化配置: stdlib root logger + RotatingFileHandler + faulthandler。

设计:
- data/logs/api.log         全量日志, 10MB × 10 backup
- data/logs/api-errors.log  WARNING+, 5MB × 10 backup
- data/logs/fault.log       SIGABRT/SIGSEGV C-level 线程栈(append, 不 rotate)

structlog 通过 ProcessorFormatter 桥接到 stdlib logging, 所有 structlog.get_logger
调用最终落到这三个 handler。

为什么用 stdlib RotatingFileHandler 而非 structlog 自带:
- 项目惯例: 使用最少新依赖
- structlog 没有原生 rotation, 必须依赖 stdlib 的 handler
- faulthandler 是 stdlib builtin, 无侵入

雷区 1 (mini_racer SIGABRT) 触发时, Python 的 stdout buffer 来不及 flush, 但
faulthandler.enable 注册的 file 是用 fd 写入, 不经 stdout buffer, 崩溃时仍能落盘。
"""
from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import signal
import sys
from pathlib import Path

import structlog


_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"

_fault_log_file = None  # 模块级 keep-alive(防 GC), faulthandler 持有的 fd 必须保持开


def get_fault_log_file():
    """暴露 fault.log 的文件对象, 给 ak_call watchdog 用 faulthandler.dump_traceback_later
    在 mini_racer hang(雷区 1 hang 变种)时把所有线程栈直写到 fault.log。返回 None 表示
    setup_logging 还没跑过(测试 / import 期), 调用方应安全跳过。
    """
    return _fault_log_file


def _make_logs_dir() -> Path:
    data_dir = Path(os.getenv("APP_DATA_DIR", str(_DEFAULT_DATA_DIR)))
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def _enable_faulthandler(logs_dir: Path) -> None:
    """C 层崩溃时把所有线程栈写入 fault.log。Python 的 stdout buffer
    在 SIGABRT 时来不及 flush, 但 faulthandler 用 fd 直写, 崩溃时仍能落盘。
    """
    global _fault_log_file
    fault_path = logs_dir / "fault.log"
    # append 模式, 永不 rotate(崩溃罕见, 文件不会爆)
    _fault_log_file = open(fault_path, "ab")
    faulthandler.enable(file=_fault_log_file, all_threads=True)
    # SIGABRT 是 mini_racer V8 抛的; SIGSEGV 是段错误
    for sig in (signal.SIGABRT, signal.SIGSEGV, signal.SIGBUS):
        try:
            faulthandler.register(sig, file=_fault_log_file, all_threads=True)
        except (RuntimeError, OSError):
            # macOS 上 SIGBUS 可能不可注册, 无害跳过
            pass


def setup_logging(level: str | None = None) -> None:
    """配置 stdlib root logger + structlog ProcessorFormatter 桥接。

    幂等: 重复调用会清空 root handler 重设, 无副作用。
    """
    log_level = (level or os.getenv("APP_LOG_LEVEL", "INFO")).upper()
    logs_dir = _make_logs_dir()

    # 1. 启用 faulthandler(C 崩溃栈)
    _enable_faulthandler(logs_dir)

    # 2. structlog ProcessorFormatter 把 structlog event_dict 渲染成可读字符串
    #    foreign_pre_chain 让 stdlib logger(uvicorn / apscheduler / 第三方)
    #    也走同一渲染管线, 不至于格式割裂。
    pre_chain: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
    ]
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=False),
        foreign_pre_chain=pre_chain,
    )

    # 3. handlers
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    api_log = logs_dir / "api.log"
    file_handler = logging.handlers.RotatingFileHandler(
        api_log, maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    error_log = logs_dir / "api-errors.log"
    error_handler = logging.handlers.RotatingFileHandler(
        error_log, maxBytes=5 * 1024 * 1024, backupCount=10, encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(formatter)

    # 4. root logger 重设(幂等)
    root = logging.getLogger()
    root.setLevel(log_level)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(stdout_handler)
    root.addHandler(file_handler)
    root.addHandler(error_handler)

    # 5. structlog 配置: 把 event 透传到 stdlib(经 ProcessorFormatter 渲染)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    log = structlog.get_logger(__name__)
    log.info("logging.setup_done",
             log_dir=str(logs_dir),
             level=log_level,
             api_log=str(api_log),
             error_log=str(error_log),
             fault_log=str(logs_dir / "fault.log"))
