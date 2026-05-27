import pandas as pd
import pytest

from core.integrations.outlets import Outcome
from core.integrations.response_eval import evaluate_response


def test_evaluate_none_is_empty():
    assert evaluate_response(None, source="sina") == Outcome.empty


def test_evaluate_empty_dataframe_is_empty():
    assert evaluate_response(pd.DataFrame(), source="sina") == Outcome.empty


def test_evaluate_normal_sina_quote_df_is_ok():
    df = pd.DataFrame({"day": ["2026-05-27 09:30:00"], "close": [3000.5], "volume": [12345]})
    assert evaluate_response(df, source="sina") == Outcome.ok


def test_evaluate_sina_html_response_is_banned():
    # sina 反爬时偶尔返回 1 行单列 HTML 片段
    df = pd.DataFrame({"col0": ["<html><body>access denied</body></html>"]})
    assert evaluate_response(df, source="sina") == Outcome.banned


def test_evaluate_em_returns_ok_for_normal_dataframe():
    df = pd.DataFrame({"代码": ["600519"], "最新价": [1800.0], "涨跌幅": [1.5]})
    assert evaluate_response(df, source="em") == Outcome.ok


def test_evaluate_unknown_source_falls_back_to_basic_check():
    df = pd.DataFrame({"x": [1, 2, 3]})
    assert evaluate_response(df, source="unknown") == Outcome.ok
