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
import os
import pickle
import subprocess
import sys
import time
import tempfile
from typing import Any

import structlog

from core.services._locks import acquire as _racer_acquire

log = structlog.get_logger(__name__)
_DEFAULT_TIMEOUT_S = float(os.getenv("AK_CALL_TIMEOUT_S", "25"))


async def ak_call(
    func_name: str,
    *args: Any,
    caller: str | None = None,
    ak_timeout_s: float | None = None,
    **kwargs: Any,
) -> Any:
    """串行化执行 akshare 接口。

    func_name: ak 模块下的函数名, 用字符串避免调用方再 `import akshare`。
    caller: 诊断字符串(默认用 func_name), 出现在 racer.* 日志里。
    """
    label = caller or func_name
    async with _racer_acquire(f"ak:{label}"):
        started = time.monotonic()
        timeout_s = ak_timeout_s or _DEFAULT_TIMEOUT_S
        log.info(
            "ak_call.start",
            func=func_name,
            caller=label,
            timeout_s=timeout_s,
            args_count=len(args),
            kwargs=_safe_kwargs(kwargs),
        )
        try:
            result = await asyncio.to_thread(
                _run_ak_in_child_process,
                func_name,
                args,
                kwargs,
                timeout_s,
            )
        except Exception as e:
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            log.warning(
                "ak_call.failed",
                func=func_name,
                caller=label,
                elapsed_ms=elapsed_ms,
                error_type=type(e).__name__,
                error=str(e),
            )
            raise
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        log.info(
            "ak_call.success",
            func=func_name,
            caller=label,
            elapsed_ms=elapsed_ms,
            result=_result_summary(result),
        )
        return result


def _safe_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        text = str(value)
        out[key] = text if len(text) <= 80 else f"{text[:77]}..."
    return out


def _result_summary(result: Any) -> dict[str, Any]:
    shape = getattr(result, "shape", None)
    if shape is not None:
        try:
            return {"type": type(result).__name__, "shape": tuple(shape)}
        except TypeError:
            return {"type": type(result).__name__}
    if isinstance(result, (list, tuple, set, dict)):
        return {"type": type(result).__name__, "len": len(result)}
    return {"type": type(result).__name__}


def _run_ak_in_child_process(
    func_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    timeout_s: float,
) -> Any:
    with tempfile.TemporaryDirectory(prefix="marketpulse-ak-") as tmp:
        input_path = os.path.join(tmp, "input.pkl")
        output_path = os.path.join(tmp, "output.pkl")
        with open(input_path, "wb") as fp:
            pickle.dump((args, kwargs), fp)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "core.integrations.akshare_worker",
                func_name,
                input_path,
                output_path,
            ],
            cwd=os.getcwd(),
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace")[-4000:]
            stdout = proc.stdout.decode("utf-8", errors="replace")[-1000:]
            raise RuntimeError(
                f"akshare worker failed rc={proc.returncode}: {func_name}; "
                f"stdout={stdout!r}; stderr={stderr!r}"
            )
        if not os.path.exists(output_path):
            raise RuntimeError(f"akshare worker produced no output: {func_name}")
        with open(output_path, "rb") as fp:
            status, payload = pickle.load(fp)
        if status == "ok":
            return payload
        error_type, message, tb = payload
        raise RuntimeError(f"{func_name} failed in child process: {error_type}: {message}\n{tb}")
    # subprocess.run raises TimeoutExpired; keep error text concise and stable.
