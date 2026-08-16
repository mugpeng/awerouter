# Design: protocol-based provider grouping

Date: 2026-08-16
Status: approved (in chat), implementing

## Problem

providers.json groups providers by *agent* (`claude`, `codex`). The only
supported client is Claude Code; what actually distinguishes provider groups
is the wire protocol the endpoint speaks. A profile should declare a
protocol, and the serve banner should tell the matching client where to
point.

## Decision

Replace the agent dimension with a protocol dimension. Three protocols:

| id                | endpoint (appended to base_url) | client                    |
|-------------------|---------------------------------|---------------------------|
| `anthropic`       | `/v1/messages`                  | Claude Code (`ANTHROPIC_BASE_URL`) |
| `openai-chat`     | `/v1/chat/completions`          | OpenAI-compatible clients / Codex (`wire_api = "chat"`) |
| `openai-responses`| `/v1/responses`                 | Codex (`wire_api = "responses"`) |

**No protocol translation.** Same-protocol passthrough only: awerouter
receives protocol X and forwards protocol X. The response path is already
opaque byte streaming, so SSE shape differences cost nothing; only the
request-side signal extraction is per-protocol.

## Changes

### Config (breaking, no auto-migration)

- providers.json outer keys: protocol ids. Old `claude`/`codex` groups fail
  at load with a rename hint (`claude` → `anthropic`, `codex` →
  `openai-chat` / `openai-responses`).
- routing.json: `"agent"` → `"protocol"` (same value space). Old field fails
  with a rename hint. Unknown protocol ids fail with the valid list.
- base_url semantics: prefix before the protocol's endpoint path (unchanged
  for anthropic; openai providers drop the trailing `/v1`).
- `add` wizard / `list` / `show` / validation use the protocol field.

### New module: protocols.py

Registry keyed by protocol id: signal extractor + endpoint path.

Extractors produce `InspectResult` from each request shape:

- anthropic: `messages`, text/image content blocks, `tools[].name` prefix
  `web_search_`.
- openai-chat: `messages`, `text`/`image_url` parts, nested
  `tools[].function.name` (flat `name` accepted leniently).
- openai-responses: `input` string or items, `input_text`/`output_text`/
  `text` parts, `input_image` parts, builtin `{type: "web_search"}` tools or
  flat function names. Non-message items (`reasoning`, `function_call`,
  `function_call_output`) carry no text and are skipped — consistent with
  the existing "messages only, system/tools excluded" token accounting.

Shared token estimate (non-CJK ÷ 4, CJK ÷ 1.5) moves here; `router.resolve`
is unchanged except it now takes a precomputed `InspectResult` instead of a
raw body.

### Server

- The retry/fallback/streaming/log loop is extracted into one
  `_proxy_flow(request, protocol)`; the three POST handlers are thin.
  All three endpoints are always mounted; a protocol mismatch returns a
  clear JSON 400 ("profile X speaks anthropic; this endpoint serves
  openai-chat").
- `count_tokens` is anthropic-only (guard, 400 otherwise); OpenAI has no
  such endpoint.
- Banner prints per-protocol client hints: anthropic → `ANTHROPIC_BASE_URL`
  + tier env (current); openai protocols → `OPENAI_BASE_URL` + Codex
  `wire_api` note.

### Known behavior boundary (documented)

OpenAI clients are single-model (no tier env story like Claude Code), so L2
tier matching effectively never fires for them; openai traffic routes by
L1 + L3 with a flash default. Fallback, logging, stats, calibrate are
protocol-agnostic.

### Testing

- Per-protocol extractor unit tests (text/image/web_search variants,
  CJK+ASCII token estimate, responses item-type skipping).
- Server e2e against a fake upstream for all three endpoints: routing,
  model rewrite, auth replacement, passthrough, fallback.
- Config: new schema validation, old `agent` field / old group errors.

### Migration

Hard break; user hand-edits config (awerouter's own ~/.config done during
implementation). Version bump to 0.3.0, README (en/cn) + CHANGELOG updated.
