"""响应质量评估 — 区分 ok / empty / banned 等结果, 给 breaker / outlet 上报。

关键洞察: "成功返回但内容异常" 也是失败 (sina 反爬时返回伪正常 HTML)。
参考: §4.3.4
"""
from __future__ import annotations

import re
from typing import Any

from core.integrations.outlets import Outcome

_HTML_RE = re.compile(r"<\s*(html|body|head|script|head)\b", re.IGNORECASE)


def evaluate_response(result: Any, *, source: str) -> Outcome:
    """识别 banned 伪正常返回。

    规则:
    - None / 空 → empty
    - sina 系: 列只有 1 个 + 任一格内容看起来是 HTML → banned
    - 其他源: 非空 → ok (源特定规则可后续扩展)
    """
    if result is None:
        return Outcome.empty
    shape = getattr(result, "shape", None)
    empty = getattr(result, "empty", None)
    if empty is True:
        return Outcome.empty
    if shape is not None:
        try:
            rows, *_ = tuple(shape)
            if rows == 0:
                return Outcome.empty
        except (TypeError, ValueError):
            pass
    if source == "sina":
        try:
            cols = list(getattr(result, "columns", []))
            if len(cols) <= 1:
                # 取第一列前 1 行内容判断是否 HTML
                first_col = cols[0] if cols else None
                if first_col is not None:
                    sample = str(result[first_col].iloc[0])
                    if _HTML_RE.search(sample):
                        return Outcome.banned
        except Exception:  # noqa: BLE001
            pass
    return Outcome.ok
