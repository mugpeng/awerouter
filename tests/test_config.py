"""Tests for awerouter.config."""

import json

import pytest

from awerouter.config import (
    _parse_destination,
    available_templates,
    detect_auth_header,
    expand_value,
    format_providers_display,
    format_routing_display,
    init_config,
    is_loopback_url,
    load_default_profile,
    load_for_profile,
    load_providers,
    load_routing,
    merge_config,
    redact,
    save_provider,
    validate_profiles,
)
from awerouter.types import AwecompressConfig, Destination, OdcpConfig, Provider, RoutingProfile, Settings, ToolRoutingConfig


# ---------------------------------------------------------------------------
# detect_auth_header
# ---------------------------------------------------------------------------

class TestDetectAuthHeader:
    def test_anthropic(self):
        assert detect_auth_header("https://api.anthropic.com") == "x-api-key"

    def test_anthropic_subpath(self):
        assert detect_auth_header("https://api.anthropic.com/v1/messages") == "x-api-key"

    def test_stepfun(self):
        assert detect_auth_header("https://api.stepfun.com/step_plan") == "authorization"

    def test_other(self):
        assert detect_auth_header("https://open.bigmodel.cn/api/anthropic") == "authorization"

    def test_evil_subpath_not_anthropic(self):
        """Substring in path must not trigger x-api-key detection."""
        assert detect_auth_header("https://evil.com/anthropic.com/proxy") == "authorization"

    def test_anthropic_subdomain(self):
        assert detect_auth_header("https://api.anthropic.com") == "x-api-key"


# ---------------------------------------------------------------------------
# is_loopback_url
# ---------------------------------------------------------------------------

class TestIsLoopbackUrl:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://localhost",
        "http://[::1]:8080",
        "http://127.5.6.7",          # whole 127.0.0.0/8 is loopback
    ])
    def test_loopback(self, url):
        assert is_loopback_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://api.anthropic.com",
        "https://api.stepfun.com/step_plan",
        "http://192.168.1.10:8000",  # LAN vLLM is off-machine (no auth there = warning)
        "http://evil.com/127.0.0.1",
        "http://127.0.0.1.evil.com",
    ])
    def test_off_machine(self, url):
        assert is_loopback_url(url) is False


# ---------------------------------------------------------------------------
# expand_value / redact
# ---------------------------------------------------------------------------

class TestExpandValue:
    def test_no_expansion(self):
        assert expand_value("plain", {}) == "plain"

    def test_expand_existing(self):
        assert expand_value("${FOO}", {"FOO": "bar"}) == "bar"

    def test_expand_missing_dies(self):
        with pytest.raises(SystemExit):
            expand_value("${MISSING}", {})

    def test_non_string_passthrough(self):
        assert expand_value(42, {}) == 42
        assert expand_value(None, {}) is None

    def test_multiple_refs(self):
        assert expand_value("${A}_${B}", {"A": "x", "B": "y"}) == "x_y"


class TestRedact:
    def test_redacts_secret_keys(self):
        data = {"api_key": "secret123", "name": "ok"}
        r = redact(data)
        assert r["api_key"] == "<redacted>"
        assert r["name"] == "ok"

    def test_nested(self):
        data = {"outer": {"auth_token": "t", "safe": "v"}}
        r = redact(data)
        assert r["outer"]["auth_token"] == "<redacted>"


# ---------------------------------------------------------------------------
# _parse_destination
# ---------------------------------------------------------------------------

class TestParseDestination:
    def test_valid(self):
        d = _parse_destination("stepfun,step-3.5-flash")
        assert d.provider_name == "stepfun"
        assert d.model == "step-3.5-flash"

    def test_spaces(self):
        d = _parse_destination(" anthropic , claude-opus-5 ")
        assert d.provider_name == "anthropic"
        assert d.model == "claude-opus-5"

    def test_missing_provider_dies(self):
        with pytest.raises(SystemExit):
            _parse_destination(",model")

    def test_missing_model_dies(self):
        with pytest.raises(SystemExit):
            _parse_destination("provider,")


# ---------------------------------------------------------------------------
# File-based helpers
# ---------------------------------------------------------------------------

def _write_config(tmp_path, providers, routing):
    (tmp_path / "providers.json").write_text(json.dumps(providers))
    (tmp_path / "routing.json").write_text(json.dumps(routing))


# Sentinel for "JSON key absent" in parametrized no-auth tests.
_ABSENT = object()


# ---------------------------------------------------------------------------
# load_providers (nested by protocol)
# ---------------------------------------------------------------------------

class TestLoadProviders:
    def test_nested_by_protocol(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "anthropic": {"p": {"base_url": "https://api.stepfun.com/step_plan", "auth": "${K}"}},
            "openai-chat":  {"p": {"base_url": "https://api.stepfun.com/step_plan/v1", "auth": "${K}"}},
        }, {})
        result = load_providers()
        assert "anthropic" in result and "openai-chat" in result
        assert result["anthropic"]["p"].auth_header == "authorization"

    def test_anthropic_auto_detects_x_api_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "anthropic": {"anthropic": {"base_url": "https://api.anthropic.com", "auth": "${K}"}},
        }, {})
        result = load_providers()
        assert result["anthropic"]["anthropic"].auth_header == "x-api-key"

    def test_explicit_auth_header_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "anthropic": {"p": {"base_url": "https://x", "auth": "${K}", "auth_header": "x-api-key"}},
        }, {})
        result = load_providers()
        assert result["anthropic"]["p"].auth_header == "x-api-key"

    def test_missing_base_url_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {"p": {"auth": "${K}"}}}, {})
        with pytest.raises(SystemExit, match="missing base_url"):
            load_providers()

    @pytest.mark.parametrize("auth", [_ABSENT, None, ""])
    def test_missing_auth_allowed(self, tmp_path, monkeypatch, auth):
        """auth absent/null/empty = no-auth upstream (local model servers)."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        entry = {"base_url": "http://127.0.0.1:11434"}
        if auth is not _ABSENT:
            entry["auth"] = auth
        _write_config(tmp_path, {"anthropic": {"ollama": entry}}, {})
        result = load_providers()
        assert result["anthropic"]["ollama"].auth is None

    def test_old_agent_group_dies_with_rename_hint(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"claude": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {})
        with pytest.raises(SystemExit, match="anthropic"):
            load_providers()

    def test_unknown_protocol_group_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"nope": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {})
        with pytest.raises(SystemExit, match="protocol id"):
            load_providers()

    def test_codex_sentinel_loads_in_responses_group(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"openai-responses": {
            "codex": {"base_url": "https://chatgpt.com/backend-api/codex", "auth": "codex"},
        }}, {})
        result = load_providers()
        assert result["openai-responses"]["codex"].auth == "codex"

    def test_codex_sentinel_in_other_group_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {
            "codex": {"base_url": "https://chatgpt.com/backend-api/codex", "auth": "codex"},
        }}, {})
        with pytest.raises(SystemExit, match="openai-responses"):
            load_providers()

    def test_claude_sentinel_loads_in_anthropic_group(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {
            "claude": {"base_url": "https://api.anthropic.com", "auth": "claude"},
        }}, {})
        result = load_providers()
        assert result["anthropic"]["claude"].auth == "claude"

    def test_claude_sentinel_in_other_group_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"openai-chat": {
            "claude": {"base_url": "https://api.anthropic.com/v1", "auth": "claude"},
        }}, {})
        with pytest.raises(SystemExit, match="anthropic"):
            load_providers()

    def test_models_list_parsed(self, tmp_path, monkeypatch):
        """A provider may declare the models it can directly serve."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {
            "stepfun": {"base_url": "https://x", "auth": "${K}",
                        "models": ["step-3.7-flash", "step-router-v1"]},
            "glm": {"base_url": "https://y", "auth": "${K}"},
        }}, {})
        result = load_providers()
        step = result["anthropic"]["stepfun"]
        assert step.models == ("step-3.7-flash", "step-router-v1")
        assert result["anthropic"]["glm"].models == ()

    def test_models_must_be_list_of_strings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {
            "p": {"base_url": "https://x", "auth": "${K}", "models": "step-3.7-flash"},
        }}, {})
        with pytest.raises(SystemExit, match="models"):
            load_providers()

    def test_models_empty_entry_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {
            "p": {"base_url": "https://x", "auth": "${K}", "models": [""]},
        }}, {})
        with pytest.raises(SystemExit, match="models"):
            load_providers()


