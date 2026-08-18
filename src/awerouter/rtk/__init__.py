"""RTK tool-result compression: rewrite tool_result content in request bodies
before routing/forwarding, cutting the tokens coding-agent sessions resubmit
every turn (git diffs, grep hits, listings, build logs).

Python port of 9router's open-sse/rtk/ (MIT), which itself ports the rtk
Rust filters (https://github.com/rtk-ai/rtk, Apache 2.0 — pipe_cmd.rs and the
per-command filter sources). Semantics, constants, and detection order follow
upstream; the traversal is keyed on awerouter's wire protocol instead of
sniffing message shapes.

Contract (fail-open, deterministic):
- Any failure leaves the body untouched — never raise out of here.
- Filters are pure text transforms, so the same history compresses to the same
  bytes every turn and provider prompt-cache prefixes survive.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from awerouter.protocols import estimate_tokens
from awerouter.rtk.apply import safe_apply
from awerouter.rtk.autodetect import detect_filter
from awerouter.rtk.constants import MIN_COMPRESS_SIZE, RAW_CAP, SMART_TRUNCATE_MIN_LINES
from awerouter.rtk.filters import dedup_log, smart_truncate


@dataclass
class RtkHit:
    shape: str      # where the text lived (claude-string, openai-tool, ...)
    filter: str     # filter that compressed it
    saved: int      # characters removed


@dataclass
class RtkStats:
    chars_before: int = 0
    chars_after: int = 0
    hits: list = field(default_factory=list)
    # estimate_tokens(before) - estimate_tokens(after) over rewritten texts;
    # what RequestLog.rtk_saved records.
    saved_tokens: int = 0


def compress_body(body, protocol: str) -> "RtkStats | None":
    """Compress tool-result text in a request body, in place.

    Returns stats, or None when nothing walkable was found or anything went
    wrong (partial rewrites already made simply pass through).
    """
    if not isinstance(body, dict):
        return None
    stats = RtkStats()
    try:
        if protocol == "anthropic":
            _walk_anthropic(body, stats)
        elif protocol == "openai-chat":
            _walk_openai_chat(body, stats)
        elif protocol == "openai-responses":
            _walk_openai_responses(body, stats)
        else:
            return None
    except Exception as exc:  # noqa: BLE001 — fail-open is the contract
        print(f"[rtk] compress_body error: {exc}", file=sys.stderr)
        return None
    return stats


def format_log(stats: "RtkStats | None") -> "str | None":
    """One-line request summary, or None when nothing was compressed."""
    if stats is None or not stats.hits:
        return None
    saved = stats.chars_before - stats.chars_after
    pct = f"{100 * saved / stats.chars_before:.1f}" if stats.chars_before else "0"
    filters = []
    for hit in stats.hits:
        if hit.filter not in filters:
            filters.append(hit.filter)
    return f"[rtk] saved {saved}/{stats.chars_before} chars ({pct}%) " \
           f"via [{','.join(filters)}] hits={len(stats.hits)}"


# ---------------------------------------------------------------------------
# Per-protocol traversal — mirrors the tool-result locations protocols.py
# reads for routing signals.
# ---------------------------------------------------------------------------

def _walk_anthropic(body: dict, stats: RtkStats) -> None:
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            if block.get("is_error") is True:  # preserve error traces
                continue
            c = block.get("content")
            if isinstance(c, str):
                block["content"] = _compress_text(c, stats, "claude-string")
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and part.get("type") == "text" \
                            and isinstance(part.get("text"), str):
                        part["text"] = _compress_text(part["text"], stats, "claude-array")


def _walk_openai_chat(body: dict, stats: RtkStats) -> None:
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = _compress_text(content, stats, "openai-tool")
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" \
                        and isinstance(part.get("text"), str):
                    part["text"] = _compress_text(part["text"], stats, "openai-tool-array")


def _walk_openai_responses(body: dict, stats: RtkStats) -> None:
    input_value = body.get("input")
    if not isinstance(input_value, list):
        return
    for item in input_value:
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            continue
        output = item.get("output")
        if isinstance(output, str):
            item["output"] = _compress_text(output, stats, "openai-responses-string")
        elif isinstance(output, list):
            for part in output:
                if isinstance(part, dict) and part.get("type") == "input_text" \
                        and isinstance(part.get("text"), str):
                    part["text"] = _compress_text(part["text"], stats, "openai-responses-array")


def _compress_text(text: str, stats: RtkStats, shape: str) -> str:
    size_in = len(text)
    stats.chars_before += size_in

    if size_in < MIN_COMPRESS_SIZE or size_in > RAW_CAP:
        stats.chars_after += size_in
        return text

    fn = detect_filter(text)
    if fn is None:
        stats.chars_after += size_in
        return text

    out = safe_apply(fn, text)

    # dedup-log is the generic catch-all, so a unique-line blob (file dumps
    # read via shell — codex's cat/sed reads) reaches it and saves nothing;
    # smart-truncate was upstream's intended last resort for exactly that.
    if fn is dedup_log and len(out) >= size_in \
            and len(text.split("\n")) >= SMART_TRUNCATE_MIN_LINES:
        truncated = safe_apply(smart_truncate, text)
        if truncated and len(truncated) < size_in:
            out = truncated
            fn = smart_truncate

    # never return empty, never grow the input
    if not out or len(out) >= size_in:
        stats.chars_after += size_in
        return text

    stats.chars_after += len(out)
    stats.hits.append(RtkHit(
        shape=shape,
        filter=getattr(fn, "filter_name", None) or fn.__name__,
        saved=size_in - len(out),
    ))
    stats.saved_tokens += max(0, estimate_tokens(text) - estimate_tokens(out))
    return out
