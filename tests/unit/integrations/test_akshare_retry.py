"""ak_call 瞬时网络错误重试单测。

根因(2026-06-08): 冷启动经代理打 sina 偶发 SSLError: UNEXPECTED_EOF, 而 ak_call
此前无重试 → 一次抖动该标的就卡到次日。这里固化"瞬时网络错误重试后成功"行为,
以及"非瞬时错误(banned/数据问题)不重试直接抛"。

测试态 middleware=None(未注入 breaker/ratelimit/outlet), ak_call 直接走 worker
池 fetch 路径(等价 Plan 1 末尾版本), 正好隔离出重试逻辑。
"""
from __future__ import annotations

import pytest

import core.integrations.akshare as ak


class _FakePool:
    """可编排的假 worker 池: 按 side_effects 列表逐次抛错 / 返回。"""

    def __init__(self, side_effects: list):
        self._effects = list(side_effects)
        self.calls = 0

    async def call(self, func_name, args, kwargs, timeout_s):
        self.calls += 1
        eff = self._effects.pop(0)
        if isinstance(eff, BaseException):
            raise eff
        return eff


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    # 缩短退避, 单测不真睡
    monkeypatch.setattr(ak, "_NET_RETRY_BASE_S", 0.0)
    monkeypatch.setattr(ak, "_NET_RETRY_ATTEMPTS", 2)


def _ssl_eof() -> RuntimeError:
    return RuntimeError(
        "stock_zh_a_daily failed in worker: SSLError: HTTPSConnectionPool("
        "host='finance.sina.com.cn', port=443): Max retries exceeded "
        "(Caused by SSLError(SSLEOFError(8, 'UNEXPECTED_EOF_WHILE_READING')))"
    )


async def test_retry_succeeds_after_transient_ssl_eof(monkeypatch):
    """前两次 SSLEOF, 第三次成功 → ak_call 最终返回成功结果, 共调用 3 次。"""
    pool = _FakePool([_ssl_eof(), _ssl_eof(), "OK_DATA"])
    monkeypatch.setattr(ak, "get_worker_pool", lambda: pool)
    result = await ak.ak_call("stock_zh_a_daily", caller="test")
    assert result == "OK_DATA"
    assert pool.calls == 3  # 1 初次 + 2 重试


async def test_retry_exhausted_raises(monkeypatch):
    """持续 SSLEOF 超过重试上限 → 最终抛出(1 初次 + 2 重试 = 3 次)。"""
    pool = _FakePool([_ssl_eof(), _ssl_eof(), _ssl_eof()])
    monkeypatch.setattr(ak, "get_worker_pool", lambda: pool)
    with pytest.raises(RuntimeError, match="UNEXPECTED_EOF"):
        await ak.ak_call("stock_zh_a_daily", caller="test")
    assert pool.calls == 3


async def test_non_transient_error_not_retried(monkeypatch):
    """非网络瞬时错误(如数据/代码错误)不重试, 第一次就抛, 只调用 1 次。"""
    pool = _FakePool([ValueError("invalid symbol code"), "SHOULD_NOT_REACH"])
    monkeypatch.setattr(ak, "get_worker_pool", lambda: pool)
    with pytest.raises(ValueError, match="invalid symbol"):
        await ak.ak_call("stock_zh_a_daily", caller="test")
    assert pool.calls == 1
