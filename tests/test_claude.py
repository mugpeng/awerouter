"""Unit tests for awerouter.claude — awerouter-owned Claude OAuth login."""

import base64
import hashlib
import io
import json
import os
import time
import urllib.error
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from awerouter import claude
from awerouter.claude import (
    AUTH_SENTINEL,
    ClaudeAuthError,
    apply_claude_auth,
    begin_login,
    claude_auth_path,
    complete_login,
    load_claude_login,
    login_status,
    logout,
)


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    d = tmp_path / "cfg"
    monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(d))
    return d


def _write_store(access_token="at", refresh_token="rt", expires_at=None, extra=None):
    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at if expires_at is not None else time.time() + 3600,
    }
    payload.update(extra or {})
    path = claude_auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _token_response(access_token="at-2", refresh_token="rt-2", expires_in=3600):
    return {"access_token": access_token, "refresh_token": refresh_token,
            "expires_in": expires_in, "scope": "user:inference"}


class TestBeginLogin:
    def test_url_carries_pkce_and_client(self):
        url, verifier, state = begin_login()
        q = parse_qs(urlparse(url).query)
        assert urlparse(url).netloc == "platform.claude.com"
        assert q["client_id"] == [claude.CLIENT_ID]
        assert q["response_type"] == ["code"]
        assert q["redirect_uri"] == [claude.REDIRECT_URI]
        assert q["scope"] == [claude.SCOPES]
        assert q["code_challenge_method"] == ["S256"]
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        assert q["code_challenge"] == [expected]
        assert q["state"] == [state]

    def test_each_call_is_a_fresh_pkce_pair(self):
        _, verifier_a, state_a = begin_login()
        _, verifier_b, state_b = begin_login()
        assert verifier_a != verifier_b
        assert state_a != state_b


class TestCompleteLogin:
    def test_exchanges_and_saves(self, store_dir, monkeypatch):
        sent = {}
        monkeypatch.setattr(claude, "_token_request",
                            lambda payload: sent.update(payload) or _token_response("at-1", "rt-1", 7200))
        url, verifier, state = begin_login()
        code = url + "#code"  # any opaque code
        payload = complete_login(code, verifier, state)
        assert sent["grant_type"] == "authorization_code"
        assert sent["code"] == code
        assert sent["code_verifier"] == verifier
        assert sent["state"] == state
        assert sent["redirect_uri"] == claude.REDIRECT_URI
        assert payload["access_token"] == "at-1"
        on_disk = json.loads(claude_auth_path().read_text(encoding="utf-8"))
        assert on_disk["refresh_token"] == "rt-1"
        assert on_disk["expires_at"] == pytest.approx(time.time() + 7200, abs=5)

    def test_missing_access_token_dies(self, store_dir, monkeypatch):
        monkeypatch.setattr(claude, "_token_request",
                            lambda payload: {"refresh_token": "rt"})
        url, verifier, state = begin_login()
        with pytest.raises(ClaudeAuthError, match="access_token"):
            complete_login("c", verifier, state)

    def test_missing_refresh_token_is_kept_null(self, store_dir, monkeypatch):
        monkeypatch.setattr(claude, "_token_request",
                            lambda payload: {"access_token": "at", "expires_in": 60})
        _, verifier, state = begin_login()
        payload = complete_login("c", verifier, state)
        assert payload["refresh_token"] is None

    def test_store_file_is_owner_only(self, store_dir, monkeypatch):
        monkeypatch.setattr(claude, "_token_request", lambda payload: _token_response())
        _, verifier, state = begin_login()
        complete_login("c", verifier, state)
        if os.name == "posix":
            assert (claude_auth_path().stat().st_mode & 0o777) == 0o600


class TestLoadClaudeLogin:
    def test_fresh_token_avoids_network(self, store_dir, monkeypatch):
        _write_store("at-1")
        monkeypatch.setattr(claude, "_refresh", lambda p: pytest.fail("must not refresh"))
        assert load_claude_login() == "at-1"

    def test_stale_token_refreshes_and_rotates_store(self, store_dir, monkeypatch):
        _write_store("at-1", "rt-1", expires_at=time.time() + 10)  # inside the margin
        monkeypatch.setattr(claude, "_token_request", lambda p: _token_response("at-2", "rt-2"))
        assert load_claude_login() == "at-2"
        on_disk = json.loads(claude_auth_path().read_text(encoding="utf-8"))
        assert on_disk["access_token"] == "at-2"
        assert on_disk["refresh_token"] == "rt-2"
        assert on_disk["expires_at"] == pytest.approx(time.time() + 3600, abs=5)


