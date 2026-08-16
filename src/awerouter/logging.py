"""Structured append-only request log."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from awerouter.types import RequestLog

# Anthropic-style prompt-cache TTL; gaps longer than this expire the prefix cache.
_CACHE_TTL_S = 300


def _log_file() -> Path:
    """Resolve log file path on each call (AWEROUTER_LOG_DIR is live-readable)."""
    d = Path(os.environ.get("AWEROUTER_LOG_DIR", "~/.local/state/awerouter")).expanduser()
    return d / "requests.jsonl"


_ROTATED_SUFFIX = ".1"
_DEFAULT_MAX_BYTES = 50 * 1024 * 1024


def _max_bytes() -> int:
    try:
        return int(os.environ.get("AWEROUTER_LOG_MAX_BYTES", _DEFAULT_MAX_BYTES))
    except ValueError:
        return _DEFAULT_MAX_BYTES


def _rotate_if_needed() -> None:
    """Rotate to requests.jsonl.1 (single backup) when the file exceeds the cap."""
    f = _log_file()
    try:
        if f.stat().st_size > _max_bytes():
            f.replace(f.with_name(f.name + _ROTATED_SUFFIX))
    except FileNotFoundError:
        pass


def ensure_log_dir() -> None:
    _log_file().parent.mkdir(parents=True, exist_ok=True)


def append(log: RequestLog) -> None:
    _rotate_if_needed()
    ensure_log_dir()
    with open(_log_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": log.ts,
            "request_id": log.request_id,
            "profile": log.profile,
            "model_in": log.model_in,
            "label": log.label,
            "destination": log.destination,
            "provider": log.provider,
            "model_out": log.model_out,
            "status": log.status,
            "ms": log.ms,
            "duration_ms": log.duration_ms,
            "bytes": log.bytes,
            "token_count": log.token_count,
        }, ensure_ascii=False) + "\n")


def _tail_lines(n: int) -> list:
    """Read the last n lines from the end of the log file (no full read)."""
    f = _log_file()
    if not f.exists():
        return []
    lines = []
    buf = b""
    with open(f, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        remaining = fh.tell()
        while remaining > 0 and len(lines) <= n:
            step = min(8192, remaining)
            remaining -= step
            fh.seek(remaining)
            buf = fh.read(step) + buf
            *complete, buf = buf.split(b"\n")
            lines.extend(reversed(complete))
    if buf:
        lines.append(buf)
    lines.reverse()
    return [l.decode("utf-8", "replace") for l in lines[-n:]]


def tail(n: int = 20) -> list[RequestLog]:
    result: list[RequestLog] = []
    for line in _tail_lines(n):
        if not line:
            continue
        try:
            data = json.loads(line)
            result.append(RequestLog(
                ts=data.get("ts", ""),
                request_id=data.get("request_id", ""),
                profile=data.get("profile", ""),
                model_in=data.get("model_in", ""),
                label=data.get("label", ""),
                destination=data.get("destination", ""),
                provider=data.get("provider", ""),
                model_out=data.get("model_out", ""),
                status=data.get("status"),
                ms=data.get("ms", 0),
                duration_ms=data.get("duration_ms", 0),
                bytes=data.get("bytes", 0),
                token_count=data.get("token_count", 0),
            ))
        except json.JSONDecodeError:
            continue
    return result


def token_totals(since=None, profile=None) -> dict:
    """Count requests and message tokens by destination, plus fallback count.

    Input-side accounting for `savings`: message tokens of flash-served requests
    are exactly the pro input tokens a pro-only setup would additionally bill.
    """
    f = _log_file()
    if not f.exists():
        return {}
    out = {
        "flash": {"requests": 0, "tokens": 0},
        "pro": {"requests": 0, "tokens": 0},
        "fallback": 0,
    }
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not _passes(data, since, profile):
            continue
        dest = data.get("destination", "")
        if dest in out:
            out[dest]["requests"] += 1
            out[dest]["tokens"] += data.get("token_count", 0)
        if "fallback" in data.get("label", ""):
            out["fallback"] += 1
    if not (out["flash"]["requests"] or out["pro"]["requests"]):
        return {}
    return out


def cadence(since=None, profile=None) -> dict:
    """Switch cadence vs cache TTL, for the savings cache-sensitivity view.

    Anthropic-style prompt caches live ~5 minutes. The cost of interleaving
    flash traffic depends on whether pro requests stay within that window:
    gaps <= TTL mean the pro prefix cache survives; expired gaps mean the
    next pro request re-warms it at cache-write price.
    """
    f = _log_file()
    if not f.exists():
        return {}
    rows = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        try:
            data = json.loads(line)
            ts = datetime.fromisoformat(data["ts"])
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
        if not _passes(data, since, profile):
            continue
        rows.append((ts, data.get("destination", "")))
    rows.sort()
    if not rows:
        return {}

    def gaps(times):
        return [(times[i] - times[i - 1]).total_seconds() for i in range(1, len(times))]

    ttl = _CACHE_TTL_S
    all_gaps = gaps([t for t, _ in rows])
    pro_gaps = gaps([t for t, d in rows if d == "pro"])
    return {
        "requests": len(rows),
        "ttl_s": ttl,
        "alternations": sum(
            1 for i in range(1, len(rows)) if rows[i][1] != rows[i - 1][1]
        ),
        "pro_gaps": len(pro_gaps),
        "pro_gaps_expired": sum(1 for g in pro_gaps if g > ttl),
        # In a pro-only world every request hits pro, so expired gaps between
        # *all* requests are the re-warm points of that baseline.
        "all_gaps": len(all_gaps),
        "all_gaps_expired": sum(1 for g in all_gaps if g > ttl),
    }


def _new_profile_bucket() -> dict:
    return {
        "requests": 0,
        "tokens": 0,
        "errors": 0,
        "fallbacks": 0,
        # Estimated pro input saved: message tokens of requests served by flash
        # that a pro-only setup would have billed at pro's input price.
        "flash_tokens": 0,
        "flash_requests": 0,
        "by_label": {},
        "by_destination": {},
        "by_provider": {},
        "by_model": {},
        # (first-byte ms, total ms) samples per breakdown dimension
        "_ms": {"destination": {}, "provider": {}, "model": {}},
    }


def _entry_ts(data: dict):
    """Parsed entry timestamp (UTC-aware), or None if missing/unparseable."""
    ts = data.get("ts", "")
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _passes(data: dict, since, profile) -> bool:
    """Shared window/profile filter for all analytics readers."""
    if since is not None:
        ts = _entry_ts(data)
        if ts is None or ts < since:
            return False
    if profile is not None and (data.get("profile", "") or "(unknown)") != profile:
        return False
    return True


def log_start():
    """Timestamp of the oldest retained entry (coverage floor for windows)."""
    f = _log_file()
    if not f.exists():
        return None
    with open(f, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            return _entry_ts(data)
    return None


def _percentile(values: list, p: int):
    if not values:
        return None
    s = sorted(values)
    idx = max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))
    return s[idx]


def stats(since=None, profile=None) -> dict:
    """Aggregate request stats.

    since: aware datetime lower bound (None = all history; entries with an
    unparseable ts are excluded when a filter is active).
    profile: restrict to one routing profile id (None = all).
    """
    f = _log_file()
    if not f.exists():
        return {}
    by_profile: dict = {}
    total_tokens = 0
    total_requests = 0
    errors = 0
    fallbacks = 0
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since is not None:
            ts = _entry_ts(data)
            if ts is None or ts < since:
                continue
        if profile is not None and (data.get("profile", "") or "(unknown)") != profile:
            continue
        label = data.get("label", "unknown")
        dest = data.get("destination", "unknown")
        prov = data.get("provider", "unknown")
        model = data.get("model_out", "unknown")
        tokens = data.get("token_count", 0)
        status = data.get("status")
        bucket = by_profile.setdefault(
            data.get("profile", "") or "(unknown)", _new_profile_bucket()
        )
        bucket["requests"] += 1
        bucket["tokens"] += tokens
        bucket["by_label"][label] = bucket["by_label"].get(label, 0) + 1
        bucket["by_destination"][dest] = bucket["by_destination"].get(dest, 0) + 1
        bucket["by_provider"][prov] = bucket["by_provider"].get(prov, 0) + 1
        bucket["by_model"][model] = bucket["by_model"].get(model, 0) + 1
        if isinstance(status, int) and status >= 400:
            bucket["errors"] += 1
            errors += 1
        if "fallback" in label:
            bucket["fallbacks"] += 1
            fallbacks += 1
        if dest == "flash":
            bucket["flash_tokens"] += tokens
            bucket["flash_requests"] += 1
        sample = (data.get("ms", 0), data.get("duration_ms", 0))
        for dim, key in (("destination", dest), ("provider", prov), ("model", model)):
            bucket["_ms"][dim].setdefault(key, []).append(sample)
        total_tokens += tokens
        total_requests += 1
    for bucket in by_profile.values():
        latency: dict = {}
        for dim, groups in bucket.pop("_ms").items():
            for key, samples in groups.items():
                entry = {
                    "p50": _percentile([m for m, _ in samples], 50),
                    "p95": _percentile([m for m, _ in samples], 95),
                }
                durations = [d for _, d in samples if d]
                if durations:
                    entry["total_p50"] = _percentile(durations, 50)
                    entry["total_p95"] = _percentile(durations, 95)
                latency.setdefault(dim, {})[key] = entry
        bucket["latency"] = latency
    return {
        "total_requests": total_requests,
        "total_tokens": total_tokens,
        "errors": errors,
        "fallbacks": fallbacks,
        "flash_tokens": sum(p["flash_tokens"] for p in by_profile.values()),
        "flash_requests": sum(p["flash_requests"] for p in by_profile.values()),
        "by_profile": by_profile,
    }


def clear_logs() -> list:
    """Delete the request log and its rotated backup. Returns removed paths."""
    current = _log_file()
    paths = (current, current.with_name(current.name + _ROTATED_SUFFIX))
    removed = []
    for p in paths:
        try:
            p.unlink()
            removed.append(p)
        except FileNotFoundError:
            pass
    return removed


# L3 labels: threshold-sensitive (decided by token_count vs longContextThreshold).
# L1 (webSearch) and L2 (background/think) route the same regardless of threshold.
_L3_LABELS = frozenset({"default", "longContext", "image"})


def token_distribution(since=None, profile=None) -> dict:
    """Token distribution of L3 traffic for calibrating longContextThreshold.

    Only L3 requests (label in default/longContext/image) are threshold-sensitive;
    L1/L2 route identically no matter where the threshold sits.
    """
    f = _log_file()
    if not f.exists():
        return {}
    tokens: list[int] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not _passes(data, since, profile):
            continue
        if data.get("label", "") not in _L3_LABELS:
            continue
        tokens.append(data.get("token_count", 0))
    if not tokens:
        return {}
    tokens.sort()
    n = len(tokens)

    def pct(p: int) -> int:
        idx = max(0, min(n - 1, round(p / 100 * (n - 1))))
        return tokens[idx]

    def count_below(threshold: int) -> int:
        # number of requests that would go flash (default) at this threshold
        return sum(1 for t in tokens if t <= threshold)

    return {
        "n": n,
        "min": tokens[0],
        "p50": pct(50),
        "p75": pct(75),
        "p90": pct(90),
        "p95": pct(95),
        "p99": pct(99),
        "max": tokens[-1],
        "candidates": [
            {"threshold": pct(90), "flash_pct": round(100 * count_below(pct(90)) / n)},
            {"threshold": pct(95), "flash_pct": round(100 * count_below(pct(95)) / n)},
            {"threshold": pct(99), "flash_pct": round(100 * count_below(pct(99)) / n)},
        ],
    }
