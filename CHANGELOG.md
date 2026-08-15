# Changelog

## Unreleased

Code-quality hardening pass (no behavior change on the happy path).

### Fixed / Hardened
- `_proxy_request` no longer mutates the request body in place (shallow copy per upstream attempt).
- `Destination` is pure data again: provider resolution happens at the call site, removing the two-phase init and the lying `ResolveResult.provider` type.
- `detect_auth_header` matches the URL netloc instead of a substring — `https://evil.com/anthropic.com` no longer misdetected as Anthropic.
- `config show` now cross-validates routing.json destinations against providers.json, so bad references fail immediately instead of on first request.

### Added
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
