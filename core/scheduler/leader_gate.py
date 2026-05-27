"""Leader gate — scheduler 内 cron job 执行前调用 ensure_leader()。

设计:collector 启动时 set_leader() 注入 Leader 实例; cron job 在 is_leader()
查询是否 is_leader, 否则 return 跳过本轮。

api 进程不调用 set_leader,所以即便 api 中误注册了 cron 也会被 gate 拦下(防御深度)。
单进程 dev 时 set_leader 也未必调用,is_leader() 默认返回 True (友好)。
"""
from __future__ import annotations

from typing import Protocol


class _LeaderLike(Protocol):
    def is_leader(self) -> bool: ...


_leader: _LeaderLike | None = None


def set_leader(leader: _LeaderLike) -> None:
    global _leader
    _leader = leader


def is_leader() -> bool:
    """无 leader 注入时默认 True(单进程 dev 友好)。"""
    return _leader.is_leader() if _leader is not None else True
