"""ODCP context pruning: cross-message strategies beside rtk's per-text
compression. Where rtk shrinks each tool result's size, odcp drops whole
superseded content from the history coding agents resubmit every turn:

- dedup — repeated identical tool calls (same tool, same arguments) keep only
  the newest output; older ones become a one-line placeholder. Re-reading a
  file or re-running a command costs one line instead of N full outputs.
- purgeErrors — once a failed tool call is a few user messages old, its
  (often bulky) input strings become placeholders; the error text itself
  stays, so the model still sees what failed. Applies to anthropic only:
  the other served protocols carry no error mark on the wire.

Behavior follows the public documentation of Opencode-DCP's dynamic context
pruning (AGPL-3.0); this is an independent implementation for awerouter's
wire protocols, not a translation of its source — this module is the license
boundary between DCP's ideas and this MPL codebase.

Contract (mirrors rtk):
- Fail-open: any error leaves the body as-is (partial rewrites pass through).
- Deterministic per body: the same history prunes to the same bytes, so a
  repeat request keeps provider prompt-cache prefixes. Unlike rtk, pruning
  is not prefix-stable across turns by construction: a new duplicate
  rewrites an earlier message (was verbatim, becomes a placeholder) and an
  error aging past the threshold rewrites once. Those two events are the
  accepted cache trade this feature exists for.
- The trailing turn is never touched: dedup keeps the newest output, and
  purgeErrors requires the failure to be `purge_turns` user messages old.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field

from awerouter.protocols import EDIT_TOOLS, estimate_tokens

# One-line, self-describing replacement notes: the model must understand why
# content vanished and that nothing is hidden from it maliciously.
_DEDUP_NOTE = "[odcp: superseded output of an identical earlier tool call removed]"
_PURGED_INPUT_NOTE = "[odcp: input removed — the call failed]"

# Tools odcp never rewrites: editors (the model reasons over the exact
# old_string/new_string history) and task/planning calls whose visible trace
# matters more than their size. Matched on the lowercase name with separators
# stripped, so TodoWrite == todo_write == todowrite.
_PROTECTED_TOOLS = (
    {t.replace("_", "").replace("-", "") for t in EDIT_TOOLS}
    | {
        "task", "skill", "batch", "todowrite", "todoread",
        "updateplan", "planenter", "planexit", "askuserquestion",
    }
)


def _is_protected(name: str) -> bool:
    return name.lower().replace("_", "").replace("-", "") in _PROTECTED_TOOLS


@dataclass
class OdcpHit:
    strategy: str      # "dedup" | "purgeErrors"
    saved: int         # characters removed


@dataclass
class OdcpStats:
    chars_saved: int = 0
    saved_tokens: int = 0
    hits: list = field(default_factory=list)


def prune_body(body, protocol: str, config) -> "OdcpStats | None":
    """Prune superseded tool-call content from a request body, in place.

    config is the profile's OdcpConfig (None = odcp off; callers check that).
    Returns stats, or None when nothing walkable was found or anything went
    wrong (partial rewrites already made simply pass through).
    """
    if not isinstance(body, dict):
        return None
    stats = OdcpStats()
    try:
        if protocol == "anthropic":
            _prune_anthropic(body, config, stats)
        elif protocol == "openai-chat":
            _prune_openai_chat(body, config, stats)
        elif protocol == "openai-responses":
            _prune_openai_responses(body, config, stats)
        else:
            return None
    except Exception as exc:  # noqa: BLE001 — fail-open is the contract
        print(f"[odcp] prune_body error: {exc}", file=sys.stderr)
        return None
    return stats


def format_log(stats: "OdcpStats | None") -> "str | None":
    """One-line request summary, or None when nothing was pruned."""
    if stats is None or not stats.hits:
        return None
    strategies = []
    for hit in stats.hits:
        if hit.strategy not in strategies:
            strategies.append(hit.strategy)
    return f"[odcp] saved {stats.chars_saved} chars " \
           f"via [{','.join(strategies)}] hits={len(stats.hits)}"


# ---------------------------------------------------------------------------
# Rules — applied to one pairing walk's worth of tool calls
# ---------------------------------------------------------------------------

@dataclass
class _Call:
    name: str
    arguments: object            # dict or parsed JSON args (raw string when unparseable)
    output_holder: dict          # wire object carrying the tool's output text
    output_key: str              # "content" (anthropic result / openai tool msg) | "output" (responses)
    part_type: str               # text-part type when the output is an array
    input_block: "dict | None"   # tool_use block whose inputs purgeErrors rewrites (anthropic)
    user_index: "int | None"     # user-message ordinal carrying the result (anthropic)
    errored: bool                # the wire marks the call failed (anthropic is_error)


def _dedup(calls: list, config, stats: OdcpStats) -> None:
    """Keep the newest output of every identical (tool, arguments) pair.

    Walking in order, each repeat replaces its predecessor's output, so after
    one pass every occurrence but the last carries the note — and each is
    rewritten at most once.
    """
    if not config.dedup:
        return
    kept: dict = {}
    for call in calls:
        if call.errored or _is_protected(call.name):
            continue
        sig = _signature(call.name, call.arguments)
        prev = kept.get(sig)
        if prev is not None:
            _replace_output(prev, _DEDUP_NOTE, stats, "dedup")
        kept[sig] = call


def _purge_errors(calls: list, config, stats: OdcpStats, total_users: int) -> None:
    """Strip the input strings of errored calls `purge_turns` user messages
    old. Age counts user messages after the one carrying the error, so the
    current turn (age 0) is always safe."""
    if not config.purge_errors:
        return
    for call in calls:
        if not call.errored or _is_protected(call.name):
            continue
        if call.user_index is None \
                or total_users - 1 - call.user_index < config.purge_turns:
            continue
        _purge_inputs(call, stats)


def _purge_inputs(call: _Call, stats: OdcpStats) -> None:
    params = call.input_block.get("input") if call.input_block else None
    if not isinstance(params, dict):
        return
    for key, value in params.items():
        if isinstance(value, str) \
                and _record(stats, "purgeErrors", value, _PURGED_INPUT_NOTE):
            params[key] = _PURGED_INPUT_NOTE


def _replace_output(call: _Call, note: str, stats: OdcpStats, strategy: str) -> None:
    content = call.output_holder.get(call.output_key)
    if isinstance(content, str):
        if _record(stats, strategy, content, note):
            call.output_holder[call.output_key] = note
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == call.part_type \
                    and isinstance(part.get("text"), str) \
                    and _record(stats, strategy, part["text"], note):
                part["text"] = note


def _record(stats: OdcpStats, strategy: str, before: str, after: str) -> bool:
    """Count a replacement, but only when it actually shrinks the body."""
    saved = len(before) - len(after)
    if saved <= 0:
        return False
    stats.hits.append(OdcpHit(strategy, saved))
    stats.chars_saved += saved
    stats.saved_tokens += max(0, estimate_tokens(before) - estimate_tokens(after))
    return True


def _signature(name: str, arguments) -> str:
    """A call's identity: tool name plus arguments with None values dropped
    and keys sorted, so '{"a":1,"b":2}' and '{"b":2,"a":1}' collapse."""
    return name + "::" + json.dumps(
        _canonical(arguments), sort_keys=True, ensure_ascii=False)


def _canonical(value):
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items()) if v is not None}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def _json_or_raw(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


# ---------------------------------------------------------------------------
# Per-protocol pairing walks — mirror the tool-result locations rtk reads.
# ---------------------------------------------------------------------------

def _prune_anthropic(body: dict, config, stats: OdcpStats) -> None:
    messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
    call_by_id: dict = {}
    calls: list = []
    user_seen = -1   # ordinal of the current message when it is a user message
    for msg in messages:
        is_user = msg.get("role") == "user"
        if is_user:
            user_seen += 1
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                call_by_id[block.get("id")] = block
            elif btype == "tool_result":
                use = call_by_id.get(block.get("tool_use_id"))
                if not isinstance(use, dict) or not isinstance(use.get("name"), str):
                    continue
                calls.append(_Call(
                    name=use["name"],
                    arguments=use.get("input"),
                    output_holder=block,
                    output_key="content",
                    part_type="text",
                    input_block=use,
                    user_index=user_seen if is_user else None,
                    errored=block.get("is_error") is True,
                ))
    _dedup(calls, config, stats)
    _purge_errors(calls, config, stats, user_seen + 1)


def _prune_openai_chat(body: dict, config, stats: OdcpStats) -> None:
    if not config.dedup:
        return
    call_by_id: dict = {}
    calls: list = []
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "assistant":
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") if isinstance(call, dict) else None
                if isinstance(fn, dict) and isinstance(fn.get("name"), str):
                    call_by_id[call.get("id")] = fn
        elif role == "tool":
            fn = call_by_id.get(msg.get("tool_call_id"))
            if fn is not None:
                calls.append(_Call(
                    name=fn["name"],
                    arguments=_json_or_raw(fn.get("arguments")),
                    output_holder=msg,
                    output_key="content",
                    part_type="text",
                    input_block=None,
                    user_index=None,
                    errored=False,
                ))
    _dedup(calls, config, stats)


def _prune_openai_responses(body: dict, config, stats: OdcpStats) -> None:
    if not config.dedup:
        return
    items = body.get("input")
    if not isinstance(items, list):
        return
    call_by_id: dict = {}
    calls: list = []
    for item in items:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "function_call":
            call_by_id[item.get("call_id")] = item
        elif itype == "function_call_output":
            call = call_by_id.get(item.get("call_id"))
            if call is not None and isinstance(call.get("name"), str):
                calls.append(_Call(
                    name=call["name"],
                    arguments=_json_or_raw(call.get("arguments")),
                    output_holder=item,
                    output_key="output",
                    part_type="input_text",
                    input_block=None,
                    user_index=None,
                    errored=False,
                ))
    _dedup(calls, config, stats)
