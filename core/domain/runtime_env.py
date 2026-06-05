"""SSoT: 运行环境分层 (APP_ENV).

本地默认 test(标的少, 高频轮询无压力); 线上 `export APP_ENV=prod`
(标的 ~400, 轮询须拉长卡 sina 5/s 限频)。

采集间隔等"按环境取不同值"的参数统一经此模块, 禁止散点 os.getenv("APP_ENV")。
见 spec docs/.../2026-06-05-env-tiering-symbol-expansion-data-cleanup-design.md 第 1-2 章。
"""
from __future__ import annotations

import os


def app_env() -> str:
    """归一化的运行环境: 'prod' 或 'test'(默认)。"""
    return "prod" if os.getenv("APP_ENV", "test").strip().lower() == "prod" else "test"


def is_prod() -> bool:
    return app_env() == "prod"


def tiered_int(env_key: str, *, test: int, prod: int) -> int:
    """按环境取整数参数, 支持 env_key 显式覆盖(优先级最高)。

    优先级: 显式 env_key > APP_ENV 分层默认。
    例: POLL_INTERVAL_S 显式设了就用它, 否则 prod=90 / test=10。
    """
    raw = os.getenv(env_key)
    if raw is not None and raw.strip():
        try:
            return int(raw)
        except ValueError:
            pass
    return prod if is_prod() else test
