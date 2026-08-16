"""Tests for awerouter.logging."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from awerouter.logging import (
    cadence,
    clear_logs,
    log_start,
    stats,
    tail,
    token_distribution,
    token_totals,
)
from awerouter.types import RequestLog


def _log(ts: str, label: str, token_count: int, destination="flash", bytes_=100,
         profile="cc-1", status=200, ms=10, model_out="m", provider="p", duration_ms=0):
    return RequestLog(
        ts=ts, request_id="req-1", model_in="c1/pro", label=label, destination=destination,
        provider=provider, model_out=model_out, status=status, ms=ms,
        duration_ms=duration_ms, bytes=bytes_,
        token_count=token_count, profile=profile,
    )


@pytest.fixture
def _log_dir(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("AWEROUTER_LOG_DIR", str(log_dir))
    return log_dir


class TestTokenTotals:
    def test_empty(self, _log_dir):
        assert token_totals() == {}

    def test_counts_tokens_and_fallback(self, _log_dir):
        from awerouter.logging import append
        append(_log("t1", "default", 100, "flash"))
        append(_log("t2", "think", 50, "pro"))
        append(_log("t3", "default→fallback", 30, "pro"))
        t = token_totals()
        assert t["flash"] == {"requests": 1, "tokens": 100}
        assert t["pro"] == {"requests": 2, "tokens": 80}
        assert t["fallback"] == 1

    def test_ignores_unknown_destinations(self, _log_dir):
        from awerouter.logging import append
        append(_log("t1", "default", 10, destination="weird"))
        assert token_totals() == {}

    def test_since_and_profile_filters(self, _log_dir):
        from awerouter.logging import append
        append(_log("2026-08-10T00:00:00+00:00", "default", 100, "flash"))
        append(_log("2026-08-16T00:00:00+00:00", "default", 50, "flash", profile="cc-2"))
        cutoff = datetime(2026, 8, 15, tzinfo=timezone.utc)
        t = token_totals(cutoff, "cc-2")
        assert t["flash"] == {"requests": 1, "tokens": 50}


class TestCadence:
    def test_empty(self, _log_dir):
        assert cadence() == {}

    def test_alternations_and_ttl_gaps(self, _log_dir):
        from awerouter.logging import append
        # pro at t0, pro at t+60s (within TTL), flash at t+120s, pro at t+600s (expired)
        append(_log("2026-01-01T00:00:00+00:00", "default", 10, "pro"))
        append(_log("2026-01-01T00:01:00+00:00", "default", 10, "pro"))
        append(_log("2026-01-01T00:02:00+00:00", "default", 10, "flash"))
        append(_log("2026-01-01T00:12:00+00:00", "default", 10, "pro"))
        c = cadence()
        assert c["requests"] == 4
        assert c["alternations"] == 2          # pro->flash, flash->pro
        assert c["pro_gaps"] == 2              # 60s (within) + 600s (expired)
        assert c["pro_gaps_expired"] == 1
        assert c["all_gaps"] == 3
        assert c["all_gaps_expired"] == 1

    def test_skips_bad_timestamps(self, _log_dir):
        from awerouter.logging import append
        append(_log("t1", "default", 10, "flash"))  # not ISO — skipped
        assert cadence() == {}


class TestStats:
    def test_empty(self, _log_dir):
        assert stats() == {}

    def test_aggregates_by_profile(self, _log_dir):
        from awerouter.logging import append
        append(_log("t1", "default", 10, "flash", profile="cc-1"))
        append(_log("t2", "longContext", 500, "pro", profile="cc-1"))
        append(_log("t3", "default", 7, "flash", profile="cc-2"))
        s = stats()
        assert s["total_requests"] == 3
        assert set(s["by_profile"]) == {"cc-1", "cc-2"}
        p1 = s["by_profile"]["cc-1"]
        assert p1["requests"] == 2
        assert p1["by_label"]["default"] == 1
        assert p1["by_label"]["longContext"] == 1
        assert p1["by_destination"]["flash"] == 1
        assert p1["by_destination"]["pro"] == 1

    def test_unknown_profile_bucket(self, _log_dir):
        """Log lines without a profile field (pre-feature) group under (unknown)."""
        import json as _json
        from awerouter.logging import _log_file, ensure_log_dir
        ensure_log_dir()
        with open(_log_file(), "a") as f:
            f.write(_json.dumps({
                "ts": "t0", "label": "default", "destination": "flash",
                "provider": "p", "model_out": "m", "status": 200,
                "ms": 1, "bytes": 1, "token_count": 5,
            }) + "\n")
        s = stats()
        assert "(unknown)" in s["by_profile"]

    def test_flash_offload_counts_flash_only(self, _log_dir):
        """flash_tokens sums message tokens of flash-served requests (the
        pro-input a single-pro setup would have billed)."""
        from awerouter.logging import append
        append(_log("t1", "default", 100, "flash"))           # counts
        append(_log("t2", "background", 50, "flash"))         # counts
        append(_log("t3", "longContext", 900, "pro"))         # excluded (served by pro)
        append(_log("t4", "default→fallback", 200, "pro"))    # excluded (fell back to pro)
        s = stats()
        assert s["flash_tokens"] == 150
        assert s["flash_requests"] == 2
        assert s["by_profile"]["cc-1"]["flash_tokens"] == 150

    def test_tokens_not_bytes(self, _log_dir):
        from awerouter.logging import append
        append(_log("t1", "default", 100, "flash"))
        append(_log("t2", "longContext", 50, "pro"))
        s = stats()
        assert s["total_tokens"] == 150
        assert "total_bytes" not in s
        assert s["by_profile"]["cc-1"]["tokens"] == 150

    def test_by_model(self, _log_dir):
        from awerouter.logging import append
        append(_log("t1", "default", 10, "flash", model_out="sf-flash"))
        append(_log("t2", "longContext", 20, "pro", model_out="opus-5"))
        append(_log("t3", "default", 30, "flash", model_out="sf-flash"))
        s = stats()
        assert s["by_profile"]["cc-1"]["by_model"] == {"sf-flash": 2, "opus-5": 1}

    def test_errors_and_fallbacks(self, _log_dir):
        from awerouter.logging import append
        append(_log("t1", "default", 10, "flash"))
        append(_log("t2", "default", 10, "pro", status=503))
        append(_log("t3", "default→fallback", 10, "pro", status=200))
        append(_log("t4", "think", 10, "pro", status=401))
        s = stats()
        assert s["errors"] == 2
        assert s["fallbacks"] == 1
        p = s["by_profile"]["cc-1"]
        assert p["errors"] == 2
        assert p["fallbacks"] == 1

    def test_latency_percentiles_per_dimension(self, _log_dir):
        from awerouter.logging import append
        for i in range(1, 11):
            append(_log(f"t{i}", "default", i, "flash", ms=i * 100, provider="sf", model_out="sf-flash"))
        append(_log("t11", "longContext", 1, "pro", ms=5000, provider="ant", model_out="opus"))
        s = stats()
        lat = s["by_profile"]["cc-1"]["latency"]
        assert set(lat) == {"destination", "provider", "model"}
        assert set(lat["destination"]) == {"flash", "pro"}
        assert 500 <= lat["destination"]["flash"]["p50"] <= 600
        assert lat["provider"]["ant"]["p50"] == 5000
        assert lat["model"]["sf-flash"]["p95"] == 1000

    def test_total_duration_percentiles(self, _log_dir):
        """duration_ms (streaming included) shows up as total_p50/total_p95;
        legacy entries without it (0) are excluded from the totals."""
        from awerouter.logging import append
        append(_log("t1", "default", 1, "flash", ms=100, duration_ms=2000))
        append(_log("t2", "default", 1, "flash", ms=200, duration_ms=4000))
        append(_log("t3", "default", 1, "flash", ms=300))  # legacy: no duration
        entry = stats()["by_profile"]["cc-1"]["latency"]["destination"]["flash"]
        assert "total_p50" in entry
        assert entry["total_p50"] == 2000
        assert entry["total_p95"] == 4000

    def test_since_filters_by_entry_ts(self, _log_dir):
        from awerouter.logging import append
        old = "2026-08-10T00:00:00+00:00"
        new = "2026-08-16T00:00:00+00:00"
        append(_log(old, "default", 100, "flash"))
        append(_log(new, "default", 50, "flash"))
        cutoff = datetime(2026, 8, 15, tzinfo=timezone.utc)
        s = stats(since=cutoff)
        assert s["total_requests"] == 1
        assert s["total_tokens"] == 50

    def test_since_excludes_unparseable_ts(self, _log_dir):
        """Fake/legacy ts values can't be placed in a window — excluded when filtering."""
        from awerouter.logging import append
        append(_log("t1", "default", 100, "flash"))
        s = stats(since=datetime(2020, 1, 1, tzinfo=timezone.utc))
        assert s["total_requests"] == 0

    def test_profile_filter(self, _log_dir):
        from awerouter.logging import append
        append(_log("t1", "default", 10, "flash", profile="cc-1"))
        append(_log("t2", "default", 20, "flash", profile="cc-2"))
        s = stats(profile="cc-2")
        assert set(s["by_profile"]) == {"cc-2"}
        assert s["total_tokens"] == 20


