"""Per-protocol request signal extraction and endpoint metadata.

Three wire protocols share the routing core (router.resolve); only the
request-side signal extraction and the upstream endpoint path differ. The
response path is opaque byte passthrough — protocol-agnostic by design.
"""

import json
import re

from awerouter.types import InspectResult

PROTOCOL_IDS = ("anthropic", "openai-chat", "openai-responses")

# Upstream path appended to a provider's base_url. base_url uses the native
# client convention: anthropic = ANTHROPIC_BASE_URL style (no /v1),
# openai = OPENAI_BASE_URL style (includes /v1).
ENDPOINT_PATHS = {
    "anthropic": "/v1/messages",
    "openai-chat": "/chat/completions",
    "openai-responses": "/responses",
}


def extract(protocol: str, body: dict) -> InspectResult:
    """Extract routing signals from a request body of the given protocol."""
    try:
        extractor = _EXTRACTORS[protocol]
    except KeyError:
        raise ValueError(f"unknown protocol: {protocol}") from None
    return extractor(body)


# ---------------------------------------------------------------------------
# Shared token estimate
# ---------------------------------------------------------------------------

_CJK = re.compile(r"[一-鿿]")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    total = len(text)
    cjk = len(_CJK.findall(text))
    non_cjk = total - cjk
    # non_cjk / 4 + cjk / 1.5  -> multiply by 12 to stay in int
    return (non_cjk * 3 + cjk * 8) // 12 or 1


# ---------------------------------------------------------------------------
# Shared content flatteners. token_count reflects everything in the request
# that is resent every turn and billed upstream: system prompt, message
# prose, tool definitions, tool results, tool-call arguments, thinking
# blocks. Images only set has_image.
# ---------------------------------------------------------------------------

# Cross-protocol token buckets (InspectResult.token_breakdown keys).
TOKEN_TYPES = ("system", "messages", "tools", "tool_results", "tool_calls", "thinking")


def _new_buckets() -> dict:
    return {key: [] for key in TOKEN_TYPES}


def _summarize(buckets: dict) -> tuple:
    """Estimate each bucket independently; token_count is the sum, so the
    breakdown always adds up (each non-empty bucket has a 1-token floor)."""
    breakdown = {
        key: estimate_tokens(" ".join(p for p in parts if p))
        for key, parts in buckets.items()
        if any(parts)
    }
    return sum(breakdown.values()), breakdown


def _block_text(content) -> str:
    """Text of a content value that is either a plain string or a list of
    blocks carrying a "text" field (anthropic system/text blocks, tool
    results, reasoning summaries)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    return ""


def _tool_defs_text(tools) -> str:
    if not tools:
        return ""
    return json.dumps(tools, ensure_ascii=False)


def _tool_use_input_text(value) -> str:
    """Tool-call arguments as text: dict (anthropic input) or JSON string
    (openai-chat / openai-responses arguments)."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return ""


# ---------------------------------------------------------------------------
# anthropic: messages[] with text/image/tool_result/tool_use/thinking blocks,
# system prompt as str or text blocks, flat tool names
# ---------------------------------------------------------------------------


