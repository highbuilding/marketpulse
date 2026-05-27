from __future__ import annotations

import os
import pickle
import sys
import traceback

# 子进程默认绕过系统代理:macOS 会把 127.0.0.1:7890 (Clash/V2Ray) 等系统级 SOCKS 代理
# 注入到 urllib/requests; 当代理服务未启动时, EM/Sina 这类国内接口会全数 ProxyError。
# 父进程 uvicorn 通常不读 macOS 系统代理(只读 env), 所以"主进程能通、子进程不通"
# 的现象就是这个差异造成的。让子进程显式无代理直连最稳妥。
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: python -m core.integrations.akshare_worker <func> <input.pkl> <output.pkl>", file=sys.stderr)
        return 2
    func_name, input_path, output_path = sys.argv[1:4]
    with open(input_path, "rb") as fp:
        args, kwargs = pickle.load(fp)
    try:
        import akshare as ak  # noqa: PLC0415

        func = getattr(ak, func_name, None)
        if func is None or not callable(func):
            raise AttributeError(f"akshare has no callable '{func_name}'")
        payload = ("ok", func(*args, **kwargs))
    except Exception as e:  # noqa: BLE001
        payload = ("err", (type(e).__name__, str(e), traceback.format_exc()))
    with open(output_path, "wb") as fp:
        pickle.dump(payload, fp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

