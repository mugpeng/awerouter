"""Tests for awerouter.cli top-level commands."""

import json

from click.testing import CliRunner

from awerouter.cli import cli
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
        r = CliRunner().invoke(cli, ["savings"])
        assert r.exit_code == 0
        assert "(no logs yet)" in r.output

    def test_token_accounting(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_logs(monkeypatch, tmp_path)
        r = CliRunner().invoke(cli, ["savings"])
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
        assert "money saved" in r.output


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
        assert any(l.startswith("cc-1\tanthropic\tstepfun/sf-flash\tanthropic/opus\tL3>8000") for l in lines)
        assert any(l.startswith("cc-2\tanthropic\tstepfun/sf-flash\tstepfun/sf-pro\tL3>4000") for l in lines)


class TestShow:
    def test_show_all_without_arg(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["show"])
        assert r.exit_code == 0
        assert "providers.json" in r.output
        assert "cc-1" in r.output

    def test_show_single_profile(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["show", "cc-1"])
        assert r.exit_code == 0
        assert "stepfun/sf-flash" in r.output or "sf-flash" in r.output
        assert "cc-2" not in r.output.replace("available", "")  # other profile not shown

    def test_show_unknown_profile_dies(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["show", "nope"])
        assert r.exit_code != 0
        assert "not found" in r.output


class TestAdd:
    def test_wizard_new_and_existing_provider(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        answers = "\n".join([
            "cc-3",                    # profile name
            "",                        # protocol (default anthropic)
            "newprov",                 # flash provider (new)
            "https://api.newprov.com",  #   base_url
            "NEWPROV_KEY",             #   auth env var
            "nv-flash",                #   flash model
            "anthropic",               # pro provider (existing)
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

        # the wizard result must actually serve
        _, profile, _ = load_for_profile("cc-3")
        assert profile.destinations["pro"].model == "opus-9"

    def test_wizard_auto_inits_missing_config(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)  # no config files
        answers = "\n".join([
            "cc-1", "", "newprov", "https://x", "K", "m1", "newprov", "m2", "",
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


class TestStats:
    def _seed_log(self, tmp_path, monkeypatch):
        from awerouter.logging import append
        from awerouter.types import RequestLog
        log_dir = tmp_path / "logs"
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(log_dir))
        append(RequestLog(
            ts="2026-08-16T00:00:00+00:00", request_id="r1", model_in="auto",
            label="default", destination="flash", provider="stepfun",
            model_out="sf-flash", status=200, ms=800, bytes=100,
            token_count=120, profile="cc-1",
        ))

    def test_shows_tokens_and_by_model(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["stats"])
        assert r.exit_code == 0, r.output
        assert "total_requests : 1" in r.output
        assert "~total_tokens  : 120" in r.output
        assert "total_bytes" not in r.output
        assert "by_model" in r.output
        assert "sf-flash" in r.output
        assert "errors         : 0" in r.output

    def test_clean_confirmed_removes_log(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["stats", "--clean"], input="y\n")
        assert r.exit_code == 0, r.output
        assert "removed" in r.output
        assert not (tmp_path / "logs" / "requests.jsonl").exists()

    def test_clean_declined_keeps_log(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["stats", "--clean"], input="n\n")
        assert r.exit_code == 0, r.output
        assert "aborted" in r.output
        assert (tmp_path / "logs" / "requests.jsonl").exists()

    def test_since_window(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["stats", "--since", "today"])
        assert r.exit_code == 0, r.output
        # entry ts is 2026-08-16 UTC midnight; depending on local tz it may fall
        # inside or before today's window — either way the command succeeds and
        # reports the window line
        assert "window" in r.output

    def test_bad_since_errors(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["stats", "--since", "blah"])
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
