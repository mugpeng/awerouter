"""Claude subscription account (OAuth login owned by awerouter) as a provider auth source.

A providers.json entry with "auth": "claude" routes through a Claude Pro/Max
subscription login that awerouter itself owns — no local Claude Code CLI login
is needed (and none is borrowed: the CLI keeps its own credentials when
present; each OAuth login is an independent session). `awerouter config login claude`
runs the same PKCE device flow the Claude Code CLI uses:

  browser  {AUTHORIZE_URL}   (user logs in, pastes the shown code back here)
  POST     {TOKEN_URL}       (authorization_code + code_verifier + state)
  POST     {TOKEN_URL}       (refresh_token; rotates, new one saved atomically)

  POST {base_url}/v1/messages       (Anthropic Messages protocol, anthropic group)
  Authorization: Bearer <access_token>
  anthropic-beta: oauth-2025-04-20  (required for OAuth tokens, merged with
                                      any beta flags already on the request)

The wire contract (endpoints, client id, header set) is the reverse-engineered
public one shared by every community client; it can drift, and per Anthropic's
2026 policy third-party use of subscription OAuth tokens is ToS-restricted —
this rides the user's own subscription, at their own risk.

Unlike the codex login (read-only by design — OpenAI refresh tokens are
single-use, the CLI owns refresh), awerouter owns this login end to end:
access tokens are short-lived (~hours) and refreshed here with a rotating
refresh token, persisted in the config dir with 0600 perms. A threading lock
plus a re-read-and-compare keeps concurrent in-flight requests from racing
the refresh (the second one reuses the first one's tokens).

Multiple accounts: a provider's optional "authHome" names the dir this
account's store lives in (<authHome>/claude-auth.json instead of the config
dir's single claude-auth.json), so several Claude subscriptions ride side by
side as separate providers; `config login claude <dir>` fills one. Each store
refreshes independently — one lock serializes them all, which is correct
(they share no state) and rare enough never to matter.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Sentinel providers.json auth value selecting the awerouter-owned Claude login.
AUTH_SENTINEL = "claude"

# Claude Code's hard-coded public OAuth client (no third-party registration
# exists; every community client uses this one).
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

# The OAuth host moved console.anthropic.com -> platform.claude.com; the legacy
# token URL stays as a fallback when the current one 404/405s.
AUTHORIZE_URL = "https://platform.claude.com/oauth/authorize"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_LEGACY_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
SCOPES = (
    "org:create_api_key user:profile user:inference user:sessions:claude_code "
    "user:mcp_servers user:file_upload"
)

# Beta flag the Messages API requires on OAuth-token-authenticated requests.
OAUTH_BETA = "oauth-2025-04-20"

# Treat the access token as expiring this early, so a refresh never races the
# clock into a 401 (mirrors the 5-minute margin the CLI family uses).
EXPIRY_MARGIN_S = 300

_refresh_lock = threading.Lock()


class ClaudeAuthError(Exception):
    """No usable Claude login: store missing, rejected, or unreachable."""


def claude_auth_path(home: "str | None" = None) -> Path:
    # An explicit home (a provider's authHome) relocates this account's store;
    # default mirrors config.config_dir (importing it would cycle: config
    # imports this module).
    base = home or os.environ.get("AWEROUTER_CONFIG_DIR", "~/.config/awerouter")
    return Path(base).expanduser() / "claude-auth.json"


# ---------------------------------------------------------------------------
# OAuth wire (PKCE)
# ---------------------------------------------------------------------------


def begin_login() -> tuple[str, str, str]:
    """(authorize_url, code_verifier, state) for a fresh PKCE pair."""
    verifier = secrets.token_urlsafe(64)[:128]  # RFC 7636 allows 43-128 chars
    state = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    from urllib.parse import urlencode
    url = AUTHORIZE_URL + "?" + urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    return url, verifier, state


def _token_request(payload: dict) -> dict:
    """POST one OAuth token request (exchange or refresh), with legacy-host fallback."""
    last_error = None
    for token_url in (TOKEN_URL, _LEGACY_TOKEN_URL):
        req = urllib.request.Request(
            token_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json", "accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code in (404, 405) and token_url != _LEGACY_TOKEN_URL:
                last_error = f"{token_url} -> {exc.code}"  # host moved again; try legacy
                continue
            detail = _error_detail(body) or f"HTTP {exc.code}"
            raise ClaudeAuthError(f"claude oauth {payload['grant_type']} rejected: {detail}") from None
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise ClaudeAuthError(f"cannot reach {token_url}: {exc}") from None
    raise ClaudeAuthError(f"token endpoints unreachable: {last_error}")


def _error_detail(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return ""
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("error_description") or err.get("error") or "")
    if isinstance(err, str):
        desc = payload.get("error_description")
        return f"{err}: {desc}" if desc else err
    return ""


def _normalize_tokens(resp: dict, previous: dict | None = None) -> dict:
    """Token response -> store payload; a missing refresh_token keeps the old one."""
    access_token = resp.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ClaudeAuthError("claude oauth response has no access_token")
    try:
        expires_in = int(resp.get("expires_in"))
    except (TypeError, ValueError):
        expires_in = 3600
    refresh_token = resp.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        refresh_token = (previous or {}).get("refresh_token")
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": time.time() + expires_in,
        "obtained_at": datetime.now(timezone.utc).isoformat(),
        "scopes": resp.get("scope") or SCOPES,
    }


# ---------------------------------------------------------------------------
# Token store
# ---------------------------------------------------------------------------


def _read_store(home: "str | None" = None) -> dict:
    path = claude_auth_path(home)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ClaudeAuthError(f"claude login not found: {path} — run: awerouter config login claude{f' {home}' if home else ''}") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaudeAuthError(f"cannot read claude login {path}: {exc}") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
        raise ClaudeAuthError(f"no access_token in {path} — run: awerouter config login claude{f' {home}' if home else ''}")
    return payload


def _write_store(payload: dict, home: "str | None" = None) -> None:
    path = claude_auth_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)  # bearer + refresh tokens: owner-only, ssh-key style
    os.replace(tmp, path)


def _fresh(payload: dict) -> bool:
    try:
        return time.time() < float(payload.get("expires_at", 0)) - EXPIRY_MARGIN_S
    except (TypeError, ValueError):
        return False


def _refresh(payload: dict) -> dict:
    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise ClaudeAuthError(
            "claude login has no refresh_token — run: awerouter config login claude")
    resp = _token_request({
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
    })
    return _normalize_tokens(resp, payload)


def load_claude_login(force: bool = False, home: "str | None" = None) -> str:
    """A valid access token, refreshing first when stale (or forced).

    Thread-safe: under the lock the store is re-read, so a concurrent refresh
    in this process is reused instead of raced. A refresh rejected because
    another awerouter process rotated the token underneath us recovers the
    same way (file changed -> fresh token wins).
    """
    payload = _read_store(home)
    if not force and _fresh(payload):
        return payload["access_token"]
    with _refresh_lock:
        payload = _read_store(home)
        if not force and _fresh(payload):
            return payload["access_token"]
        seen_refresh = payload.get("refresh_token")
        try:
            payload = _refresh(payload)
        except ClaudeAuthError:
            current = _read_store(home)
            if current.get("refresh_token") != seen_refresh and _fresh(current):
                return current["access_token"]  # another process refreshed; ride it
            raise
        _write_store(payload, home)
        return payload["access_token"]


def apply_claude_auth(headers: dict, home: "str | None" = None, force: bool = False) -> None:
    """Write the full claude auth header set from the owned login."""
    headers["authorization"] = f"Bearer {load_claude_login(force, home)}"
    flags = [f.strip() for f in headers.get("anthropic-beta", "").split(",") if f.strip()]
    if OAUTH_BETA not in flags:
        flags.append(OAUTH_BETA)
    headers["anthropic-beta"] = ",".join(flags)
    headers.setdefault("anthropic-version", "2023-06-01")


# ---------------------------------------------------------------------------
# CLI entry points (login / logout in cli.py)
# ---------------------------------------------------------------------------


def complete_login(code: str, verifier: str, state: str, home: "str | None" = None) -> dict:
    """Exchange the pasted authorization code; returns the stored payload."""
    resp = _token_request({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "state": state,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    })
    payload = _normalize_tokens(resp)
    _write_store(payload, home)
    return payload


def login_status(home: "str | None" = None) -> "dict | None":
    """Stored login summary for display, or None when logged out."""
    try:
        payload = _read_store(home)
    except ClaudeAuthError:
        return None
    return {
        "path": str(claude_auth_path(home)),
        "expires_at": payload.get("expires_at"),
        "fresh": _fresh(payload),
    }


def logout(home: "str | None" = None) -> "Path | None":
    """Delete the stored login. Returns the removed path, or None if absent."""
    path = claude_auth_path(home)
    if not path.exists():
        return None
    path.unlink()
    return path