class TestAuthHome:
    """A home (a provider's authHome) relocates one account's store, so
    several Claude subscriptions ride side by side."""

    def test_store_lives_in_the_named_dir(self, store_dir, tmp_path):
        home = tmp_path / "accounts" / "claude-work"
        claude._write_store({"access_token": "at-work", "refresh_token": "rt",
                             "expires_at": time.time() + 3600}, str(home))
        assert claude_auth_path(str(home)) == home / "claude-auth.json"
        assert load_claude_login(home=str(home)) == "at-work"
        assert not claude_auth_path().exists()  # the default store is untouched

    def test_missing_home_store_names_the_dir_in_the_hint(self, store_dir, tmp_path):
        home = str(tmp_path / "accounts" / "nope")
        with pytest.raises(ClaudeAuthError, match=f"config login claude {home}"):
            load_claude_login(home=home)

    def test_refresh_of_one_home_leaves_the_default_alone(self, store_dir, tmp_path, monkeypatch):
        _write_store("at-default", "rt-default", expires_at=time.time() - 1)
        home = tmp_path / "accounts" / "claude-work"
        claude._write_store({"access_token": "at-work", "refresh_token": "rt-work",
                             "expires_at": time.time() - 1}, str(home))
        monkeypatch.setattr(claude, "_token_request",
                            lambda p: _token_response("at-2", "rt-2"))
        assert load_claude_login(home=str(home)) == "at-2"
        on_disk = json.loads((home / "claude-auth.json").read_text(encoding="utf-8"))
        assert on_disk["access_token"] == "at-2"
        # the default store was not refreshed nor overwritten
        assert json.loads(claude_auth_path().read_text(encoding="utf-8"))["access_token"] == "at-default"

    def test_logout_removes_only_that_home(self, store_dir, tmp_path):
        _write_store("at-default")
        home = tmp_path / "accounts" / "claude-work"
        home.mkdir(parents=True)
        (home / "claude-auth.json").write_text("{}", encoding="utf-8")
        removed = logout(str(home))
        assert removed == home / "claude-auth.json"
        assert claude_auth_path().exists()
        assert login_status(str(home)) is None


    def test_refresh_without_new_refresh_token_keeps_old(self, store_dir, monkeypatch):
        _write_store("at-1", "rt-1", expires_at=time.time() - 1)
        monkeypatch.setattr(claude, "_token_request",
                            lambda p: _token_response("at-2", refresh_token=None))
        assert load_claude_login() == "at-2"
        on_disk = json.loads(claude_auth_path().read_text(encoding="utf-8"))
        assert on_disk["refresh_token"] == "rt-1"

    def test_forced_refresh_even_when_fresh(self, store_dir, monkeypatch):
        _write_store("at-1")
        monkeypatch.setattr(claude, "_token_request", lambda p: _token_response("at-2"))
        assert load_claude_login(force=True) == "at-2"

    def test_store_without_refresh_token_dies_with_hint(self, store_dir, monkeypatch):
        _write_store("at-1", refresh_token=None)
        with pytest.raises(ClaudeAuthError, match="awerouter config login claude"):
            load_claude_login(force=True)

    def test_rejected_refresh_with_unchanged_store_dies(self, store_dir, monkeypatch):
        _write_store("at-1", "rt-1", expires_at=time.time() - 1)

        def rejected(payload):
            raise ClaudeAuthError("invalid_grant: token expired")

        monkeypatch.setattr(claude, "_refresh", rejected)
        with pytest.raises(ClaudeAuthError, match="invalid_grant"):
            load_claude_login()

    def test_rejected_refresh_rides_another_process_rotation(self, store_dir, monkeypatch):
        """Another awerouter process rotated the tokens underneath our refresh:
        the file now holds a different (fresh) refresh token — use it instead
        of surfacing the rejection."""
        _write_store("at-1", "rt-1", expires_at=time.time() - 1)

        def rejected_and_rotated(payload):
            _write_store("at-9", "rt-9")  # the other process wins the race
            raise ClaudeAuthError("invalid_grant: refresh token reuse")

        monkeypatch.setattr(claude, "_refresh", rejected_and_rotated)
        assert load_claude_login() == "at-9"

    def test_missing_store_hints_login(self, store_dir):
        with pytest.raises(ClaudeAuthError, match="awerouter config login claude"):
            load_claude_login()

    def test_broken_store_json(self, store_dir):
        claude_auth_path().parent.mkdir(parents=True, exist_ok=True)
        claude_auth_path().write_text("{not json", encoding="utf-8")
        with pytest.raises(ClaudeAuthError, match="cannot read"):
            load_claude_login()

    def test_store_without_access_token(self, store_dir):
        claude_auth_path().parent.mkdir(parents=True, exist_ok=True)
        claude_auth_path().write_text(json.dumps({"refresh_token": "rt"}), encoding="utf-8")
        with pytest.raises(ClaudeAuthError, match="awerouter config login claude"):
            load_claude_login()