class TestLogStart:
    def test_empty(self, _log_dir):
        assert log_start() is None

    def test_returns_oldest_entry_ts(self, _log_dir):
        from awerouter.logging import append
        append(_log("2026-08-10T00:00:00+00:00", "default", 1))
        append(_log("2026-08-16T00:00:00+00:00", "default", 1))
        start = log_start()
        assert start == datetime(2026, 8, 10, tzinfo=timezone.utc)

    def test_skips_corrupt_first_line(self, _log_dir):
        from awerouter.logging import _log_file, append, ensure_log_dir
        ensure_log_dir()
        with open(_log_file(), "a") as f:
            f.write("{not json}\n")
        append(_log("2026-08-16T00:00:00+00:00", "default", 1))
        start = log_start()
        assert start == datetime(2026, 8, 16, tzinfo=timezone.utc)


class TestTokenDistributionFilters:
    def test_profile_filter(self, _log_dir):
        from awerouter.logging import append
        append(_log("t1", "default", 10, profile="cc-1"))
        append(_log("t2", "default", 20, profile="cc-2"))
        d = token_distribution(profile="cc-2")
        assert d["n"] == 1
        assert d["max"] == 20

    def test_since_filter(self, _log_dir):
        from awerouter.logging import append
        append(_log("2026-08-10T00:00:00+00:00", "default", 10))
        append(_log("2026-08-16T00:00:00+00:00", "default", 20))
        d = token_distribution(since=datetime(2026, 8, 15, tzinfo=timezone.utc))
        assert d["n"] == 1
        assert d["max"] == 20