# ---------------------------------------------------------------------------
# load_routing (settings + profiles)
# ---------------------------------------------------------------------------

class TestLoadRouting:
    def test_settings_defaults_when_absent(self, tmp_path, monkeypatch):
        """settings block is optional; defaults are flash/pro."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {
                "protocol": "anthropic", "longContextThreshold": 8000,
                "destinations": {"flash": "p,m1", "pro": "p,m2"},
            },
        })
        settings, profiles = load_routing()
        assert settings.background_model == "flash"
        assert settings.think_model == "pro"
        assert "cc-1" in profiles

    def test_settings_explicit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"backgroundModel": "bg", "thinkModel": "strong", "webSearchModel": "flash"},
            "cc-1": {
                "protocol": "anthropic", "longContextThreshold": 1,
                "destinations": {"flash": "p,m", "pro": "p,m"},
            },
        })
        settings, _ = load_routing()
        assert settings.background_model == "bg"
        assert settings.think_model == "strong"
        assert settings.web_search_model == "flash"

    def test_settings_search_discount_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        assert load_routing()[0].search_result_discount == 0.3

    def test_settings_search_discount_explicit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"searchResultDiscount": 0.5},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        assert load_routing()[0].search_result_discount == 0.5

    @pytest.mark.parametrize("bad", ["fast", -0.1, 1.5])
    def test_settings_search_discount_invalid_dies(self, tmp_path, monkeypatch, bad):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"searchResultDiscount": bad},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="searchResultDiscount"):
            load_routing()

    def test_settings_long_context_auto_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        cfg = load_routing()[0].long_context_auto
        assert (cfg.percentile, cfg.window_days, cfg.min_samples, cfg.fallback_threshold) == (95, 7, 50, 8000)

    def test_settings_long_context_auto_explicit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"longContextAuto": {
                "percentile": 90, "windowDays": 14, "minSamples": 20, "fallbackThreshold": 4000,
            }},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        cfg = load_routing()[0].long_context_auto
        assert (cfg.percentile, cfg.window_days, cfg.min_samples, cfg.fallback_threshold) == (90, 14, 20, 4000)

    def test_settings_long_context_auto_partial(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"longContextAuto": {"percentile": 99}},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        cfg = load_routing()[0].long_context_auto
        assert cfg.percentile == 99
        assert cfg.fallback_threshold == 8000

    def test_settings_tool_routing_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        tr = load_routing()[0].tool_routing
        assert (tr.web_search, tr.edit) == (None, "pro")

    def test_settings_tool_routing_null_disables(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"toolRouting": {"webSearch": "flash", "edit": None}},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        tr = load_routing()[0].tool_routing
        assert (tr.web_search, tr.edit) == ("flash", None)

    def test_settings_tool_routing_invalid_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"toolRouting": {"edit": "turbo"}},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="toolRouting"):
            load_routing()

    @pytest.mark.parametrize("removed_key", ["search", "mechanical"])
    def test_settings_tool_routing_removed_keys_die(self, tmp_path, monkeypatch, removed_key):
        """search/mechanical were removed in v0.4.8: they defaulted to flash,
        which is already the fall-through — old configs must fail loudly."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"toolRouting": {removed_key: "flash"}},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="removed"):
            load_routing()

    def test_settings_websearch_toolrouting_overrides_legacy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"webSearchModel": "pro", "toolRouting": {"webSearch": "flash"}},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        assert load_routing()[0].tool_routing.web_search == "flash"

    def test_settings_websearch_absent_falls_back_to_legacy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"webSearchModel": "flash"},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        settings = load_routing()[0]
        assert settings.web_search_model == "flash"
        assert settings.tool_routing.web_search is None

    def test_settings_websearch_model_invalid_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"webSearchModel": "turbo"},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="webSearchModel"):
            load_routing()

    def test_settings_image_default_models(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"imageModel": "flash", "defaultModel": "pro"},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        settings = load_routing()[0]
        assert settings.image_model == "flash"
        assert settings.default_model == "pro"

    def test_settings_image_default_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        settings = load_routing()[0]
        assert settings.image_model == "pro"
        assert settings.default_model == "flash"

    @pytest.mark.parametrize("key", ["imageModel", "defaultModel"])
    def test_settings_image_default_invalid_dies(self, tmp_path, monkeypatch, key):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {key: "turbo"},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match=key):
            load_routing()

    @pytest.mark.parametrize("key,bad", [
        ("percentile", 0), ("percentile", 100), ("percentile", "95"), ("percentile", True),
        ("windowDays", 0), ("minSamples", 0), ("fallbackThreshold", -1),
    ])
    def test_settings_long_context_auto_invalid_dies(self, tmp_path, monkeypatch, key, bad):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"longContextAuto": {key: bad}},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="longContextAuto"):
            load_routing()

    def test_settings_image_bridge_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"imageBridge": True},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        assert load_routing()[0].image_bridge is True

    def test_settings_image_bridge_defaults_off(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        assert load_routing()[0].image_bridge is False

    @pytest.mark.parametrize("bad", ["yes", 1, "true"])
    def test_settings_image_bridge_non_bool_dies(self, tmp_path, monkeypatch, bad):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"imageBridge": bad},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="'imageBridge'"):
            load_routing()

    def test_settings_long_context_auto_not_object_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"longContextAuto": 5},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="'longContextAuto' must be an object"):
            load_routing()

    def test_threshold_auto_marks_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": "auto",
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        _, profiles = load_routing()
        assert profiles["cc-1"].threshold_auto is True
        # before serve resolves it, reads see the configured fallback
        assert profiles["cc-1"].long_context_threshold == 8000

    def test_threshold_auto_placeholder_is_configured_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"longContextAuto": {"fallbackThreshold": 4000}},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": "auto",
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        _, profiles = load_routing()
        assert profiles["cc-1"].long_context_threshold == 4000

    @pytest.mark.parametrize("bad", ["fast", -5])
    def test_threshold_invalid_dies(self, tmp_path, monkeypatch, bad):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": bad,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="longContextThreshold"):
            load_routing()

    def test_threshold_numeric_string_still_parses(self, tmp_path, monkeypatch):
        """Pre-existing leniency: a numeric string keeps working."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": "8000",
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        _, profiles = load_routing()
        assert profiles["cc-1"].long_context_threshold == 8000
        assert profiles["cc-1"].threshold_auto is False

    def test_multiple_profiles(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
            "cx-1": {"protocol": "openai-chat",  "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        _, profiles = load_routing()
        assert set(profiles) == {"cc-1", "cx-1"}
        assert profiles["cc-1"].protocol == "anthropic"

    def test_missing_protocol_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="protocol"):
            load_routing()

    def test_old_agent_field_dies_with_rename_hint(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"agent": "claude", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="renamed to 'protocol'"):
            load_routing()

    def test_unknown_protocol_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "nope", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="unknown protocol"):
            load_routing()

    def test_protocol_list_parses(self, tmp_path, monkeypatch):
        """A protocol list serves several wire protocols on one port."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": ["anthropic", "openai-chat"], "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        _, profiles = load_routing()
        assert profiles["cc-1"].protocols == ("anthropic", "openai-chat")
        assert profiles["cc-1"].protocol == "anthropic+openai-chat"

    def test_protocol_string_normalizes_to_one_tuple(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        _, profiles = load_routing()
        assert profiles["cc-1"].protocols == ("anthropic",)
        assert profiles["cc-1"].protocol == "anthropic"

    def test_protocol_list_duplicate_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": ["anthropic", "anthropic"], "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="more than once"):
            load_routing()

    def test_protocol_list_unknown_entry_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": ["anthropic", "nope"], "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="unknown protocol 'nope'"):
            load_routing()

    def test_protocol_empty_list_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": [], "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="'protocol' must be"):
            load_routing()

    def test_profile_no_longer_needs_background_think(self, tmp_path, monkeypatch):
        """backgroundModel/thinkModel moved to settings — profile omits them."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        _, profiles = load_routing()
        assert not hasattr(profiles["cc-1"], "background_model")

    def test_port_parsed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}, "port": 20129},
        })
        _, profiles = load_routing()
        assert profiles["cc-1"].port == 20129

    def test_port_optional_defaults_to_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        _, profiles = load_routing()
        assert profiles["cc-1"].port is None

    def test_port_out_of_range_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        for bad in (0, 65536):
            _write_config(tmp_path, {}, {
                "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                         "destinations": {"flash": "p,m", "pro": "p,m"}, "port": bad},
            })
            with pytest.raises(SystemExit, match="'port'"):
                load_routing()

    def test_port_non_int_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        for bad in ("20129", 1.5, True):
            _write_config(tmp_path, {}, {
                "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                         "destinations": {"flash": "p,m", "pro": "p,m"}, "port": bad},
            })
            with pytest.raises(SystemExit, match="'port'"):
                load_routing()


# ---------------------------------------------------------------------------
# load_for_profile / load_default_profile
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# per-profile settings overrides
# ---------------------------------------------------------------------------

class TestProfileSettings:
    """Settings keys may sit directly in a profile body (flat, next to
    protocol/destinations); they override the global block key by key, and
    missing keys (nested fields included) inherit from it."""

    def test_absent_keys_inherit_global(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"backgroundModel": "bg", "thinkModel": "strong",
                         "imageModel": "flash"},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        settings, profiles = load_routing()
        p = profiles["cc-1"].settings
        assert p.background_model == "bg"
        assert p.think_model == "strong"
        assert p.image_model == "flash"
        assert p.default_model == "flash"  # unset everywhere -> type default

    def test_override_image_default_models(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"imageModel": "pro", "defaultModel": "flash"},
            "plain": {"protocol": "anthropic", "longContextThreshold": 1,
                      "destinations": {"flash": "p,m", "pro": "p,m"}},
            "mm": {"protocol": "anthropic", "longContextThreshold": 1,
                   "destinations": {"flash": "p,m", "pro": "p,m"},
                   "imageModel": "flash", "defaultModel": "pro"},
        })
        settings, profiles = load_routing()
        assert settings.image_model == "pro"          # global untouched
        assert settings.default_model == "flash"
        assert profiles["plain"].settings.image_model == "pro"
        assert profiles["mm"].settings.image_model == "flash"
        assert profiles["mm"].settings.default_model == "pro"

    def test_override_image_bridge(self, tmp_path, monkeypatch):
        """A flat 'imageBridge' in the profile body overrides the global off."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"imageBridge": False},
            "plain": {"protocol": "anthropic", "longContextThreshold": 1,
                      "destinations": {"flash": "p,m", "pro": "p,m"}},
            "mm": {"protocol": "anthropic", "longContextThreshold": 1,
                   "destinations": {"flash": "p,m", "pro": "p,m"},
                   "imageBridge": True},
        })
        settings, profiles = load_routing()
        assert settings.image_bridge is False        # global untouched
        assert profiles["plain"].settings.image_bridge is False
        assert profiles["mm"].settings.image_bridge is True

    def test_override_background_think_tier_labels(self, tmp_path, monkeypatch):
        """backgroundModel/thinkModel in a profile body parse as overrides —
        the pre-v0.5.2 per-profile shape, now honored again."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"backgroundModel": "flash", "thinkModel": "pro"},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"},
                     "backgroundModel": "c1/flash", "thinkModel": "c1/think"},
        })
        p = load_routing()[1]["cc-1"].settings
        assert p.background_model == "c1/flash"
        assert p.think_model == "c1/think"

    def test_nested_tool_routing_inherits_field_by_field(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"toolRouting": {"webSearch": "flash", "edit": "pro"}},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"},
                     "toolRouting": {"edit": None}},
        })
        tr = load_routing()[1]["cc-1"].settings.tool_routing
        assert tr.web_search == "flash"  # inherited from global
        assert tr.edit is None           # overridden to disabled

    def test_long_context_auto_partial_inherit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"longContextAuto": {"percentile": 90, "windowDays": 14}},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"},
                     "longContextAuto": {"percentile": 99}},
        })
        cfg = load_routing()[1]["cc-1"].settings.long_context_auto
        assert cfg.percentile == 99
        assert cfg.window_days == 14    # inherited
        assert cfg.min_samples == 50    # unset everywhere -> default

    def test_threshold_auto_uses_profile_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": "auto",
                     "destinations": {"flash": "p,m", "pro": "p,m"},
                     "longContextAuto": {"fallbackThreshold": 4000}},
        })
        _, profiles = load_routing()
        assert profiles["cc-1"].long_context_threshold == 4000

    def test_legacy_websearch_model_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"webSearchModel": "pro"},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"},
                     "webSearchModel": "flash"},
        })
        p = load_routing()[1]["cc-1"].settings
        assert p.web_search_model == "flash"
        assert p.tool_routing.web_search is None  # legacy fallback still applies

    def test_search_discount_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"searchResultDiscount": 0.3},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"},
                     "searchResultDiscount": 0.5},
        })
        settings, profiles = load_routing()
        assert settings.search_result_discount == 0.3
        assert profiles["cc-1"].settings.search_result_discount == 0.5

    def test_unknown_profile_key_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"},
                     "imageModle": "flash"},
        })
        with pytest.raises(SystemExit, match="profile 'cc-1'.*unknown key.*imageModle"):
            load_routing()

    def test_nested_settings_block_in_profile_dies(self, tmp_path, monkeypatch):
        """Overrides are flat in the profile body — a nested `settings` key
        (the never-released intermediate shape) dies as an unknown key."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"},
                     "settings": {"imageModel": "flash"}},
        })
        with pytest.raises(SystemExit, match="unknown key.*'settings'"):
            load_routing()

    def test_unknown_key_in_global_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "settings": {"bogus": 1},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="unknown key.*bogus"):
            load_routing()

    @pytest.mark.parametrize("key,bad", [
        ("imageModel", "turbo"), ("defaultModel", "turbo"), ("webSearchModel", "turbo"),
        ("searchResultDiscount", 2), ("toolRouting", {"edit": "turbo"}),
        ("longContextAuto", {"percentile": 0}),
    ])
    def test_invalid_value_dies_naming_profile(self, tmp_path, monkeypatch, key, bad):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"},
                     key: bad},
        })
        with pytest.raises(SystemExit, match=f"cc-1.*{key}"):
            load_routing()

    def test_load_for_profile_returns_effective(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {
            "settings": {"defaultModel": "flash"},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m1", "pro": "p,m2"},
                     "defaultModel": "pro"},
        })
        providers, profile, settings = load_for_profile("cc-1")
        assert settings.default_model == "pro"
        assert settings is profile.settings


