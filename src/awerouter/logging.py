"""Structured append-only request log."""

import json
import os
from pathlib import Path

from awerouter.types import RequestLog


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
            "model_in": log.model_in,
            "label": log.label,
            "destination": log.destination,
            "provider": log.provider,
            "model_out": log.model_out,
            "status": log.status,
            "ms": log.ms,
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
                model_in=data.get("model_in", ""),
                label=data.get("label", ""),
                destination=data.get("destination", ""),
                provider=data.get("provider", ""),
                model_out=data.get("model_out", ""),
                status=data.get("status"),
                ms=data.get("ms", 0),
                bytes=data.get("bytes", 0),
                token_count=data.get("token_count", 0),
            ))
        except json.JSONDecodeError:
            continue
    return result


def stats() -> dict:
    f = _log_file()
    if not f.exists():
        return {}
    by_label: dict[str, int] = {}
    by_dest: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    total_bytes = 0
    total_requests = 0
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        label = data.get("label", "unknown")
        dest = data.get("destination", "unknown")
        prov = data.get("provider", "unknown")
        by_label[label] = by_label.get(label, 0) + 1
        by_dest[dest] = by_dest.get(dest, 0) + 1
        by_provider[prov] = by_provider.get(prov, 0) + 1
        total_bytes += data.get("bytes", 0)
        total_requests += 1
    return {
        "total_requests": total_requests,
        "total_bytes": total_bytes,
        "by_label": by_label,
        "by_destination": by_dest,
        "by_provider": by_provider,
    }


# L3 labels: threshold-sensitive (decided by token_count vs longContextThreshold).
# L1 (webSearch) and L2 (background/think) route the same regardless of threshold.
_L3_LABELS = frozenset({"default", "longContext", "image"})


def token_distribution() -> dict:
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
