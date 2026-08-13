# Changelog

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
