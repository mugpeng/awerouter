"""Tests for awerouter.router."""

import pytest

from awerouter.router import (
    _estimate_tokens,
    _extract_text,
    _has_image,
    _has_web_search,
    inspect,
    resolve,
)
from awerouter.types import Destination, Provider


def _cfg():
    return {
        "flash": Destination("stepfun", "step-3.5-flash", Provider("stepfun", "http://x", "k")),
        "pro": Destination("anthropic", "claude-opus-5", Provider("anthropic", "http://x", "k", "x-api-key")),
    }


def _resolve(model, body, threshold=32000, web_search_model="pro"):
    return resolve(model, body, _cfg(), "flash", "think", threshold, web_search_model)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

class TestInspect:
    def test_empty_messages(self):
        r = inspect({"messages": []})
        assert r.message_count == 0
        assert r.token_count == 0
        assert not r.has_image
        assert not r.has_web_search

    def test_text_extraction(self):
        r = inspect({"messages": [{"content": "hello world"}]})
        assert r.token_count >= 1

    def test_multilingual(self):
        r = inspect({"messages": [{"content": "你好世界"}]})
        assert r.token_count >= 1

    def test_has_image_false(self):
        r = inspect({"messages": [{"content": "look at this"}]})
        assert r.has_image is False

    def test_has_image_true(self):
        r = inspect({"messages": [{"content": [{"type": "image", "data": "xyz"}]}]})
        assert r.has_image is True

    def test_has_web_search_false(self):
        r = inspect({"messages": [], "tools": [{"name": "bash", "description": "x"}]})
        assert r.has_web_search is False

    def test_has_web_search_true(self):
        r = inspect({"messages": [], "tools": [{"name": "web_search_20250813", "description": "x"}]})
        assert r.has_web_search is True

    def test_no_messages_key(self):
        r = inspect({})
        assert r.message_count == 0
        assert r.token_count == 0
        assert not r.has_image
        assert not r.has_web_search


# ---------------------------------------------------------------------------
# Three-layer routing
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
        # model=c1/flash would go L2 flash, but L1 web_search wins
        body = {"messages": [{"content": "search"}], "tools": [{"name": "web_search_20250813"}]}
        r = _resolve("flash", body)
        assert r.destination == "pro"
        assert r.label == "webSearch"

    def test_priority_think_over_l3(self):
        # model=c1/think -> L2 pro, even if short (L3 would also be pro)
        body = {"messages": [{"content": "hi"}]}
        r = _resolve("think", body)
        assert r.label == "think"
