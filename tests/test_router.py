"""Tests for awerouter.router and per-protocol signal extraction."""

import json

import pytest

from awerouter.protocols import effective_tokens, estimate_tokens, extract
from awerouter.router import resolve
from awerouter.types import Destination


def _cfg():
    return {
        "flash": Destination("stepfun", "step-3.5-flash"),
        "pro": Destination("anthropic", "claude-opus-5"),
    }


def _resolve(model, body, threshold=32000, web_search_model="pro", search_discount=0.3,
             tool_edit_dest="pro"):
    return resolve(
        model, extract("anthropic", body), _cfg(), "flash", "think",
        threshold, web_search_model, search_discount, tool_edit_dest,
    )


# ---------------------------------------------------------------------------
# Token estimate (protocol-agnostic)
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_ascii(self):
        assert estimate_tokens("hello world") == 2  # 11 chars / 4

    def test_cjk_counts_heavier(self):
        assert estimate_tokens("你好") == 1  # 2 chars / 1.5 -> 1

    def test_nonzero_floor(self):
        assert estimate_tokens("x") >= 1


# ---------------------------------------------------------------------------
# anthropic extraction
# ---------------------------------------------------------------------------


class TestExtractAnthropic:
    def test_empty_messages(self):
        r = extract("anthropic", {"messages": []})
        assert r.message_count == 0
        assert r.token_count == 0
        assert not r.has_image
        assert not r.has_web_search

    def test_text_extraction(self):
        r = extract("anthropic", {"messages": [{"content": "hello world"}]})
        assert r.token_count >= 1

    def test_multilingual(self):
        r = extract("anthropic", {"messages": [{"content": "你好世界"}]})
        assert r.token_count >= 1

    def test_has_image_true(self):
        r = extract("anthropic", {"messages": [{"content": [{"type": "image", "data": "xyz"}]}]})
        assert r.has_image is True

    def test_has_web_search_true(self):
        r = extract("anthropic", {"messages": [], "tools": [{"name": "web_search_20250813"}]})
        assert r.has_web_search is True

    def test_no_messages_key(self):
        r = extract("anthropic", {})
        assert r.message_count == 0
        assert r.token_count == 0

    def test_system_prompt_counted(self):
        r = extract("anthropic", {"system": "you are a router", "messages": []})
        assert r.token_count == estimate_tokens("you are a router")

    def test_system_prompt_blocks_counted(self):
        r = extract("anthropic", {"system": [{"type": "text", "text": "be brief"}], "messages": []})
        assert r.token_count == estimate_tokens("be brief")

    def test_tool_definitions_counted(self):
        tools = [{"name": "get_weather", "input_schema": {"type": "object"}}]
        r = extract("anthropic", {"messages": [], "tools": tools})
        assert r.token_count == estimate_tokens(json.dumps(tools, ensure_ascii=False))

    def test_tool_result_counted(self):
        r = extract("anthropic", {"messages": [{"content": [
            {"type": "tool_result", "content": "tool output text"},
        ]}]})
        assert r.token_count == estimate_tokens("tool output text")

    def test_tool_result_blocks_counted(self):
        r = extract("anthropic", {"messages": [{"content": [
            {"type": "tool_result", "content": [{"type": "text", "text": "block output"}]},
        ]}]})
        assert r.token_count == estimate_tokens("block output")

    def test_tool_use_input_counted(self):
        r = extract("anthropic", {"messages": [{"content": [
            {"type": "tool_use", "id": "t1", "name": "get_weather", "input": {"city": "SF"}},
        ]}]})
        assert r.token_count == estimate_tokens('{"city": "SF"}')

    def test_thinking_counted(self):
        r = extract("anthropic", {"messages": [{"content": [
            {"type": "thinking", "thinking": "let me consider options"},
        ]}]})
        assert r.token_count == estimate_tokens("let me consider options")

    def test_mixed_content_counted(self):
        tools = [{"name": "get_weather"}]
        r = extract("anthropic", {
            "system": "sys prompt",
            "tools": tools,
            "messages": [
                {"role": "user", "content": "question"},
                {"role": "user", "content": [{"type": "tool_result", "content": "answer data"}]},
            ],
        })
        assert r.token_breakdown["system"] == estimate_tokens("sys prompt")
        assert r.token_breakdown["messages"] == estimate_tokens("question")
        assert r.token_breakdown["tool_results"] == estimate_tokens("answer data")
        assert r.token_breakdown["tools"] == estimate_tokens(json.dumps(tools, ensure_ascii=False))
        assert r.token_count == sum(r.token_breakdown.values())

    def test_token_count_is_sum_of_breakdown(self):
        r = extract("anthropic", {
            "system": "sys",
            "tools": [{"name": "t"}],
            "messages": [{"content": [
                {"type": "text", "text": "hello"},
                {"type": "tool_result", "content": "result"},
                {"type": "tool_use", "id": "t1", "name": "t", "input": {"x": 1}},
                {"type": "thinking", "thinking": "hmm"},
            ]}],
        })
        assert set(r.token_breakdown) == {"system", "messages", "tools", "tool_results", "tool_calls", "thinking"}
        assert r.token_count == sum(r.token_breakdown.values())

    def test_file_search_result_tokens(self):
        r = extract("anthropic", {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Grep", "input": {"q": "x"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "match1\nmatch2\nmatch3"},
            ]},
        ]})
        assert r.file_search_tokens == estimate_tokens("match1\nmatch2\nmatch3")

    def test_non_search_tool_result_not_measured(self):
        r = extract("anthropic", {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "x"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "file content"},
            ]},
        ]})
        assert r.file_search_tokens == 0

    def test_orphan_tool_result_not_measured(self):
        """tool_use missing (compacted history) -> counts as non-search."""
        r = extract("anthropic", {"messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "gone", "content": "match1"},
            ]},
        ]})
        assert r.file_search_tokens == 0