class TestLoadForProfile:
    def test_returns_settings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {
            "settings": {"backgroundModel": "bg", "thinkModel": "strong"},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m1", "pro": "p,m2"}},
        })
        providers, profile, settings = load_for_profile("cc-1")
        assert settings.background_model == "bg"
        assert profile.destinations["flash"].provider_name == "p"

    def test_unknown_profile_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {}}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="not found"):
            load_for_profile("nope")

    def test_dest_provider_missing_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "nonexistent,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="provider 'nonexistent'"):
            load_for_profile("cc-1")

    def test_protocol_missing_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {
            "cc-1": {"protocol": "openai-chat", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="protocol 'openai-chat'"):
            load_for_profile("cc-1")

    def test_multi_protocol_returns_grouped_providers(self, tmp_path, monkeypatch):
        """load_for_profile hands back one provider group per served protocol."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "anthropic": {"p": {"base_url": "https://a", "auth": "${K}"}},
            "openai-chat": {"p": {"base_url": "https://a/v1", "auth": "${K}"}},
        }, {
            "cc-1": {"protocol": ["anthropic", "openai-chat"], "longContextThreshold": 1,
                     "destinations": {"flash": "p,m1", "pro": "p,m2"}},
        })
        providers, profile, _ = load_for_profile("cc-1")
        assert set(providers) == {"anthropic", "openai-chat"}
        assert providers["anthropic"]["p"].base_url == "https://a"
        assert providers["openai-chat"]["p"].base_url == "https://a/v1"
        assert profile.protocols == ("anthropic", "openai-chat")

    def test_multi_protocol_dest_missing_in_one_group_dies(self, tmp_path, monkeypatch):
        """Every destination must resolve in every served group — a name present
        in one protocol's group but absent in another's is a config error."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "anthropic": {"p": {"base_url": "https://a", "auth": "${K}"}, "q": {"base_url": "https://b", "auth": "${K}"}},
            "openai-chat": {"p": {"base_url": "https://a/v1", "auth": "${K}"}},
        }, {
            "cc-1": {"protocol": ["anthropic", "openai-chat"], "longContextThreshold": 1,
                     "destinations": {"flash": "q,m1", "pro": "p,m2"}},
        })
        with pytest.raises(SystemExit, match="'openai-chat' group"):
            load_for_profile("cc-1")


