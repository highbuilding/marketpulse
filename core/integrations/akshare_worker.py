"""akshare 子进程 worker。

两种模式(由 argv 区分):
- **常驻循环**(无额外 argv, 池模式): 启动时 import akshare 一次, 之后从 stdin
  读 length-prefixed pickle 请求帧、执行、写 stdout。避免每次调用冷启动重 import
  akshare(~0.8s CPU)。由 core.integrations.ak_worker_pool 拉起复用。
- **一次性**(argv = <func> <input.pkl> <output.pkl>, 向后兼容): 读 pkl→调用→写 pkl→退出。

子进程隔离的意义(雷区 1): akshare sina 系内部 py_mini_racer 解 JS, V8 析构有 race
→ SIGABRT。隔离在独立进程, 崩溃只死单个 worker, 主进程无 V8 实例不受影响; 池负责
重建。常驻复用不改变隔离语义, 只省去重复 import。

stdin/stdout 协议(常驻模式): 每帧 = 4 字节大端无符号长度 + pickle body。
请求 body = (func_name, args, kwargs); 响应 body = ("ok", result) | ("err", (type, msg, tb))。
"""
from __future__ import annotations

import os
import pickle
import struct
import sys
import traceback

# 子进程从父进程继承 env(包括 HTTPS_PROXY/HTTP_PROXY 设置)。
# 仅在父进程也没设代理时,才用 NO_PROXY="*" 强制直连绕过 macOS 系统代理 —
# 因为 macOS 会把系统级 SOCKS 代理(Clash 等)注入 urllib/requests,
# 当代理软件未启动时会全数 ProxyError。
# 父进程通过 core.integrations.proxy_setup.setup_process_proxy() 显式设了
# HTTPS_PROXY 时,子进程跟随走代理,**不**强制 NO_PROXY。
if not (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")):
    os.environ.setdefault("NO_PROXY", "*")
    os.environ.setdefault("no_proxy", "*")


def _call_ak(ak, func_name: str, args, kwargs):
    """执行单次 akshare 调用, 返回 ("ok", result) | ("err", (type, msg, tb))。"""
    try:
        func = getattr(ak, func_name, None)
        if func is None or not callable(func):
            raise AttributeError(f"akshare has no callable '{func_name}'")
        return ("ok", func(*args, **kwargs))
    except Exception as e:  # noqa: BLE001
        return ("err", (type(e).__name__, str(e), traceback.format_exc()))


# ── 常驻模式: length-prefixed framing over stdin/stdout ──

def _read_frame(stream) -> bytes | None:
    """读一帧: 4 字节大端长度 + body。EOF(父进程关 stdin)返 None。"""
    header = stream.read(4)
    if not header or len(header) < 4:
        return None
    (length,) = struct.unpack(">I", header)
    body = b""
    while len(body) < length:
        chunk = stream.read(length - len(body))
        if not chunk:
            return None  # 半包遇 EOF, 视为断开
        body += chunk
    return body


def _write_frame(stream, body: bytes) -> None:
    stream.write(struct.pack(">I", len(body)))
    stream.write(body)
    stream.flush()


def _serve() -> int:
    """常驻循环: import akshare 一次, 持续从 stdin 处理请求。"""
    import akshare as ak  # noqa: PLC0415  (一次性, 之后所有请求复用)

    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    # ready 握手: import 完成后发一个空帧, 池据此确认 worker 可服务再放进可用队列,
    # 杜绝"worker 还在 import(~1s)时请求写管道 → BrokenPipe"的启动竞态。
    _write_frame(stdout, b"")
    while True:
        req = _read_frame(stdin)
        if req is None:
            return 0  # 父进程关闭 stdin → 干净退出
        try:
            func_name, args, kwargs = pickle.loads(req)
        except Exception as e:  # noqa: BLE001
            payload = ("err", ("UnpicklingError", str(e), traceback.format_exc()))
        else:
            payload = _call_ak(ak, func_name, args, kwargs)
        # 单次调用异常已收进 payload, 不退出循环; 仅协议/写失败才崩
        _write_frame(stdout, pickle.dumps(payload))


def _run_once(func_name: str, input_path: str, output_path: str) -> int:
    """一次性模式(向后兼容): 读 pkl→调用→写 pkl→退出。"""
    with open(input_path, "rb") as fp:
        args, kwargs = pickle.load(fp)
    import akshare as ak  # noqa: PLC0415
    payload = _call_ak(ak, func_name, args, kwargs)
    with open(output_path, "wb") as fp:
        pickle.dump(payload, fp)
    return 0


def main() -> int:
    # argv 区分模式: 3 个额外参数 = 一次性; 否则常驻循环。
    if len(sys.argv) == 4:
        return _run_once(sys.argv[1], sys.argv[2], sys.argv[3])
    return _serve()


if __name__ == "__main__":
    raise SystemExit(main())
