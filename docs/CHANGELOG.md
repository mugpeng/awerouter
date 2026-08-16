# Changelog

## Unreleased

### Changed
- `config show [PROFILE]`: single-profile redacted view (the providers it uses + its routing entry); no argument keeps the full-config view.
- `config path` prints both config file paths (`providers.json`, `routing.json`) instead of the config directory.
- `config edit` opens `providers.json` or `routing.json` — the file is an optional argument (`providers` / `routing`) or an interactive choice — instead of opening the config directory; snapshots the file to `.bak` first.
- `awerouter add` wizard: prints a `providers.json` category overview, and provider selection is a choice list over the category's existing providers (`<new>` adds one) instead of free text with a hint.
- Removed `config init` (use top-level `awerouter init`); error hints updated to match.

### Added
- `awerouter restore [providers|routing]`: restore a config file from its `.bak` backup. Backups are single-slot (aweswitch convention) and written by `config edit` and the `add` wizard before every write.
- `usage clean`: deletes saved request logs after a confirmation prompt — moved off `usage stats --clean` so `stats` stays read-only.

## v0.3.1 - 2026-08-16

### Changed
- **CLI cleanup**: removed `awerouter show` (use `awerouter config show` instead) and removed the bare `awerouter usage` default view (use `awerouter usage stats` explicitly).

## v0.3.0 - 2026-08-16

### Added
- AI agent setup docs (`README.ai.md`) with step-by-step install, config, and verification guide.
- `awerouter` skill docs for AI agent management of routing via natural language.
- Environment variable setup guide for `${ENV_VAR}` auth references across platforms.
- aweswitch integration section: profile-based launching with `ANTHROPIC_BASE_URL` pointing at the awerouter daemon.
- `webSearchModel` setting: L1 web_search traffic now routes to `settings.webSearchModel` (default `pro`) instead of hardcoded pro.
- README badge reorganized; Ko-fi badge moved to header.

## v0.2.9 - 2026-08-16

### Breaking
- **CLI restructure**: `log`, `stats`, `savings`, and `calibrate` merge into one `usage` group — `awerouter usage [stats|tail|savings|calibrate]`. Bare `awerouter usage` shows the stats summary; window options (`--since`, `--profile`) sit between `usage` and the subcommand.