class TestApplyClaudeAuth:
    def test_writes_full_header_set(self, store_dir):
        _write_store("tok-1")
        headers = {"authorization": "Bearer dummy-client-key"}
        apply_claude_auth(headers)
        assert headers["authorization"] == "Bearer tok-1"
        assert headers["anthropic-beta"] == claude.OAUTH_BETA
        assert headers["anthropic-version"] == "2023-06-01"

    def test_merges_oauth_beta_with_existing_flags(self, store_dir):
        _write_store("tok-1")
        headers = {"anthropic-beta": "interleaved-thinking-2025-05-14"}
        apply_claude_auth(headers)
        assert headers["anthropic-beta"] == (
            "interleaved-thinking-2025-05-14," + claude.OAUTH_BETA)

    def test_keeps_client_version_header(self, store_dir):
        _write_store("tok-1")
        headers = {"anthropic-version": "2023-01-01"}
        apply_claude_auth(headers)
        assert headers["anthropic-version"] == "2023-01-01"


class TestTokenRequest:
    def _fake_urlopen(self, monkeypatch, responses):
        """responses: list of values — a dict means HTTP 200, an exception is raised."""
        calls = []

        def fake(req, timeout=None):
            calls.append(req.full_url)
            out = responses[len(calls) - 1]
            if isinstance(out, Exception):
                raise out
            body = json.dumps(out).encode("utf-8")

            class _Resp:
                def read(self):
                    return body

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

            return _Resp()

        monkeypatch.setattr("awerouter.claude.urllib.request.urlopen", fake)
        return calls

    def test_success_hits_current_host(self, monkeypatch):
        calls = self._fake_urlopen(monkeypatch, [{"access_token": "at"}])
        resp = claude._token_request({"grant_type": "refresh_token"})
        assert resp["access_token"] == "at"
        assert calls == [claude.TOKEN_URL]

    def test_404_falls_back_to_legacy_host(self, monkeypatch):
        err = urllib.error.HTTPError(
            claude.TOKEN_URL, 404, "Not Found", None, io.BytesIO(b""))
        calls = self._fake_urlopen(monkeypatch, [err, {"access_token": "at"}])
        resp = claude._token_request({"grant_type": "refresh_token"})
        assert resp["access_token"] == "at"
        assert calls == [claude.TOKEN_URL, claude._LEGACY_TOKEN_URL]

    def test_grant_rejection_carries_detail(self, monkeypatch):
        err = urllib.error.HTTPError(
            claude.TOKEN_URL, 400, "Bad Request", None,
            io.BytesIO(json.dumps({"error": "invalid_grant",
                                   "error_description": "refresh token expired"}).encode()))
        self._fake_urlopen(monkeypatch, [err])
        with pytest.raises(ClaudeAuthError, match="invalid_grant: refresh token expired"):
            claude._token_request({"grant_type": "refresh_token"})

    def test_network_failure_names_the_host(self, monkeypatch):
        import socket
        self._fake_urlopen(monkeypatch, [
            urllib.error.URLError(socket.timeout("timed out"))])
        with pytest.raises(ClaudeAuthError, match="cannot reach"):
            claude._token_request({"grant_type": "refresh_token"})


class TestStatusAndLogout:
    def test_status_absent_when_logged_out(self, store_dir):
        assert login_status() is None

    def test_status_reports_expiry(self, store_dir):
        _write_store(expires_at=time.time() + 3600)
        status = login_status()
        assert status is not None and status["fresh"] is True
        assert datetime.fromtimestamp(status["expires_at"], tz=timezone.utc)

    def test_logout_removes_store(self, store_dir):
        _write_store()
        removed = logout()
        assert removed == claude_auth_path()
        assert not claude_auth_path().exists()

    def test_logout_without_store(self, store_dir):
        assert logout() is None


def test_sentinel_value():
    # providers.json writes this exact string; the docs and templates rely on it
    assert AUTH_SENTINEL == "claude"
