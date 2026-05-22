"""微信通道占位 - 后续接入企业微信 / 公众号 / Server 酱。

当前 enabled=False, send 直接抛 NotImplementedError, 业务调用方按 channel 路由
不会调用到这里(没有 recipient.channel='wechat' 的记录)。
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


class WeChatChannel:
    name = "wechat"
    enabled = False

    async def send(
        self, recipient: str, subject: str, body: str,
        html: str | None = None,
    ) -> None:
        log.info("notify.wechat.placeholder", to=recipient)
        raise NotImplementedError(
            "wechat channel not implemented yet; pick: 企业微信 / Server 酱 / 公众号 推送"
        )
