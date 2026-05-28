from __future__ import annotations

import os
import pickle
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