class TestClearLogs:
    def test_removes_log_and_backup(self, _log_dir):
        from awerouter.logging import append, ensure_log_dir
        append(_log("t1", "default", 1))
        ensure_log_dir()
        backup = _log_dir / "requests.jsonl.1"
        backup.write_text("old\n")
        removed = clear_logs()
        assert set(removed) == {_log_dir / "requests.jsonl", backup}
        assert not (_log_dir / "requests.jsonl").exists()
        assert not backup.exists()

    def test_missing_files_are_noop(self, _log_dir):
        assert clear_logs() == []


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

    def test_returns_request_id(self, _log_dir):
        from awerouter.logging import append
        append(_log("t0", "default", 1))
        assert tail(1)[0].request_id == "req-1"

    def test_large_file_tail_from_end(self, _log_dir):
        """tail must not need the whole file — write many long lines, ask for few."""
        from awerouter.logging import append
        for i in range(2000):
            append(_log(f"t{i}", "default", i, bytes_=200))
        entries = tail(5)
        assert len(entries) == 5
        assert entries[-1].ts == "t1999"
        assert entries[0].ts == "t1995"


class TestRotation:
    def test_rotates_when_over_cap(self, _log_dir, monkeypatch):
        from awerouter.logging import append
        monkeypatch.setenv("AWEROUTER_LOG_MAX_BYTES", "1")
        append(_log("t1", "default", 1))
        append(_log("t2", "default", 2))
        assert (_log_dir / "requests.jsonl.1").exists()
        # current file holds only the latest entry
        entries = tail(10)
        assert [e.ts for e in entries] == ["t2"]

    def test_no_rotation_under_cap(self, _log_dir):
        from awerouter.logging import append
        append(_log("t1", "default", 1))
        append(_log("t2", "default", 2))
        assert not (_log_dir / "requests.jsonl.1").exists()
        assert len(tail(10)) == 2


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
