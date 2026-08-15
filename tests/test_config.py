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
from awerouter.types import Destination, Provider, RoutingProfile, Settings


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
# load_providers (nested by agent)
# ---------------------------------------------------------------------------

class TestLoadProviders:
    def test_nested_by_agent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "claude": {"p": {"base_url": "https://api.stepfun.com/step_plan", "auth": "${K}"}},
            "codex":  {"p": {"base_url": "https://api.stepfun.com/v1",        "auth": "${K}"}},
        }, {})
        result = load_providers()
        assert "claude" in result and "codex" in result
        assert result["claude"]["p"].auth_header == "authorization"

    def test_anthropic_auto_detects_x_api_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "claude": {"anthropic": {"base_url": "https://api.anthropic.com", "auth": "${K}"}},
        }, {})
        result = load_providers()
        assert result["claude"]["anthropic"].auth_header == "x-api-key"

    def test_explicit_auth_header_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "claude": {"p": {"base_url": "https://x", "auth": "${K}", "auth_header": "x-api-key"}},
        }, {})
        result = load_providers()
        assert result["claude"]["p"].auth_header == "x-api-key"

    def test_missing_field_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"claude": {"p": {"base_url": "https://x"}}}, {})
        with pytest.raises(SystemExit, match="missing"):
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
                "agent": "claude", "longContextThreshold": 8000,
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
                "agent": "claude", "longContextThreshold": 1,
                "destinations": {"flash": "p,m", "pro": "p,m"},
            },
        })
        settings, _ = load_routing()
        assert settings.background_model == "bg"
        assert settings.think_model == "strong"
        assert settings.web_search_model == "flash"

    def test_multiple_profiles(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"agent": "claude", "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
            "cx-1": {"agent": "codex",  "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        _, profiles = load_routing()
        assert set(profiles) == {"cc-1", "cx-1"}
        assert profiles["cc-1"].agent == "claude"

    def test_missing_agent_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="agent"):
            load_routing()

    def test_profile_no_longer_needs_background_think(self, tmp_path, monkeypatch):
        """backgroundModel/thinkModel moved to settings — profile omits them."""
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"agent": "claude", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        _, profiles = load_routing()
        assert not hasattr(profiles["cc-1"], "background_model")


# ---------------------------------------------------------------------------
# load_for_profile / load_default_profile
# ---------------------------------------------------------------------------

class TestLoadForProfile:
    def test_returns_settings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"claude": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {
            "settings": {"backgroundModel": "bg", "thinkModel": "strong"},
            "cc-1": {"agent": "claude", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m1", "pro": "p,m2"}},
        })
        providers, profile, settings = load_for_profile("cc-1")
        assert settings.background_model == "bg"
        assert profile.destinations["flash"].provider_name == "p"

    def test_unknown_profile_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"claude": {}}, {
            "cc-1": {"agent": "claude", "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="not found"):
            load_for_profile("nope")

    def test_dest_provider_missing_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"claude": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {
            "cc-1": {"agent": "claude", "longContextThreshold": 1,
                     "destinations": {"flash": "nonexistent,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="provider 'nonexistent'"):
            load_for_profile("cc-1")

    def test_agent_missing_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"claude": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {
            "cc-1": {"agent": "nope", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="agent 'nope'"):
            load_for_profile("cc-1")


class TestValidateProfiles:
    def _providers(self):
        return {"claude": {"p": Provider("p", "https://x", "${K}")}}

    def _profile(self, flash="p,m"):
        return {"cc-1": RoutingProfile("cc-1", "claude", 1, {
            "flash": Destination(flash.split(",")[0], flash.split(",")[1]),
            "pro": Destination("p", "m2"),
        })}

    def test_valid_passes(self):
        validate_profiles(self._providers(), self._profile())

    def test_unknown_provider_dies(self):
        with pytest.raises(SystemExit, match="provider 'q'"):
            validate_profiles(self._providers(), self._profile("q,m"))

    def test_unknown_agent_dies(self):
        profiles = {"cc-1": RoutingProfile("cc-1", "codex", 1, {
            "flash": Destination("p", "m1"), "pro": Destination("p", "m2"),
        })}
        with pytest.raises(SystemExit, match="agent 'codex'"):
            validate_profiles(self._providers(), profiles)


class TestLoadDefaultProfile:
    def test_single_auto_selects(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"claude": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {
            "cc-1": {"agent": "claude", "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        _, profile, settings = load_default_profile()
        assert profile.name == "cc-1"
        assert settings.background_model == "flash"

    def test_multiple_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"claude": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {
            "cc-1": {"agent": "claude", "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
            "cc-2": {"agent": "claude", "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
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
            "claude": {"p": Provider("p", "https://x", "${K}")},
        }
        data = json.loads(format_providers_display(all_providers))
        assert data["claude"]["p"]["auth"] == "${K}"

    def test_routing_shows_settings_and_profiles(self):
        settings = Settings(background_model="flash", think_model="pro", web_search_model="pro")
        profiles = {
            "cc-1": RoutingProfile("cc-1", "claude", 8000, {
                "flash": Destination("p", "m1"), "pro": Destination("p", "m2"),
            }),
        }
        data = json.loads(format_routing_display(settings, profiles))
        assert data["settings"]["backgroundModel"] == "flash"
        assert data["settings"]["webSearchModel"] == "pro"
        assert data["cc-1"]["agent"] == "claude"
        assert "backgroundModel" not in data["cc-1"]
