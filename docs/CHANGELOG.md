# Changelog

## v0.4.6 - 2026-08-18

RTK compression fixes from real-traffic verification: read dumps were never compressed, and one detection bug actively corrupted Claude Code reads.

### Added
- RTK savings surfaced in `usage` views (previously `rtk_saved` was logged but never shown): the shared header on `usage log/stats/tokens/savings` prints `rtk: saved N input tokens (x/y requests compressed)` when the window has any; `usage log` appends `rtk=+N` to entries that were compressed (`+` marks trimmed tokens, not included in `tokens=`); `usage savings` adds an rtk block noting it stacks with flash offload. Backed by `logging.rtk_totals(since, profile)`. Nothing prints when nothing was compressed.

### Fixed
- `autodetect.py` read-numbered gate counted lines inside the 1024-char detection window (max a few dozen), so the `>= 250` threshold never held and every file-read dump fell through to dedup-log with zero savings. The gate now counts full-text lines.
- `READ_NUMBERED_LINE_RE` only matched Cursor's `N|content`. It now also matches opencode's `N: content` (`read.ts` emits `${n}: ${line}`; the `: ` variant requires the space so clock times don't match) and Claude Code's `N→content`.
- `_RE_PORCELAIN` matched any line with 3+ leading spaces as git-status porcelain, so Claude Code read dumps (right-padded line numbers) were rewritten to a single "clean — nothing to commit" line. Real porcelain never has both XY slots blank (git omits unmodified entries); a lookahead now rejects that case.
- Long unique-line blobs (codex reads files via shell `cat`/`sed`, no line numbers) hit dedup-log, saved nothing, and smart-truncate was unreachable behind it. `_compress_text` now falls back to smart-truncate when dedup-log yields no shrink and the text is ≥ 250 lines.

## v0.4.5 - 2026-08-18

L4 matures: parallel batches get deterministic precedence, codex's shell-wrapped calls are classified, todo/task turns join the flash side, and every tool-keyed rule (L1 webSearch included) now lives in one `settings.toolRouting` block.

### Added
- Trailing-batch semantics: the L4 signal is now the trailing parallel batch of tool calls (`InspectResult.last_tools`) taking its strongest phase (`last_phase`, edit > search > mechanical) — `[Grep, Edit]` and `[Edit, Grep]` route identically.
- Shell sniffing feeds L4: a trailing `exec_command`/`shell` call is classified by its command text — search binaries count as search, `apply_patch` counts as edit (closes the codex blind spot; shared `_pipeline_heads` parser).
- New `mechanical` phase: todo/subagent bookkeeping tools (`todo`/`todos`/`todowrite`/`todo_write`/`task`) route to `toolRouting.mechanical` (default flash, label `toolMech`).
- `settings.toolRouting` absorbs L1: `webSearch` key joins `search`/`edit`/`mechanical` in one block; the legacy top-level `webSearchModel` still works as a fallback (`toolRouting.webSearch` wins). `webSearchModel` values are now validated at load time. Serve banner prints the full mapping on one `tool -> ...` line.
- Tests: batch precedence both orders, later-batch replacement, responses run semantics, shell-wrapped search/edit classification, mechanical rule, webSearch merge/fallback/validation.

## v0.4.3 - 2026-08-18

Optional per-profile RTK tool-result compression: profiles with `"rtk": true` rewrite verbose tool output (git diff/status/log, grep hits, listings, build logs) in place before routing, cutting the request tokens coding-agent sessions resubmit every turn. Off by default — routing stays fully transparent unless you opt in.

### Added
- `src/awerouter/rtk/`: Python port of the RTK compression pipeline (via 9router's JS port of rtk-ai/rtk) — `autodetect.py` (12-format priority chain, 1024-char detect window), `filters.py` (git-diff, git-status, git-log, build-output, grep, find, tree, ls, search-list, read-numbered, dedup-log, smart-truncate), `apply.py` (safe_apply fail-open wrapper), `constants.py` (upstream thresholds), and `compress_body(body, protocol)` traversal for all three wire protocols (anthropic `tool_result` blocks, openai-chat `role:"tool"` messages, openai-responses `function_call_output` items).
- `"rtk": true` profile flag in routing.json (default off; non-bool dies at load; `config show` prints it only when set).
- Compression hook in `_proxy_flow` and `handle_count_tokens`, before routing-signal extraction: L3 decisions, `effective_tokens`, and usage logs reflect the compressed request that is actually billed. `/v1/messages/count_tokens` is compressed too, so client context estimates match reality.
- Per-request opt-out header `X-Awerouter-Token-Saver: off`; serve banner prints `rtk -> on` with the header hint when enabled.
- `RequestLog.rtk_saved`: estimated input tokens saved per request (0 = off/none), written to and read from requests.jsonl.

### Design notes
- Fail-open is the contract: safe_apply catches filter errors, compress_body catches traversal errors, and compress_text guards (below 500 chars / above 10 MiB untouched; empty or larger output reverts to original; `is_error` results skipped to preserve stack traces).
- Filters are pure deterministic text transforms — the same history compresses to the same bytes every turn, so provider prompt-cache prefixes survive.
- After enabling rtk, re-run `usage calibrate`: a threshold tuned on uncompressed traffic over-triggers pro (`"auto"` self-corrects after its window).

## v0.4.2 - 2026-08-18

New L4 routing layer: what the agent just did decides where the next turn goes. Search-phase turns (Grep/Glob/LS just returned) route to flash; edit-phase turns (Edit/Write/apply_patch just ran) route to pro.

### Added
- `InspectResult.last_tool`: lowercased name of the most recent tool call in the resent history, extracted per protocol (anthropic `tool_use`, openai-chat `tool_calls`, openai-responses `function_call`).
- L4 tool-phase routing in `router.py`, below L3: search-class → `settings.toolRouting.search` (default flash), edit-class → `settings.toolRouting.edit` (default pro), with labels `toolSearch`/`toolEdit`. `toolEdit` is excluded from L3 calibration (pro at any threshold); `toolSearch` stays in — it flips flash/pro with the threshold exactly like `default`.
- `settings.toolRouting` in routing.json: `{"search": "flash", "edit": "pro"}` — destination keys or `null` to disable a rule; block absent = both defaults on. Invalid values die with a clear message.
- `EDIT_TOOLS` name set (edit/write/multiedit/notebook_edit/apply_patch/str_replace/replace_in_file/write_to_file/apply_diff/create_file, case-insensitive); search-class reuses `FILE_SEARCH_TOOLS`.
- Serve banner prints the active mapping (`tool-phase -> search→flash  edit→pro`).
- Tests: last_tool extraction per protocol, L4 decisions and precedence (L3 long-context beats tool-flash; L1 web_search beats tool-edit), rule disabling, config parsing.

### Design notes
- L4 sits below L3 on purpose: sessions above `longContextThreshold` stay pro regardless of the last tool, so flash never sees very long contexts and the long-context crossing stays one-way flash→pro.
- Read-class tools are deliberately not classified: after reads the next turn often writes code, and rising read volume already crosses L3 to pro before that turn.
- Sub-threshold sessions now alternate flash↔pro by phase (search → flash, edit → pro); within the cache TTL this alternation is cheap — monitor it with `usage savings` (alternations / expired pro gaps).

## v0.4.1 - 2026-08-18

awerouter can now upgrade itself and tells you when a new release is out. The serve banner also stops recommending the chat wire that Codex removed.

### Added
- `awerouter self-update [--check]`: upgrades to the latest PyPI release — pipx-managed installs run `pipx upgrade awerouter`, everything else `pip install --upgrade`; `--check` shows versions only.
- `update_check.py`: background PyPI check after every command (at most once a day, cached as `update-check.json` in the config dir; `AWEROUTER_NO_UPDATE_CHECK=1` disables it). A newer release prints a one-line reminder after the command finishes, throttled to once a day. `serve` keeps the check enabled so long sessions still refresh the cache, and the reminder printing after Ctrl-C is the natural moment to upgrade.
- Serve banner shows an update hint (`update available: x → y  (awerouter self-update)`) from the cached check — no network, no thread, may lag the newest release by a day.
- Tests: `test_update_check.py` covers version comparison, skip rules, cache freshness/remind throttling, the kill switch, the banner hint, and the `self-update` command (`--check`, up-to-date, installer invocation).

### Changed
- Serve banner no longer tells Codex users on an openai-chat profile to set `wire_api = "chat"` — Codex 0.122+ rejects it. It now says to point Codex at an openai-responses profile; openai-responses profiles keep the `wire_api = "responses"` hint. The old-`agent`-field migration message in `config.py` matches.

## v0.4.0 - 2026-08-17

awerouter now detects file-search tools wrapped inside shell commands (Codex `exec_command` / `shell`), and usage commands surface the discounted file-search token counts alongside raw totals.

### Added
- `protocols.py`: `_is_file_search()`, `_is_search_command()`, and `_shell_is_search()` recognize file-search traffic inside shell commands — compound commands split on `;` / `&&` / `||` / newlines, pipeline heads are inspected, env-prefixed and absolute-path binaries are supported, and `git grep` is recognized. Both Codex's single-command `cmd` string and older argv-form `command` arrays are handled. Case-insensitive matching covers opencode's lowercase `grep` / `glob` / `list` names, and `list` joins the direct file-search tool set.
- `usage log --tokens` annotates `tool_results` with the embedded file-search count, e.g. `results=20(search=20)`.
- `usage tokens` and `usage stats` headers now print the active `search discount`, raw `search` token total, and L3 `effective` token total for the filtered window.
- Tests: `test_router.py` covers case-insensitive names, shell-wrapped search binaries, env-prefixed paths, `git grep`, non-search shell commands, multiline commands, and argv-form command arrays.

### Changed
- L3 difficulty scoring now applies `searchResultDiscount` to file-search tokens whether they come from direct file-search tool results or from shell-wrapped search commands, so Codex bulk-search traffic no longer inflates effective context.
- `usage log` window filters (`--profile`, `--since`) read the whole log first, then filter, then take the last `--lines` — matches outside the raw tail window are no longer silently dropped.
- `usage tokens` prints the file-search subset inside the `tool_results` line (`includes 20 search at 30% weight`) so per-type totals stay additive and backward-compatible.

## v0.3.9 - 2026-08-17

`longContextThreshold` can now be set to `"auto"` in `routing.json`. At each `serve` start, awerouter takes the `percentile` of the profile's own L3 effective-token distribution over the trailing `windowDays` as the threshold. With fewer than `minSamples` L3 requests in the window, `fallbackThreshold` applies instead. All four knobs live in `settings.longContextAuto` (all optional).

### Added
- `AutoThresholdConfig` dataclass (`types.py`): `percentile` (default 95), `windowDays` (default 7), `minSamples` (default 50), `fallbackThreshold` (default 8000).
- `auto_threshold()` in `logging.py`: picks the threshold from a profile's own L3 traffic over the configured trailing window, or returns `None` when samples are insufficient.
- `_resolve_auto_threshold()` in `server.py`: materializes `"auto"` once at serve start (before the socket opens), so the value is fixed for the process lifetime. The banner prints what was picked and why.
- `_l3_tokens()` helper + `_base_label()`: `token_distribution` now delegates to `_l3_tokens`, which strips the `→fallback` suffix before the L3 label check — flash→pro fallback entries still calibrate.
- `default-routing.json` ships with a `longContextAuto` block using the defaults.
- `awerouter add` wizard accepts `"auto"` for `longContextThreshold` and persists it as the string `"auto"`.
- `awerouter list` shows `L3>auto` for auto profiles.
- `usage calibrate` ends with the value `"auto"` would pick under the current policy (or a fallback notice when samples are insufficient), independent of the `--since` view.
- Tests: `test_cli.py`, `test_config.py`, `test_logging.py`, `test_server.py` cover the new type, config parsing, wizard output, calibration display, auto threshold logic, and serve-start resolution.

### Changed
- `load_routing()` parses `settings.longContextAuto` (partial fills keep defaults) and marks profiles with `threshold_auto=True` when `longContextThreshold` is the string `"auto"`. Before serve resolves it, the profile reads `fallbackThreshold`.
- `format_routing_display()` serializes `longContextAuto` and prints `"auto"` for auto profiles.
- Removed stale TODO docs (`docs/todo/code-quality.md`, `docs/todo/l3-complexity.md`).

## v0.3.8 - 2026-08-17

### Added
- `settings.searchResultDiscount` (default `0.3`): file-search tool results (Grep/Glob/LS) are now discounted when L3 scores request difficulty. Bulk search hits inflate context cheaply, so they no longer alone push a request to pro.

### Changed
- L3 difficulty scoring now uses `effective_tokens(token_count, file_search_tokens, discount)` instead of raw `token_count`, so flash can carry longer conversations that include file-search results.
- `usage calibrate` now prints the active `searchResultDiscount` so threshold tuning matches the actual scoring rule.

## v0.3.7 - 2026-08-17

### Added
- `awerouter usage tokens [--since] [--profile]`: input-token totals and share by content type (messages, system prompt, tool definitions, tool results, tool-call arguments, thinking). Entries logged before per-type counting are reported separately as not itemized.
- `awerouter usage log --tokens`: per-entry view that swaps the status/latency/model-in columns for the per-type token breakdown (`msg/sys/tools/results/calls/think`).

### Changed
- Token counting now includes system prompt, tool definitions, tool results, tool-call inputs, and thinking blocks across all three protocols. Previously only message prose was counted (and inconsistently across protocols). `longContextThreshold` values need recalibration via `usage calibrate` after upgrading.
- Requests are now logged with a per-type `tokens` breakdown; `token_count` is the sum of the per-type estimates (each with a 1-token floor), so totals may differ from the previous single-pass estimate by a few tokens per request. Routing semantics unchanged.

## v0.3.6 - 2026-08-17

### Changed
- Python 3.9 compatibility fix: added `from __future__ import annotations` to `config.py`, `logging.py`, `server.py`, and `router.py` so PEP 604 union syntax (`int | None`) works at runtime on 3.9.
- Test compatibility: relaxed bare `awerouter usage` help assertion to accept click 8.1's exit 0.

### Added
- Project logo added to README header (`logo/logo.png`).

## v0.3.5 - 2026-08-17

### Changed
- `config show [PROFILE]`: single-profile redacted view (the providers it uses + its routing entry); no argument keeps the full-config view.
- `config path` prints both config file paths (`providers.json`, `routing.json`) instead of the config directory.
- `config edit` opens `providers.json` or `routing.json` — the file is an optional argument (`providers` / `routing`) or an interactive choice — instead of opening the config directory; snapshots the file to `.bak` first.
- `awerouter add` wizard: prints a `providers.json` category overview, and provider selection is a choice list over the category's existing providers (`<new>` adds one) instead of free text with a hint.
- Removed `config init` (use top-level `awerouter init`); error hints updated to match.
- **Version single-sourced** in `awerouter.__version__`: `pyproject.toml` reads it dynamically (`tool.setuptools.dynamic`) and the serve banner/`GET /` import it, so a release bump touches exactly one file instead of three lockstep edits (v0.3.1 had bumped `pyproject.toml`, `__init__.py`, and `server.py` in unison).
- **`usage` window options moved onto the subcommands**: `--since` and `--profile` are now options of `log`, `stats`, `calibrate`, and `savings` (`awerouter usage stats --since today`), no longer sitting between `usage` and the subcommand. `clean` takes no window options — it deletes the whole log either way.

### Added
- **Predictable port allocation**: optional `port` field in a `routing.json` profile pins its listen port; precedence `--port` flag > profile `port` > 20128 default. An explicitly chosen port that is already in use now fails loudly (clients hardcode it) instead of silently drifting to a random port. Without a configured port, serve scans upward from 20128 for the first free port — the first instance gets 20128, the next 20129, in start order — replacing the old random-port fallback, so parallel instances land on predictable sequential ports. `awerouter list` and `config show` display the port, and the serve banner notes when it came from `routing.json`.
- `awerouter restore [providers|routing]`: restore a config file from its `.bak` backup. Backups are single-slot (aweswitch convention) and written by `config edit` and the `add` wizard before every write.
- `usage clean`: deletes saved request logs after a confirmation prompt — moved off `usage stats --clean` so `stats` stays read-only.
- Request log records `protocol` and `agent` per request. Protocol is the wire protocol served; agent is the calling client normalized from its `User-Agent` header (`claude-cli/...` → `claude-code`, `codex_cli_rs/...` → `codex`, `opencode/...` → `opencode`; unknown clients keep their first UA token) — awerouter only sees the wire request, so the UA is the only place caller identity exists. `usage log` shows both columns; `usage stats` labels each profile with its protocol and adds a `by_agent` breakdown. Legacy entries without the fields keep parsing (shown as `-` / `(unknown)`).

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
