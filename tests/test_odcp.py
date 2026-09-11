"""Tests for awerouter.odcp — dedup + purgeErrors over the three protocols."""

import json

from awerouter import odcp
from awerouter.types import OdcpConfig

CFG = OdcpConfig()
DEDUP_NOTE = "[odcp: superseded output of an identical earlier tool call removed]"
PURGED_NOTE = "[odcp: input removed — the call failed]"

BIG = "x" * 400   # comfortably larger than the replacement notes


def _tuse(id_, name, **params):
    return {"type": "tool_use", "id": id_, "name": name, "input": dict(params)}


def _tresult(id_, text, is_error=None):
    block = {"type": "tool_result", "tool_use_id": id_, "content": text}
    if is_error is not None:
        block["is_error"] = is_error
    return block


def _pair(id_, name, text, **params):
    """One assistant tool_use + its user tool_result, as two messages."""
    return [
        {"role": "assistant", "content": [_tuse(id_, name, **params)]},
        {"role": "user", "content": [_tresult(id_, text)]},
    ]


# ---------------------------------------------------------------------------
# signatures
# ---------------------------------------------------------------------------

class TestSignature:
    def test_key_order_irrelevant(self):
        assert odcp._signature("Bash", {"a": 1, "b": 2}) == odcp._signature("Bash", {"b": 2, "a": 1})

    def test_explicit_none_differs_from_missing_value(self):
        assert odcp._signature("Bash", {"a": 1}) != odcp._signature(
            "Bash", {"a": 1, "b": None})

    def test_nested_normalization(self):
        a = {"x": {"b": 2, "a": None}, "y": [1, {"k": None, "j": 2}]}
        b = {"x": {"a": None, "b": 2}, "y": [1, {"j": 2, "k": None}]}
        assert odcp._signature("Bash", a) == odcp._signature("Bash", b)

    def test_different_args_differ(self):
        assert odcp._signature("Bash", {"a": 1}) != odcp._signature("Bash", {"a": 2})

    def test_different_tools_differ(self):
        assert odcp._signature("Bash", {"a": 1}) != odcp._signature("Read", {"a": 1})


# ---------------------------------------------------------------------------
# dedup — anthropic
# ---------------------------------------------------------------------------

class TestDedupAnthropic:
    def _three_reads(self, text):
        msgs = [{"role": "user", "content": "go"}]
        for i in range(3):
            msgs.extend(_pair(f"t{i}", "Bash", text, command="cat config.py"))
        return {"model": "m", "messages": msgs}

    def test_only_newest_output_survives(self):
        body = self._three_reads(BIG)
        odcp.prune_body(body, "anthropic", CFG)
        contents = [m["content"][0]["content"] for m in body["messages"]
                    if isinstance(m["content"], list) and m["content"][0]["type"] == "tool_result"]
        assert contents == [DEDUP_NOTE, DEDUP_NOTE, BIG]

    def test_interleaved_signatures_keep_their_own_newest(self):
        body = {"model": "m", "messages": [
            {"role": "user", "content": "go"},
            *_pair("a1", "Bash", BIG, command="cat a.py"),
            *_pair("b1", "Bash", BIG, command="cat b.py"),
            *_pair("a2", "Bash", BIG, command="cat a.py"),
            *_pair("b2", "Bash", BIG, command="cat b.py"),
        ]}
        odcp.prune_body(body, "anthropic", CFG)
        by_id = {b["tool_use_id"]: b["content"]
                 for m in body["messages"] if isinstance(m["content"], list)
                 for b in m["content"] if b.get("type") == "tool_result"}
        assert by_id == {"a1": DEDUP_NOTE, "b1": DEDUP_NOTE, "a2": BIG, "b2": BIG}

    def test_different_args_not_deduped(self):
        body = {"model": "m", "messages": [
            {"role": "user", "content": "go"},
            *_pair("t1", "Bash", BIG, command="cat a.py"),
            *_pair("t2", "Bash", BIG, command="cat b.py"),
        ]}
        odcp.prune_body(body, "anthropic", CFG)
        assert body["messages"][2]["content"][0]["content"] == BIG
        assert body["messages"][4]["content"][0]["content"] == BIG

    def test_protected_tools_untouched(self):
        body = {"model": "m", "messages": [
            {"role": "user", "content": "go"},
            *_pair("t1", "Write", BIG, file_path="a.py", content=BIG),
            *_pair("t2", "Write", BIG, file_path="a.py", content=BIG),
        ]}
        odcp.prune_body(body, "anthropic", CFG)
        assert body["messages"][2]["content"][0]["content"] == BIG

    def test_errored_results_not_deduped(self):
        body = {"model": "m", "messages": [
            {"role": "user", "content": "go"},
            *_pair("t1", "Bash", "Error: boom", command="cat a.py"),
            *_pair("t2", "Bash", BIG, command="cat a.py"),
        ]}
        body["messages"][2]["content"][0]["is_error"] = True
        odcp.prune_body(body, "anthropic", CFG)
        assert body["messages"][2]["content"][0]["content"] == "Error: boom"

    def test_array_content_text_parts_replaced(self):
        body = {"model": "m", "messages": [
            {"role": "user", "content": "go"},
            *_pair("t1", "Bash", [{"type": "text", "text": BIG}], command="cat a.py"),
            *_pair("t2", "Bash", [{"type": "text", "text": BIG}], command="cat a.py"),
        ]}
        odcp.prune_body(body, "anthropic", CFG)
        assert body["messages"][2]["content"][0]["content"][0]["text"] == DEDUP_NOTE
        assert body["messages"][4]["content"][0]["content"][0]["text"] == BIG

    def test_output_smaller_than_note_untouched(self):
        body = {"model": "m", "messages": [
            {"role": "user", "content": "go"},
            *_pair("t1", "Bash", "ok", command="cat a.py"),
            *_pair("t2", "Bash", "ok", command="cat a.py"),
        ]}
        odcp.prune_body(body, "anthropic", CFG)
        assert body["messages"][2]["content"][0]["content"] == "ok"

    def test_determinism_second_pass_is_noop(self):
        body = self._three_reads(BIG)
        first = odcp.prune_body(body, "anthropic", CFG)
        second = odcp.prune_body(body, "anthropic", CFG)
        assert json.dumps(body) == json.dumps(body)  # sanity
        assert second.hits == []
        assert first.chars_saved > 0 and first.saved_tokens > 0

    def test_stats_shape(self):
        stats = odcp.prune_body(self._three_reads(BIG), "anthropic", CFG)
        assert all(h.strategy == "dedup" for h in stats.hits)
        assert "dedup" in odcp.format_log(stats)


