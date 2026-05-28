"""统一代理出口 — 让 akshare 子进程 + 美股 SDK + sina HTTP 等所有出站请求走同一代理。

用法:
- 在 .env 或环境变量加 `MARKETPULSE_PROXY_URL=http://127.0.0.1:7890`
- collector / api 启动期调 setup_process_proxy(), 读 env 设置 process-wide
  HTTP_PROXY / HTTPS_PROXY,所有 requests / httpx / yfinance / Alpaca SDK 自动跟随。
- 没设置时(默认)无代理直连,行为与改造前一致。

设计决策:
- 单一开关 env 控制全局,不做 per-source / per-call 路由(那是 Outlet 池的事,Plan 4)
- akshare_worker 子进程通过继承父进程 env 自动跟随(不需特殊注入)
- akshare_worker 顶部的 NO_PROXY="*" 改为条件化:仅在父进程也没设代理时才生效
"""
from __future__ import annotations

import os

import structlog

log = structlog.get_logger(__name__)

ENV_KEY = "MARKETPULSE_PROXY_URL"
"""主开关 env 名。空 / 未设 = 直连;有值 = 全局走代理。"""


def get_proxy_url() -> str | None:
    """返回当前配置的代理 URL,空字符串和未设都视为 None。"""
    url = os.environ.get(ENV_KEY, "").strip()
    return url or None


def setup_process_proxy() -> None:
    """启动期注入 process-wide HTTP_PROXY/HTTPS_PROXY。

    幂等;重复调用安全。已设置 HTTPS_PROXY 时不覆盖(尊重启动方显式配置)。
    """
    url = get_proxy_url()
    if not url:
        log.info("proxy.disabled", note="无 MARKETPULSE_PROXY_URL,所有出站请求走直连")
        return

    # 不覆盖已显式设置的(可能用户在 shell 里手动 export 了)
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        if not os.environ.get(key):
            os.environ[key] = url

    # 关键:取消任何 NO_PROXY 强制(项目曾默认 NO_PROXY="*" 绕过代理)
    for key in ("NO_PROXY", "no_proxy"):
        os.environ.pop(key, None)

    log.info("proxy.enabled", url=_sanitize_url(url),
             note="ak_call / Alpaca / yfinance / sina HTTP 全部走代理")


def _sanitize_url(url: str) -> str:
    """日志脱敏:把 http://user:pass@host:port 改成 http://***:***@host:port。"""
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1) if "://" in url else ("http", url)
    creds_host = rest.split("@", 1)
    if len(creds_host) != 2:
        return url
    return f"{scheme}://***:***@{creds_host[1]}"
