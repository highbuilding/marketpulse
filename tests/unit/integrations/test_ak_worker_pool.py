"""AkWorkerPool 单测。

用一个轻量假 worker(tests/unit/integrations/_fake_ak_worker.py)替代真 akshare worker,
行为由 func_name 控制: echo / sleep / crash。验证池的正常往返、超时杀重建、崩溃重建、
并发不串话。不依赖真实 akshare(网络/重 import)。
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from core.integrations import ak_worker_pool
from core.integrations.ak_worker_pool import AkWorkerPool

# 把池的 worker 命令指向假 worker(同 framing 协议, 行为可控)
_FAKE_CMD = [sys.executable, "-m", "tests.unit.integrations._fake_ak_worker"]


@pytest.fixture
def patch_cmd(monkeypatch):
    monkeypatch.setattr(ak_worker_pool, "_WORKER_CMD", _FAKE_CMD)


@pytest.fixture
async def pool(patch_cmd):
    p = AkWorkerPool(size=2)
    await p.start()
    yield p
    await p.aclose()


async def test_echo_roundtrip(pool):
    """正常往返: echo 把 args 原样返回。"""
    r = await pool.call("echo", ("hello",), {}, timeout_s=5.0)
    assert r == "hello"


async def test_worker_error_propagates(pool):
    """worker 内调用异常 → RuntimeError 上抛, 但 worker 不死(继续服务)。"""
    with pytest.raises(RuntimeError, match="boom"):
        await pool.call("raise", ("boom",), {}, timeout_s=5.0)
    # worker 仍健康: 下一次 echo 正常
    r = await pool.call("echo", ("again",), {}, timeout_s=5.0)
    assert r == "again"


async def test_timeout_kills_and_rebuilds(pool):
    """调用卡死 → wait_for 超时 → 杀 worker + 重建; 后续调用仍能用。"""
    with pytest.raises(subprocess.TimeoutExpired):
        await pool.call("sleep", (10.0,), {}, timeout_s=0.5)
    # 池重建了 worker, echo 仍正常
    r = await pool.call("echo", ("recovered",), {}, timeout_s=5.0)
    assert r == "recovered"


async def test_crash_rebuilds(pool):
    """worker 崩溃(进程退出)→ 管道断 → 重建; 后续可用。"""
    with pytest.raises((RuntimeError, BrokenPipeError, EOFError)):
        await pool.call("crash", (), {}, timeout_s=5.0)
    r = await pool.call("echo", ("after_crash",), {}, timeout_s=5.0)
    assert r == "after_crash"


async def test_concurrent_no_crosstalk(pool):
    """并发多调用各拿各的结果, 不串话(2 worker, 4 并发请求)。"""
    import asyncio
    results = await asyncio.gather(*[
        pool.call("echo", (f"msg{i}",), {}, timeout_s=5.0) for i in range(4)
    ])
    assert results == ["msg0", "msg1", "msg2", "msg3"]


async def test_slow_startup_no_broken_pipe(patch_cmd, monkeypatch):
    """回归: worker 慢启动(模拟 import akshare ~1s)期间, start() 等 ready 握手,
    请求不会撞上未就绪 worker 触发 BrokenPipe(2026-06-08 实测过的 bug)。"""
    monkeypatch.setenv("FAKE_WORKER_STARTUP_DELAY_S", "1.0")
    p = AkWorkerPool(size=2)
    await p.start()  # 应阻塞等 worker ready(~1s), 而非立即返回
    try:
        # 启动后立刻打请求, 不应 BrokenPipe
        r = await p.call("echo", ("ready",), {}, timeout_s=5.0)
        assert r == "ready"
    finally:
        await p.aclose()