# ---------------------------------------------------------------------------
# openai-chat extraction
# ---------------------------------------------------------------------------


class TestExtractOpenAIChat:
    def test_string_content(self):
        r = extract("openai-chat", {"messages": [{"role": "user", "content": "hello world"}]})
        assert r.token_count == 2

    def test_text_parts(self):
        r = extract("openai-chat", {"messages": [
            {"role": "user", "content": [{"type": "text", "text": "hi there"}]},
        ]})
        assert r.token_count >= 1

    def test_image_url_part(self):
        r = extract("openai-chat", {"messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]},
        ]})
        assert r.has_image is True

    def test_nested_function_tool_web_search(self):
        r = extract("openai-chat", {
            "messages": [],
            "tools": [{"type": "function", "function": {"name": "web_search_20250813", "parameters": {}}}],
        })
        assert r.has_web_search is True

    def test_flat_tool_name_accepted_leniently(self):
        r = extract("openai-chat", {"messages": [], "tools": [{"name": "web_search_x"}]})
        assert r.has_web_search is True

    def test_regular_function_tool_not_web_search(self):
        r = extract("openai-chat", {
            "messages": [],
            "tools": [{"type": "function", "function": {"name": "get_weather"}}],
        })
        assert r.has_web_search is False

    def test_message_count(self):
        r = extract("openai-chat", {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]})
        assert r.message_count == 2

    def test_system_message_counted(self):
        r = extract("openai-chat", {"messages": [
            {"role": "system", "content": "sys prompt"},
            {"role": "user", "content": "hi"},
        ]})
        assert r.token_breakdown["system"] == estimate_tokens("sys prompt")
        assert r.token_breakdown["messages"] == estimate_tokens("hi")

    def test_tool_result_content_counted(self):
        r = extract("openai-chat", {"messages": [{"role": "tool", "content": "tool output"}]})
        assert r.token_breakdown["tool_results"] == estimate_tokens("tool output")

    def test_file_search_result_tokens(self):
        r = extract("openai-chat", {"messages": [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "Glob", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "a.py\nb.py"},
        ]})
        assert r.file_search_tokens == estimate_tokens("a.py\nb.py")

    def test_file_search_names_match_case_insensitive(self):
        """opencode sends lowercase names (grep/glob/list)."""
        r = extract("openai-chat", {"messages": [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "grep", "arguments": "{}"}},
                {"id": "c2", "function": {"name": "list", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "hits"},
            {"role": "tool", "tool_call_id": "c2", "content": "files"},
        ]})
        assert r.file_search_tokens == estimate_tokens("hits files")

    def test_non_search_tool_result_not_measured(self):
        r = extract("openai-chat", {"messages": [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "Bash", "arguments": "ls"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "output"},
        ]})
        assert r.file_search_tokens == 0

    def test_file_search_accumulates_across_turns(self):
        """file_search_tokens is a cumulative history total, like every bucket:
        it only grows when a new search result enters the resent history, and
        stays flat while non-search results pile up (usage-log sessions show
        long flat stretches for exactly this reason)."""
        turn1 = [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "grep", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "hit1 hit2"},
        ]
        r1 = extract("openai-chat", {"messages": turn1})
        assert r1.file_search_tokens == estimate_tokens("hit1 hit2")

        turn2 = turn1 + [
            {"role": "assistant", "tool_calls": [
                {"id": "c2", "function": {"name": "read", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c2", "content": "whole file body"},
        ]
        r2 = extract("openai-chat", {"messages": turn2})
        assert r2.file_search_tokens == r1.file_search_tokens
        assert r2.token_breakdown["tool_results"] > r1.token_breakdown["tool_results"]

        turn3 = turn2 + [
            {"role": "assistant", "tool_calls": [
                {"id": "c3", "function": {"name": "glob", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "c3", "content": "a.py b.py"},
        ]
        r3 = extract("openai-chat", {"messages": turn3})
        assert r3.file_search_tokens == estimate_tokens("hit1 hit2 a.py b.py")
        tools = [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}]
        r = extract("openai-chat", {"messages": [], "tools": tools})
        assert r.token_count == estimate_tokens(json.dumps(tools, ensure_ascii=False))

    def test_tool_call_arguments_counted(self):
        r = extract("openai-chat", {"messages": [{"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "SF"}'}},
        ]}]})
        assert r.token_count == estimate_tokens('{"city": "SF"}')


# ---------------------------------------------------------------------------
# openai-responses extraction
# ---------------------------------------------------------------------------


class TestExtractOpenAIResponses:
    def test_input_string(self):
        r = extract("openai-responses", {"input": "hello world"})
        assert r.token_count == 2
        assert r.message_count == 1

    def test_input_empty_string(self):
        r = extract("openai-responses", {"input": ""})
        assert r.token_count == 0
        assert r.message_count == 0

    def test_items_with_string_content(self):
        r = extract("openai-responses", {"input": [{"role": "user", "content": "hi there"}]})
        assert r.token_count >= 1
        assert r.message_count == 1

    def test_input_text_and_input_image_parts(self):
        r = extract("openai-responses", {"input": [{"role": "user", "content": [
            {"type": "input_text", "text": "look"},
            {"type": "input_image", "image_url": "x"},
        ]}]})
        assert r.token_count >= 1
        assert r.has_image is True

    def test_non_message_items_skipped(self):
        """reasoning / function_call(_output) items are not messages
        (message_count), but their payloads count toward token_count."""
        r = extract("openai-responses", {"input": [
            {"type": "reasoning", "summary": []},
            {"type": "function_call", "name": "f", "arguments": "{}"},
            {"type": "function_call_output", "output": "result"},
            {"role": "user", "content": "hello"},
        ]})
        assert r.message_count == 1
        assert r.token_breakdown["tool_calls"] == 1    # "{}" (2 chars / 4 -> floor 1)
        assert r.token_breakdown["tool_results"] == 1  # "result" (6 chars / 4)
        assert r.token_breakdown["messages"] == 1      # "hello" (5 chars / 4)
        assert r.token_count == 3

    def test_builtin_web_search_tool(self):
        r = extract("openai-responses", {"input": [], "tools": [{"type": "web_search"}]})
        assert r.has_web_search is True

    def test_builtin_web_search_disabled(self):
        r = extract("openai-responses", {
            "input": [],
            "tools": [{"type": "web_search", "external_web_access": False}],
        })
        assert r.has_web_search is False

    def test_flat_function_tool_web_search(self):
        r = extract("openai-responses", {
            "input": [],
            "tools": [{"type": "function", "name": "web_search_20250813", "parameters": {}}],
        })
        assert r.has_web_search is True

    def test_no_input_key(self):
        r = extract("openai-responses", {})
        assert r.token_count == 0
        assert r.message_count == 0

    def test_instructions_counted_with_string_input(self):
        r = extract("openai-responses", {"instructions": "be brief", "input": "hi"})
        assert r.token_breakdown["system"] == estimate_tokens("be brief")
        assert r.token_breakdown["messages"] == estimate_tokens("hi")

    def test_instructions_counted_with_items(self):
        r = extract("openai-responses", {"instructions": "be brief", "input": [{"role": "user", "content": "hi"}]})
        assert r.token_breakdown["system"] == estimate_tokens("be brief")
        assert r.token_breakdown["messages"] == estimate_tokens("hi")

    def test_tool_definitions_counted(self):
        tools = [{"type": "function", "name": "get_weather", "parameters": {"type": "object"}}]
        r = extract("openai-responses", {"input": [], "tools": tools})
        assert r.token_count == estimate_tokens(json.dumps(tools, ensure_ascii=False))

    def test_function_call_arguments_counted(self):
        r = extract("openai-responses", {"input": [
            {"type": "function_call", "name": "f", "arguments": '{"x": 1}'},
        ]})
        assert r.token_count == estimate_tokens('{"x": 1}')

    def test_function_call_output_counted(self):
        r = extract("openai-responses", {"input": [
            {"type": "function_call_output", "output": "result text"},
        ]})
        assert r.token_count == estimate_tokens("result text")

    def test_reasoning_summary_counted(self):
        r = extract("openai-responses", {"input": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "chain of thought"}]},
        ]})
        assert r.token_count == estimate_tokens("chain of thought")

    def test_file_search_result_tokens(self):
        r = extract("openai-responses", {"input": [
            {"type": "function_call", "call_id": "c1", "name": "LS", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": "dir listing"},
        ]})
        assert r.file_search_tokens == estimate_tokens("dir listing")

    def test_non_search_tool_result_not_measured(self):
        r = extract("openai-responses", {"input": [
            {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": "rg foo"},
            {"type": "function_call_output", "call_id": "c1", "output": "hits"},
        ]})
        assert r.file_search_tokens == 0

    def test_shell_wrapped_search_counted(self):
        """codex wraps searches in exec_command as one compound cmd string
        (captured from codex 0.147 traffic); any search segment qualifies."""
        cmd = ("echo '--- rg matches ---'; rg -n --no-heading 'needle' . | sed -E 's/x/y/'; "
               "echo '--- files ---'; find . -type f -name '*.txt'")
        output = "Chunk ID: e68e3e\nOutput:\n./src/f1.txt:2\n./src/f2.txt:2"
        r = extract("openai-responses", {"input": [
            {"type": "function_call", "call_id": "c1", "name": "exec_command",
             "arguments": json.dumps({"cmd": cmd, "yield_time_ms": 1000})},
            {"type": "function_call_output", "call_id": "c1", "output": output},
        ]})
        assert r.file_search_tokens == estimate_tokens(output)

    def test_shell_env_prefixed_and_path_binary_counted(self):
        r = extract("openai-responses", {"input": [
            {"type": "function_call", "call_id": "c1", "name": "exec_command",
             "arguments": json.dumps({"cmd": "FOO=1 /usr/bin/rg -n x . && git grep -l y"})},
            {"type": "function_call_output", "call_id": "c1", "output": "hits"},
        ]})
        assert r.file_search_tokens == estimate_tokens("hits")

    def test_shell_non_search_command_not_counted(self):
        r = extract("openai-responses", {"input": [
            {"type": "function_call", "call_id": "c1", "name": "exec_command",
             "arguments": json.dumps({"cmd": "cargo build --release; echo done"})},
            {"type": "function_call_output", "call_id": "c1", "output": "Compiling..."},
        ]})
        assert r.file_search_tokens == 0

    def test_shell_multiline_command_search_after_first_line(self):
        r = extract("openai-responses", {"input": [
            {"type": "function_call", "call_id": "c1", "name": "exec_command",
             "arguments": json.dumps({"cmd": "cd /tmp\necho start\nrg -n needle ."})},
            {"type": "function_call_output", "call_id": "c1", "output": "hits"},
        ]})
        assert r.file_search_tokens == estimate_tokens("hits")

    def test_shell_argv_command_form_counted(self):
        """older codex `shell` sends the command as an argv array."""
        r = extract("openai-responses", {"input": [
            {"type": "function_call", "call_id": "c1", "name": "shell",
             "arguments": json.dumps({"command": ["bash", "-lc", "rg -n x ."]})},
            {"type": "function_call_output", "call_id": "c1", "output": "hits"},
        ]})
        assert r.file_search_tokens == estimate_tokens("hits")


def test_extract_unknown_protocol_raises():
    with pytest.raises(ValueError):
        extract("nope", {})


# ---------------------------------------------------------------------------
# Routing pipeline (signals via anthropic bodies; L-logic is protocol-blind)
# ---------------------------------------------------------------------------


class TestResolve:
    # L1: web_search forces pro regardless of model/tokens

    def test_l1_web_search_forces_pro(self):
        body = {"messages": [{"content": "hi"}], "tools": [{"name": "web_search_20250813"}]}
        r = _resolve("flash", body)
        assert r.destination == "pro"
        assert r.label == "webSearch"

    def test_l1_web_search_short_query(self):
        body = {"messages": [{"content": "?"}], "tools": [{"name": "web_search_20250813"}]}
        r = _resolve("flash", body)
        assert r.destination == "pro"

    def test_l1_web_search_can_go_flash(self):
        """If flash provider supports web_search, route there."""
        body = {"messages": [{"content": "hi"}], "tools": [{"name": "web_search_20250813"}]}
        r = _resolve("flash", body, web_search_model="flash")
        assert r.destination == "flash"
        assert r.label == "webSearch"

    # L2: tier labels

    def test_l2_background_goes_flash(self):
        body = {"messages": [{"content": "hi"}]}
        r = _resolve("flash", body)
        assert r.destination == "flash"
        assert r.label == "background"

    def test_l2_think_goes_pro(self):
        body = {"messages": [{"content": "think hard"}]}
        r = _resolve("think", body)
        assert r.destination == "pro"
        assert r.label == "think"

    # L3: difficulty scoring (cost-first: default -> flash)

    def test_l3_long_context_goes_pro(self):
        body = {"messages": [{"content": "x" * 500}]}
        r = _resolve("auto", body, threshold=100)
        assert r.destination == "pro"
        assert r.label == "longContext"

    def test_l3_search_discount_keeps_flash(self):
        """Grep results inflate the raw count past the threshold, but the
        discounted effective count stays under it."""
        body = {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Grep", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 500},
            ]},
        ]}
        r = _resolve("auto", body, threshold=100)  # raw 126 tokens, effective ~39
        assert r.destination == "flash"
        assert r.label == "default"

    def test_l3_non_search_results_go_pro(self):
        """Same bulk from a non-search tool (Read) still crosses."""
        body = {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 500},
            ]},
        ]}
        r = _resolve("auto", body, threshold=100)
        assert r.destination == "pro"
        assert r.label == "longContext"

    def test_l3_discount_one_restores_raw_comparison(self):
        """Weight 1.0 = feature off: search results count in full."""
        body = {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Grep", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 500},
            ]},
        ]}
        r = _resolve("auto", body, threshold=100, search_discount=1.0)
        assert r.destination == "pro"
        assert r.label == "longContext"

    def test_l3_monotone_across_session(self):
        """Append-only history: effective tokens never drop, and the
        destination flips flash -> pro at most once (no flip-flop)."""
        tool_call = {"type": "tool_use", "id": "t1", "name": "Grep", "input": {}}
        search_result = {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 400}
        effs = []
        dests = []
        for i in range(1, 6):
            body = {"messages": [
                {"role": "assistant", "content": [tool_call]},
                {"role": "user", "content": [search_result] * i},
            ]}
            feat = extract("anthropic", body)
            effs.append(effective_tokens(feat.token_count, feat.file_search_tokens))
            dests.append(_resolve("auto", body, threshold=100).destination)
        assert effs == sorted(effs)
        assert sum(1 for a, b in zip(dests, dests[1:]) if a != b) <= 1
        if "pro" in dests:  # once pro, always pro
            assert set(dests[dests.index("pro"):]) == {"pro"}

    def test_l3_image_goes_pro(self):
        body = {"messages": [{"content": [{"type": "image", "data": "x"}]}]}
        r = _resolve("auto", body)
        assert r.destination == "pro"
        assert r.label == "image"

    def test_l3_default_goes_flash(self):
        body = {"messages": [{"content": "short question"}]}
        r = _resolve("auto", body)
        assert r.destination == "flash"
        assert r.label == "default"

    def test_l3_no_model_defaults_flash(self):
        body = {"messages": [{"content": "hi"}]}
        r = _resolve(None, body)
        assert r.destination == "flash"

    def test_l3_no_messages_defaults_flash(self):
        body = {}
        r = _resolve("auto", body)
        assert r.destination == "flash"

    # Priority: L1 > L2 > L3

    def test_priority_web_search_over_l2(self):
        # model=flash would go L2 flash, but L1 web_search wins
        body = {"messages": [{"content": "search"}], "tools": [{"name": "web_search_20250813"}]}
        r = _resolve("flash", body)
        assert r.destination == "pro"
        assert r.label == "webSearch"

    def test_priority_think_over_l3(self):
        # model=think -> L2 pro, even if short (L3 would also be pro)
        body = {"messages": [{"content": "hi"}]}
        r = _resolve("think", body)
        assert r.label == "think"


