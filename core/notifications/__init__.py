"""Notifications 子系统 export。"""
from core.notifications.channel import NotificationChannel, NotificationError
from core.notifications.email import EmailChannel
from core.notifications.templates import render_summary
from core.notifications.wechat import WeChatChannel

__all__ = [
    "NotificationChannel",
    "NotificationError",
    "EmailChannel",
    "WeChatChannel",
    "render_summary",
]
