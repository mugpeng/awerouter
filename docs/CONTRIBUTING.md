# Contributing to awerouter

`awerouter` is intentionally small: a transparent Anthropic proxy that routes Claude Code requests by structural signals — no keyword guessing, no LLM classifier.

The project should stay focused on that job. Prefer changes that make routing clearer, safer, or cheaper to operate. Avoid turning it into a general API gateway, a model-evaluation framework, or a config platform.

## Development Setup

Clone the repository and run the tests:

```bash
git clone https://github.com/wehuman01/awerouter.git
cd awerouter
python3 -m pip install -e ".[dev]"
python3 -m pytest
```

For local CLI testing:

```bash
python3 -m pip install -e .
awerouter --help
```

## Branches

The repository uses two long-lived branches:

- `main` is the stable line.
- `dev` is the integration branch for day-to-day development.

Do feature work on `dev` unless a maintainer says otherwise.

## Engineering Taste

Prefer solutions that are simple, clear, decoupled, honest, focused, and durable.

- Simple: make the smallest change that solves the real problem.
- Clear: optimize for the next reader, not for cleverness.
- Decoupled: keep boundaries clean, but do not add abstractions without a real need.
- Honest: make complexity, state, side effects, assumptions, and failure modes visible.
- Focused: preserve the transparent-proxy model — inspect requests structurally, never parse response bodies, keep no session state.
- Durable: choose behavior that is easy to test and maintain.
- First principles: identify the real problem and hard constraints before adding concepts.

## Architecture

```
Claude Code
   │ POST /v1/messages (full history, stateless protocol)
   ▼
awerouter
   ├─ resolve(): L1 capability guard (web_search, image) → L2 tier
   │             label (backgroundModel/thinkModel) → L3 difficulty
   │             (request tokens > threshold) → L4 edit
   │             checkpoint (trailing tool batch changed code) → else default
   ├─ rewrite model + auth header, forward to provider
   ├─ pre-stream fallback: flash → pro on 429/408/5xx/network errors
   └─ opaque SSE passthrough (response bytes are never parsed)
   ▼
provider (flash: cheap/fast — pro: strong/accurate)
```

Key design decisions:

- **Context lives in the client.** Every request carries the full history; the router keeps no session state, so restarts are lossless and routing is decided per request.
- **Four-layer first-match-wins routing.** L1/L2/L4 are exact signals; L3 is the only threshold-sensitive layer, tunable via `awerouter usage calibrate`. L4 routes the turn after an edit to pro — flash drafts, pro reviews. Any edit-class call in the trailing parallel tool batch marks the batch (order-insensitive: `[Grep, Edit]` ≡ `[Edit, Grep]`); shell-wrapped calls (codex `exec_command`/`shell`) are classified by command text. L4 sits below L3 so long contexts never fall back to flash. Earlier versions also routed search/mechanical phases to flash; since flash is already the fall-through default, those rules changed nothing and were removed (v0.4.8).
- **Opaque response path.** The proxy streams response bytes through untouched. Anything that needs response parsing (e.g., output-token accounting) must justify breaking this property.
- **Fallback only before the first byte.** Once streaming starts, a request is never re-attempted, so clients never see duplicated output.

## Config Semantics

Two files in `~/.config/awerouter/` (override with `AWEROUTER_CONFIG_DIR`):

- `providers.json` — `{protocol: {provider: {base_url, auth?, auth_header?}}}`. Secrets use `${VAR}` references; missing env vars die at request time with an actionable message. `auth` is optional — omitted means a no-auth upstream (local model servers; requests go out with no auth header). `auth_header` is auto-detected from the base_url netloc (`anthropic.com` → `x-api-key`, else `Authorization` with auto-prefixed `Bearer `).
- `routing.json` — optional `settings` (`backgroundModel`, `thinkModel`, `toolRouting` with `webSearch`/`edit`; legacy `webSearchModel` still works, `imageModel`/`defaultModel` destination keys, `searchResultDiscount`, `longContextAuto`) plus profile entries `{protocol (id or list — a list serves several wire protocols on one port), port?, longContextThreshold, rtk?, odcp? (true or {dedup?, purgeErrors?}), destinations: {flash, pro}}` where a destination is `"provider,model"` and `longContextThreshold` is an integer or `"auto"`. Any settings key may also sit flat in a profile body and overrides the global one key by key (nested blocks field by field); missing keys inherit. Unknown keys die at load — in `settings` and in profile bodies.

Rules:

- `config show`/`list` cross-validate destinations against providers, so bad references fail at load time.
- Never print raw secret values; `config show` redacts literal keys.
- One `serve` process serves exactly one profile.

