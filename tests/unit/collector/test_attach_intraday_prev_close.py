from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.domain.models import Bar
from apps.collector.base import attach_intraday_route


def test_response_includes_prev_close_when_bar_repo_given():
    app = FastAPI()
    intraday_repo = MagicMock()
    intraday_repo.fetch_day.return_value = [
        {"ts": "2026-06-01T14:31:00+00:00", "price": 100.0,
         "cum_amount": 1000.0, "cum_volume": 10, "avg_price": 100.0}]
    bar_repo = MagicMock()
    bar_repo.fetch_history_paged.return_value = [
        Bar(market="us", symbol="AAPL", ts=datetime(2026, 5, 30, tzinfo=timezone.utc),
            open=Decimal("1"), high=Decimal("2"), low=Decimal("1"), close=Decimal("99.5"),
            volume=1, interval="1d")]
    attach_intraday_route(app, lambda: intraday_repo, "us", get_bar_repo=lambda: bar_repo)
    c = TestClient(app)
    r = c.get("/internal/intraday-line", params={"symbol": "AAPL"})
    assert r.status_code == 200
    body = r.json()
    assert body["prev_close"] == 99.5
    assert len(body["points"]) == 1
