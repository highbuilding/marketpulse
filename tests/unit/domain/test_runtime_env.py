"""runtime_env 分层逻辑单测。"""
import importlib

import core.domain.runtime_env as re


def _reload():
    importlib.reload(re)
    return re


def test_app_env_defaults_to_test(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    assert re.app_env() == "test"
    assert re.is_prod() is False


def test_app_env_prod(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    assert re.app_env() == "prod"
    assert re.is_prod() is True


def test_app_env_case_insensitive(monkeypatch):
    monkeypatch.setenv("APP_ENV", "PROD")
    assert re.is_prod() is True
    monkeypatch.setenv("APP_ENV", "Test")
    assert re.is_prod() is False


def test_tiered_int_picks_by_env(monkeypatch):
    monkeypatch.delenv("POLL_INTERVAL_S", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")
    assert re.tiered_int("POLL_INTERVAL_S", test=10, prod=90) == 90
    monkeypatch.setenv("APP_ENV", "test")
    assert re.tiered_int("POLL_INTERVAL_S", test=10, prod=90) == 10


def test_tiered_int_explicit_override_wins(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("POLL_INTERVAL_S", "25")
    # 显式 env 覆盖优先于 APP_ENV 分层默认
    assert re.tiered_int("POLL_INTERVAL_S", test=10, prod=90) == 25


def test_tiered_int_invalid_override_falls_back(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("POLL_INTERVAL_S", "abc")
    assert re.tiered_int("POLL_INTERVAL_S", test=10, prod=90) == 10