# ---------------------------------------------------------------------------
# purgeErrors — anthropic only (the wire marks errors there)
# ---------------------------------------------------------------------------

def _error_body(turns_after, tool="Bash", protected=False):
    name = "Write" if protected else "Bash"
    params = {"content": BIG, "file_path": "a.py"} if protected else {"command": BIG}
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [_tuse("t1", name, **params)]},
        {"role": "user", "content": [_tresult("t1", "Error: boom", is_error=True)]},
    ]
    for i in range(turns_after):
        msgs.append({"role": "user", "content": f"more {i}"})
        msgs.append({"role": "assistant", "content": f"ok {i}"})
    return {"model": "m", "messages": msgs}


class TestPurgeErrors:
    def test_old_error_inputs_purged(self):
        body = _error_body(5)
        odcp.prune_body(body, "anthropic", CFG)
        use = body["messages"][1]["content"][0]
        assert use["input"] == {"command": PURGED_NOTE}
        # the error text itself stays
        assert body["messages"][2]["content"][0]["content"] == "Error: boom"

    def test_young_error_untouched(self):
        body = _error_body(3)   # age 3 < turns 4
        odcp.prune_body(body, "anthropic", CFG)
        assert body["messages"][1]["content"][0]["input"] == {"command": BIG}

    def test_boundary_age_equals_turns_pruned(self):
        body = _error_body(4)   # age == turns == 4
        odcp.prune_body(body, "anthropic", CFG)
        assert body["messages"][1]["content"][0]["input"] == {"command": PURGED_NOTE}

    def test_non_string_params_untouched(self):
        msgs = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": [_tuse("t1", "Bash", timeout=30, command=BIG)]},
            {"role": "user", "content": [_tresult("t1", "Error: boom", is_error=True)]},
        ]
        for i in range(6):
            msgs.append({"role": "user", "content": "x"})
        body = {"model": "m", "messages": msgs}
        odcp.prune_body(body, "anthropic", CFG)
        assert body["messages"][1]["content"][0]["input"] == {"timeout": 30, "command": PURGED_NOTE}

    def test_protected_tool_errors_untouched(self):
        body = _error_body(6, protected=True)
        odcp.prune_body(body, "anthropic", CFG)
        assert body["messages"][1]["content"][0]["input"]["content"] == BIG

    def test_purge_disabled(self):
        body = _error_body(6)
        odcp.prune_body(body, "anthropic", OdcpConfig(purge_errors=False))
        assert body["messages"][1]["content"][0]["input"] == {"command": BIG}

    def test_successful_call_inputs_never_purged(self):
        body = {"model": "m", "messages": [
            {"role": "user", "content": "go"},
            *_pair("t1", "Bash", "out", command=BIG),
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
        ]}
        odcp.prune_body(body, "anthropic", CFG)
        assert body["messages"][1]["content"][0]["input"] == {"command": BIG}

    def test_openai_protocols_have_no_error_marks(self):
        # purgeErrors is documented anthropic-only; openai bodies carry no
        # error flag, so nothing may be rewritten there.
        body = {"model": "m", "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "Bash", "arguments": json.dumps({"command": BIG})}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "Error: boom"},
        ]}
        odcp.prune_body(body, "openai-chat", CFG)
        assert body["messages"][1]["tool_calls"][0]["function"]["arguments"] == json.dumps({"command": BIG})
        assert body["messages"][2]["content"] == "Error: boom"


