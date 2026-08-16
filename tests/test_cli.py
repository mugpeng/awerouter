"""Tests for awerouter.cli top-level commands."""

import json

from click.testing import CliRunner

from awerouter.cli import _resolve_port, _run_serve, cli
from awerouter.config import load_for_profile


def _setup(tmp_path, monkeypatch, providers=None, routing=None):
    monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
    if providers is not None:
        (tmp_path / "providers.json").write_text(json.dumps(providers))
    if routing is not None:
        (tmp_path / "routing.json").write_text(json.dumps(routing))


def _providers():
    return {"anthropic": {
        "stepfun": {"base_url": "https://api.stepfun.com/step_plan", "auth": "${K1}"},
        "anthropic": {"base_url": "https://api.anthropic.com", "auth": "${K2}"},
    }}


def _routing():
    return {
        "cc-1": {"protocol": "anthropic", "longContextThreshold": 8000,
                 "destinations": {"flash": "stepfun,sf-flash", "pro": "anthropic,opus"}},
        "cc-2": {"protocol": "anthropic", "longContextThreshold": 4000,
                 "destinations": {"flash": "stepfun,sf-flash", "pro": "stepfun,sf-pro"}},
    }


class TestSavings:
    def _seed_logs(self, monkeypatch, tmp_path):
        from awerouter.logging import append
        from awerouter.types import RequestLog
        log_dir = tmp_path / "logs"
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(log_dir))
        append(RequestLog(ts="2026-01-01T00:00:00+00:00", request_id="r1", model_in="auto",
                          label="default", destination="flash", provider="p", model_out="m",
                          status=200, ms=1, bytes=1, token_count=100, profile="cc-1"))
        append(RequestLog(ts="2026-01-01T00:01:00+00:00", request_id="r2", model_in="pro",
                          label="think", destination="pro", provider="p", model_out="m",
                          status=200, ms=1, bytes=1, token_count=30, profile="cc-1"))
        append(RequestLog(ts="2026-01-01T00:12:00+00:00", request_id="r3", model_in="auto",
                          label="default→fallback", destination="pro", provider="p", model_out="m",
                          status=200, ms=1, bytes=1, token_count=20, profile="cc-1"))

    def test_no_logs(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(tmp_path / "empty"))
        r = CliRunner().invoke(cli, ["usage", "savings"])
        assert r.exit_code == 0
        assert "(no logs yet)" in r.output

    def test_token_accounting(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_logs(monkeypatch, tmp_path)
        r = CliRunner().invoke(cli, ["usage", "savings"])
        assert r.exit_code == 0, r.output
        assert "requests: 3  (flash 1 / pro 2, 33% flash, fallback 1)" in r.output
        lines = r.output.splitlines()
        assert any(l.strip().startswith("flash") and "100" in l for l in lines)
        assert any(l.strip().startswith("pro") and "50" in l for l in lines)
        assert any(l.strip().startswith("total") and "150" in l for l in lines)
        assert "offloaded to flash 100  (67% of input tokens)" in r.output
        assert "150 → 50" in r.output
        assert "cache sensitivity" in r.output
        assert "alternations: 1" in r.output
        assert "consecutive-pro gaps: 1 (0 within TTL, 1 expired)" in r.output
        assert "offload worth 10–100 pro-equivalent input tokens" in r.output
        assert "plug in your input prices" in r.output
        assert "(100 × pro − 100 × flash) / 1,000,000" in r.output
        assert "(10 × pro − 100 × flash) / 1,000,000" in r.output


class TestInit:
    def test_top_level_init_creates_both_files(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["init"])
        assert r.exit_code == 0
        assert (tmp_path / "providers.json").exists()
        assert (tmp_path / "routing.json").exists()

    def test_top_level_init_refuses_existing(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)
        CliRunner().invoke(cli, ["init"])
        r = CliRunner().invoke(cli, ["init"])
        assert r.exit_code != 0
        assert "already exists" in r.output


class TestList:
    def test_lists_profiles(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["list"])
        assert r.exit_code == 0
        lines = r.output.splitlines()
        assert any(l.startswith("cc-1\tanthropic\t-\tstepfun/sf-flash\tanthropic/opus\tL3>8000") for l in lines)
        assert any(l.startswith("cc-2\tanthropic\t-\tstepfun/sf-flash\tstepfun/sf-pro\tL3>4000") for l in lines)

    def test_lists_profile_port(self, tmp_path, monkeypatch):
        routing = _routing()
        routing["cc-1"]["port"] = 20129
        _setup(tmp_path, monkeypatch, _providers(), routing)
        r = CliRunner().invoke(cli, ["list"])
        assert r.exit_code == 0
        lines = r.output.splitlines()
        assert any(l.startswith("cc-1\tanthropic\t20129\t") for l in lines)
        assert any(l.startswith("cc-2\tanthropic\t-\t") for l in lines)


class TestResolvePort:
    """--port > profile 'port' field > 20128; explicit ports must not drift."""

    def _profile(self, port=None):
        from awerouter.types import RoutingProfile
        return RoutingProfile("cc-1", "anthropic", 1, {}, port)

    def test_cli_flag_wins(self):
        assert _resolve_port(3000, self._profile(20129)) == (3000, True)

    def test_profile_port_when_no_flag(self):
        assert _resolve_port(None, self._profile(20129)) == (20129, True)

    def test_default_when_nothing_set(self):
        assert _resolve_port(None, self._profile()) == (20128, False)

    def test_run_serve_passes_resolved_port(self, tmp_path, monkeypatch):
        routing = _routing()
        routing["cc-1"]["port"] = 20129
        _setup(tmp_path, monkeypatch, _providers(), routing)
        calls = {}

        async def fake_serve(host, port, providers, profile, settings, port_explicit=False):
            calls["args"] = (host, port, port_explicit)

        monkeypatch.setattr("awerouter.cli._serve", fake_serve)
        _run_serve("cc-1", None, "127.0.0.1")
        assert calls["args"] == ("127.0.0.1", 20129, True)

    def test_run_serve_cli_flag_overrides_profile(self, tmp_path, monkeypatch):
        routing = _routing()
        routing["cc-1"]["port"] = 20129
        _setup(tmp_path, monkeypatch, _providers(), routing)
        calls = {}

        async def fake_serve(host, port, providers, profile, settings, port_explicit=False):
            calls["args"] = (host, port, port_explicit)

        monkeypatch.setattr("awerouter.cli._serve", fake_serve)
        _run_serve("cc-1", 3000, "127.0.0.1")
        assert calls["args"] == ("127.0.0.1", 3000, True)


class TestAdd:
    def test_wizard_new_and_existing_provider(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        answers = "\n".join([
            "cc-3",                    # profile name
            "",                        # protocol (default anthropic)
            "<new>",                   # flash provider → create one
            "newprov",                 #   provider name
            "https://api.newprov.com",  #   base_url
            "NEWPROV_KEY",             #   auth env var
            "nv-flash",                #   flash model
            "anthropic",               # pro provider (existing, from the choice list)
            "opus-9",                  #   pro model
            "",                        # threshold (default 8000)
        ]) + "\n"
        r = CliRunner().invoke(cli, ["add"], input=answers)
        assert r.exit_code == 0, r.output
        assert "Profile 'cc-3' added" in r.output

        providers = json.loads((tmp_path / "providers.json").read_text())
        assert providers["anthropic"]["newprov"]["base_url"] == "https://api.newprov.com"
        assert providers["anthropic"]["newprov"]["auth"] == "${NEWPROV_KEY}"
        # existing provider untouched
        assert providers["anthropic"]["anthropic"]["auth"] == "${K2}"

        routing = json.loads((tmp_path / "routing.json").read_text())
        assert routing["cc-3"]["destinations"]["flash"] == "newprov,nv-flash"
        assert routing["cc-3"]["destinations"]["pro"] == "anthropic,opus-9"

        # writes are preceded by a .bak snapshot
        assert json.loads((tmp_path / "routing.json.bak").read_text()) == _routing()

        # the wizard result must actually serve
        _, profile, _ = load_for_profile("cc-3")
        assert profile.destinations["pro"].model == "opus-9"

    def test_wizard_shows_category_overview(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        answers = "\n".join([
            "cc-3", "", "stepfun", "sf-flash", "anthropic", "opus-9", "",
        ]) + "\n"
        r = CliRunner().invoke(cli, ["add"], input=answers)
        assert r.exit_code == 0, r.output
        assert "providers.json categories:" in r.output
        assert "anthropic          anthropic, stepfun" in r.output
        assert "openai-chat        (empty)" in r.output

    def test_wizard_auto_inits_missing_config(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)  # no config files
        answers = "\n".join([
            "cc-1",                    # profile name
            "",                        # protocol (default anthropic; template has providers)
            "<new>",                   # flash provider → create one
            "newprov", "https://x", "K", "m1",
            "newprov",                 # pro provider (now in the choice list)
            "m2", "",
        ]) + "\n"
        r = CliRunner().invoke(cli, ["add"], input=answers)
        assert r.exit_code == 0, r.output
        assert (tmp_path / "routing.json").exists()

    def test_wizard_duplicate_profile_dies(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        answers = "cc-1\n"
        r = CliRunner().invoke(cli, ["add"], input=answers)
        assert r.exit_code != 0
        assert "already exists" in r.output


class TestUsage:
    def _seed_log(self, tmp_path, monkeypatch):
        from datetime import datetime, timezone
        from awerouter.logging import append
        from awerouter.types import RequestLog
        log_dir = tmp_path / "logs"
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(log_dir))
        # Seed "now" so --since today always includes the entry, whenever the
        # suite runs (a fixed date falls out of the window the next day).
        append(RequestLog(
            ts=datetime.now(timezone.utc).isoformat(), request_id="r1", model_in="auto",
            label="default", destination="flash", provider="stepfun",
            model_out="sf-flash", status=200, ms=800, duration_ms=1500, bytes=100,
            token_count=120, profile="cc-1", protocol="anthropic", agent="claude-code",
        ))

    def test_bare_usage_shows_help(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage"])
        assert r.exit_code != 0, r.output
        assert "Usage:" in r.output
        assert "total_requests" not in r.output  # no default view

    def test_stats_subcommand(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "stats"])
        assert r.exit_code == 0, r.output
        assert "total_requests : 1" in r.output
        assert "profile cc-1 [anthropic]" in r.output
        assert "claude-code" in r.output

    def test_log_subcommand(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "log", "--lines", "5"])
        assert r.exit_code == 0, r.output
        assert "sf-flash" in r.output
        assert "tokens=120" in r.output
        assert "anthropic" in r.output
        assert "claude-code" in r.output

    def test_log_all(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "log", "--all"])
        assert r.exit_code == 0, r.output
        assert "sf-flash" in r.output
        assert "tokens=120" in r.output

    def test_clean_confirmed_removes_log(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "clean"], input="y\n")
        assert r.exit_code == 0, r.output
        assert "removed" in r.output
        assert not (tmp_path / "logs" / "requests.jsonl").exists()

    def test_clean_declined_keeps_log(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "clean"], input="n\n")
        assert r.exit_code == 0, r.output
        assert "aborted" in r.output
        assert (tmp_path / "logs" / "requests.jsonl").exists()

    def test_stats_has_no_clean_flag(self, tmp_path, monkeypatch):
        """stats is read-only; deleting logs lives in `usage clean`."""
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "stats", "--clean"])
        assert r.exit_code != 0
        assert (tmp_path / "logs" / "requests.jsonl").exists()

    def test_since_window_on_subcommand(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "stats", "--since", "today"])
        assert r.exit_code == 0, r.output
        assert "window" in r.output

    def test_group_rejects_window_options(self, tmp_path, monkeypatch):
        """--since/--profile moved off the group onto the subcommands."""
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["usage", "--since", "today", "stats"])
        assert r.exit_code != 0
        assert "No such option" in r.output

    def test_window_options_on_savings(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "savings", "--since", "today"])
        assert r.exit_code == 0, r.output
        assert "window" in r.output
        assert "requests:" in r.output

    def test_profile_filter_on_log(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "log", "--profile", "cc-1"])
        assert r.exit_code == 0, r.output
        assert "claude-code" in r.output
        r2 = CliRunner().invoke(cli, ["usage", "log", "--profile", "other"])
        assert r2.exit_code == 0, r2.output
        assert "(no logs yet)" in r2.output

    def test_clean_has_no_window_options(self, tmp_path, monkeypatch):
        """clean deletes everything; a window filter on it would be misleading."""
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "clean", "--since", "today"])
        assert r.exit_code != 0
        assert (tmp_path / "logs" / "requests.jsonl").exists()

    def test_calibrate_subcommand(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "calibrate"])
        assert r.exit_code == 0, r.output

    def test_bad_since_errors(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["usage", "stats", "--since", "blah"])
        assert r.exit_code != 0


