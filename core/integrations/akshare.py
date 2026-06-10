"""所有 akshare 调用的统一入口 (Plan 2: 加三层中间件穿透)。

为什么必须收口:
- akshare 的 sina 系接口内部用 py_mini_racer 解 JS, V8 析构有 race。
- 我们靠 (1) 子进程隔离 (彻底根治) (2) 三层中间件 (breaker/ratelimit/outlet)
  来兜住稳定性。

约束:
- 项目里不再允许 `import akshare` + 直接 `await asyncio.to_thread(ak.xxx, ...)`。
- 所有调用都要走 `ak_call("xxx", ...)`, 中间件/日志会自动加上。
- caller 字符串便于诊断。

三层中间件 (collector 启动时 ak_middleware.setup() 注入):
1. SourceBreaker — per-source 熔断, 状态写 Redis
2. RedisTokenBucket — per-source 令牌桶, 阻塞 acquire
3. OutletPool — 出口管理, env_extras (HTTP_PROXY 等) 注入子进程

未注入中间件时 (api 进程 / 测试) ak_call 行为等价 Plan 1 末尾版本。

历史: 2026-05-28 之前曾有进程级 asyncio.Lock 包住整个 ak_call 来防 V8 race,
子进程化后已无意义 (主进程根本没 V8 实例), 反而让 cron 互相排队 → 已移除.
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

log = structlog.get_logger(__name__)
_DEFAULT_TIMEOUT_S = float(os.getenv("AK_CALL_TIMEOUT_S", "25"))
# ratelimit acquire 最长阻塞等待 (秒)。prod 大标的集(~300)首轮请求挤令牌桶,
# 配合 poller 启动 jitter 错峰后通常排不到上限;仍可经 env 调大兜底。
_RATELIMIT_MAX_WAIT_S = float(os.getenv("AK_RATELIMIT_MAX_WAIT_S", "30"))

# 瞬时网络错误内层重试: 经代理 TLS 抖动 / 连接重置 / DNS 抖动等"重试一下就好"的错误。
# 根因(2026-06-08 坐实): 冷启动经代理 7890 打 sina 偶发 SSLError: UNEXPECTED_EOF,
# 而 ak_call 此前无任何重试 → 一次抖动该标的就卡到次日。只重试网络类瞬时错误,
# 不重试 banned/限频/空数据(那些重试有害或无意义)。
_NET_RETRY_ATTEMPTS = int(os.getenv("AK_NET_RETRY_ATTEMPTS", "2"))   # 额外重试次数
_NET_RETRY_BASE_S = float(os.getenv("AK_NET_RETRY_BASE_S", "0.8"))   # 指数退避基数

# 瞬时网络错误特征。子进程/worker 把底层异常包成 RuntimeError 字符串透传
# ("... failed in worker: SSLError: ..."), 故按消息特征匹配, 不靠异常类型。
_TRANSIENT_NET_MARKERS = (
    "UNEXPECTED_EOF",
    "SSLError",
    "SSLEOFError",
    "Max retries exceeded",
    "Connection reset",
    "Connection aborted",
    "ConnectionError",
    "RemoteDisconnected",
    "Read timed out",
    "ReadTimeout",
    "ConnectTimeout",
    "Temporary failure in name resolution",
)

def _is_transient_network_error(exc: BaseException) -> bool:
    """异常是否为"重试一下可能就好"的瞬时网络错误。

    sina 阵发坏数据(2026-06-10 坐实): sina 服务端阵发返回非预期内容(限流页/空/HTML),
    akshare stock_zh_a_minute 解析 data_text.split("=(")[1] 时 IndexError。与请求频率
    无关(实测峰值 4/s 仍触发, 11:15-30 几乎没请求也失败=熔断拒了)。当瞬时坏数据重试,
    重试仍失败才计熔断 → 避免阵发坏数据快速累积触发 breaker open 把好时段也拒了。
    用 "解析函数名 + IndexError" 组合判定, 避免误伤真正的代码 IndexError。
    """
    msg = str(exc)
    if any(m in msg for m in _TRANSIENT_NET_MARKERS):
        return True
    # sina 坏数据: stock_zh_a_minute 解析 IndexError(限流页/空响应)
    if "stock_zh_a_minute" in msg and "list index out of range" in msg:
        return True
    return False


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


# ── 常驻 worker 池单例(collector 启动时 init, api/crypto 不 init → None 退回旧路径)──
_worker_pool = None  # type: ignore[var-annotated]


def get_worker_pool():
    """返回当前进程的 worker 池单例; 未 init(api/crypto/测试)返 None。"""
    return _worker_pool


async def init_worker_pool(size: int) -> None:
    """collector lifespan 启动池。重复调用幂等(已存在则跳过)。"""
    global _worker_pool
    if _worker_pool is not None:
        return
    from core.integrations.ak_worker_pool import AkWorkerPool
    _worker_pool = AkWorkerPool(size)
    await _worker_pool.start()


async def close_worker_pool() -> None:
    """collector lifespan 关闭池。"""
    global _worker_pool
    if _worker_pool is not None:
        await _worker_pool.aclose()
        _worker_pool = None


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
        await middleware.ratelimits[source].acquire(
            blocking=True, max_wait_s=_RATELIMIT_MAX_WAIT_S)

    # 三层: outlet acquire
    lease = None
    env_extras: dict[str, str] = {}
    if middleware is not None:
        lease = await middleware.outlet_pool.acquire()
        env_extras = dict(lease.env)

    started = time.monotonic()
    timeout_s = ak_timeout_s or _DEFAULT_TIMEOUT_S
    log.info("ak_call.start", func=func_name, caller=label, source=source,
             outlet=lease.outlet_id if lease else None,
             timeout_s=timeout_s, args_count=len(args),
             kwargs=_safe_kwargs(kwargs))
    outcome: Outcome
    result: Any = None
    try:
        # 默认(env_extras 空, LocalOutlet)走常驻 worker 池, 复用进程省去每次
        # import akshare(~0.8s CPU)。代理池注入 env_extras 时退回一次性子进程
        # (池 worker 用启动 env, 无法按请求改 env)。
        # 瞬时网络错误(经代理 TLS 抖动等)内层重试: 只重试网络类, 指数退避。
        # 重试在 _report_all 之前完成 → 恢复后只向 breaker 报一次成功, 不误触熔断。
        pool = get_worker_pool()
        attempt = 0
        while True:
            try:
                if pool is not None and not env_extras:
                    result = await pool.call(func_name, args, kwargs, timeout_s)
                else:
                    result = await asyncio.to_thread(
                        _run_ak_in_child_process,
                        func_name, args, kwargs, timeout_s, env_extras,
                    )
                break
            except subprocess.TimeoutExpired:
                raise  # timeout 不在网络瞬时重试范围, 交给外层 breaker
            except Exception as e:  # noqa: BLE001
                if attempt >= _NET_RETRY_ATTEMPTS or not _is_transient_network_error(e):
                    raise
                delay = _NET_RETRY_BASE_S * (2 ** attempt)
                attempt += 1
                log.warning("ak_call.net_retry", func=func_name, caller=label,
                            source=source, attempt=attempt,
                            max_attempts=_NET_RETRY_ATTEMPTS, delay_s=round(delay, 2),
                            error_type=type(e).__name__, error=str(e)[:120])
                await asyncio.sleep(delay)
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
    if outcome == Outcome.empty:
        # 空数据通常意味着上游异常 (节假日 / 代码迁移 / sina 静默降级),
        # 提到 WARN 让 api-errors.log 抓得到, 否则会被 INFO 海洋淹没。
        log.warning("ak_call.empty", func=func_name, caller=label,
                    source=source, elapsed_ms=elapsed_ms,
                    result=_result_summary(result))
        return result
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
