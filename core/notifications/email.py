"""SMTP 邮件通道。

环境变量(`.env.example`):
- SMTP_HOST  必填; 缺失则 enabled=False, 通道退化为 noop
- SMTP_PORT  默认 465 (SSL)
- SMTP_USER  必填
- SMTP_PASS  必填
- SMTP_FROM  默认 = SMTP_USER
- SMTP_USE_SSL  默认 true (false 走 STARTTLS)
"""
from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
from email.message import EmailMessage

import structlog

from core.notifications.channel import NotificationError

log = structlog.get_logger(__name__)


class EmailChannel:
    name = "email"

    def __init__(self) -> None:
        self.host = os.getenv("SMTP_HOST", "")
        self.port = int(os.getenv("SMTP_PORT", "465") or "465")
        self.user = os.getenv("SMTP_USER", "")
        self.password = os.getenv("SMTP_PASS", "")
        self.from_addr = os.getenv("SMTP_FROM") or self.user
        self.use_ssl = (os.getenv("SMTP_USE_SSL", "true").lower() != "false")
        self.enabled = bool(self.host and self.user and self.password)
        if not self.enabled:
            log.warning("notify.email.disabled",
                        reason="missing SMTP_HOST/USER/PASS env",
                        has_host=bool(self.host),
                        has_user=bool(self.user),
                        has_pass=bool(self.password))

    async def send(
        self, recipient: str, subject: str, body: str,
        html: str | None = None,
    ) -> None:
        if not self.enabled:
            log.info("notify.email.skip_disabled", to=recipient)
            return
        await asyncio.to_thread(self._send_sync, recipient, subject, body, html)

    def _send_sync(
        self, recipient: str, subject: str, body: str, html: str | None,
    ) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = recipient
        msg.set_content(body)
        if html:
            # multipart/alternative: 不支持 HTML 的客户端自动 fallback 到 text/plain
            msg.add_alternative(html, subtype="html")
        try:
            if self.use_ssl:
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.host, self.port, context=ctx, timeout=15) as s:
                    s.login(self.user, self.password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=15) as s:
                    s.starttls(context=ssl.create_default_context())
                    s.login(self.user, self.password)
                    s.send_message(msg)
            log.info("notify.email.sent", to=recipient, subject=subject,
                     has_html=bool(html))
        except Exception as e:  # noqa: BLE001
            log.warning("notify.email.failed", to=recipient, error=str(e))
            raise NotificationError(f"smtp send failed: {e}") from e
