"""30min 补扫 cron: 对全标的全信号周期跑 scan_symbol_readonly, 捞回漏事件。

完整性兜底: 事件驱动(bus:bars.updated → consumer)可能丢事件(Redis Stream
maxlen 挤出 / collector 发事件失败 / aggregate_and_publish 失败), 纯事件驱动
一旦丢就静默漏信号(cd:* cron 已移除)。补扫用同一只读路径 scan_symbol_readonly
全量过一遍: 漏的捞回, 已有的 upsert 幂等不重复, 不引入偏移(偏移源是
fetch_fresh_bars 聚合, 补扫不碰)。新增信号同样发 bus:signal.new(下游一致)。
"""
from __future__ import annotations

import structlog

from core.domain.intervals import SIGNAL_INTERVALS

log = structlog.get_logger(__name__)


async def sweep_symbols_for_market(scan_svc, symbols, *, market: str) -> int:
    """对 symbols 的全信号周期跑只读扫描, 返回新增信号总数。单个失败不中断。"""
    total = 0
    for sym in symbols:
        for iv in SIGNAL_INTERVALS:
            try:
                total += await scan_svc.scan_symbol_readonly(sym, iv) or 0
            except Exception as e:  # noqa: BLE001
                log.warning("signal_sweep.failed", symbol=sym, interval=iv,
                            error=str(e))
    log.info("signal_sweep.done", market=market, symbols=len(symbols), new=total)
    return total
