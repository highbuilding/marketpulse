import os

import pytest

from core.integrations.proxy_setup import (
    ENV_KEY, get_proxy_url, setup_process_proxy, _sanitize_url,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """每个测试前清空相关 env,避免污染。"""
    for k in (ENV_KEY, "HTTPS_PROXY", "HTTP_PROXY", "https_proxy",
              "http_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)


def test_get_proxy_url_returns_none_when_unset():
    assert get_proxy_url() is None


def test_get_proxy_url_returns_none_when_empty(monkeypatch):
    monkeypatch.setenv(ENV_KEY, "")
    assert get_proxy_url() is None


def test_get_proxy_url_strips_whitespace(monkeypatch):
    monkeypatch.setenv(ENV_KEY, "  http://127.0.0.1:7890  ")
    assert get_proxy_url() == "http://127.0.0.1:7890"


def test_setup_with_no_env_keeps_environment_clean():
    setup_process_proxy()
    assert "HTTPS_PROXY" not in os.environ
    assert "HTTP_PROXY" not in os.environ
    assert "NO_PROXY" not in os.environ


def test_setup_with_proxy_url_injects_env(monkeypatch):
    monkeypatch.setenv(ENV_KEY, "http://127.0.0.1:7890")
    setup_process_proxy()
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert os.environ["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert os.environ["https_proxy"] == "http://127.0.0.1:7890"
    assert os.environ["http_proxy"] == "http://127.0.0.1:7890"


def test_setup_clears_no_proxy_when_proxy_enabled(monkeypatch):
    monkeypatch.setenv(ENV_KEY, "http://127.0.0.1:7890")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    setup_process_proxy()
    assert "NO_PROXY" not in os.environ
    assert "no_proxy" not in os.environ


def test_setup_does_not_overwrite_explicit_https_proxy(monkeypatch):
    monkeypatch.setenv(ENV_KEY, "http://config-url:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://user-explicit:9000")
    setup_process_proxy()
    # 用户显式设的优先
    assert os.environ["HTTPS_PROXY"] == "http://user-explicit:9000"


def test_setup_is_idempotent(monkeypatch):
    monkeypatch.setenv(ENV_KEY, "http://127.0.0.1:7890")
    setup_process_proxy()
    setup_process_proxy()  # 第二次调用不应抛
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:7890"


def test_sanitize_url_no_credentials():
    assert _sanitize_url("http://127.0.0.1:7890") == "http://127.0.0.1:7890"


def test_sanitize_url_with_credentials():
    assert _sanitize_url("http://user:pass@proxy.com:8080") == "http://***:***@proxy.com:8080"
    assert _sanitize_url("https://abc:def@proxy.com:443") == "https://***:***@proxy.com:443"
