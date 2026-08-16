"""Tests for awerouter.router and per-protocol signal extraction."""

import pytest

from awerouter.protocols import estimate_tokens, extract
from awerouter.router import resolve
from awerouter.types import Destination


def _cfg():
    return {
        "flash": Destination("stepfun", "step-3.5-flash"),
        "pro": Destination("anthropic", "claude-opus-5"),
    }


def _resolve(model, body, threshold=32000, web_search_model="pro"):
    return resolve(model, extract("anthropic", body), _cfg(), "flash", "think", threshold, web_search_model)


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
        """reasoning / function_call items carry no message text; no content -> skipped."""
        r = extract("openai-responses", {"input": [
            {"type": "reasoning", "summary": []},
            {"type": "function_call", "name": "f", "arguments": "{}"},
            {"type": "function_call_output", "output": "result"},
            {"role": "user", "content": "hello"},
        ]})
        assert r.message_count == 1
        assert r.token_count == 1  # only "hello" (5 chars / 4)

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


def test_extract_unknown_protocol_raises():
    with pytest.raises(ValueError):
        extract("nope", {})


# ---------------------------------------------------------------------------
# Three-layer routing (signals via anthropic bodies; L-logic is protocol-blind)
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
        r = resolve("auto", extract("openai-responses", body), _cfg(), "flash", "think", 8)
        assert r.destination == "flash"
        assert r.label == "default"
