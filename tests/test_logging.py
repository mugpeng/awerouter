"""Tests for awerouter.logging."""

import json
from pathlib import Path

import pytest

from awerouter.logging import stats, tail, token_distribution
from awerouter.types import RequestLog


def _log(ts: str, label: str, token_count: int, destination="flash", bytes_=100):
    return RequestLog(
        ts=ts, model_in="c1/pro", label=label, destination=destination,
        provider="p", model_out="m", status=200, ms=10, bytes=bytes_,
        token_count=token_count,
    )


@pytest.fixture
def _log_dir(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("AWEROUTER_LOG_DIR", str(log_dir))
    return log_dir


class TestStats:
    def test_empty(self, _log_dir):
        assert stats() == {}

    def test_aggregates(self, _log_dir):
        from awerouter.logging import append
        append(_log("t1", "default", 10, "flash"))
        append(_log("t2", "longContext", 500, "pro"))
        s = stats()
        assert s["total_requests"] == 2
        assert s["by_label"]["default"] == 1
        assert s["by_label"]["longContext"] == 1
        assert s["by_destination"]["flash"] == 1
        assert s["by_destination"]["pro"] == 1


class TestTail:
    def test_empty(self, _log_dir):
        assert tail(10) == []

    def test_returns_last_n(self, _log_dir):
        from awerouter.logging import append
        for i in range(5):
            append(_log(f"t{i}", "default", i))
        entries = tail(3)
        assert len(entries) == 3
        assert entries[-1].token_count == 4


class TestTokenDistribution:
    def test_empty(self, _log_dir):
        assert token_distribution() == {}

    def test_filters_non_l3(self, _log_dir):
        """L1/L2 labels (webSearch, background, think) excluded — not threshold-sensitive."""
        from awerouter.logging import append
        append(_log("t1", "background", 10))     # L2 — excluded
        append(_log("t2", "think", 20))           # L2 — excluded
        append(_log("t3", "webSearch", 30))       # L1 — excluded
        d = token_distribution()
        assert d == {}

    def test_includes_l3_labels(self, _log_dir):
        from awerouter.logging import append
        append(_log("t1", "default", 10))
        append(_log("t2", "longContext", 500))
        append(_log("t3", "image", 50))
        d = token_distribution()
        assert d["n"] == 3
        assert d["min"] == 10
        assert d["max"] == 500

    def test_percentiles(self, _log_dir):
        from awerouter.logging import append
        for i in range(1, 11):  # tokens 1..10, all L3 "default"
            append(_log(f"t{i}", "default", i * 100))
        d = token_distribution()
        assert d["n"] == 10
        assert d["min"] == 100
        assert d["max"] == 1000
        # p50 of 10 sorted items = 5th-6th = 500-600
        assert 500 <= d["p50"] <= 600

    def test_candidates_flash_pct(self, _log_dir):
        """At p90 threshold, ~90% of L3 traffic should go flash."""
        from awerouter.logging import append
        for i in range(1, 11):
            append(_log(f"t{i}", "default", i * 100))
        d = token_distribution()
        c = d["candidates"]
        assert len(c) == 3
        # p90 threshold = 900, 9 of 10 tokens <= 900 → 90%
        assert c[0]["flash_pct"] == 90
        # p99 threshold = 1000, all 10 <= 1000 → 100%
        assert c[2]["flash_pct"] == 100

    def test_single_request(self, _log_dir):
        from awerouter.logging import append
        append(_log("t1", "default", 42))
        d = token_distribution()
        assert d["n"] == 1
        assert d["min"] == d["max"] == 42
        for c in d["candidates"]:
            assert c["flash_pct"] == 100