## Subscription Logins

Two auth sentinels ride subscription logins instead of API keys: `"auth": "codex"` (only in `openai-responses`; reads `$CODEX_HOME/auth.json`, default `~/.codex/auth.json`) and `"auth": "claude"` (only in `anthropic`; awerouter's own login via `awerouter config login claude` — the same PKCE device flow Claude Code uses, tokens in `~/.config/awerouter/claude-auth.json`, mode 0600). Each sentinel only loads in its own protocol group because its backend speaks exactly one wire protocol.

Refresh ownership follows who owns the login:

- **codex — read-only.** OpenAI refresh tokens are single-use and rotating; refreshing here would invalidate the CLI's login, so the CLI keeps sole ownership. awerouter re-reads `auth.json` on every request, and once more on an upstream 401 (the CLI usually refreshed it). Access tokens live ~10 days — keep using `codex` (or awewarm) and the login stays fresh.
- **claude — owned by awerouter.** The inverse design: access tokens are short-lived (~hours) and renewed with the rotating refresh token, the new pair persisted atomically before the request proceeds. An in-process lock plus a re-read under it keep concurrent requests from racing the refresh; a second awerouter process winning the race is recovered the same way (refresh rejected + file changed underneath → use the winner's token).

401 ladder (both sentinels): one forced refresh/re-read and retry of the same destination; a 401 that survives means the login is dead — flash requests fall back to a keyed pro destination (one printed line each, `401-retry` marker in `usage log`), and the 401 surfaces to the client only when pro rides the same login. A missing login is a 503 with a login hint (`run: codex login` / `run: awerouter config login claude`) plus a serve-start warning — deliberately no fallback, unlike a mid-session expiry: a missing login is a config error, and silently serving it from a paid pro would hide both the error and the bill.

codex/claude providers honor `https_proxy`/`all_proxy` (chatgpt.com, api.anthropic.com, and platform.claude.com often need the proxy); every other provider always connects directly.

Codex-backend normalization: `store` is forced to `false` (zero-data-retention; `store: true` is rejected), `max_output_tokens` is dropped (rejected), and since the backend has no non-streaming mode, a non-streaming request goes upstream as SSE and comes back buffered into a single JSON response with output items rebuilt from the stream events. Claude needs no body normalization; per-request headers injected instead: `Authorization: Bearer <access_token>` plus `anthropic-beta: oauth-2025-04-20`.

The Claude wire contract (client id, `platform.claude.com` authorize/token with legacy `console.anthropic.com` as a 404/405 fallback) is the reverse-engineered public contract shared by community clients — it can break without notice.

## Logging

Requests append one JSONL line to `~/.local/state/awerouter/requests.jsonl` (override with `AWEROUTER_LOG_DIR`; rotate at `AWEROUTER_LOG_MAX_BYTES`, default 50 MB). Entries include `request_id`, `profile`, label, destination, status, the L3 request-token estimate, and its per-type `tokens` breakdown (sum equals `token_count`). Log every completed request — even client disconnects and 502s — because `stats` and `calibrate` are only as honest as the log.

## Documentation

If you change command behavior, config shape, routing rules, or install steps, update the relevant docs in the same change:

- `README.md`
- `README_cn.md`
- `README.ai.md`
- `resources/skills/awerouter/SKILL.md`
- `docs/CHANGELOG.md`
- tests that define the behavior

## Testing

Before committing, run:

```bash
python3 -m pytest
```

If a change affects routing decisions, config parsing, or log/stats output, add or update tests under `tests/` — routing behavior is fully covered by unit tests and a fake-upstream integration harness (`tests/test_server.py`).

## Releasing

Releases are automated through GitHub Actions. The release path is:

1. Prepare release changes on `dev`.
2. Merge `dev` into `main`.
3. Push a `v*` tag from `main`.
4. Let `.github/workflows/release.yml` create the GitHub Release and publish to PyPI.

Before tagging a release:

- Update the version in `pyproject.toml` **and** `src/awerouter/__init__.py` (they must match; the release workflow fails the tag otherwise).
- Add a top-level `## vX.Y.Z` entry to `docs/CHANGELOG.md` (the workflow extracts it as the release notes and fails if missing).
- Confirm the repository has `PYPI_API_TOKEN` configured as a GitHub Actions secret.

Recommended local checks:

```bash
python3 -m pytest
python3 -m build
python3 -m twine check dist/*
```

The release workflow intentionally fails if the tag does not match the package version or the changelog entry is missing. Do not manually create the GitHub Release for a tagged release; the workflow owns that step.

## Questions

When in doubt, keep the change smaller. A focused fix or documentation improvement is better than a broad rewrite.
