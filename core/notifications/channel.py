"""通知通道抽象 + 错误类型。

每个通道(email / wechat / ...)实现 NotificationChannel Protocol。
NotificationService 按 recipient.channel 字段路由到对应 channel.send。
"""
from __future__ import annotations

from typing import Protocol


class NotificationError(Exception):
    """通道发送失败 - 调用方决定是否记 audit / 重试。"""


class NotificationChannel(Protocol):
    name: str
    enabled: bool

    async def send(
        self, recipient: str, subject: str, body: str,
        html: str | None = None,
    ) -> None:
        """发送一条通知; 失败抛 NotificationError。

        body: text/plain 内容(必传, 邮件客户端不支持 HTML 时降级)
        html: text/html 内容(可选, 与 body 组成 multipart/alternative)
        """
        ...