class TestResolveEditCheckpoint:
    """L4: the turn after the trailing batch changed code goes to pro
    (below L3). Every other phase is the flash default."""

    @staticmethod
    def _body(tool_name, result="x"):
        return {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": tool_name, "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": result},
            ]},
        ]}

    def test_edit_phase_goes_pro(self):
        r = _resolve("auto", self._body("Edit"))
        assert r.destination == "pro"
        assert r.label == "toolEdit"

    def test_long_context_beats_edit_checkpoint(self):
        """Above the threshold the session stays pro no matter what tool just
        ran: flash's capability ceiling and the one-way session invariant win."""
        body = {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Edit", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 500},
            ]},
        ]}
        r = _resolve("auto", body, threshold=100)
        assert r.destination == "pro"
        assert r.label == "longContext"

    def test_web_search_beats_edit_pro(self):
        body = self._body("Write")
        body["tools"] = [{"name": "web_search_20250813"}]
        r = _resolve("auto", body)
        assert r.label == "webSearch"

    @pytest.mark.parametrize("tool_name", ["Read", "Grep", "Glob", "TodoWrite", "Task"])
    def test_non_edit_phase_falls_through(self, tool_name):
        """Search/mechanical turns are the default flash route — no rule."""
        r = _resolve("auto", self._body(tool_name))
        assert r.destination == "flash"
        assert r.label == "default"

    def test_null_disables_rule(self):
        r = _resolve("auto", self._body("Edit"), tool_edit_dest=None)
        assert r.label == "default"

    def test_edit_dest_can_be_flash(self):
        r = _resolve("auto", self._body("Edit"), tool_edit_dest="flash")
        assert r.destination == "flash"
        assert r.label == "toolEdit"


