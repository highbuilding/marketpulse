from unittest.mock import MagicMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.scheduler.scheduler import attach_fundamentals_jobs


def test_attach_fundamentals_jobs_can_skip_symbol_flow():
    sched = AsyncIOScheduler(timezone="UTC")

    attach_fundamentals_jobs(
        sched,
        fund_flow=MagicMock(),
        watchlist=MagicMock(),
        include_symbol_flow=False,
    )

    job_ids = {job.id for job in sched.get_jobs()}
    assert "ff:north" in job_ids
    assert "ff:purge" in job_ids
    assert "ff:symbols" not in job_ids


def test_attach_fundamentals_jobs_keeps_symbol_flow_by_default():
    sched = AsyncIOScheduler(timezone="UTC")

    attach_fundamentals_jobs(
        sched,
        fund_flow=MagicMock(),
        watchlist=MagicMock(),
    )

    job_ids = {job.id for job in sched.get_jobs()}
    assert "ff:symbols" in job_ids
