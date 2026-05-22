"""CD 信号汇总通知服务。

主流程 maybe_send_summary(market):
  1. 拉本市场启用的 symbol_notification_config (按 interval 过滤)
  2. SignalRepo.latest_signals_today 拿当日 (count + 最新触发 price + bar_ts)
  3. 按 config 过滤 (config 没勾该 interval 的 cell 丢弃)
  4. 算 snapshot hash (只哈希 count, 不含 price 避免误发), 与上一轮 audit 比对
  5. hash 一致或空 cells → 留 audit 但不发
  6. 否则用 directory 拿中文名 + 渲染 (subject, text, html), 广播给本市场 enabled 收件人
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import structlog

from core.domain.markets import infer_market
from core.domain.market_sessions import MARKET_TZ
from core.notifications.channel import NotificationChannel, NotificationError
from core.notifications.templates import render_summary
from core.persistence.notification_repo import NotificationRepo
from core.persistence.signal_repo import SignalRepo, TodaySignalCell

if TYPE_CHECKING:
    from core.services.symbol_directory_service import SymbolDirectoryService

log = structlog.get_logger(__name__)


class NotificationService:
    def __init__(
        self,
        notif_repo: NotificationRepo,
        signal_repo: SignalRepo,
        channels: dict[str, NotificationChannel],
        directory_service: "SymbolDirectoryService | None" = None,
    ) -> None:
        self.notif_repo = notif_repo
        self.signal_repo = signal_repo
        self.channels = channels  # {"email": EmailChannel(), "wechat": WeChatChannel()}
        self.directory_service = directory_service

    # ---------- public ----------

    async def maybe_send_summary(self, market: str) -> bool:
        """扫描后调一次; 返回是否实际发出。"""
        cells = await self._compute_counts(market)
        snapshot_hash = self._hash(cells)
        last = await self.notif_repo.last_audit_hash(market)

        if snapshot_hash == last:
            log.debug("notify.skip_same_hash", market=market, hash=snapshot_hash[:8])
            await self.notif_repo.record_audit(market, snapshot_hash, sent=False)
            return False
        if not cells:
            log.debug("notify.skip_empty", market=market)
            await self.notif_repo.record_audit(market, snapshot_hash, sent=False)
            return False

        recipients = await self.notif_repo.list_recipients(market, only_enabled=True)
        if not recipients:
            log.info("notify.no_recipients", market=market)
            await self.notif_repo.record_audit(
                market, snapshot_hash, sent=False, error="no recipients",
            )
            return False

        # 拉中文名(可选, 失败不阻塞通知)
        symbols = sorted({sym for (sym, _, _) in cells.keys()})
        name_map: dict[str, str] = {}
        if self.directory_service is not None and symbols:
            try:
                name_map = await self.directory_service.get_names(symbols)
            except Exception as e:  # noqa: BLE001
                log.warning("notify.directory_lookup_failed",
                            market=market, error=str(e))

        subject, text_body, html_body = render_summary(market, cells, name_map)
        ok, err = await self._broadcast(recipients, subject, text_body, html_body)
        await self.notif_repo.record_audit(
            market, snapshot_hash, sent=ok,
            recipients_count=len(recipients), error=err,
        )
        log.info("notify.summary", market=market, sent=ok,
                 recipients=len(recipients), counts=len(cells))
        return ok

    async def send_test(self, market: str) -> tuple[bool, int, str | None]:
        """测试发送 - 给本市场所有 enabled 收件人发一条样例邮件。"""
        recipients = await self.notif_repo.list_recipients(market, only_enabled=True)
        if not recipients:
            return False, 0, "no recipients configured"
        subject = f"[MarketPulse] {market} 测试邮件"
        body = (
            "这是一封 MarketPulse 通知系统测试邮件。\n"
            "如果你收到了这封邮件,说明 SMTP 配置正确。\n"
            "今后将收到本市场 CD 信号变化汇总。"
        )
        ok, err = await self._broadcast(recipients, subject, body)
        return ok, len(recipients), err

    # ---------- internals ----------

    async def _compute_counts(
        self, market: str,
    ) -> dict[tuple[str, str, str], TodaySignalCell]:
        """根据 symbol_notification_config 过滤后的当日信号 cell (含 count + price)。"""
        configs = await self.notif_repo.list_symbol_configs()
        # 过滤本市场 symbol
        market_configs = {
            cfg.symbol: set(cfg.intervals)
            for cfg in configs
            if infer_market(cfg.symbol) == market
        }
        if not market_configs:
            return {}
        since = self._today_start_utc(market)
        raw = await self.signal_repo.latest_signals_today(
            list(market_configs.keys()), since,
        )
        # 仅保留 (symbol 启用了该 interval) 的 cell
        return {
            (sym, iv, st): cell
            for (sym, iv, st), cell in raw.items()
            if iv in market_configs.get(sym, set())
        }

    @staticmethod
    def _today_start_utc(market: str) -> datetime:
        """本市场 tz 的今日自然日 00:00 → UTC。

        用作 SignalRepo.latest_signals_today 的 bar_ts 下限。
        - A 股 1d bar ts = BJT 00:00, 与 today_start 完全相等 ✓
        - 美股 1d bar ts = ET 00:00, 与 today_start 完全相等 ✓
        - 60m / 4h / 15m / 30m bar ts = 本市场 wall-clock close → 均 > today_start ✓
        - crypto 24h, today = UTC 自然日, 同理 ✓
        """
        tz = ZoneInfo(MARKET_TZ.get(market, "UTC"))
        now_local = datetime.now(tz)
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        return start_local.astimezone(timezone.utc)

    @staticmethod
    def _hash(cells: dict[tuple[str, str, str], TodaySignalCell] | dict[tuple[str, str, str], int]) -> str:
        """稳定 sha256: 按 key 排序后序列化, 只哈希 count (price 不含, 避免同一根 bar 收盘前后微变误发)。

        兼容旧测试: cells 的 value 既可能是 int (旧风格), 也可能是 TodaySignalCell。
        """
        def _to_count(v):
            return v.count if isinstance(v, TodaySignalCell) else v
        items = sorted(
            ((sym, iv, st, _to_count(v)) for (sym, iv, st), v in cells.items()),
        )
        payload = json.dumps(items, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    async def _broadcast(
        self, recipients: list, subject: str, body: str,
        html: str | None = None,
    ) -> tuple[bool, str | None]:
        """逐个发, 任一失败记 error 返回 False。"""
        first_err: str | None = None
        all_ok = True
        for r in recipients:
            channel = self.channels.get(r.channel)
            if channel is None or not getattr(channel, "enabled", False):
                msg = f"channel {r.channel} disabled or missing"
                log.warning("notify.channel_unavailable",
                            channel=r.channel, to=r.address)
                first_err = first_err or msg
                all_ok = False
                continue
            try:
                await channel.send(r.address, subject, body, html=html)
            except NotificationError as e:
                first_err = first_err or str(e)
                all_ok = False
            except Exception as e:  # noqa: BLE001
                first_err = first_err or str(e)
                all_ok = False
                log.warning("notify.broadcast_unexpected",
                            channel=r.channel, to=r.address, error=str(e))
        return all_ok, first_err