# ---------------------------------------------------------------------------
# dedup — openai-chat / openai-responses
# ---------------------------------------------------------------------------

class TestDedupOpenaiChat:
    def _body(self, text):
        args = json.dumps({"command": "cat config.py"})
        return {"model": "m", "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "Bash", "arguments": args}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": text},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c2", "type": "function", "function": {"name": "Bash", "arguments": args}},
            ]},
            {"role": "tool", "tool_call_id": "c2", "content": text},
        ]}

    def test_older_tool_message_replaced(self):
        body = self._body(BIG)
        odcp.prune_body(body, "openai-chat", CFG)
        assert body["messages"][2]["content"] == DEDUP_NOTE
        assert body["messages"][4]["content"] == BIG

    def test_unparseable_arguments_still_dedupe_verbatim(self):
        raw = "not json {"
        body = self._body(BIG)
        for m in body["messages"]:
            if m.get("role") == "assistant":
                m["tool_calls"][0]["function"]["arguments"] = raw
        odcp.prune_body(body, "openai-chat", CFG)
        assert body["messages"][2]["content"] == DEDUP_NOTE


class TestDedupOpenaiResponses:
    def _body(self, text):
        if isinstance(text, list):
            out1, out2 = [dict(p) for p in text], [dict(p) for p in text]
        else:
            out1 = out2 = text
        return {"model": "m", "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "go"}]},
            {"type": "function_call", "call_id": "c1", "name": "Bash",
             "arguments": json.dumps({"command": "cat config.py"})},
            {"type": "function_call_output", "call_id": "c1", "output": out1},
            {"type": "function_call", "call_id": "c2", "name": "Bash",
             "arguments": json.dumps({"command": "cat config.py"})},
            {"type": "function_call_output", "call_id": "c2", "output": out2},
        ]}

    def test_older_output_replaced(self):
        body = self._body(BIG)
        odcp.prune_body(body, "openai-responses", CFG)
        assert body["input"][2]["output"] == DEDUP_NOTE
        assert body["input"][4]["output"] == BIG

    def test_array_output_parts_replaced(self):
        body = self._body([{"type": "input_text", "text": BIG}])
        odcp.prune_body(body, "openai-responses", CFG)
        assert body["input"][2]["output"][0]["text"] == DEDUP_NOTE
        assert body["input"][4]["output"][0]["text"] == BIG

    def test_shell_wrapped_apply_patch_output_is_protected(self):
        body = {"model": "m", "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "go"}]},
            {"type": "function_call", "call_id": "c1", "name": "exec_command",
             "arguments": json.dumps({"cmd": "apply_patch <<'PATCH'\n*** Begin Patch\nPATCH"})},
            {"type": "function_call_output", "call_id": "c1", "output": BIG},
            {"type": "function_call", "call_id": "c2", "name": "exec_command",
             "arguments": json.dumps({"cmd": "apply_patch <<'PATCH'\n*** Begin Patch\nPATCH"})},
            {"type": "function_call_output", "call_id": "c2", "output": BIG},
        ]}
        odcp.prune_body(body, "openai-responses", CFG)
        assert body["input"][2]["output"] == BIG


# ---------------------------------------------------------------------------
# fail-open contract
# ---------------------------------------------------------------------------

class TestFailOpen:
    def test_non_dict_body(self):
        assert odcp.prune_body(None, "anthropic", CFG) is None
        assert odcp.prune_body("nope", "anthropic", CFG) is None

    def test_unknown_protocol(self):
        assert odcp.prune_body({"messages": []}, "gemini", CFG) is None

    def test_malformed_messages_do_not_raise(self):
        body = {"messages": ["not a dict", {"role": "user", "content": "text only"},
                             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "gone"}]}]}
        stats = odcp.prune_body(body, "anthropic", CFG)
        assert stats is not None and stats.hits == []

    def test_missing_keys_tolerated(self):
        assert odcp.prune_body({}, "anthropic", CFG) is not None
        assert odcp.prune_body({}, "openai-responses", CFG) is not None

    def test_format_log_none_when_nothing_pruned(self):
        assert odcp.format_log(None) is None
        assert odcp.format_log(odcp.OdcpStats()) is None