class TestTrailingBatch:
    def test_anthropic_batch_any_edit_marks_it(self):
        """A parallel batch is "edit" when any call in it changed code,
        regardless of order — an edit matters more than the grep next to it."""
        for names in (("Grep", "Edit"), ("Edit", "Grep")):
            r = extract("anthropic", {"messages": [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": names[0], "input": {}},
                    {"type": "tool_use", "id": "t2", "name": names[1], "input": {}},
                ]},
            ]})
            assert r.last_tools == tuple(n.lower() for n in names)
            assert r.last_phase == "edit"

    def test_anthropic_batch_without_edit_is_blank(self):
        r = extract("anthropic", {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "TodoWrite", "input": {}},
                {"type": "tool_use", "id": "t2", "name": "Grep", "input": {}},
            ]},
        ]})
        assert r.last_phase == ""

    def test_anthropic_later_batch_replaces_earlier(self):
        r = extract("anthropic", {"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Edit", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t2", "name": "Grep", "input": {}},
            ]},
        ]})
        assert r.last_tools == ("grep",)
        assert r.last_phase == ""

    def test_anthropic_none(self):
        r = extract("anthropic", {"messages": [{"content": "hi"}]})
        assert r.last_tools == ()
        assert r.last_phase == ""

    def test_openai_chat_batch(self):
        r = extract("openai-chat", {"messages": [
            {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "grep", "arguments": "{}"}},
                {"id": "c2", "function": {"name": "write", "arguments": "{}"}},
            ]},
        ]})
        assert r.last_tools == ("grep", "write")
        assert r.last_phase == "edit"

    def test_openai_responses_run_and_outputs(self):
        r = extract("openai-responses", {"input": [
            {"type": "function_call", "call_id": "c1", "name": "Glob", "arguments": "{}"},
            {"type": "function_call", "call_id": "c2", "name": "Task", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": "files"},
            {"type": "function_call_output", "call_id": "c2", "output": "spawned"},
        ]})
        assert r.last_tools == ("glob", "task")
        assert r.last_phase == ""   # no edit-class call in the run

    def test_responses_shell_wrapped_search_and_edit(self):
        """codex wraps tools in exec_command; only the command's edit-ness
        sets the phase (search commands still feed the L3 discount, tested
        elsewhere)."""
        search = extract("openai-responses", {"input": [
            {"type": "function_call", "call_id": "c1", "name": "exec_command",
             "arguments": json.dumps({"cmd": "echo hi; rg -n needle ."})},
        ]})
        assert search.last_phase == ""
        edit = extract("openai-responses", {"input": [
            {"type": "function_call", "call_id": "c1", "name": "exec_command",
             "arguments": json.dumps({"cmd": "cd src && apply_patch <<'EOF'"})},
        ]})
        assert edit.last_phase == "edit"
        neither = extract("openai-responses", {"input": [
            {"type": "function_call", "call_id": "c1", "name": "exec_command",
             "arguments": json.dumps({"cmd": "cargo build --release"})},
        ]})
        assert neither.last_phase == ""


class TestResolveAcrossProtocols:
    """The resolve() pipeline is signal-based: identical decisions for
    equivalent openai-chat / openai-responses bodies."""

    @pytest.mark.parametrize("protocol,body", [
        ("anthropic", {"messages": [{"content": "hi"}]}),
        ("openai-chat", {"messages": [{"role": "user", "content": "hi"}]}),
        ("openai-responses", {"input": "hi"}),
    ])
    def test_short_defaults_flash(self, protocol, body):
        r = resolve("auto", extract(protocol, body), _cfg(), "flash", "think", 8)
        assert r.destination == "flash"
        assert r.label == "default"

    @pytest.mark.parametrize("protocol,body", [
        ("anthropic", {"messages": [{"content": "x" * 200}]}),
        ("openai-chat", {"messages": [{"role": "user", "content": "x" * 200}]}),
        ("openai-responses", {"input": "x" * 200}),
    ])
    def test_long_goes_pro(self, protocol, body):
        r = resolve("auto", extract(protocol, body), _cfg(), "flash", "think", 8)
        assert r.destination == "pro"
        assert r.label == "longContext"

    def test_web_search_disabled_does_not_force_pro(self):
        body = {"input": "hi", "tools": [{"type": "web_search", "external_web_access": False}]}
        # threshold 100: tool definitions now count toward tokens, keep L3 out of the way
        r = resolve("auto", extract("openai-responses", body), _cfg(), "flash", "think", 100)
        assert r.destination == "flash"
        assert r.label == "default"
