# Contributing to awerouter

`awerouter` is intentionally small: a transparent Anthropic proxy that routes Claude Code requests by structural signals — no keyword guessing, no LLM classifier.

The project should stay focused on that job. Prefer changes that make routing clearer, safer, or cheaper to operate. Avoid turning it into a general API gateway, a model-evaluation framework, or a config platform.

## Development Setup

Clone the repository and run the tests:

```bash
git clone https://github.com/mugpeng/awerouter.git
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
   ├─ resolve(): L1 capability guard (web_search) → L2 tier label
   │             (backgroundModel/thinkModel) → L3 difficulty
   │             (request tokens > threshold or image) → else flash
   ├─ rewrite model + auth header, forward to provider
   ├─ pre-stream fallback: flash → pro on 429/408/5xx/network errors
   └─ opaque SSE passthrough (response bytes are never parsed)
   ▼
provider (flash: cheap/fast — pro: strong/accurate)
```

Key design decisions:

- **Context lives in the client.** Every request carries the full history; the router keeps no session state, so restarts are lossless and routing is decided per request.
- **Three-layer first-match-wins routing.** L1/L2 are exact signals; L3 is the only threshold-sensitive layer, tunable via `awerouter usage calibrate`.
- **Opaque response path.** The proxy streams response bytes through untouched. Anything that needs response parsing (e.g., output-token accounting) must justify breaking this property.
- **Fallback only before the first byte.** Once streaming starts, a request is never re-attempted, so clients never see duplicated output.

## Config Semantics

Two files in `~/.config/awerouter/` (override with `AWEROUTER_CONFIG_DIR`):

- `providers.json` — `{agent: {provider: {base_url, auth, auth_header?}}}`. Secrets use `${VAR}` references; missing env vars die at request time with an actionable message. `auth_header` is auto-detected from the base_url netloc (`anthropic.com` → `x-api-key`, else `Authorization` with auto-prefixed `Bearer `).
- `routing.json` — optional `settings` (`backgroundModel`, `thinkModel`, `webSearchModel`) plus profile entries `{agent, longContextThreshold, destinations: {flash, pro}}` where a destination is `"provider,model"`.

Rules:

- `config show`/`list` cross-validate destinations against providers, so bad references fail at load time.
- Never print raw secret values; `config show` redacts literal keys.
- One `serve` process serves exactly one profile.

## Logging

Requests append one JSONL line to `~/.local/state/awerouter/requests.jsonl` (override with `AWEROUTER_LOG_DIR`; rotate at `AWEROUTER_LOG_MAX_BYTES`, default 50 MB). Entries include `request_id`, `profile`, label, destination, status, and the L3 request-token estimate. Log every completed request — even client disconnects and 502s — because `stats` and `calibrate` are only as honest as the log.

## Documentation

If you change command behavior, config shape, routing rules, or install steps, update the relevant docs in the same change:

- `README.md`
- `README_cn.md`
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
