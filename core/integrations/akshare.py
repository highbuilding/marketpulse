"""所有 akshare 调用的统一入口 (Plan 2: 加三层中间件穿透)。

为什么必须收口:
- akshare 的 sina 系接口内部用 py_mini_racer 解 JS, V8 析构有 race。
- 我们靠 (1) 子进程隔离 (2) 全局 asyncio.Lock 串行化 + 收口减少入口面 (3) 三层中间件
  来兜住稳定性。

约束:
- 项目里不再允许 `import akshare` + 直接 `await asyncio.to_thread(ak.xxx, ...)`。
- 所有调用都要走 `ak_call("xxx", ...)`, 锁/中间件/日志会自动加上。
- caller 字符串便于诊断。

三层中间件 (collector 启动时 ak_middleware.setup() 注入):
1. SourceBreaker — per-source 熔断, 状态写 Redis
2. RedisTokenBucket — per-source 令牌桶, 阻塞 acquire
3. OutletPool — 出口管理, env_extras (HTTP_PROXY 等) 注入子进程

未注入中间件时 (api 进程 / 测试) ak_call 行为等价 Plan 1 末尾版本。
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

from core.integrations import ak_middleware
from core.integrations.outlets import Outcome
from core.integrations.response_eval import evaluate_response
from core.services._locks import acquire as _racer_acquire

log = structlog.get_logger(__name__)
_DEFAULT_TIMEOUT_S = float(os.getenv("AK_CALL_TIMEOUT_S", "25"))

# func_name -> source 映射(用于 breaker/ratelimit 分发)
# 不完整时默认 source="sina"(akshare 大多走 sina 系)
_FUNC_TO_SOURCE = {
    # em 系
    "stock_zh_a_spot_em": "em",
    "stock_hk_spot_em": "em",
    "stock_cyq_em": "em",
    "stock_zh_a_hist_em": "em",
    "fund_etf_spot_em": "em",
    "stock_individual_fund_flow": "em",
    "stock_individual_fund_flow_rank_em": "em",
    "stock_hsgt_hist_em": "em",
    "stock_board_industry_name_em": "em",
    "stock_board_concept_name_em": "em",
    "stock_hk_index_daily_em": "em",
    # ths 系
    "stock_board_industry_cons_ths": "ths",
    "stock_board_concept_cons_ths": "ths",
    # 其他 → sina
}


def _infer_source(func_name: str) -> str:
    return _FUNC_TO_SOURCE.get(func_name, "sina")


async def ak_call(
    func_name: str,
    *args: Any,
    caller: str | None = None,
    ak_timeout_s: float | None = None,
    **kwargs: Any,
) -> Any:
    label = caller or func_name
    source = _infer_source(func_name)
    middleware = ak_middleware.get()

    # 一层: breaker check
    if middleware is not None and source in middleware.breakers:
        breaker = middleware.breakers[source]
        if not await breaker.allow():
            log.warning("ak_call.breaker_open", func=func_name, caller=label, source=source)
            raise RuntimeError(f"breaker open for source={source}")

    # 二层: ratelimit acquire (blocking)
    if middleware is not None and source in middleware.ratelimits:
        await middleware.ratelimits[source].acquire(blocking=True)

    # 三层: outlet acquire
    lease = None
    env_extras: dict[str, str] = {}
    if middleware is not None:
        lease = await middleware.outlet_pool.acquire()
        env_extras = dict(lease.env)

    async with _racer_acquire(f"ak:{label}"):
        started = time.monotonic()
        timeout_s = ak_timeout_s or _DEFAULT_TIMEOUT_S
        log.info("ak_call.start", func=func_name, caller=label, source=source,
                 outlet=lease.outlet_id if lease else None,
                 timeout_s=timeout_s, args_count=len(args),
                 kwargs=_safe_kwargs(kwargs))
        outcome: Outcome
        result: Any = None
        try:
            result = await asyncio.to_thread(
                _run_ak_in_child_process,
                func_name, args, kwargs, timeout_s, env_extras,
            )
            outcome = evaluate_response(result, source=source)
        except subprocess.TimeoutExpired:
            outcome = Outcome.timeout
            await _report_all(middleware, source, lease, outcome)
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            log.warning("ak_call.timeout", func=func_name, caller=label,
                        source=source, elapsed_ms=elapsed_ms)
            raise
        except Exception as e:
            outcome = Outcome.parse_error
            await _report_all(middleware, source, lease, outcome)
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            log.warning("ak_call.failed", func=func_name, caller=label,
                        source=source, elapsed_ms=elapsed_ms,
                        error_type=type(e).__name__, error=str(e))
            raise

        await _report_all(middleware, source, lease, outcome)
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        if outcome == Outcome.banned:
            log.warning("ak_call.banned_signature", func=func_name, caller=label,
                        source=source, elapsed_ms=elapsed_ms)
            raise RuntimeError(f"banned signature detected for source={source}")
        log.info("ak_call.success", func=func_name, caller=label, source=source,
                 outcome=outcome.value, elapsed_ms=elapsed_ms,
                 result=_result_summary(result))
        return result


async def _report_all(middleware, source, lease, outcome) -> None:
    if middleware is None:
        return
    success = outcome in (Outcome.ok, Outcome.empty)
    if source in middleware.breakers:
        try:
            await middleware.breakers[source].report(success=success)
        except Exception as e:  # noqa: BLE001
            log.warning("breaker.report_failed", source=source, error=str(e))
    if lease is not None:
        try:
            await middleware.outlet_pool.report(lease, outcome)
        except Exception as e:  # noqa: BLE001
            log.warning("outlet.report_failed", outlet=lease.outlet_id, error=str(e))


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
    env_extras: dict[str, str] | None = None,
) -> Any:
    with tempfile.TemporaryDirectory(prefix="marketpulse-ak-") as tmp:
        input_path = os.path.join(tmp, "input.pkl")
        output_path = os.path.join(tmp, "output.pkl")
        with open(input_path, "wb") as fp:
            pickle.dump((args, kwargs), fp)
        env = {**os.environ, **(env_extras or {})}
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
            env=env,
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