class TestValidateProfiles:
    def _providers(self):
        return {"anthropic": {"p": Provider("p", "https://x", "${K}")}}

    def _profile(self, flash="p,m"):
        return {"cc-1": RoutingProfile("cc-1", "anthropic", 1, {
            "flash": Destination(flash.split(",")[0], flash.split(",")[1]),
            "pro": Destination("p", "m2"),
        })}

    def test_valid_passes(self):
        validate_profiles(self._providers(), self._profile())

    def test_unknown_provider_dies(self):
        with pytest.raises(SystemExit, match="provider 'q'"):
            validate_profiles(self._providers(), self._profile("q,m"))

    def test_unknown_protocol_dies(self):
        profiles = {"cc-1": RoutingProfile("cc-1", "openai-chat", 1, {
            "flash": Destination("p", "m1"), "pro": Destination("p", "m2"),
        })}
        with pytest.raises(SystemExit, match="protocol 'openai-chat'"):
            validate_profiles(self._providers(), profiles)


class TestLoadDefaultProfile:
    def test_single_auto_selects(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        _, profile, settings = load_default_profile()
        assert profile.name == "cc-1"
        assert settings.background_model == "flash"

    def test_multiple_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
            "cc-2": {"protocol": "anthropic", "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="multiple profiles"):
            load_default_profile()


# ---------------------------------------------------------------------------
# save_provider
# ---------------------------------------------------------------------------

class TestSaveProvider:
    def test_noauth_omits_key(self, tmp_path, monkeypatch):
        """Local providers are written without an auth key, not with a fake one."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {}}, {})
        save_provider("anthropic", "ollama", "http://127.0.0.1:11434", None)
        data = json.loads((tmp_path / "providers.json").read_text())
        assert data["anthropic"]["ollama"] == {"base_url": "http://127.0.0.1:11434"}

    def test_auth_round_trips(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {}}, {})
        save_provider("anthropic", "p", "https://x", "${K}")
        data = json.loads((tmp_path / "providers.json").read_text())
        assert data["anthropic"]["p"]["auth"] == "${K}"
        assert load_providers()["anthropic"]["p"].auth == "${K}"


# ---------------------------------------------------------------------------
# init_config
# ---------------------------------------------------------------------------

class TestInitConfig:
    def test_already_exists_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        (tmp_path / "providers.json").write_text("{}")
        (tmp_path / "routing.json").write_text("{}")
        with pytest.raises(SystemExit, match="already exists"):
            init_config()

    def test_default_writes_parseable_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        init_config()
        assert json.loads((tmp_path / "providers.json").read_text())
        assert json.loads((tmp_path / "routing.json").read_text())

    def test_named_template(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        init_config("step-glm")
        routing = json.loads((tmp_path / "routing.json").read_text())
        assert routing["cc-router-1"]["destinations"] == {
            "flash": "stepfun,step-3.7-flash",
            "pro": "glm,glm-5.3",
        }

    def test_unknown_template_lists_available(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        with pytest.raises(SystemExit, match="unknown template 'nope'"):
            init_config("nope")
        assert not (tmp_path / "providers.json").exists()

    @pytest.mark.parametrize("name", available_templates())
    def test_bundled_templates_validate(self, tmp_path, monkeypatch, name):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        init_config(name)
        providers_all = load_providers()
        _, profiles = load_routing()
        validate_profiles(providers_all, profiles)


class TestMergeConfig:
    def _seed_minimal(self, tmp_path, monkeypatch):
        """One provider, one profile, no settings — everything the template
        carries is missing, so a merge should add all of it."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "anthropic": {"stepfun": {"base_url": "https://api.stepfun.com/step_plan",
                                      "auth": "${MINE}"}},
        }, {
            "mine": {"protocol": "anthropic", "longContextThreshold": 8000,
                     "destinations": {"flash": "stepfun,f", "pro": "stepfun,p"}},
        })

    def test_fill_missing_providers_settings_profiles(self, tmp_path, monkeypatch):
        self._seed_minimal(tmp_path, monkeypatch)
        report = merge_config("step-glm-mm")
        assert report["providers_added"] == [
            "anthropic.glm", "openai-chat.stepfun", "openai-chat.glm"]
        assert report["providers_skipped"] == ["anthropic.stepfun"]
        assert report["profiles_added"] == ["cc-router-1"]
        assert report["settings_added"] == ["imageModel", "defaultModel", "imageBridge"]
        assert set(report["behavior_shift"]) == {
            "imageModel=flash", "defaultModel=pro", "imageBridge=True"}
        # existing entry keeps its own base_url/auth
        providers = json.loads((tmp_path / "providers.json").read_text())
        assert providers["anthropic"]["stepfun"]["auth"] == "${MINE}"
        assert providers["anthropic"]["glm"]["base_url"] == "https://open.bigmodel.cn/api/anthropic"
        # merged config loads clean and the new profile serves both protocols
        settings, profiles = load_routing()
        assert settings.image_model == "flash"
        assert settings.default_model == "pro"
        assert profiles["cc-router-1"].protocols == ("anthropic", "openai-chat")
        assert "mine" in profiles

    def test_no_op_merge_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        init_config("step-glm-mm")
        before_p = (tmp_path / "providers.json").read_text()
        before_r = (tmp_path / "routing.json").read_text()
        report = merge_config("step-glm-mm")
        assert not any(report[k] for k in ("providers_added", "profiles_added",
                                           "settings_added", "behavior_shift"))
        assert (tmp_path / "providers.json").read_text() == before_p
        assert (tmp_path / "routing.json").read_text() == before_r
        assert not (tmp_path / "routing.json.bak").exists()

    def test_second_run_is_idempotent(self, tmp_path, monkeypatch):
        self._seed_minimal(tmp_path, monkeypatch)
        merge_config("step-glm-mm")
        after_first = (tmp_path / "routing.json").read_text()
        report = merge_config("step-glm-mm")
        assert not any(report[k] for k in ("providers_added", "profiles_added",
                                           "settings_added"))
        assert (tmp_path / "routing.json").read_text() == after_first

    def test_existing_values_never_overwritten(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "anthropic": {"stepfun": {"base_url": "https://my-proxy.internal/plan",
                                      "auth": "${MINE}"},
                          "glm": {"base_url": "https://glm.internal", "auth": "${GLM_MINE}"}},
            "openai-chat": {"stepfun": {"base_url": "https://sf.internal/v1", "auth": "${MINE}"},
                            "glm": {"base_url": "https://glm.internal/v4", "auth": "${GLM_MINE}"}},
        }, {
            "settings": {"imageModel": "pro", "defaultModel": "flash",
                         "searchResultDiscount": 0.5, "imageBridge": False},
            "cc-router-1": {"protocol": "anthropic", "longContextThreshold": 12345,
                            "destinations": {"flash": "stepfun,f", "pro": "glm,p"}},
        })
        before = (tmp_path / "routing.json").read_text()
        report = merge_config("step-glm-mm")
        assert report["providers_added"] == []
        assert report["profiles_added"] == []
        assert report["settings_added"] == []
        assert report["behavior_shift"] == []
        assert set(report["providers_skipped"]) == {
            "anthropic.stepfun", "anthropic.glm", "openai-chat.stepfun", "openai-chat.glm"}
        assert report["profiles_skipped"] == ["cc-router-1"]
        assert (tmp_path / "routing.json").read_text() == before

    def test_profile_collision_skipped_and_reported(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        init_config("default")
        report = merge_config("step-glm-mm")
        assert report["profiles_skipped"] == ["cc-router-1"]
        routing = json.loads((tmp_path / "routing.json").read_text())
        assert routing["cc-router-1"]["protocol"] == "anthropic"
        assert routing["cc-router-1"]["destinations"]["pro"] == "anthropic,claude-opus-5"

    def test_backup_holds_pre_merge_content(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        init_config("default")
        merge_config("step-glm-mm")
        rbak = json.loads((tmp_path / "routing.json.bak").read_text())
        assert "imageModel" not in rbak["settings"]
        pbak = json.loads((tmp_path / "providers.json.bak").read_text())
        assert "glm" not in pbak["anthropic"]

    def test_non_dict_settings_dies_before_writes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {}}, {"settings": ["nope"]})
        with pytest.raises(SystemExit, match="'settings' must be an object"):
            merge_config("step-glm-mm")
        # providers.json must not carry a half-applied merge
        assert json.loads((tmp_path / "providers.json").read_text()) == {"anthropic": {}}

    def test_unknown_template_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {}}, {})
        with pytest.raises(SystemExit, match="unknown template 'nope'"):
            merge_config("nope")


