"""Per-protocol request signal extraction and endpoint metadata.

Three wire protocols share the routing core (router.resolve); only the
request-side signal extraction and the upstream endpoint path differ. The
response path is opaque byte passthrough — protocol-agnostic by design.
"""

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
# anthropic: messages[] with text/image content blocks, flat tool names
# ---------------------------------------------------------------------------


def _extract_anthropic(body: dict) -> InspectResult:
    messages = body.get("messages", [])
    parts: list[str] = []
    has_image = False
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "image":
                    has_image = True
    return InspectResult(
        token_count=estimate_tokens(" ".join(parts)),
        has_image=has_image,
        has_web_search=_has_web_search_flat(body),
        message_count=len(messages),
    )


def _has_web_search_flat(body: dict) -> bool:
    for tool in body.get("tools", []) or []:
        name = tool.get("name", "") if isinstance(tool, dict) else ""
        if name.startswith("web_search_"):
            return True
    return False


# ---------------------------------------------------------------------------
# openai-chat: messages[] with text/image_url parts, nested function tools
# ---------------------------------------------------------------------------


def _extract_openai_chat(body: dict) -> InspectResult:
    messages = body.get("messages", [])
    parts: list[str] = []
    has_image = False
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    parts.append(part.get("text", ""))
                elif part.get("type") == "image_url":
                    has_image = True
    return InspectResult(
        token_count=estimate_tokens(" ".join(parts)),
        has_image=has_image,
        has_web_search=_has_web_search_chat(body),
        message_count=len(messages),
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
# builtin tool types plus flat function names. Non-message items (reasoning,
# function_call, function_call_output) carry no text and are skipped.
# ---------------------------------------------------------------------------


def _extract_openai_responses(body: dict) -> InspectResult:
    input_value = body.get("input")
    if isinstance(input_value, str):
        return InspectResult(
            token_count=estimate_tokens(input_value),
            has_image=False,
            has_web_search=_has_web_search_responses(body),
            message_count=1 if input_value else 0,
        )
    parts: list[str] = []
    has_image = False
    message_count = 0
    for item in input_value or []:
        content = item.get("content") if isinstance(item, dict) else None
        if content is None:
            continue
        message_count += 1
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("input_text", "output_text", "text"):
                    parts.append(part.get("text", ""))
                elif part.get("type") == "input_image":
                    has_image = True
    return InspectResult(
        token_count=estimate_tokens(" ".join(parts)),
        has_image=has_image,
        has_web_search=_has_web_search_responses(body),
        message_count=message_count,
    )


def _has_web_search_responses(body: dict) -> bool:
    for tool in body.get("tools", []) or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "web_search":
            return True
        if tool.get("name", "").startswith("web_search_"):
            return True
    return False


_EXTRACTORS = {
    "anthropic": _extract_anthropic,
    "openai-chat": _extract_openai_chat,
    "openai-responses": _extract_openai_responses,
}
