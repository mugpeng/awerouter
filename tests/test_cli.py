"""Tests for awerouter.cli top-level commands."""

import json
import os
import signal
import subprocess

import pytest

from click.testing import CliRunner

from awerouter import runtime, service
from awerouter.cli import _resolve_port, _run_serve, cli
from awerouter.config import load_for_profile, load_routing


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
    def _seed_logs(self, monkeypatch, tmp_path, rtk=(0, 0, 0), odcp=(0, 0, 0)):
        from awerouter.logging import append
        from awerouter.types import RequestLog
        log_dir = tmp_path / "logs"
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(log_dir))
        rows = (
            ("r1", "2026-01-01T00:00:00+00:00", "auto", "default", "flash", 100, rtk[0], odcp[0]),
            ("r2", "2026-01-01T00:01:00+00:00", "pro", "think", "pro", 30, rtk[1], odcp[1]),
            ("r3", "2026-01-01T00:12:00+00:00", "auto", "default→fallback", "pro", 20, rtk[2], odcp[2]),
        )
        for rid, ts, model_in, label, dest, tokens, rtk_saved, odcp_saved in rows:
            append(RequestLog(ts=ts, request_id=rid, model_in=model_in,
                              label=label, destination=dest, provider="p", model_out="m",
                              status=200, ms=1, bytes=1, token_count=tokens, profile="cc-1",
                              rtk_saved=rtk_saved, odcp_saved=odcp_saved))

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
        assert "search discount: 30%" in r.output
        assert "search: 0" in r.output
        assert "requests: 3  (flash 1 / pro 2, 33% flash, fallback 1)" in r.output
        lines = r.output.splitlines()
        assert any(line.strip().startswith("flash") and "100" in line for line in lines)
        assert any(line.strip().startswith("pro") and "50" in line for line in lines)
        assert any(line.strip().startswith("total") and "150" in line for line in lines)
        assert "offloaded to flash 100  (67% of input tokens)" in r.output
        assert "150 → 50" in r.output
        assert "cache sensitivity" in r.output
        assert "alternations: 1" in r.output
        assert "consecutive-pro gaps: 1 (0 within TTL, 1 expired)" in r.output
        assert "offload worth 10–100 pro-equivalent input tokens" in r.output
        assert "plug in your input prices" in r.output
        assert "(100 × pro − 100 × flash) / 1,000,000" in r.output
        assert "(10 × pro − 100 × flash) / 1,000,000" in r.output

    def test_rtk_shown_when_saved(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_logs(monkeypatch, tmp_path, rtk=(500, 0, 200))
        r = CliRunner().invoke(cli, ["usage", "savings"])
        assert r.exit_code == 0, r.output
        assert "rtk: saved 700 input tokens (2/3 requests compressed)" in r.output
        assert "rtk compression (input trimmed before billing, stacks with flash offload):" in r.output
        assert "saved 700 input tokens across 2 requests" in r.output

    def test_rtk_marker_in_usage_log(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_logs(monkeypatch, tmp_path, rtk=(500, 0, 200))
        r = CliRunner().invoke(cli, ["usage", "log"])
        assert r.exit_code == 0, r.output
        assert "rtk=+500" in r.output
        assert "rtk=+200" in r.output
        assert r.output.count("rtk=+") == 2  # zero-saving requests stay unmarked

    def test_rtk_hidden_when_nothing_saved(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_logs(monkeypatch, tmp_path)
        for cmd in (["usage", "savings"], ["usage", "log"], ["usage", "stats"]):
            r = CliRunner().invoke(cli, cmd)
            assert r.exit_code == 0, r.output
            assert "rtk:" not in r.output
            assert "rtk compression" not in r.output

    def test_odcp_shown_when_saved(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_logs(monkeypatch, tmp_path, odcp=(300, 0, 100))
        r = CliRunner().invoke(cli, ["usage", "savings"])
        assert r.exit_code == 0, r.output
        assert "odcp: saved 400 input tokens (2/3 requests pruned)" in r.output
        assert "odcp pruning (superseded tool-call content dropped, stacks with the above):" in r.output
        assert "saved 400 input tokens across 2 requests" in r.output

    def test_odcp_marker_in_usage_log(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_logs(monkeypatch, tmp_path, odcp=(300, 0, 100))
        r = CliRunner().invoke(cli, ["usage", "log"])
        assert r.exit_code == 0, r.output
        assert "odcp=+300" in r.output
        assert "odcp=+100" in r.output
        assert r.output.count("odcp=+") == 2  # zero-saving requests stay unmarked

    def test_odcp_hidden_when_nothing_saved(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_logs(monkeypatch, tmp_path)
        for cmd in (["usage", "savings"], ["usage", "log"]):
            r = CliRunner().invoke(cli, cmd)
            assert r.exit_code == 0, r.output
            assert "odcp:" not in r.output
            assert "odcp pruning" not in r.output
            assert "rtk=+" not in r.output


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

    def test_init_named_template(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["init", "glm-codex"])
        assert r.exit_code == 0
        assert "template: glm-codex" in r.output
        routing = json.loads((tmp_path / "routing.json").read_text())
        assert routing["cc-router-1"]["protocol"] == "openai-responses"

    def test_init_step_glm_mm_template(self, tmp_path, monkeypatch):
        """The multimodal-sidekick template parses end-to-end: imageModel
        flash, defaultModel pro, imageBridge on, dual-protocol (anthropic +
        openai-chat), stepfun flash next to glm pro in both provider groups."""
        _setup(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["init", "step-glm-mm"])
        assert r.exit_code == 0
        routing = json.loads((tmp_path / "routing.json").read_text())
        assert routing["settings"] == {"imageModel": "flash", "defaultModel": "pro",
                                       "imageBridge": True}
        assert routing["cc-router-1"]["protocol"] == ["anthropic", "openai-chat"]
        assert routing["cc-router-1"]["destinations"] == {
            "flash": "stepfun,step-3.7-flash", "pro": "glm,glm-5.3"}
        settings, profiles = load_routing()
        assert settings.image_model == "flash"
        assert settings.default_model == "pro"
        assert settings.image_bridge is True
        assert profiles["cc-router-1"].protocols == ("anthropic", "openai-chat")

    def test_init_unknown_template(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["init", "nope"])
        assert r.exit_code != 0
        assert "unknown template 'nope'" in r.output
        assert "step-glm" in r.output

    def test_init_merge_into_existing(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["init", "step-glm-mm", "--merge"])
        assert r.exit_code == 0, r.output
        assert "providers added: anthropic.glm, openai-chat.stepfun, openai-chat.glm" in r.output
        assert "profiles added: cc-router-1" in r.output
        assert "settings added: imageModel, defaultModel" in r.output
        assert "skipped (already present): anthropic.stepfun" in r.output
        assert "warning: newly set in settings: imageModel=flash, defaultModel=pro" in r.output
        assert "awerouter config restore routing" in r.output
        settings, profiles = load_routing()
        assert settings.image_model == "flash"
        assert settings.default_model == "pro"
        assert profiles["cc-router-1"].protocols == ("anthropic", "openai-chat")
        assert set(profiles) == {"cc-1", "cc-2", "cc-router-1"}

    def test_init_merge_second_run_is_no_op(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        CliRunner().invoke(cli, ["init", "step-glm-mm", "--merge"])
        r = CliRunner().invoke(cli, ["init", "step-glm-mm", "--merge"])
        assert r.exit_code == 0
        assert "nothing to merge; config already covers this template" in r.output

    def test_init_merge_on_missing_config_creates(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["init", "step-glm-mm", "--merge"])
        assert r.exit_code == 0, r.output
        assert "template: step-glm-mm" in r.output
        assert (tmp_path / "providers.json").exists()
        assert (tmp_path / "routing.json").exists()


class TestList:
    def test_lists_profiles(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["list"])
        assert r.exit_code == 0
        lines = r.output.splitlines()
        assert any(line.startswith("cc-1\tanthropic\t-\tstepfun/sf-flash\tanthropic/opus\tL3>8000") for line in lines)
        assert any(line.startswith("cc-2\tanthropic\t-\tstepfun/sf-flash\tstepfun/sf-pro\tL3>4000") for line in lines)

    def test_lists_profile_port(self, tmp_path, monkeypatch):
        routing = _routing()
        routing["cc-1"]["port"] = 20129
        _setup(tmp_path, monkeypatch, _providers(), routing)
        r = CliRunner().invoke(cli, ["list"])
        assert r.exit_code == 0
        lines = r.output.splitlines()
        assert any(line.startswith("cc-1\tanthropic\t20129\t") for line in lines)
        assert any(line.startswith("cc-2\tanthropic\t-\t") for line in lines)

    def test_lists_auto_threshold(self, tmp_path, monkeypatch):
        routing = _routing()
        routing["cc-1"]["longContextThreshold"] = "auto"
        _setup(tmp_path, monkeypatch, _providers(), routing)
        r = CliRunner().invoke(cli, ["list"])
        assert r.exit_code == 0
        assert any(line.startswith("cc-1\tanthropic\t-\tstepfun/sf-flash\tanthropic/opus\tL3>auto")
                   for line in r.output.splitlines())


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

        async def fake_serve(host, port, providers, profile, settings, port_explicit=False,
                             background=False):
            calls["args"] = (host, port, port_explicit, background)

        monkeypatch.setattr("awerouter.cli._serve", fake_serve)
        _run_serve("cc-1", None, "127.0.0.1")
        assert calls["args"] == ("127.0.0.1", 20129, True, False)

    def test_run_serve_cli_flag_overrides_profile(self, tmp_path, monkeypatch):
        routing = _routing()
        routing["cc-1"]["port"] = 20129
        _setup(tmp_path, monkeypatch, _providers(), routing)
        calls = {}

        async def fake_serve(host, port, providers, profile, settings, port_explicit=False,
                             background=False):
            calls["args"] = (host, port, port_explicit, background)

        monkeypatch.setattr("awerouter.cli._serve", fake_serve)
        _run_serve("cc-1", 3000, "127.0.0.1", background=True)
        assert calls["args"] == ("127.0.0.1", 3000, True, True)


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

    def test_wizard_accepts_auto_threshold(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        answers = "\n".join([
            "cc-3",        # profile name
            "",            # protocol (default anthropic)
            "stepfun",     # flash provider (existing)
            "sf-flash",    # flash model
            "stepfun",     # pro provider (existing)
            "sf-pro",      # pro model
            "auto",        # threshold
        ]) + "\n"
        r = CliRunner().invoke(cli, ["add"], input=answers)
        assert r.exit_code == 0, r.output
        routing = json.loads((tmp_path / "routing.json").read_text())
        assert routing["cc-3"]["longContextThreshold"] == "auto"
        _, profile, _ = load_for_profile("cc-3")
        assert profile.threshold_auto is True

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
            token_count=140, profile="cc-1", protocol="anthropic", agent="claude-code",
            tokens={"messages": 80, "system": 30, "tools": 10, "tool_results": 20},
            file_search_tokens=20,
        ))

    def test_bare_usage_shows_help(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage"])
        # click <8.2 shows help and exits 0; click >=8.2 exits 2 — support both
        assert r.exit_code in (0, 2), r.output
        assert "Usage:" in r.output
        assert "total_requests" not in r.output  # no default view

    def test_log_window_filter_reads_whole_file(self, tmp_path, monkeypatch):
        """--profile/--since filter the whole log first, then take the last
        --lines — matches outside the raw last-20 window must still show."""
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        log_dir = tmp_path / "logs"
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(log_dir))
        from awerouter.logging import append
        from awerouter.types import RequestLog

        def _entry(i, profile):
            return RequestLog(
                ts=f"2026-01-01T00:{i:02d}:00+00:00", request_id=f"r{i}", model_in="auto",
                label="default", destination="flash", provider="stepfun", model_out="sf-flash",
                status=200, ms=1, bytes=1, token_count=1, profile=profile, protocol="anthropic",
            )

        for i in range(3):
            append(_entry(i, "cc-2"))
        for i in range(3, 28):  # push the cc-2 entries out of the raw last-20 window
            append(_entry(i, "cc-1"))
        r = CliRunner().invoke(cli, ["usage", "log", "--profile", "cc-2"])
        assert r.exit_code == 0, r.output
        lines = [line for line in r.output.splitlines() if "tokens=" in line]
        assert len(lines) == 3

    def test_stats_subcommand(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "stats"])
        assert r.exit_code == 0, r.output
        assert "search discount: 30%" in r.output
        assert "search: 20" in r.output
        assert "total_requests : 1" in r.output
        assert "profile cc-1 [anthropic]" in r.output
        assert "claude-code" in r.output

    def test_log_subcommand(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "log", "--lines", "5"])
        assert r.exit_code == 0, r.output
        assert "sf-flash" in r.output
        assert "tokens=140" in r.output
        assert "anthropic" in r.output
        assert "claude-code" in r.output

    def test_log_all(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "log", "--all"])
        assert r.exit_code == 0, r.output
        assert "sf-flash" in r.output
        assert "tokens=140" in r.output

    def test_log_tokens_flag(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "log", "--tokens"])
        assert r.exit_code == 0, r.output
        assert "msg=80" in r.output
        assert "sys=30" in r.output
        assert "tools=10" in r.output
        # search token count nested inside results: results=20(search=20)
        assert "results=20(search=20)" in r.output
        # header line
        assert "search discount: 30%" in r.output
        assert "search: 20" in r.output
        assert "status=" not in r.output
        assert "in=auto" not in r.output

    def test_tokens_subcommand(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "tokens"])
        assert r.exit_code == 0, r.output
        assert "input tokens by type (1 requests, total 140  search 20  effective 126):" in r.output
        assert "messages" in r.output and "80" in r.output
        assert "system" in r.output and "30" in r.output
        assert "tools" in r.output and "10" in r.output
        assert "avg 80/req" in r.output
        # search embedded in tool_results, effective total in header
        assert "tool_results           20   14%  avg 20/req  (includes 20 search at 30% weight)" in r.output

    def test_tokens_no_logs(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(tmp_path / "empty"))
        r = CliRunner().invoke(cli, ["usage", "tokens"])
        assert r.exit_code == 0
        assert "(no logs yet)" in r.output

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
        assert "file-search tool results weighed at 30%" in r.output

    def test_calibrate_reflects_configured_discount(self, tmp_path, monkeypatch):
        routing = {"settings": {"searchResultDiscount": 0.5}, **_routing()}
        _setup(tmp_path, monkeypatch, _providers(), routing)
        self._seed_log(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["usage", "calibrate"])
        assert r.exit_code == 0, r.output
        assert "weighed at 50%" in r.output

    def test_calibrate_shows_auto_pick(self, tmp_path, monkeypatch):
        """The auto line names the policy's own window, independent of --since."""
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        self._seed_log(tmp_path, monkeypatch)  # 1 sample < minSamples 50
        r = CliRunner().invoke(cli, ["usage", "calibrate"])
        assert r.exit_code == 0, r.output
        assert "'auto'" in r.output
        assert "fewer than 50 L3 requests" in r.output
        assert "fallbackThreshold 8,000" in r.output

    def test_bad_since_errors(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["usage", "stats", "--since", "blah"])
        assert r.exit_code != 0


class TestBareProfileLaunch:
    def test_unknown_subcommand_resolves_to_profile(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        calls = []
        monkeypatch.setattr("awerouter.cli._run_serve",
                            lambda p, port, host, background=False: calls.append((p, port, host)))
        r = CliRunner().invoke(cli, ["cc-1", "--port", "20999"])
        assert r.exit_code == 0, r.output
        assert calls == [("cc-1", 20999, "127.0.0.1")]

    def test_bare_profile_accepts_background_flag(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        calls = []
        monkeypatch.setattr("awerouter.cli._run_serve",
                            lambda p, port, host, background=False: calls.append((p, host, background)))
        r = CliRunner().invoke(cli, ["cc-1", "-d"])
        assert r.exit_code == 0, r.output
        assert calls == [("cc-1", "127.0.0.1", True)]

    def test_defined_command_wins(self, tmp_path, monkeypatch):
        """A command name is never treated as a profile name."""
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        calls = []
        monkeypatch.setattr("awerouter.cli._run_serve",
                            lambda p, port, host, background=False: calls.append(p))
        r = CliRunner().invoke(cli, ["list"])
        assert r.exit_code == 0
        assert calls == []  # list ran, serve never did


class TestServeGroup:
    def test_bare_serve_auto_selects(self, tmp_path, monkeypatch):
        """Bare `awerouter serve` keeps its pre-group meaning: auto-select."""
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        calls = []
        monkeypatch.setattr("awerouter.cli._run_serve",
                            lambda p, port, host, background=False: calls.append((p, port, host)))
        r = CliRunner().invoke(cli, ["serve"])
        assert r.exit_code == 0, r.output
        assert calls == [(None, None, "127.0.0.1")]

    def test_run_takes_profile(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        calls = []
        monkeypatch.setattr("awerouter.cli._run_serve",
                            lambda p, port, host, background=False: calls.append(p))
        r = CliRunner().invoke(cli, ["serve", "run", "cc-1"])
        assert r.exit_code == 0, r.output
        assert calls == ["cc-1"]

    def test_profile_shorthand_still_starts(self, tmp_path, monkeypatch):
        """`awerouter serve <profile>` (pre-subcommand spelling) keeps working."""
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        calls = []
        monkeypatch.setattr("awerouter.cli._run_serve",
                            lambda p, port, host, background=False: calls.append((p, port, host)))
        r = CliRunner().invoke(cli, ["serve", "cc-1", "--port", "20999"])
        assert r.exit_code == 0, r.output
        assert calls == [("cc-1", 20999, "127.0.0.1")]


def _seed_instance(pid, profile="cc-1", port=20128, background=True):
    runtime.run_dir().mkdir(parents=True, exist_ok=True)
    runtime._pid_file(pid).write_text(json.dumps({
        "pid": pid, "profile": profile, "protocol": "anthropic",
        "port": port, "host": "127.0.0.1", "background": background,
        "started": 1700000000.0,
    }) + "\n", encoding="utf-8")


def _no_installed_services(tmp_path, monkeypatch):
    """Point the service-file scan at empty dirs: real LaunchAgents/systemd
    installs on the developer's machine must not leak into status output."""
    monkeypatch.setattr(service, "launchd_dir", lambda: tmp_path / "LaunchAgents")
    monkeypatch.setattr(service, "unit_dir", lambda: tmp_path / "systemd")


class TestStatus:
    def test_empty(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(tmp_path / "state"))
        _no_installed_services(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["serve", "status"])
        assert r.exit_code == 0, r.output
        assert "(no running instances)" in r.output
        assert "awerouter serve run <profile>" in r.output

    def test_lists_foreground_and_background(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(tmp_path / "state"))
        _no_installed_services(tmp_path, monkeypatch)
        _seed_instance(os.getpid(), profile="cc-1", port=20128, background=False)
        r = CliRunner().invoke(cli, ["serve", "status"])
        assert r.exit_code == 0, r.output
        assert f"cc-1\tfg\tpid {os.getpid()}\t127.0.0.1:20128\t[anthropic]" in r.output
        assert "up " in r.output


@pytest.mark.skipif(os.name == "nt", reason="awerouter stop is POSIX-only")
class TestStop:
    def test_nothing_to_stop(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(tmp_path / "state"))
        r = CliRunner().invoke(cli, ["serve", "stop"])
        assert r.exit_code == 0, r.output
        assert "(nothing to stop)" in r.output

    def test_no_instance_for_profile(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(tmp_path / "state"))
        _seed_instance(os.getpid(), profile="cc-1")
        r = CliRunner().invoke(cli, ["serve", "stop", "cc-2"])
        assert r.exit_code == 0, r.output
        assert "no running instance for profile 'cc-2'" in r.output

    def test_signals_and_reports(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(tmp_path / "state"))
        _seed_instance(os.getpid(), profile="cc-1", port=20128)
        state = {"alive": True}
        killed = []

        def fake_kill(pid, sig):
            assert sig == signal.SIGTERM
            killed.append(pid)
            state["alive"] = False

        monkeypatch.setattr(runtime, "_is_awerouter_process", lambda pid: True)
        monkeypatch.setattr(runtime, "pid_alive", lambda pid: state["alive"])
        monkeypatch.setattr(os, "kill", fake_kill)
        r = CliRunner().invoke(cli, ["serve", "stop", "cc-1"])
        assert r.exit_code == 0, r.output
        assert killed == [os.getpid()]
        assert f"stopped cc-1 (pid {os.getpid()}, port 20128)" in r.output
        assert "still shutting down" not in r.output


class TestServeBackground:
    """`serve run -d` spawns the detached daemon and reports its registration."""

    @staticmethod
    def _child(pid=4242, returncode=None):
        return type("Child", (), {"pid": pid, "poll": lambda self: returncode})()

    def _patch_spawn(self, monkeypatch, child_obj, instance):
        spawned = {}

        def fake_popen(cmd, **kwargs):
            spawned["cmd"] = cmd
            spawned["kwargs"] = kwargs
            return child_obj

        monkeypatch.setattr("awerouter.cli.subprocess.Popen", fake_popen)
        monkeypatch.setattr("awerouter.runtime.instance_by_pid",
                            lambda pid: instance if pid == child_obj.pid else None)
        return spawned

    @staticmethod
    def _instance(port=20128):
        return {"pid": 4242, "profile": "cc-1", "protocol": "anthropic",
                "port": port, "host": "127.0.0.1", "background": True}

    @pytest.mark.skipif(os.name == "nt", reason="awerouter background serve is POSIX-only")
    def test_spawns_detached_daemon(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(tmp_path / "state"))
        spawned = self._patch_spawn(monkeypatch, self._child(), self._instance())
        r = CliRunner().invoke(cli, ["serve", "run", "cc-1", "-d"])
        assert r.exit_code == 0, r.output
        cmd = spawned["cmd"]
        assert cmd[1:4] == ["-m", "awerouter", "__serve_daemon__"]
        assert cmd[4] == "cc-1"  # resolved profile name, not None
        assert spawned["kwargs"]["start_new_session"] is True
        assert spawned["kwargs"]["stdin"] is subprocess.DEVNULL
        assert "running in background (pid 4242)" in r.output
        assert "127.0.0.1:20128" in r.output
        assert "awerouter serve stop cc-1" in r.output
        # daemon log lives under the state dir
        assert str(tmp_path / "state" / "serve-cc-1.log") in r.output

    def test_old_profile_shorthand_keeps_background(self, tmp_path, monkeypatch):
        """`awerouter serve <profile> -d` (pre-subcommand spelling) still flags bg.

        The shorthand runs serve in-process (registered as background), so the
        assertion patches _serve, not the detached-spawn path."""
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        calls = []

        async def fake_serve(host, port, providers, profile, settings,
                             port_explicit=False, background=False):
            calls.append((profile.name, background))

        monkeypatch.setattr("awerouter.cli._serve", fake_serve)
        r = CliRunner().invoke(cli, ["serve", "cc-1", "-d"])
        assert r.exit_code == 0, r.output
        assert calls == [("cc-1", True)]

    @pytest.mark.skipif(os.name == "nt", reason="awerouter background serve is POSIX-only")
    def test_passes_port_flag_through(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(tmp_path / "state"))
        spawned = self._patch_spawn(monkeypatch, self._child(), self._instance(port=3000))
        r = CliRunner().invoke(cli, ["serve", "run", "cc-1", "-d", "--port", "3000"])
        assert r.exit_code == 0, r.output
        assert spawned["cmd"][spawned["cmd"].index("--port") + 1] == "3000"

    @pytest.mark.skipif(os.name == "nt", reason="awerouter background serve is POSIX-only")
    def test_failed_child_dies_with_log_tail(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(tmp_path / "state"))
        # the daemon writes its die message to the log before exiting
        log = tmp_path / "state" / "serve-cc-1.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("awerouter: port 20128 is already in use\n")
        self._patch_spawn(monkeypatch, self._child(returncode=1), None)
        r = CliRunner().invoke(cli, ["serve", "run", "cc-1", "-d"])
        assert r.exit_code != 0
        assert "failed to start" in r.output
        assert "already in use" in r.output

    def test_daemon_command_runs_background_serve(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        monkeypatch.setenv("AWEROUTER_LOG_DIR", str(tmp_path / "state"))
        calls = []

        async def fake_serve(host, port, providers, profile, settings, port_explicit=False,
                             background=False):
            calls.append((port, background))

        monkeypatch.setattr("awerouter.cli._serve", fake_serve)
        r = CliRunner().invoke(cli, ["__serve_daemon__", "cc-1"])
        assert r.exit_code == 0, r.output
        assert calls == [(20128, True)]


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

    def test_show_single_profile_settings_are_effective(self, tmp_path, monkeypatch):
        """Per-profile show resolves overrides: the settings block shows what
        this profile actually routes with; override keys sit flat in the entry."""
        routing = {
            "settings": {"imageModel": "pro", "defaultModel": "flash"},
            "cc-1": {"protocol": "anthropic", "longContextThreshold": 8000,
                     "destinations": {"flash": "stepfun,sf-flash", "pro": "anthropic,opus"},
                     "imageModel": "flash"},
        }
        _setup(tmp_path, monkeypatch, _providers(), routing)
        r = CliRunner().invoke(cli, ["config", "show", "cc-1"])
        assert r.exit_code == 0, r.output
        shown = json.loads(r.output.split("profile:\n", 1)[1])
        assert shown["settings"]["imageModel"] == "flash"    # effective
        assert shown["settings"]["defaultModel"] == "flash"  # inherited
        assert shown["cc-1"]["imageModel"] == "flash"        # raw override, flat
        # the full view still shows the global value
        r = CliRunner().invoke(cli, ["config", "show"])
        full = json.loads(r.output.split("routing.json:\n", 1)[1])
        assert full["settings"]["imageModel"] == "pro"
        assert full["cc-1"]["imageModel"] == "flash"

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
        r = CliRunner().invoke(cli, ["config", "restore", "routing"], input="y\n")
        assert r.exit_code == 0, r.output
        assert "config ok" in r.output
        assert json.loads((tmp_path / "routing.json").read_text()) == _routing()

    def test_declined_keeps_file(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        (tmp_path / "routing.json.bak").write_text(json.dumps(_routing()))
        (tmp_path / "routing.json").write_text(json.dumps({"settings": {}}))
        r = CliRunner().invoke(cli, ["config", "restore", "routing"], input="n\n")
        assert r.exit_code == 0, r.output
        assert "aborted" in r.output
        assert json.loads((tmp_path / "routing.json").read_text()) == {"settings": {}}

    def test_no_backup_dies(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["config", "restore", "providers"], input="y\n")
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

    def test_serve_group_typo_suggests_subcommand(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["serve", "statsu"])
        assert r.exit_code != 0
        assert "did you mean 'status'" in r.output

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
        monkeypatch.setattr("awerouter.cli._run_serve",
                            lambda p, port, host, background=False: calls.append((p, port, host)))
        r = CliRunner().invoke(cli, ["cc-2"])
        assert r.exit_code == 0, r.output
        assert calls == [("cc-2", None, "127.0.0.1")]  # None = resolve in _run_serve


class TestLoginLogout:
    def test_login_claude_runs_pkce_flow(self, tmp_path, monkeypatch):
        from awerouter import claude
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(claude, "begin_login",
                            lambda: ("https://platform.claude.com/oauth/authorize?x=1", "ver", "st"))
        monkeypatch.setattr("webbrowser.open", lambda url: True)
        monkeypatch.setattr(claude, "complete_login",
                            lambda code, verifier, state: {
                                "access_token": "at", "refresh_token": "rt",
                                "expires_at": 4102444800.0, "scopes": "user:inference"})
        r = CliRunner().invoke(cli, ["config", "login", "claude"], input="the-code\n")
        assert r.exit_code == 0, r.output
        assert "authorize" in r.output          # the URL is shown for manual open
        assert "claude login saved" in r.output

    def test_login_failure_exits_with_message(self, tmp_path, monkeypatch):
        from awerouter import claude
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))

        def rejected(code, verifier, state):
            raise claude.ClaudeAuthError("invalid authorization code")

        monkeypatch.setattr(claude, "begin_login", lambda: ("u", "v", "s"))
        monkeypatch.setattr(claude, "complete_login", rejected)
        r = CliRunner().invoke(cli, ["config", "login"], input="bad-code\n")
        assert r.exit_code != 0
        assert "invalid authorization code" in r.output

    def test_login_claude_replaces_only_on_confirm(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        (tmp_path / "claude-auth.json").write_text(json.dumps(
            {"access_token": "old", "refresh_token": "rt",
             "expires_at": 4102444800.0}), encoding="utf-8")
        r = CliRunner().invoke(cli, ["config", "login", "claude"], input="n\n")
        assert r.exit_code == 0
        assert "aborted" in r.output

    def test_login_codex_points_at_the_cli(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch)
        r = CliRunner().invoke(cli, ["config", "login", "codex"])
        assert r.exit_code == 0
        assert "codex login" in r.output

    def test_logout_claude_removes_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        (tmp_path / "claude-auth.json").write_text("{}", encoding="utf-8")
        r = CliRunner().invoke(cli, ["config", "logout", "claude"])
        assert r.exit_code == 0
        assert "removed" in r.output
        assert not (tmp_path / "claude-auth.json").exists()

    def test_logout_claude_without_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        r = CliRunner().invoke(cli, ["config", "logout"])
        assert r.exit_code == 0
        assert "no claude login" in r.output


class TestMovedConfigCommands:
    """login/logout/restore moved under `config`; the released top-level
    spellings keep working as hidden aliases."""

    def test_old_login_spelling(self, tmp_path, monkeypatch):
        """Bare `awerouter login codex` must not fall through to the
        profile-name shorthand (which would try to serve a profile named
        'login')."""
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["login", "codex"])
        assert r.exit_code == 0, r.output
        assert "codex login" in r.output

    def test_old_logout_spelling(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        r = CliRunner().invoke(cli, ["logout"])
        assert r.exit_code == 0, r.output
        assert "no claude login" in r.output

    def test_old_restore_spelling(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        (tmp_path / "routing.json.bak").write_text(json.dumps(_routing()))
        (tmp_path / "routing.json").write_text(json.dumps({"settings": {}}))
        r = CliRunner().invoke(cli, ["restore", "routing"], input="y\n")
        assert r.exit_code == 0, r.output
        assert "config ok" in r.output

    def test_old_spellings_hidden_from_top_level_help(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, _providers(), _routing())
        r = CliRunner().invoke(cli, ["-h"])
        assert r.exit_code == 0, r.output
        for name in ("login", "logout", "restore"):
            assert name not in r.output
        r = CliRunner().invoke(cli, ["config", "-h"])
        assert r.exit_code == 0, r.output
        for name in ("login", "logout", "restore"):
            assert name in r.output
