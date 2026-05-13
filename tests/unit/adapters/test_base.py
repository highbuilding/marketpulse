import time

import pytest

from core.adapters.base import AdapterError, CircuitBreaker


def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker(fail_threshold=3, reset_after_s=5)
    assert cb.can_execute()
    cb.record_failure()
    cb.record_failure()
    assert cb.can_execute()
    cb.record_failure()
    assert not cb.can_execute()
    assert cb.state == "open"


def test_circuit_breaker_half_open_after_reset():
    cb = CircuitBreaker(fail_threshold=1, reset_after_s=0.05)
    cb.record_failure()
    assert not cb.can_execute()
    time.sleep(0.06)
    assert cb.can_execute()
    assert cb.state == "half_open"


def test_circuit_breaker_closes_after_success():
    cb = CircuitBreaker(fail_threshold=2, reset_after_s=0.05)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.06)
    assert cb.can_execute()
    cb.record_success()
    assert cb.state == "closed"
    assert cb.failure_count == 0


def test_adapter_error_has_source():
    err = AdapterError("timeout", source="akshare")
    assert err.source == "akshare"
    assert "akshare" in str(err)