# ---------------------------------------------------------------------------
# format helpers
# ---------------------------------------------------------------------------

class TestFormatDisplay:
    def test_providers_nested(self):
        all_providers = {
            "anthropic": {"p": Provider("p", "https://x", "${K}")},
        }
        data = json.loads(format_providers_display(all_providers))
        assert data["anthropic"]["p"]["auth"] == "${K}"

    def test_providers_noauth_shows_null(self):
        all_providers = {
            "anthropic": {"ollama": Provider("ollama", "http://127.0.0.1:11434", None)},
        }
        data = json.loads(format_providers_display(all_providers))
        assert data["anthropic"]["ollama"]["auth"] is None

    def test_providers_codex_sentinel_labeled(self):
        all_providers = {
            "openai-responses": {"codex": Provider(
                "codex", "https://chatgpt.com/backend-api/codex", "codex")},
        }
        data = json.loads(format_providers_display(all_providers))
        assert data["openai-responses"]["codex"]["auth"] == "codex (local CLI login)"

    def test_providers_claude_sentinel_labeled(self):
        all_providers = {
            "anthropic": {"claude": Provider(
                "claude", "https://api.anthropic.com", "claude")},
        }
        data = json.loads(format_providers_display(all_providers))
        assert data["anthropic"]["claude"]["auth"] == "claude (subscription OAuth login)"

    def test_routing_shows_settings_and_profiles(self):
        settings = Settings(background_model="flash", think_model="pro", web_search_model="pro")
        profiles = {
            "cc-1": RoutingProfile("cc-1", "anthropic", 8000, {
                "flash": Destination("p", "m1"), "pro": Destination("p", "m2"),
            }),
        }
        data = json.loads(format_routing_display(settings, profiles))
        assert data["settings"]["backgroundModel"] == "flash"
        assert data["settings"]["webSearchModel"] == "pro"
        assert data["settings"]["imageModel"] == "pro"
        assert data["settings"]["defaultModel"] == "flash"
        assert data["cc-1"]["protocol"] == "anthropic"
        assert "backgroundModel" not in data["cc-1"]
        assert "port" not in data["cc-1"]

    def test_routing_shows_port_when_set(self):
        settings = Settings()
        profiles = {
            "cc-1": RoutingProfile("cc-1", "anthropic", 8000, {
                "flash": Destination("p", "m1"), "pro": Destination("p", "m2"),
            }, port=20129),
        }
        data = json.loads(format_routing_display(settings, profiles))
        assert data["cc-1"]["port"] == 20129

    def test_routing_shows_tool_routing(self):
        settings = Settings(web_search_model="pro",
                            tool_routing=ToolRoutingConfig(web_search="flash", edit="pro"))
        profiles = {
            "cc-1": RoutingProfile("cc-1", "anthropic", 8000, {
                "flash": Destination("p", "m1"), "pro": Destination("p", "m2"),
            }),
        }
        data = json.loads(format_routing_display(settings, profiles))
        # webSearch shows the effective value (toolRouting wins over legacy webSearchModel)
        assert data["settings"]["toolRouting"] == {"webSearch": "flash", "edit": "pro"}

    def test_routing_shows_auto_threshold(self):
        settings = Settings()
        profiles = {
            "cc-1": RoutingProfile("cc-1", "anthropic", 8000, {
                "flash": Destination("p", "m1"), "pro": Destination("p", "m2"),
            }, threshold_auto=True),
        }
        data = json.loads(format_routing_display(settings, profiles))
        assert data["cc-1"]["longContextThreshold"] == "auto"
        auto = data["settings"]["longContextAuto"]
        assert auto == {"percentile": 95, "windowDays": 7, "minSamples": 50, "fallbackThreshold": 8000}

    def test_routing_shows_profile_override_keys_flat(self):
        """Profile entries mirror the config shape: overridden settings keys
        sit flat in the entry, next to protocol/destinations."""
        settings = Settings(image_model="pro")
        profiles = {
            "plain": RoutingProfile("plain", "anthropic", 8000, {
                "flash": Destination("p", "m1"), "pro": Destination("p", "m2")}),
            "mm": RoutingProfile("mm", "anthropic", 8000, {
                "flash": Destination("p", "m1"), "pro": Destination("p", "m2")},
                settings=Settings(image_model="flash"),
                settings_overrides={"imageModel": "flash"}),
        }
        data = json.loads(format_routing_display(settings, profiles))
        assert "imageModel" not in data["plain"]
        assert data["mm"]["imageModel"] == "flash"
        # the global block still shows the global value
        assert data["settings"]["imageModel"] == "pro"