class TestBareProfileLaunch:
    def test_unknown_subcommand_resolves_to_profile(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        calls = []
        monkeypatch.setattr("awerouter.cli._run_serve", lambda p, port, host: calls.append((p, port, host)))
        r = CliRunner().invoke(cli, ["cc-1", "--port", "20999"])
        assert r.exit_code == 0, r.output
        assert calls == [("cc-1", 20999, "127.0.0.1")]

    def test_defined_command_wins(self, tmp_path, monkeypatch):
        """A command name is never treated as a profile name."""
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        calls = []
        monkeypatch.setattr("awerouter.cli._run_serve", lambda p, port, host: calls.append((p, port, host)))
        r = CliRunner().invoke(cli, ["list"])
        assert r.exit_code == 0
        assert calls == []  # list ran, serve never did


class TestConfigCommands:
    def test_path_prints_both_files(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["config", "path"])
        assert r.exit_code == 0, r.output
        assert r.output.splitlines() == [
            str(tmp_path / "providers.json"),
            str(tmp_path / "routing.json"),
        ]

    def test_show_full_config(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["config", "show"])
        assert r.exit_code == 0, r.output
        assert "providers.json:" in r.output
        assert "routing.json:" in r.output
        assert "${K1}" in r.output  # env-ref auth shown

    def test_show_single_profile(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["config", "show", "cc-1"])
        assert r.exit_code == 0, r.output
        assert "providers:" in r.output
        assert "profile:" in r.output
        assert "cc-1" in r.output
        assert "cc-2" not in r.output  # other profiles excluded
        assert "stepfun" in r.output   # only providers this profile uses

    def test_show_unknown_profile_dies(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["config", "show", "nope"])
        assert r.exit_code != 0
        assert "not found" in r.output

    def test_init_removed_from_config_group(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["config", "init"])
        assert r.exit_code != 0
        assert "did you mean" in r.output or "-h" in r.output


class TestRestore:
    def test_restores_routing_from_bak(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        # simulate a bad edit, with the add wizard's backup still around
        (tmp_path / "routing.json.bak").write_text(json.dumps(_routing()))
        (tmp_path / "routing.json").write_text(json.dumps({"settings": {}}))
        r = CliRunner().invoke(cli, ["restore", "routing"], input="y\n")
        assert r.exit_code == 0, r.output
        assert "config ok" in r.output
        assert json.loads((tmp_path / "routing.json").read_text()) == _routing()

    def test_declined_keeps_file(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        (tmp_path / "routing.json.bak").write_text(json.dumps(_routing()))
        (tmp_path / "routing.json").write_text(json.dumps({"settings": {}}))
        r = CliRunner().invoke(cli, ["restore", "routing"], input="n\n")
        assert r.exit_code == 0, r.output
        assert "aborted" in r.output
        assert json.loads((tmp_path / "routing.json").read_text()) == {"settings": {}}

    def test_no_backup_dies(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["restore", "providers"], input="y\n")
        assert r.exit_code != 0
        assert "no backup found" in r.output


class TestCommandSuggestions:
    def test_top_level_typo_suggests_serve(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["server", "cc-1"])
        assert r.exit_code != 0
        assert "did you mean 'serve'" in r.output

    def test_subgroup_typo_suggests_subcommand(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["usage", "statsx"])
        assert r.exit_code != 0
        assert "did you mean 'stats'" in r.output

    def test_config_group_typo_suggests_subcommand(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["config", "sho"])
        assert r.exit_code != 0
        assert "did you mean 'show'" in r.output

    def test_far_off_typo_points_to_help(self, tmp_path, monkeypatch):
        """No close match + extra positional args: -h hint, not a raw
        'unexpected extra argument' error."""
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["zzzzqqq", "blah"])
        assert r.exit_code != 0
        assert "-h" in r.output
        assert "unexpected extra argument" not in r.output

    def test_subgroup_far_off_typo_points_to_help(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["usage", "zzzzqqq"])
        assert r.exit_code != 0
        assert "-h to list commands" in r.output

    def test_valid_profile_still_launches(self, tmp_path, monkeypatch):
        """The suggestion layer must not break the bare-profile shorthand."""
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        calls = []
        monkeypatch.setattr("awerouter.cli._run_serve", lambda p, port, host: calls.append((p, port, host)))
        r = CliRunner().invoke(cli, ["cc-2"])
        assert r.exit_code == 0, r.output
        assert calls == [("cc-2", None, "127.0.0.1")]  # None = resolve in _run_serve
