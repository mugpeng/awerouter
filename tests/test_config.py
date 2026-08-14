"""Tests for awerouter.config."""

import json
import shutil

import pytest

from awerouter.config import (
    ENV_REF_RE,
    SECRET_RE,
    _parse_destination,
    config_dir,
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
    providers_path,
    redact,
    resolve_provider,
    routing_path,
)
from awerouter.types import Destination, Provider, RoutingProfile


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

    def test_case_insensitive(self):
        data = {"Authorization": "Bearer xyz", "x-api-key": "abc"}
        r = redact(data)
        assert r["Authorization"] == "<redacted>"
        assert r["x-api-key"] == "<redacted>"

    def test_nested(self):
        data = {"outer": {"auth_token": "t", "safe": "v"}}
        r = redact(data)
        assert r["outer"]["auth_token"] == "<redacted>"
        assert r["outer"]["safe"] == "v"

    def test_list(self):
        data = [{"secret": "s1", "visible": "v1"}, {"secret": "s2", "visible": "v2"}]
        r = redact(data)
        assert r[0]["secret"] == "<redacted>"
        assert r[1]["visible"] == "v2"


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
# load_providers / load_routing (file-based, nested by agent / profile)
# ---------------------------------------------------------------------------

def _write_config(tmp_path, providers, routing):
    (tmp_path / "providers.json").write_text(json.dumps(providers))
    (tmp_path / "routing.json").write_text(json.dumps(routing))


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
        assert result["codex"]["p"].auth_header == "authorization"

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


class TestLoadRouting:
    def test_multiple_profiles(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {
                "agent": "claude", "backgroundModel": "c1/flash", "thinkModel": "c1/think",
                "longContextThreshold": 8000,
                "destinations": {"flash": "p,m1", "pro": "p,m2"},
            },
            "cx-1": {
                "agent": "codex", "backgroundModel": "c1/flash", "thinkModel": "c1/think",
                "longContextThreshold": 8000,
                "destinations": {"flash": "p,m1", "pro": "p,m2"},
            },
        })
        profiles = load_routing()
        assert set(profiles) == {"cc-1", "cx-1"}
        assert profiles["cc-1"].agent == "claude"
        assert profiles["cx-1"].agent == "codex"
        assert profiles["cc-1"].name == "cc-1"

    def test_missing_agent_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"backgroundModel": "x", "thinkModel": "y", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="agent"):
            load_routing()

    def test_missing_key_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {}, {
            "cc-1": {"agent": "claude", "thinkModel": "y", "longContextThreshold": 1,
                     "destinations": {"flash": "p,m", "pro": "p,m"}},
        })
        with pytest.raises(SystemExit, match="backgroundModel"):
            load_routing()


# ---------------------------------------------------------------------------
# load_for_profile / load_default_profile
# ---------------------------------------------------------------------------

class TestLoadForProfile:
    def test_resolves_and_attaches_provider(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "claude": {"p": {"base_url": "https://x", "auth": "${K}"}},
        }, {
            "cc-1": {
                "agent": "claude", "backgroundModel": "c1/flash", "thinkModel": "c1/think",
                "longContextThreshold": 8000,
                "destinations": {"flash": "p,m1", "pro": "p,m2"},
            },
        })
        providers, profile = load_for_profile("cc-1")
        assert profile.agent == "claude"
        assert "p" in providers
        # Destination provider attached
        assert profile.destinations["flash"].provider is providers["p"]

    def test_unknown_profile_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"claude": {}}, {
            "cc-1": {
                "agent": "claude", "backgroundModel": "x", "thinkModel": "y",
                "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"},
            },
        })
        with pytest.raises(SystemExit, match="not found"):
            load_for_profile("nope")

    def test_dest_provider_missing_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {
            "claude": {"p": {"base_url": "https://x", "auth": "${K}"}},
        }, {
            "cc-1": {
                "agent": "claude", "backgroundModel": "x", "thinkModel": "y",
                "longContextThreshold": 1,
                "destinations": {"flash": "nonexistent,m", "pro": "p,m"},
            },
        })
        with pytest.raises(SystemExit, match="provider 'nonexistent'"):
            load_for_profile("cc-1")

    def test_agent_not_in_providers_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"claude": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {
            "cc-1": {
                "agent": "codex", "backgroundModel": "x", "thinkModel": "y",
                "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"},
            },
        })
        with pytest.raises(SystemExit, match="agent 'codex'"):
            load_for_profile("cc-1")


class TestLoadDefaultProfile:
    def test_single_auto_selects(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"claude": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {
            "cc-1": {
                "agent": "claude", "backgroundModel": "x", "thinkModel": "y",
                "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"},
            },
        })
        _, profile = load_default_profile()
        assert profile.name == "cc-1"

    def test_multiple_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, {"claude": {"p": {"base_url": "https://x", "auth": "${K}"}}}, {
            "cc-1": {"agent": "claude", "backgroundModel": "x", "thinkModel": "y",
                     "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
            "cc-2": {"agent": "claude", "backgroundModel": "x", "thinkModel": "y",
                     "longContextThreshold": 1, "destinations": {"flash": "p,m", "pro": "p,m"}},
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
    def test_providers_nested_with_masked_auth(self):
        all_providers = {
            "claude": {"p": Provider("p", "https://x", "${STEPFUN_KEY}")},
            "codex":  {"p": Provider("p", "https://x", "literal-secret")},
        }
        data = json.loads(format_providers_display(all_providers))
        assert data["claude"]["p"]["auth"] == "${STEPFUN_KEY}"
        assert data["codex"]["p"]["auth"] == "<set>"
        assert data["claude"]["p"]["auth_header"] == "authorization"

    def test_routing_display_multiple_profiles(self):
        profiles = {
            "cc-1": RoutingProfile("cc-1", "claude", "c1/flash", "c1/think", 8000, {
                "flash": Destination("p", "m1"),
                "pro": Destination("p", "m2"),
            }),
        }
        data = json.loads(format_routing_display(profiles))
        assert data["cc-1"]["agent"] == "claude"
        assert data["cc-1"]["destinations"]["flash"] == "p,m1"
