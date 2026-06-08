"""常驻 akshare worker 池。

替代"每次 ak_call 起新子进程重 import akshare(~0.8s CPU)"—— 维护 N 个常驻
worker(各 import akshare 一次), 通过 stdin/stdout length-prefixed pickle 帧复用。

并发上限 = 池大小(天然削峰: 开盘多标的并发 ak_call 排队过 N 个 worker, 同时只
N 个干活, 不再瞬时起十几个子进程打满 CPU)。

崩溃/超时: kill 该 worker + 重建补上, 当次调用抛 subprocess.TimeoutExpired /
RuntimeError(对齐旧路径异常, ak_call 中间件无感)。保留子进程隔离(雷区 1)。

env_extras: 默认 LocalOutlet 为空, 池用启动 env。非空(未来代理池)由 akshare.py
走旧一次性子进程兜底, 不进池。
"""
from __future__ import annotations

import asyncio
import os
import pickle
import subprocess
import sys

import structlog

from core.integrations.akshare_worker import _read_frame, _write_frame

log = structlog.get_logger(__name__)

_WORKER_CMD = [sys.executable, "-m", "core.integrations.akshare_worker"]


class _Worker:
    """单个常驻 worker 子进程封装。请求/响应是阻塞管道 I/O, 由调用方在线程里跑。"""

    __slots__ = ("proc", "seq")

    def __init__(self, proc: subprocess.Popen, seq: int) -> None:
        self.proc = proc
        self.seq = seq

    def roundtrip(self, req_body: bytes) -> bytes:
        """阻塞: 写请求帧 → 读响应帧。管道断/EOF 抛 BrokenPipeError。"""
        if self.proc.stdin is None or self.proc.stdout is None:
            raise BrokenPipeError("worker pipes unavailable")
        _write_frame(self.proc.stdin, req_body)
        resp = _read_frame(self.proc.stdout)
        if resp is None:
            raise BrokenPipeError("worker closed pipe (crash/exit)")
        return resp

    def kill(self) -> None:
        try:
            self.proc.kill()
        except Exception:  # noqa: BLE001
            pass


class AkWorkerPool:
    """N 个常驻 worker 的池。call() 取空闲 worker 执行, 完归还; 崩溃则重建。"""

    def __init__(self, size: int) -> None:
        self._size = max(1, size)
        self._idle: asyncio.Queue[_Worker] = asyncio.Queue()
        self._seq = 0
        self._closed = False

    def _spawn(self) -> _Worker:
        self._seq += 1
        proc = subprocess.Popen(
            _WORKER_CMD,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,  # 继承 collector stderr: V8 SIGABRT 的 C 层栈可见, 便于诊断
            cwd=os.getcwd(),
            env={**os.environ},
            bufsize=0,
        )
        w = _Worker(proc, self._seq)
        log.info("ak_pool.worker_spawned", seq=w.seq, pid=proc.pid)
        return w

    async def start(self) -> None:
        """启动 size 个 worker, 各等 ready 握手后入队(避免 import 窗口竞态)。"""
        workers = [self._spawn() for _ in range(self._size)]
        # 并发等所有 worker import 完成(发来 ready 空帧), 最多等 30s
        await asyncio.gather(*(self._await_ready(w) for w in workers))
        for w in workers:
            self._idle.put_nowait(w)
        log.info("ak_pool.started", size=self._size)

    async def _await_ready(self, worker: _Worker, timeout_s: float = 30.0) -> None:
        """等 worker 发来 ready 帧(import akshare 完成的信号)。"""
        try:
            await asyncio.wait_for(
                asyncio.to_thread(lambda: _read_frame(worker.proc.stdout)), timeout_s)
            log.info("ak_pool.worker_ready", seq=worker.seq, pid=worker.proc.pid)
        except Exception as e:  # noqa: BLE001
            log.warning("ak_pool.worker_ready_failed", seq=worker.seq, error=str(e))
            worker.kill()
            raise

    async def call(
        self, func_name: str, args: tuple, kwargs: dict, timeout_s: float,
    ) -> object:
        """取空闲 worker 执行一次调用。超时/崩溃 kill+重建并抛异常。"""
        if self._closed:
            raise RuntimeError("ak_worker_pool closed")
        req_body = pickle.dumps((func_name, args, kwargs))
        worker = await self._idle.get()
        try:
            resp_body = await asyncio.wait_for(
                asyncio.to_thread(worker.roundtrip, req_body), timeout_s,
            )
        except asyncio.TimeoutError:
            # 卡死(akshare 调用 hang)→ 杀 worker + 重建, 抛 TimeoutExpired 对齐旧路径
            worker.kill()
            self._replace(worker)
            raise subprocess.TimeoutExpired(cmd=func_name, timeout=timeout_s)
        except BaseException:
            # 崩溃/管道断/取消 → 杀 worker + 重建, 不归还坏 worker
            worker.kill()
            self._replace(worker)
            raise
        else:
            self._idle.put_nowait(worker)  # 健康, 归还池
        status, payload = pickle.loads(resp_body)
        if status == "ok":
            return payload
        error_type, message, tb = payload
        raise RuntimeError(
            f"{func_name} failed in worker: {error_type}: {message}\n{tb}")

    def _replace(self, dead: _Worker) -> None:
        """崩溃/超时后补一个新 worker。后台等其 ready 再入队(不阻塞当前调用)。"""
        if self._closed:
            return
        log.warning("ak_pool.worker_replaced", dead_seq=dead.seq, dead_pid=dead.proc.pid)
        new_worker = self._spawn()

        async def _ready_then_enqueue():
            try:
                await self._await_ready(new_worker)
                if not self._closed:
                    self._idle.put_nowait(new_worker)
            except Exception:  # noqa: BLE001
                pass  # ready 失败已 kill; 池容量临时-1, 下次崩溃重建再补

        asyncio.ensure_future(_ready_then_enqueue())

    async def aclose(self) -> None:
        """关闭池: 关 stdin 让 worker 干净退出, 兜底 kill。"""
        self._closed = True
        drained: list[_Worker] = []
        while not self._idle.empty():
            try:
                drained.append(self._idle.get_nowait())
            except asyncio.QueueEmpty:
                break
        for w in drained:
            try:
                if w.proc.stdin is not None:
                    w.proc.stdin.close()  # EOF → worker _serve 干净 return
            except Exception:  # noqa: BLE001
                pass
        for w in drained:
            try:
                w.proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                w.kill()
        log.info("ak_pool.closed", workers=len(drained))