# ---------------------------------------------------------------------------
# rtk profile flag
# ---------------------------------------------------------------------------

class TestRtkFlag:
    def test_default_off(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        _, profiles = load_routing()
        assert profiles["cc-1"].rtk is False

    def test_true_parsed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}, "rtk": True},
        })
        _, profiles = load_routing()
        assert profiles["cc-1"].rtk is True

    @pytest.mark.parametrize("bad", ["yes", 1, "true"])
    def test_non_bool_dies(self, tmp_path, monkeypatch, bad):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}, "rtk": bad},
        })
        with pytest.raises(SystemExit, match="'rtk'"):
            load_routing()

    def test_display_shows_only_when_set(self):
        settings = Settings()
        profiles = {
            "off": RoutingProfile("off", "anthropic", 8000, {
                "flash": Destination("p", "m1"), "pro": Destination("p", "m2")}),
            "on": RoutingProfile("on", "anthropic", 8000, {
                "flash": Destination("p", "m1"), "pro": Destination("p", "m2")}, rtk=True),
        }
        data = json.loads(format_routing_display(settings, profiles))
        assert "rtk" not in data["off"]
        assert data["on"]["rtk"] is True


# ---------------------------------------------------------------------------
# odcp profile flag
# ---------------------------------------------------------------------------

