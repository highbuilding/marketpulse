"""测试用假 akshare worker —— 同 length-prefixed framing 协议, 行为由 func_name 控制。

替代真 akshare worker(避免网络 + 重 import), 供 test_ak_worker_pool.py 验证池机制。
- echo:  返回第一个 arg
- raise: 抛异常(消息=第一个 arg), worker 不退出
- sleep: 睡 args[0] 秒(测超时)
- crash: 立即退出进程(测崩溃重建)
"""
from __future__ import annotations

import os
import pickle
import sys
import time

from core.integrations.akshare_worker import _read_frame, _write_frame


def _handle(func_name, args, kwargs):
    if func_name == "echo":
        return ("ok", args[0] if args else None)
    if func_name == "raise":
        msg = args[0] if args else "error"
        return ("err", ("RuntimeError", msg, "fake traceback"))
    if func_name == "sleep":
        time.sleep(args[0] if args else 1.0)
        return ("ok", "slept")
    if func_name == "crash":
        sys.exit(1)  # 进程退出 → 管道断, 模拟崩溃
    return ("err", ("ValueError", f"unknown func {func_name}", ""))


def main() -> int:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    # 模拟 import akshare 慢启动(env 控制): ready 帧发出前先睡, 验证池等 ready 不竞态
    delay = float(os.environ.get("FAKE_WORKER_STARTUP_DELAY_S", "0"))
    if delay > 0:
        time.sleep(delay)
    _write_frame(stdout, b"")  # ready 握手(同真 worker, import 完成后发)
    while True:
        req = _read_frame(stdin)
        if req is None:
            return 0
        func_name, args, kwargs = pickle.loads(req)
        payload = _handle(func_name, args, kwargs)
        _write_frame(stdout, pickle.dumps(payload))


if __name__ == "__main__":
    raise SystemExit(main())
