"""Tests for awerouter.config."""

import json

import pytest

from awerouter.config import (
    _parse_destination,
    detect_auth_header,
    die,
    expand_value,
    format_providers_display,
    format_routing_display,
    init_config,
    load_default_profile,
    load_for_profile,
    load_providers,
    load_routing,
    redact,
    validate_profiles,
)
from awerouter.types import Destination, Provider, RoutingProfile, Settings, ToolRoutingConfig


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

    def test_missing_field_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"anthropic": {"p": {"base_url": "https://x"}}}, {})
        with pytest.raises(SystemExit, match="missing"):
            load_providers()

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
# init_config
# ---------------------------------------------------------------------------

class TestInitConfig:
    def test_already_exists_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        (tmp_path / "providers.json").write_text("{}")
        (tmp_path / "routing.json").write_text("{}")
        with pytest.raises(SystemExit, match="already exists"):
            init_config()


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