def _extract_anthropic(body: dict) -> InspectResult:
    messages = body.get("messages", [])
    b = _new_buckets()
    b["system"].append(_block_text(body.get("system")))
    b["tools"].append(_tool_defs_text(body.get("tools")))
    has_image = False
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            b["messages"].append(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    b["messages"].append(block.get("text", ""))
                elif btype == "image":
                    has_image = True
                elif btype == "tool_result":
                    b["tool_results"].append(_block_text(block.get("content")))
                elif btype == "tool_use":
                    b["tool_calls"].append(_tool_use_input_text(block.get("input")))
                elif btype == "thinking":
                    b["thinking"].append(block.get("thinking", ""))
    token_count, breakdown = _summarize(b)
    return InspectResult(
        token_count=token_count,
        has_image=has_image,
        has_web_search=_has_web_search_flat(body),
        message_count=len(messages),
        token_breakdown=breakdown,
    )


def _has_web_search_flat(body: dict) -> bool:
    for tool in body.get("tools", []) or []:
        name = tool.get("name", "") if isinstance(tool, dict) else ""
        if name.startswith("web_search_"):
            return True
    return False


# ---------------------------------------------------------------------------
# openai-chat: messages[] with text/image_url parts (system prompt and tool
# results arrive as message content), nested function tools with string
# arguments
# ---------------------------------------------------------------------------


def _extract_openai_chat(body: dict) -> InspectResult:
    messages = body.get("messages", [])
    b = _new_buckets()
    b["tools"].append(_tool_defs_text(body.get("tools")))
    has_image = False
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            bucket = b["system"]
        elif role == "tool":
            bucket = b["tool_results"]
        else:
            bucket = b["messages"]
        content = msg.get("content")
        if isinstance(content, str):
            bucket.append(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    bucket.append(part.get("text", ""))
                elif part.get("type") == "image_url":
                    has_image = True
        for call in msg.get("tool_calls") or []:
            if isinstance(call, dict) and isinstance(call.get("function"), dict):
                b["tool_calls"].append(call["function"].get("arguments") or "")
    token_count, breakdown = _summarize(b)
    return InspectResult(
        token_count=token_count,
        has_image=has_image,
        has_web_search=_has_web_search_chat(body),
        message_count=len(messages),
        token_breakdown=breakdown,
    )


def _has_web_search_chat(body: dict) -> bool:
    for tool in body.get("tools", []) or []:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name") or ""
        if not name and isinstance(tool.get("function"), dict):
            name = tool["function"].get("name", "")
        if name.startswith("web_search_"):
            return True
    return False


# ---------------------------------------------------------------------------
# openai-responses: input (string | items) with input_text/input_image parts,
# builtin tool types plus flat function names, instructions as system prompt.
# Non-message items (reasoning, function_call, function_call_output) do not
# count as messages, but their payloads count toward token_count.
# ---------------------------------------------------------------------------


def _extract_openai_responses(body: dict) -> InspectResult:
    input_value = body.get("input")
    b = _new_buckets()
    b["system"].append(_block_text(body.get("instructions")))
    b["tools"].append(_tool_defs_text(body.get("tools")))
    if isinstance(input_value, str):
        b["messages"].append(input_value)
        token_count, breakdown = _summarize(b)
        return InspectResult(
            token_count=token_count,
            has_image=False,
            has_web_search=_has_web_search_responses(body),
            message_count=1 if input_value else 0,
            token_breakdown=breakdown,
        )
    has_image = False
    message_count = 0
    for item in input_value or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if content is None:
            itype = item.get("type")
            if itype == "function_call":
                b["tool_calls"].append(item.get("arguments") or "")
            elif itype == "function_call_output":
                b["tool_results"].append(_block_text(item.get("output")))
            elif itype == "reasoning":
                b["thinking"].append(_block_text(item.get("summary")))
            continue
        message_count += 1
        if isinstance(content, str):
            b["messages"].append(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("input_text", "output_text", "text"):
                    b["messages"].append(part.get("text", ""))
                elif part.get("type") == "input_image":
                    has_image = True
    token_count, breakdown = _summarize(b)
    return InspectResult(
        token_count=token_count,
        has_image=has_image,
        has_web_search=_has_web_search_responses(body),
        message_count=message_count,
        token_breakdown=breakdown,
    )


def _has_web_search_responses(body: dict) -> bool:
    for tool in body.get("tools", []) or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "web_search":
            if tool.get("external_web_access") is False:
                continue
            return True
        if tool.get("name", "").startswith("web_search_"):
            return True
    return False


_EXTRACTORS = {
    "anthropic": _extract_anthropic,
    "openai-chat": _extract_openai_chat,
    "openai-responses": _extract_openai_responses,
}
