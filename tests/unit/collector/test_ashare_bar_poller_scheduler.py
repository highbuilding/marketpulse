from __future__ import annotations

from apps.collector.ashare.bar_poller import SCAN_INTERVAL_S, WINDOW_S, BarPoller, _slot_for_symbol


def test_slot_for_symbol_is_stable_and_inside_window():
    slot = _slot_for_symbol("600519.SH")
    assert slot == _slot_for_symbol("600519.SH")
    assert 0 <= slot < WINDOW_S
    assert slot % SCAN_INTERVAL_S == 0


def test_due_tasks_runs_symbol_once_per_window():
    poller = BarPoller(repo=None, redis_cache=None, adapter=None, collector_symbols=None)  # type: ignore[arg-type]
    active = {"600519.SH:5m", "002415.SZ:5m"}

    due_all = []
    for phase in range(0, WINDOW_S, SCAN_INTERVAL_S):
        due_all.extend(poller._due_tasks(active, now_s=phase))  # noqa: SLF001

    assert sorted(due_all) == sorted(active)
    assert poller._due_tasks(active, now_s=_slot_for_symbol("600519.SH")) == []  # noqa: SLF001

    next_window_due = []
    for phase in range(0, WINDOW_S, SCAN_INTERVAL_S):
        next_window_due.extend(poller._due_tasks(active, now_s=WINDOW_S + phase))  # noqa: SLF001
    assert sorted(next_window_due) == sorted(active)