class TestOdcpFlag:
    _BASE = {"protocol": "anthropic", "longContextThreshold": 1,
             "destinations": {"flash": "p,m", "pro": "p,m"}}

    def test_default_off(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": self._BASE})
        _, profiles = load_routing()
        assert profiles["cc-1"].odcp is None

    def test_true_yields_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": {**self._BASE, "odcp": True}})
        _, profiles = load_routing()
        assert profiles["cc-1"].odcp == OdcpConfig()

    def test_object_form_parsed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": {**self._BASE, "odcp": {
            "dedup": False, "purgeErrors": {"turns": 2}}}})
        _, profiles = load_routing()
        assert profiles["cc-1"].odcp == OdcpConfig(dedup=False, purge_errors=True, purge_turns=2)

    def test_purge_errors_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": {**self._BASE, "odcp": {"purgeErrors": False}}})
        _, profiles = load_routing()
        assert profiles["cc-1"].odcp == OdcpConfig(purge_errors=False)

    @pytest.mark.parametrize("bad", ["yes", 1])
    def test_non_bool_non_object_dies(self, tmp_path, monkeypatch, bad):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": {**self._BASE, "odcp": bad}})
        with pytest.raises(SystemExit, match="'odcp'"):
            load_routing()

    def test_unknown_key_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": {**self._BASE, "odcp": {"dedup": True, "nope": 1}}})
        with pytest.raises(SystemExit, match="unknown odcp key"):
            load_routing()

    def test_bad_turns_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": {**self._BASE, "odcp": {"purgeErrors": {"turns": 0}}}})
        with pytest.raises(SystemExit, match="'turns'"):
            load_routing()

    def test_display_round_trips_when_set(self):
        settings = Settings()
        profiles = {
            "off": RoutingProfile("off", "anthropic", 8000, {
                "flash": Destination("p", "m1"), "pro": Destination("p", "m2")}),
            "on": RoutingProfile("on", "anthropic", 8000, {
                "flash": Destination("p", "m1"), "pro": Destination("p", "m2")},
                odcp=OdcpConfig(purge_turns=6)),
        }
        data = json.loads(format_routing_display(settings, profiles))
        assert "odcp" not in data["off"]
        assert data["on"]["odcp"] == {"dedup": True, "purgeErrors": {"turns": 6}}


# ---------------------------------------------------------------------------
# backups (failover queues) and the multimodal provider flag
# ---------------------------------------------------------------------------

class TestBackups:
    def _body(self, backups):
        return {
            "protocol": "anthropic", "longContextThreshold": 1,
            "destinations": {"flash": "p,m1", "pro": "p,m2"},
            "backups": backups,
        }

    def _load(self, tmp_path, monkeypatch, backups):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": self._body(backups)})
        return load_routing()[1]["cc-1"]

    def test_lists_and_bare_string(self, tmp_path, monkeypatch):
        p = self._load(tmp_path, monkeypatch, {
            "flash": ["a,m3", "b,m4"], "pro": "c,m5"})
        assert [(d.provider_name, d.model) for d in p.backups["flash"]] == [
            ("a", "m3"), ("b", "m4")]
        assert [(d.provider_name, d.model) for d in p.backups["pro"]] == [("c", "m5")]

    def test_absent_means_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m1", "pro": "p,m2"}},
        })
        assert load_routing()[1]["cc-1"].backups == {}

    def test_bad_tier_dies(self, tmp_path, monkeypatch):
        with pytest.raises(SystemExit, match="backups key must be flash or pro"):
            self._load(tmp_path, monkeypatch, {"fast": ["a,m3"]})

    def test_backups_for_undefined_tier_dies(self, tmp_path, monkeypatch):
        """backups keyed by a tier with no destination is an actionable config
        error, not a KeyError traceback — hot reload only catches SystemExit."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": {
            "protocol": "anthropic", "longContextThreshold": 1,
            "destinations": {"flash": "p,m1"},
            "backups": {"pro": ["a,m2"]},
        }})
        with pytest.raises(SystemExit, match="must define both 'flash' and 'pro'"):
            load_routing()

    def test_missing_tier_destinations_dies(self, tmp_path, monkeypatch):
        """A single-tier destinations dict would KeyError at request time
        (resolve and the implicit hop index by tier) — die at load instead."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": {
            "protocol": "anthropic", "longContextThreshold": 1,
            "destinations": {"flash": "p,m1"},
        }})
        with pytest.raises(SystemExit, match="missing 'pro'"):
            load_routing()

    def test_not_object_dies(self, tmp_path, monkeypatch):
        with pytest.raises(SystemExit, match="'backups' must be an object"):
            self._load(tmp_path, monkeypatch, ["a,m3"])

    def test_bad_element_dies(self, tmp_path, monkeypatch):
        with pytest.raises(SystemExit, match="destination must be 'provider,model'"):
            self._load(tmp_path, monkeypatch, {"flash": ["a,m3", "nope"]})

    def test_empty_list_dies(self, tmp_path, monkeypatch):
        with pytest.raises(SystemExit, match="non-empty list"):
            self._load(tmp_path, monkeypatch, {"flash": []})

    def test_duplicate_of_primary_dies(self, tmp_path, monkeypatch):
        with pytest.raises(SystemExit, match="duplicates the primary"):
            self._load(tmp_path, monkeypatch, {"flash": ["p,m1"]})

    def test_duplicate_inside_list_dies(self, tmp_path, monkeypatch):
        with pytest.raises(SystemExit, match="repeats"):
            self._load(tmp_path, monkeypatch, {"flash": ["a,m3", "a,m3"]})

    def test_validate_missing_provider_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "anthropic": {"p": {"base_url": "https://api.stepfun.com", "auth": "${K}"}},
        }, {"cc-1": self._body({"flash": ["gone,m9"]})})
        with pytest.raises(SystemExit, match=r"'gone'.*backups 'flash'"):
            validate_profiles(load_providers(), load_routing()[1])

    def test_validate_accepts_present_provider(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "anthropic": {"p": {"base_url": "https://api.stepfun.com", "auth": "${K}"},
                          "a": {"base_url": "https://api.stepfun.com", "auth": "${K}"}},
        }, {"cc-1": self._body({"flash": ["a,m3"]})})
        validate_profiles(load_providers(), load_routing()[1])  # must not raise

    def test_display_shows_backups(self, tmp_path, monkeypatch):
        p = self._load(tmp_path, monkeypatch, {"flash": ["a,m3"]})
        out = format_routing_display(Settings(), {"cc-1": p})
        assert '"backups"' in out and '"a,m3"' in out

    def test_display_shows_pool_and_multimodal(self):
        """Failover-relevant provider flags must be visible in config show —
        two configs with different pool/multimodal must not display alike."""
        p = Provider("p", "https://api.stepfun.com", "${K}", pool="accts",
                     multimodal=True)
        out = format_providers_display({"anthropic": {"p": p}})
        assert '"pool": "accts"' in out
        assert '"multimodal": true' in out
        plain = Provider("p", "https://api.stepfun.com", "${K}")
        out2 = format_providers_display({"anthropic": {"p": plain}})
        assert "pool" not in out2 and "multimodal" not in out2

    def test_profile_view_includes_backup_providers(self, tmp_path, monkeypatch):
        """`config show <profile>` lists the providers the profile's failover
        can actually reach — backups included, not just the primaries."""
        from click.testing import CliRunner
        from awerouter.config import config_show_cmd
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "anthropic": {
                "p": {"base_url": "https://api.stepfun.com", "auth": "${K}"},
                "a": {"base_url": "https://api.stepfun.com", "auth": "${K}"},
            },
        }, {"cc-1": self._body({"flash": ["a,m3"]})})
        out = CliRunner().invoke(config_show_cmd, ["cc-1"]).output
        assert '"p"' in out and '"a"' in out

    def test_display_omits_empty_backups(self, tmp_path, monkeypatch):
        p = self._load(tmp_path, monkeypatch, {})
        assert '"backups"' not in format_routing_display(Settings(), {"cc-1": p})


