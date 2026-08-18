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

# File-search tools whose results inflate context cheaply (match lists, not
# prose the model must reason over). L3 weighs their tokens against the
# threshold at a discount — see effective_tokens — and the tool-phase routing
# layer sends their turns to flash. Names match case-insensitively:
# claude-code sends Grep/Glob/LS, opencode sends grep/glob/list.
FILE_SEARCH_TOOLS = frozenset({"grep", "glob", "ls", "list"})


def _is_file_search(name) -> bool:
    return isinstance(name, str) and name.lower() in FILE_SEARCH_TOOLS


# Code-modification tools: the most recent call being one of these means the
# agent is writing or verifying code — the tool-phase routing layer sends
# those turns to pro. Case-insensitive; covers claude-code (Edit/Write/
# NotebookEdit), opencode (edit/write), codex (apply_patch), and common
# openai-chat agents (replace_in_file, write_to_file, apply_diff).
EDIT_TOOLS = frozenset({
    "edit", "write", "multiedit", "multi_edit", "notebookedit", "notebook_edit",
    "applypatch", "apply_patch", "str_replace", "str_replace_editor",
    "replace_in_file", "write_to_file", "apply_diff", "create_file",
})


# Shell-exec tool names whose command text decides search-ness. Codex wraps
# searches here instead of exposing per-purpose tools (0.147: exec_command,
# "cmd" string; older builds: shell, "command" as an argv array); commands
# arrive as one compound shell string, e.g. "echo ..; rg -n x . | sed ..".
_SHELL_TOOLS = frozenset({"exec_command", "shell"})
_SEARCH_BINARIES = frozenset({"rg", "grep", "find", "fd", "fdfind", "ls", "ag", "ack"})


def _is_search_command(cmd) -> bool:
    """True if any segment of a compound shell command is a file search.

    Segments split on ;/&&/||/newlines, each reduced to its pipeline head
    (text before the first |) with leading FOO=bar env assignments stripped;
    any head whose first word is a search binary qualifies — codex prefixes
    real work with echo banners, so "first segment" would miss it.
    """
    if not isinstance(cmd, str) or not cmd.strip():
        return False
    for segment in re.split(r";|&&|\|\||\n", cmd):
        head = segment.split("|", 1)[0].strip()
        if not head:
            continue
        words = head.split()
        while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
            words.pop(0)
        if not words:
            continue
        first = words[0].rsplit("/", 1)[-1]
        if first in _SEARCH_BINARIES:
            return True
        if first == "git" and len(words) > 1 and words[1] == "grep":
            return True
    return False


def _shell_is_search(name, arguments) -> bool:
    """A shell-tool call counts as file search when its command says so."""
    if not isinstance(name, str) or name.lower() not in _SHELL_TOOLS:
        return False
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        return False
    if not isinstance(args, dict):
        return False
    cmd = args.get("cmd") or args.get("command")
    if isinstance(cmd, list):
        # argv form: the real command is one element, e.g. ["bash","-lc","rg x"]
        return any(_is_search_command(part) for part in cmd if isinstance(part, str))
    return _is_search_command(cmd)


def effective_tokens(token_count: int, file_search_tokens: int, discount: float = 0.3) -> int:
    """token_count with file-search result tokens weighed at `discount`.

    What L3 compares against longContextThreshold. Both inputs only grow
    under append-only history, so the result does too: a session crosses
    flash -> pro at most once (no flip-flop, prefix caches survive).
    """
    return token_count - int(file_search_tokens * (1.0 - discount))


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
    tool_names: dict = {}   # tool_use id -> name; a call always precedes its result
    search_texts: list[str] = []
    last_tool = ""
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
                    text = _block_text(block.get("content"))
                    b["tool_results"].append(text)
                    if _is_file_search(tool_names.get(block.get("tool_use_id"))):
                        search_texts.append(text)
                elif btype == "tool_use":
                    b["tool_calls"].append(_tool_use_input_text(block.get("input")))
                    name = block.get("name")
                    tool_names[block.get("id")] = name
                    if isinstance(name, str):
                        last_tool = name.lower()
                elif btype == "thinking":
                    b["thinking"].append(block.get("thinking", ""))
    token_count, breakdown = _summarize(b)
    return InspectResult(
        token_count=token_count,
        has_image=has_image,
        has_web_search=_has_web_search_flat(body),
        message_count=len(messages),
        token_breakdown=breakdown,
        file_search_tokens=estimate_tokens(" ".join(t for t in search_texts if t)),
        last_tool=last_tool,
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
    tool_names: dict = {}   # tool_call id -> function name
    search_texts: list[str] = []
    last_tool = ""
    for msg in messages:
        role = msg.get("role")
        if role == "system":
            bucket = b["system"]
        elif role == "tool":
            bucket = b["tool_results"]
        else:
            bucket = b["messages"]
        is_search_result = (
            role == "tool" and _is_file_search(tool_names.get(msg.get("tool_call_id")))
        )
        content = msg.get("content")
        if isinstance(content, str):
            bucket.append(content)
            if is_search_result:
                search_texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    text = part.get("text", "")
                    bucket.append(text)
                    if is_search_result:
                        search_texts.append(text)
                elif part.get("type") == "image_url":
                    has_image = True
        for call in msg.get("tool_calls") or []:
            if isinstance(call, dict) and isinstance(call.get("function"), dict):
                b["tool_calls"].append(call["function"].get("arguments") or "")
                name = call["function"].get("name")
                tool_names[call.get("id")] = name
                if isinstance(name, str):
                    last_tool = name.lower()
    token_count, breakdown = _summarize(b)
    return InspectResult(
        token_count=token_count,
        has_image=has_image,
        has_web_search=_has_web_search_chat(body),
        message_count=len(messages),
        token_breakdown=breakdown,
        file_search_tokens=estimate_tokens(" ".join(t for t in search_texts if t)),
        last_tool=last_tool,
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
    search_calls: dict = {}   # call_id -> output counts as file search
    search_texts: list[str] = []
    last_tool = ""
    for item in input_value or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if content is None:
            itype = item.get("type")
            if itype == "function_call":
                b["tool_calls"].append(item.get("arguments") or "")
                search_calls[item.get("call_id")] = (
                    _is_file_search(item.get("name"))
                    or _shell_is_search(item.get("name"), item.get("arguments"))
                )
                if isinstance(item.get("name"), str):
                    last_tool = item["name"].lower()
            elif itype == "function_call_output":
                text = _block_text(item.get("output"))
                b["tool_results"].append(text)
                if search_calls.get(item.get("call_id")):
                    search_texts.append(text)
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
        file_search_tokens=estimate_tokens(" ".join(t for t in search_texts if t)),
        last_tool=last_tool,
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
