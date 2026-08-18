"""fail-open filter wrapper: a filter exception must never break a request."""

from __future__ import annotations

import sys


def safe_apply(fn, text: str) -> str:
    """Run one filter; on any failure warn and pass the raw text through.

    Python rewrite based on rtk's catch_unwind / 9router's safeApply. The returned value
    is always a str: a non-str filter result is treated as a failure.
    """
    try:
        out = fn(text)
        if not isinstance(out, str):
            return text
        return out
    except Exception as exc:  # noqa: BLE001 — the whole point is to not raise
        name = getattr(fn, "filter_name", None) or getattr(fn, "__name__", "anonymous")
        print(f"[rtk] warning: filter '{name}' failed — passing through raw output: {exc}",
              file=sys.stderr)
        return text
