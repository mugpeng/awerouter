# RTK tool-result compression — design

Date: 2026-08-18
Status: approved (approach: Python port embedded in awerouter; default off, per-profile opt-in; full 12-filter set)

## Problem

Coding agents (Claude Code, Codex, OpenCode) resubmit the whole conversation every
turn, and the bulk of it is tool results: `git diff`, grep hits, directory
listings, build logs. These dominate input tokens on long sessions. 9router
solves this with an in-proxy compression layer ("RTK") that rewrites tool-result
text before forwarding upstream, typically cutting 20–40% of request tokens.

awerouter wants the same capability without giving up its zero-dependency,
zero-compile deployment or its fail-open request path.

## Why a Python port (not the rtk binary)

rtk-ai/rtk (Apache 2.0) is an 85K-line Rust CLI product; the piece a proxy needs
is ~200 lines of format detection plus the filter transforms — exactly what
9router extracted into 1,171 lines of dependency-free JS (MIT). Calling the
binary per tool result would spawn up to hundreds of processes per request
(history is recompressed every turn, deterministically, to keep prompt-cache
prefixes stable); embedding via PyO3 would add a cross-platform build matrix for
~1K lines of pure string logic. A faithful Python port of the 9router pipeline is
the right size: pure functions, no IO, no state, milliseconds of CPU per request.

## What changes

### Positioning

awerouter stays a same-protocol passthrough proxy. The one new caveat — it can
now optionally rewrite tool-result content — is opt-in per profile and off by
default. README/README_cn/skill wording changes from "never rewrites request
bodies" to "no protocol translation; optional per-profile tool-result
compression (RTK, default off)".

### New subpackage `src/awerouter/rtk/`

Port of 9router `open-sse/rtk/` (index/autodetect/applyFilter/constants/filters),
translated to Python with the same semantics, constants, and detection order:

- `constants.py` — thresholds (MIN_COMPRESS_SIZE 500 chars, RAW_CAP 10 MiB,
  DETECT_WINDOW 1024, per-filter caps). Length is `len(str)` (code points);
  upstream JS used UTF-16 units, Rust bytes — documented divergence.
- `apply.py` — `safe_apply`: filter exception → stderr warning + raw passthrough.
- `filters.py` — 12 filters: git-diff, git-status, git-log, build-output, grep,
  find, tree, ls, search-list, read-numbered, dedup-log, smart-truncate. Each
  carries a `filter_name` attribute (mirrors upstream `fn.filterName`).
- `autodetect.py` — `detect_filter(text)`: the upstream priority chain, including
  build-output-before-porcelain and the Windows drive-letter path quirk.
- `__init__.py` — `compress_body(body, protocol)` walks the protocol's
  tool-result locations and rewrites in place; `RtkStats`; `format_log(stats)`.
  Traversal is keyed on the profile's protocol (awerouter knows it; 9router
  sniffs shapes): anthropic `tool_result` blocks (skip `is_error`), openai-chat
  `role:"tool"` messages, openai-responses `function_call_output` items.
  Kiro/Gemini shapes are not ported.

Fail-open is three-layered, as upstream: `safe_apply` catches filter errors;
`compress_body` catches traversal errors (body already mutated in place —
uncompressed parts simply pass through); `_compress_text` guards (below 500
chars or above 10 MiB untouched; empty or larger output reverts to original).

### Determinism

All filters are pure text transforms — no timestamps, no randomness. The same
history compresses to the same bytes every turn, so provider prompt-cache
prefixes survive. This is a hard invariant; tests assert byte-identical repeat
compression.

### Integration points (`server.py`)

- `_proxy_flow`: compress right after `request.json()`, **before**
  `_RoutingState`/`extract` — routing decisions (L3 threshold,
  `effective_tokens`, file-search discount) and usage logs then reflect what is
  actually billed upstream. Compression runs once; retries reuse the body.
- `handle_count_tokens`: same compression, so client-side context estimates
  match reality.
- Serve banner gains one line when enabled: `rtk -> on (…; opt out per request:
  X-Awerouter-Token-Saver: off)`.
- Request-level escape hatch: header `X-Awerouter-Token-Saver: off` bypasses
  compression for that request (mirrors 9router's semantics).

### Config

`routing.json` profile field `"rtk": true` (default absent/false = transparent
passthrough). `config.py` validates bool; `config show` prints it only when set
(same convention as `port`). `awerouter add` wizard and `default-routing.json`
template are unchanged for now.

### Observability

`RequestLog` gains `rtk_saved: int` (estimated input tokens saved:
`estimate_tokens(before) - estimate_tokens(after)` summed over rewritten texts;
0 = off). `usage` CLI does not aggregate it yet — the data lands in
requests.jsonl for future reporting. Serve console prints one
`[rtk] saved …` line per request that hit.

### Calibration interaction

Once RTK is on, logged token counts are post-compression. A threshold
calibrated on uncompressed traffic will over-trigger pro; re-run
`usage calibrate` after enabling. `"auto"` thresholds self-correct after the
`longContextAuto.windowDays` window (they mix pre/post samples during
transition). Documented in README.

## Testing

- `tests/test_rtk.py` (ported from 9router `rtk.test.js` cases): all 12
  autodetect branches, safe_apply fail-open, guard clauses (min-size skip,
  never-empty, never-grow), `is_error` skip, per-protocol shape rewrites
  (anthropic string/array, openai-chat string/array, responses string/array),
  determinism, Windows drive-letter paths.
- `tests/test_server.py`: profile with `rtk=True` forwards compressed bodies
  (upstream capture), bypass header restores raw body, default off leaves body
  untouched, `count_tokens` compressed consistently, `rtk_saved` logged.
- `tests/test_config.py`: `rtk` parses, defaults false, non-bool dies, display
  shows when set.

## Attribution

Module docstring credits rtk-ai/rtk (Apache 2.0, `pipe_cmd.rs` and filter
sources) and 9router's JS port (MIT, `open-sse/rtk/`), which this code was
translated from.

## Out of scope

headroom (external LLM compression), pxpipe, caveman/ponytail prompt injection,
Kiro/Gemini message shapes, response-side compression, CLI aggregation of
`rtk_saved`, `add`-wizard integration, flipping the default to on.
