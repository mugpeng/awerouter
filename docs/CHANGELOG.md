# Changelog

## v0.6.2

Three token-saver layers and multi-account logins: awecompress frozen-summary compression joins ODCP and RTK, subscription logins multiply via `authHome`, and a batch of integration hardening fixes lands with the first PyPI release of awecompress.

### Added
- `awecompress` profile flag (`"awecompress": true`, or an object tuning `summaryModel` (`"flash"` default, `"pro"`, or a literal model id that must be declared in `providers.json` `models`), `thresholdTokens`, `keepRecentTurns`, `minSpanTokens`, `transcriptResultCap`, `protectedTools`, `protectedFilePatterns`). Ahead of ODCP/RTK, the oldest complete turns freeze into one cached summary — summary calls ride the flash (or chosen) destination with the provider's own auth, savings land in the usage log as `awecompress_saved`, and the frozen store is shared with the standalone proxy. Fail-open end to end; `X-Awerouter-Token-Saver: off` disables it with the rest. Requires `pip install awerouter[compress]` — awecompress is now on PyPI as 0.2.0.
- Provider `authHome` field (providers.json), legal only next to an `"auth": "codex"` / `"auth": "claude"` sentinel: codex reads `<authHome>/auth.json` (an aweswitch account dir works as-is — `~/.config/aweswitch/accounts/codex/<name>`); claude keeps its owned store at `<authHome>/claude-auth.json`. Absent `authHome` is exactly the old single-login behavior. Combined with the `pool` tag (gateway) or `backups` (profiles), same-model failover across accounts of one vendor becomes a few lines of config.
- Per-login failover bookkeeping: `rejected_auths` keys on sentinel+authHome, so a dead account's double-401 excludes only that account — its sibling accounts stay eligible later in the same request. Serve-start login warnings and 503/refresh hints name the exact dir that lacks a login, one line per login.
- `awerouter config login claude <dir>` / `config logout claude <dir>` (and codex's CODEX_HOME-aware hint, shell-quoted and Windows-safe): manage one account's login in its authHome; each claude store refreshes independently.
- `odcp` profile flag (`"odcp": true`, or `{"dedup": bool, "purgeErrors": bool | {"turns": int}}`, default off). Before routing and before RTK, one pairing pass over the history applies two rules: **dedup** keeps only the newest output of repeated identical tool calls (same tool + normalized arguments — explicit JSON `null` stays distinct from an omitted key), replacing older ones with a one-line placeholder; **purgeErrors** strips a failed call's input strings once it is `turns` user messages old (default 4), keeping the error text (anthropic only — other protocols carry no error mark on the wire). Editors and task/planning tools are never rewritten — including codex's shell-wrapped `apply_patch` — replacements that wouldn't shrink the body are skipped, and the trailing turn is untouched. Deterministic and fail-open, with the `X-Awerouter-Token-Saver: off` bypass; `/v1/messages/count_tokens` prunes identically. Unlike RTK, pruning rewrites earlier history when a new duplicate appears or an error crosses the age threshold — an accepted prompt-cache trade. Behavior follows [Opencode-DCP](https://github.com/Opencode-DCP/opencode-dynamic-context-pruning)'s public documentation (AGPL-3.0); implemented from that description, not translated. `odcp_saved` is logged per request and reported by `usage` next to rtk's; serve banner and `serve status` show `odcp` when on.

### Fixed
- awecompress summary calls to Anthropic-shaped upstreams now carry `anthropic-version` (the internal sender used to omit it, so Anthropic 400'd the summary call and compression silently failed open).
- Gateway hot reload validates the fresh profiles' awecompress config (`summaryModel` declarations, package presence) before swapping them in — an invalid reload is skipped and the previous config keeps serving, same as the single-profile reload.
- Codex `config login/logout` hints quote the `CODEX_HOME` path (spaces no longer break the printed command) and refuse paths containing a double quote on Windows (no `set "` injection into the copy-pasted command).

## v0.6.1

Robust resident service management: `serve restart` one-word reloads, crash-proof request handling, and macOS installs that actually stick.

### Fixed
- A request-path failure no longer kills the daemon: a `die()` inside request handling (e.g. a `${VAR}` auth reference whose value is missing from the daemon's environment) used to raise SystemExit through the handler and take the whole process down — under KeepAlive, a crash loop. A guard middleware on both apps now answers that one request with 503 (message included) and keeps serving the next.
- Resident installs on macOS no longer intermittently end up "installed — not running": `launchctl bootout` tears down asynchronously, and right after it returns the old job can still read as loaded while a racing bootstrap is silently never submitted. install() now waits for the domain to actually release the old job, then bootstraps and verifies the job really loaded (retrying within 5s before dying loudly). `serve restart` hit this ~half the time before.

### Added
- `awerouter serve restart [PROFILE]`: the one-word way to apply a changed environment variable or secret. Resident services are re-installed from the current shell — same command line, port and host as installed, env baked fresh — and an installed-but-stopped service is started too. Plain background instances are stopped and re-spawned from the current shell on the same port; foreground instances are skipped (they belong to their terminal).

## v0.6.0

Failover becomes first-class: routing destinations gain ordered `backups` queues with quota-aware cooldowns, providers declare a `multimodal` flag so the image guard survives failover, and gateway direct-forwards support a `pool` tag for same-model account rotation.

### Added
- Failover queues (`backups`) in `routing.json`: each destination key may declare an ordered failover queue — same `"provider,model"` strings as `destinations`. When the primary answers 429/408/5xx or a network error before the first streamed byte, the request walks its queue in order; smart routing itself never changes (the tier is the request's property — failover only swaps who serves it). Zero config, each tier carries one implicit cross-tier hop (flash→pro / pro→flash); an explicit list replaces it. A 429 or any response carrying `Retry-After` puts that candidate on an in-process cooldown (Retry-After honored when parseable, else 30s; capped at 60s). An exhausted queue passes the last upstream response through; a network-error 502 names every candidate tried. Every hop is stamped into the label (`background→fb:glm,glm-4.7-flash`) and counted as `fallback_hops` in the usage log.
- Provider `multimodal` flag: image-bearing requests filter their failover queue on the provider's declared `multimodal` flag — the image guard holds during failover, and undeclared means no.
- Provider `pool` tag for gateway direct-forwards: entries sharing a tag within one protocol group are the same vendor's accounts (same models, different keys). A gateway `provider/<model>` forward that 429s fails over to the **same model** on the next pool member — declaration-order wrapping from the named entry, so each account's overflow flows to the next. One tag per entry replaces mutual-backups lists; untagged entries stay pinned. Cooldowns, `→fb:` labels, `fallback_hops`, and the `multimodal` image guard all apply.
- Track `rejected_auths` to avoid reusing dead logins within a request.

### Fixed
- Config load now validates that both `flash` and `pro` destinations are present.
- Skip implicit hop when both tiers point to identical provider+model.
- Scope cooldown keys by protocol so one protocol's 429 does not cool another's.
- `git_diff` RTK filter defaults to a line cap; `git_status` handles long-form Untracked files.
- Update-check is resilient to fetch exceptions (preserves cached `latestVersion` and records the check).

## v0.5.9

Windows CI repair: since v0.5.5 the windows-latest CI legs have been red — v0.5.7's resident-service feature made `serve status` die on Windows, and the `_is_awerouter_process` tests assume a readable command line that Windows cannot provide.

### Fixed
- `awerouter serve status` works on Windows again: `installed_services()` now reports none on platforms without a service manager instead of exiting with the resident-services error (which is what `service_kind()` raised). Mutating paths (`--install`, resident stop/purge) still refuse loudly through the new `require_service_kind()` wrapper — same message as before (`src/awerouter/service.py`; `TestStatus` in `tests/test_cli.py` runs on Windows again).
- `_read_cmdline` on Windows returns "unverifiable" (None) instead of a false `""`: there is no `/proc`, and MSYS `ps` cannot see native processes, so the old fallback misclassified every live pid as dead. The `serve stop` guard it feeds is POSIX-only today, but the contract no longer lies for any future Windows stop path (`src/awerouter/runtime.py`). `TestIsAwerouterProcess` skips on Windows with that reason — command-line matching cannot be observed where reading is impossible.
- `serve status` tests no longer read the developer's real LaunchAgents/systemd directories: a machine with a resident service installed failed `TestStatus` locally even though CI was green (`tests/test_cli.py`).

## v0.5.8

Two correctness fixes to v0.5.7's features: gateway direct forwards no longer pollute per-profile usage stats, and resident-service installs keep percent-encoded env values intact.

### Fixed
- Gateway direct-forward usage attribution: a `<provider>/<model>` forward belongs to no routing profile, but the usage log recorded it under whichever profile happened to serve the protocol, skewing `usage --profile`. Those requests now log `direct` as profile/destination, with the real provider and model preserved (`tests/test_gateway.py`).
- systemd `Environment=` values escape `%` as `%%` (systemd expands % specifiers there), so a percent-encoded proxy URL baked into a resident service by `serve run --install` reaches the daemon verbatim (`tests/test_service.py`).

## v0.5.7

Resident services: `awerouter serve run <profile> --install` runs the daemon as a launchd agent on macOS or a systemd user unit on Linux. It starts at login (survives a reboot), relaunches after a crash, and `awerouter serve stop [PROFILE] [--purge]` controls it through the service manager instead of SIGTERM. The service bakes `${VAR}` auth values plus `AWEROUTER_*` and proxy variables into the file (mode 0600) because the service manager starts the daemon without your shell environment; missing referenced variables fail the install up front. Re-installing is the update path for host/port/env. `serve status` shows resident instances as `svc:launchd` / `svc:systemd` and lists installed-but-stopped services too. `awerouter serve all --install` covers gateway mode.

Gateway direct forwards: providers can declare the models that may be selected directly (`"models": [...]` in their `providers.json` entry), and `awerouter serve all` then exposes `<provider>/<model>` names — such as `stepfun/step-3.7-flash` — as fixed forwards that bypass automatic routing and the flash→pro fallback. Undeclared or unknown models are rejected with a descriptive 400; existing configurations need no changes.

### Added
- Gateway `<provider>/<model>` fixed forwards: providers declare `"models"` separately in each protocol group; a matching gateway request tracks a direct destination (skipping tier resolution, rtk/image-bridge adjust, and fallback), `/v1/models` lists the declared names, and unknown profile vs. undeclared model each return a distinct 400. Gateway app construction accepts global providers to populate the entries, with validation and test coverage.
- `awerouter serve run [PROFILE] --install` and `awerouter serve all --install`: resident service install with env baking, startup-at-login, and crash relaunch. Idempotent re-install updates the job and restarts it.
- `awerouter serve stop [PROFILE] --purge`: stops the resident instance and removes its service file so it no longer starts at login; plain `serve stop` stops the running instance but leaves it installed.
- `serve status` resident-service awareness: running instances show `svc:launchd` / `svc:systemd`; installed-but-stopped services are listed with a remove hint.
- `awerouter.service` module: platform-aware service file generation (launchd plist / systemd user unit), install/stop/purge commands, env capture from `providers.json` `${VAR}` references, and installed-service discovery from the file names/command lines.
- Runtime registration now records the service manager kind (`AWEROUTER_SERVICE`) so resident instances route through the service manager on stop.
- Test coverage for service file generation, env baking, missing-variable failure, install/stop/purge flows, status resident-mode display, and plain vs resident instance split (`tests/test_service.py`).

## v0.5.6

Gateway mode: `awerouter serve all` turns all routing.json profiles into one local endpoint. A request model name selects the profile (`<profile>/auto|flash|pro`, e.g. `step-glm/auto`); `GET /v1/models` advertises every alias. The existing `serve run <profile>` model remains unchanged, so users can migrate one client at a time or run both forms concurrently.

### Added
- `awerouter serve all [--port N] [--host HOST] [-d]`: one daemon and one port for every profile. It preserves the same-protocol proxy contract and per-profile routing/settings/providers; requests select the target profile from the model prefix, then `auto` runs the usual L1–L4 pipeline while `flash`/`pro` force that profile's destination. The gateway ignores per-profile `port` fields (its own `--port` or the normal 20128 scan is authoritative), hot-reloads profile additions/removals and every config edit, registers as `gateway` for `serve status`, and `serve stop gateway` stops it. Background mode writes `serve-gateway.log`.
- Top-level `routing.json` `defaultProfile`: optional gateway-only bare-name target for `auto` / `flash` / `pro`, keeping existing Claude Code tier environment variables compatible. It must name a configured profile; with multiple profiles and no default, bare names fail loudly and callers must use `<profile>/...`; a lone profile is its own natural default. It is not valid inside a profile and does not affect single-profile serve.
- Gateway discovery and honest failures: `/v1/models` lists default bare tiers when routable plus every profile alias; `/` identifies gateway mode. Unknown profile, invalid tier, and endpoint/profile protocol mismatch each return a descriptive 400 naming the problem and available alternatives. `/v1/messages/count_tokens` follows the same alias selection and model rewrite as inference.
- Gateway-specific test coverage (alias selection, forced/custom tiers, default behavior, all protocols, errors, token counting, config parsing/reload and hints) plus README/README_cn documentation.

### Fixed
- The gateway's registration name (`serve status` / `serve stop gateway` / `serve-gateway.log`) now comes from a single `GATEWAY_PROFILE_NAME` constant instead of scattered literals, so no real profile named `gateway` can collide with it inconsistently.

## v0.5.5

Daemon lifecycle becomes first-class: `awerouter serve run <profile> -d` runs the daemon detached — it survives the terminal (log: `<state>/serve-<profile>.log`), `awerouter serve status` lists every running serve instance (foreground and background, registered under `<state>/run/` at bind time), and `awerouter serve stop [PROFILE]` stops them with a graceful SIGTERM. Serve now also watches `routing.json`/`providers.json` and hot-reloads edits — destinations, thresholds, tool routing, settings overrides, and provider entries apply to the next request without a restart. The CLI also reorganizes: `serve` becomes a command group (`run` / `status` / `stop`) and `login` / `logout` / `restore` move under `config` — the released top-level spellings keep working as hidden aliases, so nothing breaks.

### Added
- `login`, `logout`, and `restore` moved under the `config` group — `awerouter config login [claude|codex]`, `awerouter config logout [claude|codex]`, `awerouter config restore [providers|routing]` — grouping every command that manages config-owned state (files, backups, the claude login store) next to `config path/show/edit`. The released top-level spellings keep working as hidden aliases (`awerouter login` etc.), so existing docs, scripts, and muscle memory don't break; `awerouter --help` lists only the `config` form. Error hints that name the command (missing claude login 503/warnings, `init --merge` behavior-shift note) now print the new spelling.
- `serve` is now a command group — `run` (start a daemon), `status`, `stop` — instead of a bare command with top-level `status`/`stop`. The pre-group spellings keep working: `awerouter serve <profile> [--port/--host/-d]` and `awerouter <profile>` are equivalent to `awerouter serve run <profile>` (an unknown subcommand under `serve` is a profile name, same as the top level), and bare `awerouter serve` still auto-selects the single profile. The one removal: `awerouter serve -d` (background auto-select, no profile) — name the profile or use `serve run`.
- `awerouter serve run [PROFILE] -d/--background`: spawns the daemon detached via `python -m awerouter __serve_daemon__ <profile>` (new `__main__.py`), waits for it to bind, then prints pid/port/log path plus a `status`/`stop` hint; notes when the profile is already running (starts another anyway, same as concurrent foreground instances). A child that dies before binding fails the command with the log tail. POSIX-only (`os.name == "nt"` refuses).
- `awerouter serve status` / `awerouter serve stop [PROFILE]` (new `awerouter.runtime` module): every serve process registers `{pid, profile, protocol, port, host, background, started}` under `<state>/run/<pid>.json` at bind time and unregisters on shutdown (SIGTERM/SIGHUP now shut down gracefully via signal handlers on the serve event loop). `serve status` lists live instances (stale/dead-pid files pruned on sight); `serve stop` SIGTERMs matching instances after a command-line guard (`ps` token match: `awerouter`, `-m awerouter`, `.../bin/awerouter`) so a reused pid is never signaled. Windows: `pid_alive` probes via `OpenProcess` (os.kill(pid, 0) would TerminateProcess there), and `-d`/`serve stop` refuse outright.
- Hot reload: `_serve` polls both config files' mtimes (1s) and swaps the live app's `profile`/`settings`/`providers` from a fresh `load_for_profile` — destinations, thresholds, tool routing, per-profile overrides, provider entries, and `"auto"` threshold re-resolution all apply to the next request without a restart. A file that fails to load prints one `config reload skipped` line and the previous config keeps serving until the file parses again; a changed `port` field prints a restart hint (rebinding is out of scope). The serve banner prints `hot reload -> on`.
- Skill refresh nudge: `awerouter self-update` (after a successful upgrade) and the daily update reminder now add one line when the awerouter agent skill is detected in `~/.agents/skills/` or `~/.claude/skills/` — the skill is updated along with awerouter releases, and refreshing it is best done by the coding agent itself: `aweskill update awerouter`. awerouter never writes the skill file: its lifecycle (store, projections, local-edit protection) belongs to aweskill, and the skill is not packaged in the wheel, so awerouter only points at the refresh command.

## v0.5.4

Image bridge: `settings.imageBridge` (default `false`, opt-in) gives a text-only pro model second-hand vision. When a request carries images only in history — the final message is text — awerouter has the multimodal flash destination (`imageModel`) transcribe each distinct image, replaces the image blocks with the transcription text before routing, and the session falls through to `defaultModel` (pro) instead of being pinned to flash forever. A fresh upload this turn still routes to flash natively; `router.py` is unchanged — after the rewrite `has_image` is false and the request lands in L3/default, before the rewrite the L1 image guard routes it as always. The `step-glm-mm` template now ships with `imageBridge: true`.

### Added
- `settings.imageBridge` (`true`/`false`, global or per profile, validated at load and shown by `config show`): the request-pipeline transform described above. It runs before rtk compression and routing in `_proxy_flow`, so what rtk compresses, what L3 scores, and what goes upstream are exactly the same body; `handle_count_tokens` applies the same bridge so the client's context-window estimate matches. The serve banner prints `image bridge -> on (...)` naming the transcribing destination.
- `awerouter.vision` (pure logic): per-protocol content keys (`image_key`), caption body building and response parsing for all three wire protocols, in-place image-block rewriting (`[Image n, transcribed by <model>]` text blocks), and a content-addressed caption cache (bounded FIFO, process lifetime — a restart re-transcribes each distinct image once; the cache key includes provider+model, so a destination switch never serves another model's transcription).
- Failure fallback: any caption call failure (network, non-200, empty text) leaves the request body untouched — one printed line, and the L1 image guard routes the request exactly as before. Codex subscription logins (`auth: "codex"`, SSE-only backend) skip the bridge entirely.
- `step-glm-mm` template sets `imageBridge: true`; `init --merge` flags a newly-written `imageBridge` as a behavior shift next to `imageModel`/`defaultModel`. README/README_cn document it as an "Image bridge" (图片桥接) section; README.ai.md and the awerouter skill mention the template flag.

### Behavior notes
- Bridged "vision" is second-hand: its ceiling is the transcription quality, and each distinct image costs one extra flash call (cached per image content for the process lifetime).
- The flash→pro rescue fallback (flash down on an un-rewritten image request) still carries raw images to a possibly text-only pro — pre-existing behavior, unchanged and out of scope.

## v0.5.3

Per-profile settings overrides and template merging: every `settings` key (`backgroundModel`, `thinkModel`, `webSearchModel`, `imageModel`, `defaultModel`, `searchResultDiscount`, `toolRouting`, `longContextAuto`) can now be written directly in a profile body, flat next to `protocol`/`destinations` — it applies to that profile alone, and missing keys (missing fields inside `toolRouting`/`longContextAuto` included) inherit from the global `settings` block, so a profile re-tunes exactly what differs. `backgroundModel`/`thinkModel` in a profile body are the pre-v0.5.2 per-profile shape, honored again. Serve and the router use the merged result (`RoutingProfile.settings`); a profile's own `longContextAuto.fallbackThreshold` now backs its `"auto"` threshold; `usage calibrate`/token views with `--profile` use that profile's discount. `config show <profile>` prints the effective values with the override keys flat in the entry, the serve banner lists them on an `overrides -> ...` line. Unknown keys die at load naming the offender — profile bodies now validate their key set too (previously silently ignored), and the global block gets the same check, so a typo'd key never silently inherits.

`awerouter init <template> --merge` folds a bundled template into an existing config: missing provider entries, profiles, and settings keys are filled in; existing entries always win (your `base_url`/`auth`, your profile bodies, your explicitly-set settings). Profile id collisions are skipped and reported, so merging is idempotent. Newly-written `imageModel`/`defaultModel` — global keys that re-route every existing profile — print a warning with an `awerouter restore` hint. Both files are snapshotted to `.bak` before writing and the merged config is load-validated before `init` reports done.

### Added
- Per-profile settings overrides: every `settings` key may also be written directly in a profile body, flat next to `protocol`/`destinations` — it applies to that profile alone, and missing keys (missing fields inside `toolRouting`/`longContextAuto` included) inherit from the global `settings` block, so a profile re-tunes exactly what differs (e.g. one multimodal sidekick flipping `imageModel`/`defaultModel` without touching everyone else). `backgroundModel`/`thinkModel` in a profile body are the pre-v0.5.2 per-profile shape, honored again. Serve and the router use the merged result (`RoutingProfile.settings`); a profile's own `longContextAuto.fallbackThreshold` now backs its `"auto"` threshold; `usage calibrate`/token views with `--profile` use that profile's discount. `config show <profile>` prints the effective values with the override keys flat in the entry, the serve banner lists them on an `overrides -> ...` line. Unknown keys die at load naming the offender — profile bodies now validate their key set too (previously silently ignored), and the global block gets the same check, so a typo'd key never silently inherits.
- `awerouter init <template> --merge` folds a bundled template into an existing config: missing provider entries, profiles, and settings keys are filled in; existing entries always win (your `base_url`/`auth`, your profile bodies, your explicitly-set settings). Profile id collisions are skipped and reported, so merging is idempotent. Newly-written `imageModel`/`defaultModel` — global keys that re-route every existing profile — print a warning with an `awerouter restore` hint. Both files are snapshotted to `.bak` before writing and the merged config is load-validated before `init` reports done.

## v0.5.2

Multimodal sidekicks and multi-protocol profiles: image routing and the fall-through default become settings, enabling a pro-first profile where a non-multimodal flagship (GLM 5.3) does all the work and a multimodal flash (StepFun step-3.7-flash) takes only image-bearing requests — the new `step-glm-mm` template. A profile's `protocol` now also accepts a list, so one serve instance can speak several wire protocols on a single port.

### Added
- Multi-protocol profiles: `protocol` accepts an id or a non-empty list (`["anthropic", "openai-chat"]`). One serve instance registers every protocol's endpoints and serves each listed one; clients pick by endpoint path (`/v1/messages` vs `/v1/chat/completions` vs `/v1/responses`). Every destination provider must exist in each served providers.json group (per-protocol `base_url`s) — a name missing from one group dies at load naming the group. The serve banner shows `protocol -> anthropic+openai-chat` and prints one client-hint block per protocol; `awerouter list` shows the joined form; `config show` mirrors the config shape (id vs list); the request log's `protocol` field records the endpoint that actually served the request.
- `settings.imageModel` (default `pro`) and `settings.defaultModel` (default `flash`) in routing.json. `imageModel` re-aims the image guard; `defaultModel` flips the cost-first fall-through (`pro` = everything text goes to the flagship). Both validated to `flash`/`pro` at load, shown by `config show`, and printed on the serve banner when they deviate from defaults (`image -> ... default -> ...`, and `main -> pro` instead of `main -> auto`).
- Bundled template `step-glm-mm` (`awerouter init step-glm-mm`): flash = StepFun step_plan `step-3.7-flash`, pro = GLM coding plan `glm-5.3`, with `imageModel: flash` + `defaultModel: pro` — the "solve multimodal, skip smart routing" preset. Dual-protocol: both vendors ship in the `anthropic` group too (stepfun `step_plan`, glm `open.bigmodel.cn/api/anthropic`), so one instance serves Claude Code (`ANTHROPIC_BASE_URL` without `/v1`) and openai-chat agents (`OPENAI_BASE_URL` with `/v1`) on the same port. README/README_cn document it under "Common setup templates" with the swap-flash-to-`glm-5.3-flash` variant; README.ai.md lists it.

### Changed
- The image check moved from L3 to L1 (right below web_search, above tier labels and long context): image routing is a capability decision, not a difficulty guess — with `imageModel: flash` an image-bearing request must reach the multimodal model no matter what tier label it carries or how long it is.
- `load_for_profile` and `create_app` now carry providers grouped by served protocol (`{protocol: {name: Provider}}`); `RoutingProfile.protocol` (string) became `RoutingProfile.protocols` (tuple) with a `protocol` display property — single-protocol configs and profiles are unaffected.

### Behavior notes
- At default settings the only routing change is label placement: a request with an image now labels `image` even when it also crossed `longContextThreshold` or carried a tier label. Destination-wise, a background-tier request containing an image now routes pro (was flash) — vision content is hard, and the "any image goes to imageModel, period" invariant is the one worth keeping.
- The `awerouter add` wizard still writes a single protocol; multi-protocol profiles are a hand edit (documented in README).

## v0.5.1 - 2026-08-28

Two additions on top of v0.5.0's codex story: Claude Pro/Max subscription logins become routing destinations on the Anthropic side, and routing setup gets bundled presets — `awerouter init step-glm` / `glm-codex` generate a matched providers + routing pair for known-good flash/pro combos in one shot.

Claude subscription accounts become routing destinations: `"auth": "claude"` in the `anthropic` group routes through a Claude Pro/Max subscription login that awerouter itself owns — no local Claude Code CLI login is needed (and none is borrowed; each OAuth login is an independent session). Mirrors the codex design where the mechanics allow it and inverts it where they don't: codex is read-only because OpenAI refresh tokens are single-use and the CLI must own refresh, while this login belongs to awerouter, so it refreshes.

### Added
- `awerouter.claude`: PKCE device login (`awerouter login claude` opens the browser, user pastes the code shown on the callback page) against the reverse-engineered public wire contract shared by every community client (client id, `platform.claude.com` authorize/token, legacy `console.anthropic.com` as a 404/405 fallback). Tokens persist in `~/.config/awerouter/claude-auth.json`, written atomically with 0600 perms. `awerouter logout claude` removes the login.
- Rotation-safe auto-refresh: access tokens are short-lived (~hours, 5-minute margin) and renewed with the rotating refresh token; a response without a new refresh token keeps the old one. An in-process lock plus a re-read under it keep concurrent requests from racing the refresh; a second awerouter process winning the race is recovered the same way (refresh rejected + file changed underneath → the winner's token is used).
- Request-path integration: per-request `Authorization: Bearer <access_token>` plus the OAuth-required `anthropic-beta: oauth-2025-04-20` flag (merged with any beta flags already present), `anthropic-version` defaulted when the client omitted it. No body normalization — the Messages API is the Messages API. Refresh runs off the event loop (`asyncio.to_thread`), so a renewal never blocks other in-flight requests.
- 401 handling mirrors codex: an upstream 401 forces one refresh and retries the same destination; a 401 that survives means the login is dead — flash falls back to a keyed pro (one printed line, `401-retry` marker in `usage log`) and only surfaces when pro rides the same login. A missing login is a 503 with a `run: awerouter login claude` hint plus a serve-start warning (store check only — a stale token doesn't warn, it refreshes on the first request).
- Proxy awareness extends to claude providers (`https_proxy`/`all_proxy`) — api.anthropic.com and platform.claude.com often need the shell proxy. Config guard: the `claude` sentinel dies at load in non-`anthropic` groups; `config show` renders it as `claude (subscription OAuth login)`. Default providers template ships a ready-to-use `claude` entry in the `anthropic` group.
- Bundled routing templates: `awerouter init [template]` generates a matched `providers.json` + `routing.json` pair in one shot. Templates live in `src/awerouter/resources/templates/` as `<name>.providers.json` + `<name>.routing.json` pairs (`default` is the previous built-in config); an unknown name fails with the list of available ones. Two ready-made combos ship: `step-glm` (key-only, openai-chat: flash = StepFun step_plan, pro = GLM coding plan) and `glm-codex` (openai-responses: flash = GLM coding plan, pro = ChatGPT subscription via `auth: "codex"`). README/README_cn gain a "Common setup templates" (常见搭配模版) section showing both combos as copy-paste snippets; README.ai.md and the awerouter skill document the template argument.

### Behavior notes
- ToS caveat: per Anthropic's 2026 policy, third-party use of subscription OAuth tokens is restricted — this rides the user's own subscription, at their own risk, and the wire contract can drift without notice (one-constant fixes in `awerouter/claude.py`).

## v0.5.0 - 2026-08-28

Codex subscription accounts become routing destinations: `"auth": "codex"` in the `openai-responses` group rides the local Codex CLI login (`$CODEX_HOME/auth.json`) instead of an API key, so the subscription's own models mix into flash/pro routing next to key-based providers — cheap traffic to StepFun/GLM/deepseek keys, hard traffic to the Codex account. Any Responses-speaking agent points at awerouter with a dummy key and reaches the Codex backend through the same transparent proxy as everything else (wire contract verified against the live backend, mirroring awewarm's native transport: Bearer access_token + chatgpt-account-id + OpenAI-Beta/originator headers).

### Added
- `awerouter.codex`: `AUTH_SENTINEL` ("codex"), `load_codex_login()` (accepts both the `{"tokens": {...}}` shell and a flat block), `apply_codex_auth()` writing the full header set. `_set_auth` dispatches on the sentinel; requests carry a fresh read of `auth.json` every time.
- 401 handling: the login file may change under a running serve (the CLI refreshes it) — an upstream 401 re-reads `auth.json` and retries the same destination once. A 401 that survives the re-read means the login itself is rejected: flash requests then take the flash→pro fallback (one printed line per fallback) when pro carries its own key, and surface the 401 only when pro rides the same codex login. A missing/invalid login file is a 503 with a `run: codex login` hint and no fallback — a missing login is a config error best surfaced immediately, not silently served from a paid pro; serve also prints a startup warning when `auth.json` doesn't exist yet. The 401 re-read is recorded in the request log (`codex_retried`), shown by `usage log` as a `401-retry` marker, so CLI token churn is visible in calibration data instead of hiding inside a 200's latency.
- Body normalization for codex providers: `store` is forced to `false` (the ChatGPT Codex backend is zero-data-retention and rejects `store: true`) and `max_output_tokens` is dropped (sampling controls are CLI-internal; clients that send them get 400s). The backend's SSE-only nature is bridged — a non-streaming request goes upstream as a stream and returns buffered into a single JSON response object, with output items rebuilt from `response.output_item.done` events (the terminal object omits them). Everything else passes through untouched as always.
- Proxy awareness, codex providers only: `_codex_proxy()` resolves `https_proxy`/`all_proxy` (plus macOS system settings via `getproxies()`) — chatgpt.com commonly needs the shell proxy. Loopback base_urls never take the proxy; socks-only setups are ignored (would need aiohttp-socks); every other provider keeps its direct connection, byte-identical behavior.
- Config guard: the `codex` sentinel dies at load in non-`openai-responses` groups (the backend speaks Responses only); `config show` renders it as `codex (local CLI login)`. Default providers template ships a ready-to-use `codex` entry in the `openai-responses` group.

### Behavior notes
- No token refresh, ever, by design: OpenAI refresh tokens are single-use and rotating — server-side refresh would invalidate the local CLI's login. The CLI keeps sole ownership of refresh; awerouter re-reads the file per request (and once more on 401). Access tokens live ~10 days; normal `codex` usage (or awewarm warm-ups) keeps the login fresh.
- Codex model names drift (`gpt-5-codex`/`gpt-5.1-codex` already 400); the destination's `model` field is the single place to update.
- Existing providers are untouched: static keys, no-auth locals, and the retry/fallback ladder behave exactly as before.

## v0.4.9 - 2026-08-24

Local models become first-class routing destinations: `auth` is now optional in providers.json, so local inference servers (Ollama, LM Studio, llama.cpp, vLLM) take their place next to API-key providers under the same protocol groups — and since flash→pro fallback already fires on connection errors, a flash=local / pro=cloud profile gives you local-first routing with a transparent cloud safety net. Ollama ≥ 0.14 speaks the Anthropic protocol natively, so even Claude Code profiles can put a local model on flash.

### Added
- Local (no-auth) providers: `auth` may be omitted, `null`, or `""` in providers.json — requests go upstream with no auth header at all (the client's incoming key is dropped, not forwarded). `Provider.auth` is `Optional`; `config show` renders no-auth as `null`; `awerouter add` accepts an empty auth env var (the "two filling modes" live in the wizard, not in a second config file — protocol remains the only grouping axis). Default providers template ships ready-to-use `ollama` examples for the `anthropic` (`http://127.0.0.1:11434`) and `openai-chat` (`http://127.0.0.1:11434/v1`) groups.
- Forgotten-key guard, layered: a provider with no `auth` whose `base_url` is not loopback prints a serve-start warning (`_noauth_warning`); the `add` wizard asks for confirmation in the same case before writing. Informational, not fatal — LAN no-auth servers (vLLM on 192.168.x) are legitimate. Loopback detection (`is_loopback_url`) parses the host as an IP via `ipaddress` (whole 127/8 plus `::1`) rather than prefix-matching, so `127.0.0.1.evil.com` does not count as local.
- README/README_cn: "Local models (no auth)" / "本地模型（免认证）" section — path conventions per protocol, a mixed flash=local / pro=cloud destination example, end-to-end Ollama deployment steps with default ports for LM Studio/llama.cpp/vLLM, and the local-down→cloud-fallback behavior. README.ai.md (Step 4/5) and resources/skills/awerouter/SKILL.md now cover no-auth local providers as well; CONTRIBUTING's config-semantics section catches up (optional `auth`, `protocol` group key, `toolRouting`/`longContextAuto` in settings, L4 in the routing diagram).

### Behavior notes
- A providers.json entry that previously died with "missing base_url or auth" now loads when only `auth` is absent — that was the point. A missing `base_url` still dies. A cloud API configured without auth now surfaces as an upstream 401 at request time (visible in `usage log`) plus the serve-start warning, instead of a load-time error.

## v0.4.8 - 2026-08-18

L4 sheds its dead weight: the search/mechanical phase rules are gone and the layer is now a single **edit checkpoint** — the turn after the trailing tool batch changed code goes to pro (flash drafts, pro reviews). Those rules defaulted to flash, which is already the fall-through, so at defaults this changes no routing outcome; what it buys is that config, docs, and the layer's name now match its behavior.

### Changed
- `router.resolve()`: L4 is one rule — `feat.last_phase == "edit"` → `toolRouting.edit` (default pro, label `toolEdit`). `toolSearch`/`toolMech` labels no longer produced; search/mechanical turns label `default`.
- `ToolRoutingConfig` keeps only `webSearch`/`edit`. A `toolRouting` block still containing `search` or `mechanical` dies at load with instructions to delete the key — silent ignore would hide a behavior change from anyone who had set them to `pro`.
- `_call_phase` (edit > search > mechanical ranking) replaced by `_call_is_edit`; `MECHANICAL_TOOLS` and the per-extractor `_rank` tables are gone. The trailing batch is "edit" when any call in it changed code; codex's shell-wrapped `apply_patch` still counts.
- Serve banner `tool -> ...` line and `config show` `toolRouting` block now show `webSearch` (effective, legacy fallback resolved) and `edit`.
- `usage calibrate` and `_L3_LABELS` keep accepting `toolSearch` so pre-0.4.8 logs still feed threshold calibration.

### Behavior notes
- At default settings routing is byte-identical (the removed rules were no-ops below L3). A config with `search: "pro"` (forcing pro on planning turns) is no longer expressible — that was converting most coding traffic to pro; if you want that, invert the profile (pro-leaning destinations or a lower threshold).
- README/README_cn routing table and the four-layer essay rewritten: L4 is a consequence checkpoint — structure cannot see the turn that decides an edit, but the review turn after it is structurally identifiable.

## v0.4.7 - 2026-08-18

RTK matures: smart-truncate keeps a skeleton of the truncated middle so the model can see what was cut and decide whether to re-read, autodetect is hardened against single-line false positives, end-to-end idempotency keeps provider cache prefixes stable across re-sends, and RTK savings are now visible in `usage` views. READMEs carry an experimental warning.

### Added
- `smart_truncate` and `read_numbered` now keep a "skeleton" of up to 60 signature/import/declaration lines from the truncated middle (ported from rtk Rust `filter.rs` smart_truncate): beyond head 120 + tail 60, structural lines survive adjacent-duplicate-free so later dedup passes are no-ops. The read-numbered marker names the gap start (`re-read with offset=N`) so the model knows how to fetch the middle.
- RTK savings surfaced in `usage` views (previously `rtk_saved` was logged but never shown): the shared header on `usage log/stats/tokens/savings` prints `rtk: saved N input tokens (x/y requests compressed)` when the window has any; `usage log` appends `rtk=+N` to entries that were compressed (`+` marks trimmed tokens, not included in `tokens=`); `usage savings` adds an rtk block noting it stacks with flash offload. Backed by `logging.rtk_totals(since, profile)`. Nothing prints when nothing was compressed.
- README and README_cn: experimental warning explaining lossy compression, heuristic detection limits, and the `X-Awerouter-Token-Saver: off` escape hatch.
- Detection hardened against single-line false positives (inspired by rtk Rust's layered guards — as a network proxy we can't offer its raw-output tee recovery, so detection must be stricter): long-form git-status and build-output now require TWO feature lines in the detect window, closing the class where a file dump with one stray `ERROR:` / `Finished x` / `On branch` line was summarized to near-zero.

### Fixed
- Compression is now idempotent end to end (tool results are resent every turn; any byte drift between passes would break provider cache prefixes). Fixed three drifts: grep output dropped a trailing blank line on re-detection; git-diff's 500-line cap let compacted diffs re-enter the smart-truncate fallback (cap now 240, below the 250-line gate, and the fallback skips text already carrying our compression markers); tree's truncation marker itself exceeded `TREE_MAX_LINES` so resent trees lost one more line per pass.
- `_COMPRESSED_MARKERS` guard: text already carrying compression markers is left alone by the smart-truncate fallback.
- README fail-open note clarified: guards against crashes, not heuristic misjudgment (see the warning above).

### Changed
- Removed stale TODO doc (`docs/todo/rtk.md`).

## v0.4.6 - 2026-08-18

RTK compression fixes from real-traffic verification: read dumps were never compressed, and one detection bug actively corrupted Claude Code reads.

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