### Added
- **Unversioned endpoint aliases**: `/chat/completions`, `/responses`, and `/models` are served alongside the `/v1/...` forms, so OpenAI-style clients work whether their base_url includes `/v1` or not. Fixes the `404: Not Found` hit by clients configured with a bare `http://127.0.0.1:20128` base (Anthropic clients append `/v1/messages` themselves; OpenAI clients append the bare path). The serve banner now suggests the standard `/v1` form for openai protocols.
- Typo-friendly command resolution at every level (top level, `usage`, `config`): an unknown subcommand close to a real one gets a did-you-mean suggestion (`awerouter server x` → "did you mean 'serve'?"), and far-off tokens with stray arguments get a `-h` pointer instead of the cryptic "Got unexpected extra argument". Valid bare-profile launches (`awerouter <profile> [--port/--host]`) are unaffected.
- `usage stats` rework: `~total_tokens` (estimated input message tokens) replaces the meaningless `total_bytes`; new `by_model` breakdown, error and fallback counts, and percentages on all breakdowns.
- Latency percentiles per destination **and** per provider/model, in two flavors: first-byte (`ms`) and total request duration (`duration_ms`, now logged per request; legacy entries without it are excluded from totals).
- Window filters `--since today|yesterday|Nd|YYYY-MM-DD` and `--profile NAME` on every `usage` view (entries with unparseable timestamps are excluded while filtering), plus a coverage note when the requested window predates the oldest retained log entry.
- `usage stats --clean` deletes the saved request log and its rotated backup after a confirmation prompt.
- `usage savings`: token accounting vs a pro-only baseline — message-input tokens per tier (with per-request averages), pro input tokens offloaded to flash, fallback count, and the offload share. Tokens only by design; no prices in config (multiply by your providers' input prices yourself).
- `usage savings` cache sensitivity: brackets the offload between "all cache reads" (~0.1x) and "all full price" (1x) under Anthropic-style cache economics (write ~1.25x, TTL 5 min), and reports switch cadence vs the TTL (flash<->pro alternations, consecutive-pro gaps, expired gaps) so users can judge how much a cache-warm pro-only baseline would have discounted the naive number.
- `usage savings` ends with ready-to-fill money formulas using the measured token counts (`upper` and `cache-aware`, prices per 1M tokens) — users substitute their providers' input prices and read off the saved amount.

## v0.2.5 - 2026-08-16

Protocol-based provider grouping with same-protocol passthrough for all three major wire protocols.

### Breaking
- **Config schema**: `providers.json` outer keys are now protocol ids (`anthropic` / `openai-chat` / `openai-responses`) instead of agent names; `routing.json` profiles declare `protocol` instead of `agent`. Old configs fail at load with rename hints (`claude` → `anthropic`, `codex` → `openai-chat` / `openai-responses`).
- **base_url semantics** follow each native client's convention: anthropic = `ANTHROPIC_BASE_URL` style (no `/v1`, awerouter appends `/v1/messages`); openai = `OPENAI_BASE_URL` style (includes the version segment, awerouter appends `/chat/completions` or `/responses`). Copy the URL verbatim from your client config — the same provider can use different paths per protocol (e.g. GLM: `.../api/coding/paas/v4` for chat, `.../api/v1` for responses).

### Added
- **OpenAI protocol support, same-protocol passthrough** (no translation): `POST /v1/chat/completions` and `POST /v1/responses` are served alongside `/v1/messages`. The response path stays opaque byte streaming; only request-side signal extraction is per protocol.
- Per-protocol signal extraction (`protocols.py`): text/image/web_search detection for all three request shapes, including responses-API `input` items (reasoning/function-call items carry no text and are skipped) and builtin `{type: "web_search"}` tools.
- All endpoints are always mounted; hitting one that doesn't match the profile's protocol returns a clear JSON 400 instead of a bare 404.
- Serve banner prints per-protocol client hints: `ANTHROPIC_BASE_URL` + tier env for anthropic, `OPENAI_BASE_URL` + Codex `wire_api` for the openai protocols.

### Notes
- OpenAI clients are single-model (no tier env story like Claude Code), so L2 tier matching effectively never fires for them — openai traffic routes by L1 + L3 with a flash default. Fallback, logging, stats, and calibrate are protocol-agnostic.

## v0.2.0 - 2026-08-16

Per-profile observability, project support, and release automation.

### Added
- `stats` groups by routing profile and estimates **pro input offloaded to flash**: message tokens of flash-served requests a pro-only setup would have billed at pro's input price (system prompt and tools excluded, so conservative).
- Request log records the serving profile (`profile` field); entries logged before this feature group under `(unknown)`.
- Project support: Ko-fi badge and Support section in both READMEs, `FUNDING.yml` for the GitHub sponsor button, WeChat Pay QR under `assets/images/`.
- CI workflow: test matrix (Ubuntu/macOS/Windows × Python 3.9/3.13) plus build/twine-check package job on `main` and `dev`.
- Release automation: pushing a `v*` tag verifies tag↔version match, runs tests, builds, extracts the changelog entry into the GitHub Release, and publishes to PyPI (`PYPI_API_TOKEN` secret).
- PyPI package metadata: readme, author, keywords, classifiers; `MANIFEST.in` ships READMEs and assets in the sdist.
- `docs/CONTRIBUTING.md`.

### Fixed
- `__version__` in `awerouter/__init__.py` had drifted from `pyproject.toml`; both now track the release version.

## v0.1.5 - 2026-08-16

Multi-provider profile-based routing, interactive onboarding, and code-quality hardening.

### Highlights
- **Agent-grouped providers**: `providers.json` now groups providers by agent (`claude` / `codex` / `opencode`), and each routing profile declares its `agent`, making it possible to route different agent types through the same daemon.
- **Configurable web_search routing**: L1 `web_search` destination is no longer hard-coded to `pro`; it now follows `settings.webSearchModel`, so operators can redirect it independently.
- **Interactive profile wizard**: `awerouter add` walks users through profile creation step by step, auto-creating any new providers with `${VAR}` auth references and keeping `providers.json` / `routing.json` references consistent.
- **Profile management commands**: `awerouter list` (one-line overview), `awerouter show [PROFILE]` (redacted single-profile or full-config view), and `awerouter <PROFILE>` shorthand for `serve <PROFILE>`.

### Fixed / Hardened
- `_proxy_request` no longer mutates the request body in place (shallow copy per upstream attempt).
- `detect_auth_header` matches the URL netloc instead of a substring — `https://evil.com/anthropic.com` no longer misdetected as Anthropic.
- `config show` now cross-validates `routing.json` destinations against `providers.json`, so bad references fail immediately instead of on first request.
- Network-level upstream failures now append a status-502 entry to the request log instead of leaving no trace.

### Added
- `serve` warns at startup when shell proxy vars are set without loopback exempted in `no_proxy` — the cause of empty-body 502s from proxied clients.
- `awerouter init` — top-level alias for `config init`.
- `awerouter add` — interactive wizard that builds a routing profile step by step, creating any new providers (auth stored as `${VAR}` refs) and keeping the two-file references consistent.
- `awerouter list` — one-line-per-profile overview (name, agent, flash, pro, threshold).
- `awerouter show [PROFILE]` — single-profile redacted view (providers it uses + routing entry); without an argument it shows the whole config.
- `awerouter <PROFILE>` — bare profile name as shorthand for `serve <PROFILE>` (defined commands always win over profile names).
- `serve` startup banner now prints the ready-to-copy `export ANTHROPIC_BASE_URL=...` line and the tier env vars for the aweswitch profile.
- `config edit` auto-initializes the default config when missing instead of erroring.
- Per-request `request_id` (reuses client `x-request-id` when present, otherwise generated) written to the request log and shown by `awerouter log`.
- Log rotation: the request log rotates to `requests.jsonl.1` when it exceeds `AWEROUTER_LOG_MAX_BYTES` (default 50 MB); `awerouter log` reads from the end of the file instead of loading it whole.
- `calibrate` output now clarifies the distribution counts message tokens only (system prompt and tools excluded).

## 0.1.0 - 2026-08-13

Initial release of awerouter — a local daemon that routes Claude Code requests to different providers/models based on structural request signals, on a single port.

### Features
- **Three-layer first-match-wins router**: L1 capability guard (`web_search` tool → pro), L2 tier-label match (background/think model ids), L3 difficulty score (long context / image → pro, default → flash).
- **Opaque SSE proxy**: streams Anthropic `/v1/messages` responses byte-for-byte without parsing or buffering; logs are written even on client disconnect.
- **Two-file config**: `providers.json` (secrets, `${VAR}` expansion, redacted in `config show`) and `routing.json` (strategy, safe to commit).
- **count_tokens passthrough** and `GET /v1/models` advertising the tier model ids.
- **Pre-stream flash → pro fallback** on transient upstream errors (429/408/5xx), before any byte is sent.
- **Structured append-only request log** (JSONL) with `log`, `stats`, and `calibrate` commands; `calibrate` shows L3 token distribution to tune `longContextThreshold`.
- **aweswitch integration** via a single profile pointing `ANTHROPIC_BASE_URL` at awerouter.

### Documentation
- Bilingual README (en + zh).
- MPL-2.0 license.
