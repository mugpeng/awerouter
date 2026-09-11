"""Unit tests for awerouter.codex — local Codex CLI login as provider auth."""

import json

import pytest

from awerouter.codex import AUTH_SENTINEL, CodexAuthError, apply_codex_auth, auth_json_path, load_codex_login


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


def _write_login(home, access_token="at", account_id="acc-1", refresh_token="rt"):
    (home / "auth.json").write_text(json.dumps(
        {"tokens": {"access_token": access_token, "account_id": account_id,
                    "refresh_token": refresh_token}}), encoding="utf-8")


class TestLoadCodexLogin:
    def test_tokens_shell(self, codex_home):
        _write_login(codex_home, "tok-1", "acct-1")
        assert load_codex_login() == ("tok-1", "acct-1")

    def test_flat_tokens_block(self, codex_home):
        # awewarm convention: a file without the "tokens" shell is the block itself
        (codex_home / "auth.json").write_text(
            json.dumps({"access_token": "tok-2", "account_id": "acct-2"}), encoding="utf-8")
        assert load_codex_login() == ("tok-2", "acct-2")

    def test_missing_file(self, codex_home):
        with pytest.raises(CodexAuthError, match="codex login"):
            load_codex_login()

    def test_missing_access_token(self, codex_home):
        (codex_home / "auth.json").write_text(
            json.dumps({"tokens": {"account_id": "a"}}), encoding="utf-8")
        with pytest.raises(CodexAuthError, match="access_token"):
            load_codex_login()

    def test_missing_account_id(self, codex_home):
        (codex_home / "auth.json").write_text(
            json.dumps({"tokens": {"access_token": "t"}}), encoding="utf-8")
        with pytest.raises(CodexAuthError, match="account_id"):
            load_codex_login()

    def test_broken_json(self, codex_home):
        (codex_home / "auth.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(CodexAuthError, match="cannot read"):
            load_codex_login()

    def test_path_honors_codex_home(self, codex_home):
        assert auth_json_path() == codex_home / "auth.json"


class TestAuthHome:
    """An explicit home (a provider's authHome) points the login at one
    account's CLI config dir; several codex accounts ride side by side."""

    def test_home_wins_over_codex_home_env(self, codex_home, tmp_path):
        account = tmp_path / "accounts" / "heck"
        account.mkdir(parents=True)
        _write_login(account, "tok-heck", "acct-heck")
        assert auth_json_path(str(account)) == account / "auth.json"
        assert load_codex_login(str(account)) == ("tok-heck", "acct-heck")

    def test_missing_login_names_the_home(self, codex_home, tmp_path):
        account = tmp_path / "accounts" / "nope"
        with pytest.raises(CodexAuthError, match="CODEX_HOME="):
            load_codex_login(str(account))

    def test_apply_writes_that_accounts_headers(self, codex_home, tmp_path):
        account = tmp_path / "accounts" / "heck"
        account.mkdir(parents=True)
        _write_login(account, "tok-heck", "acct-heck")
        headers = {}
        apply_codex_auth(headers, str(account))
        assert headers["authorization"] == "Bearer tok-heck"
        assert headers["chatgpt-account-id"] == "acct-heck"

    def test_default_home_hint_unchanged(self, codex_home):
        with pytest.raises(CodexAuthError, match="run: codex login"):
            load_codex_login()


class TestApplyCodexAuth:
    def test_writes_full_header_set(self, codex_home):
        _write_login(codex_home, "tok-1", "acct-1")
        headers = {"authorization": "Bearer dummy-client-key"}
        apply_codex_auth(headers)
        assert headers["authorization"] == "Bearer tok-1"
        assert headers["chatgpt-account-id"] == "acct-1"
        assert headers["OpenAI-Beta"] == "responses=experimental"
        assert headers["originator"] == "codex_cli_rs"


def test_sentinel_value():
    # providers.json writes this exact string; the docs and templates rely on it
    assert AUTH_SENTINEL == "codex"