class TestMultimodalFlag:
    def _providers(self, tmp_path, monkeypatch, entry):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {"p": entry}}, {})
        return load_providers()["anthropic"]["p"]

    def test_default_false(self, tmp_path, monkeypatch):
        p = self._providers(tmp_path, monkeypatch,
                            {"base_url": "https://api.stepfun.com", "auth": "${K}"})
        assert p.multimodal is False

    def test_true(self, tmp_path, monkeypatch):
        p = self._providers(tmp_path, monkeypatch,
                            {"base_url": "https://api.stepfun.com", "auth": "${K}",
                             "multimodal": True})
        assert p.multimodal is True

    def test_non_bool_dies(self, tmp_path, monkeypatch):
        with pytest.raises(SystemExit, match="'multimodal' must be true or false"):
            self._providers(tmp_path, monkeypatch,
                            {"base_url": "https://api.stepfun.com", "auth": "${K}",
                             "multimodal": "yes"})


class TestPoolFlag:
    def _providers(self, tmp_path, monkeypatch, entry):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {"p": entry}}, {})
        return load_providers()["anthropic"]["p"]

    def test_default_empty(self, tmp_path, monkeypatch):
        p = self._providers(tmp_path, monkeypatch,
                            {"base_url": "https://api.stepfun.com", "auth": "${K}"})
        assert p.pool == ""

    def test_parsed(self, tmp_path, monkeypatch):
        p = self._providers(tmp_path, monkeypatch,
                            {"base_url": "https://api.stepfun.com", "auth": "${K}",
                             "pool": "stepfun"})
        assert p.pool == "stepfun"

    def test_non_string_dies(self, tmp_path, monkeypatch):
        with pytest.raises(SystemExit, match="'pool' must be a non-empty"):
            self._providers(tmp_path, monkeypatch,
                            {"base_url": "https://api.stepfun.com", "auth": "${K}",
                             "pool": 3})

    def test_whitespace_only_dies(self, tmp_path, monkeypatch):
        with pytest.raises(SystemExit, match="'pool' must be a non-empty"):
            self._providers(tmp_path, monkeypatch,
                            {"base_url": "https://api.stepfun.com", "auth": "${K}",
                             "pool": "   "})


# ---------------------------------------------------------------------------
# awecompress profile flag
# ---------------------------------------------------------------------------

class TestAwecompressFlag:
    _BASE = {"protocol": "anthropic", "longContextThreshold": 1,
             "destinations": {"flash": "p,m", "pro": "p,m"}}

    def test_default_off(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": self._BASE})
        _, profiles = load_routing()
        assert profiles["cc-1"].awecompress is None

    def test_true_yields_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": {**self._BASE, "awecompress": True}})
        _, profiles = load_routing()
        assert profiles["cc-1"].awecompress == AwecompressConfig()

    def test_object_form_parsed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": {**self._BASE, "awecompress": {
            "summaryModel": "pro", "thresholdTokens": 30000,
            "protectedTools": ["task"], "protectedFilePatterns": ["**/*.sql"]}}})
        _, profiles = load_routing()
        assert profiles["cc-1"].awecompress == AwecompressConfig(
            summary_model="pro", threshold_tokens=30000,
            protected_tools=("task",), protected_file_patterns=("**/*.sql",))

    @pytest.mark.parametrize("bad", ["yes", 1])
    def test_non_bool_non_object_dies(self, tmp_path, monkeypatch, bad):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": {**self._BASE, "awecompress": bad}})
        with pytest.raises(SystemExit, match="'awecompress'"):
            load_routing()

    def test_unknown_key_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": {**self._BASE,
                                              "awecompress": {"thresholdTokens": 1, "nope": 2}}})
        with pytest.raises(SystemExit, match="unknown awecompress key"):
            load_routing()

    def test_bad_int_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": {**self._BASE,
                                              "awecompress": {"keepRecentTurns": 0}}})
        with pytest.raises(SystemExit, match="'keepRecentTurns'"):
            load_routing()

    def test_bad_list_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {"cc-1": {**self._BASE,
                                              "awecompress": {"protectedTools": "task"}}})
        with pytest.raises(SystemExit, match="'protectedTools'"):
            load_routing()

    def test_display_round_trips_when_set(self):
        settings = Settings()
        profiles = {
            "off": RoutingProfile("off", "anthropic", 8000, {
                "flash": Destination("p", "m1"), "pro": Destination("p", "m2")}),
            "on": RoutingProfile("on", "anthropic", 8000, {
                "flash": Destination("p", "m1"), "pro": Destination("p", "m2")},
                awecompress=AwecompressConfig(summary_model="pro")),
        }
        data = json.loads(format_routing_display(settings, profiles))
        assert "awecompress" not in data["off"]
        assert data["on"]["awecompress"]["summaryModel"] == "pro"
