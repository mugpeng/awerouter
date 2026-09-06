---
name: awerouter
description: "Use when helping users set up or edit awerouter routing, inspect routing profiles, interpret usage logs, or tune flash/pro split behavior for coding-agent traffic. 中文触发词：awerouter、路由配置、flash/pro、长文本阈值、Anthropic代理、OpenAI代理、用量统计、本地模型、Ollama。"
---

# awerouter

This skill covers **configuring** awerouter routing, inspecting profiles, and interpreting usage/calibration output.

## Do Not Run Long-Lived Servers

**Never start `awerouter serve run` or `awerouter serve all` for the user inside this agent.** They block the session. Tell the user to run them in their own terminal.

## Language Behavior

- Reply in the user's language when possible.
- If the user asks in Chinese, continue in Chinese.
- If the user asks in English, continue in English.

## Core Concepts

awerouter is a transparent same-protocol proxy for coding-agent traffic. It does not translate between protocols. Request bodies pass through untouched unless a profile opts into `rtk` tool-result compression or `odcp` context pruning.

Key config dir: `~/.config/awerouter/` (override with `AWEROUTER_CONFIG_DIR`).
Request log dir: `~/.local/state/awerouter/` (override with `AWEROUTER_LOG_DIR`).

Main config files:
- `providers.json` — endpoints and auth, grouped by protocol.
- `routing.json` — routing profiles and global routing strategy.

## Safe Commands

You may run these read-only or non-interactive commands:
- `awerouter init [TEMPLATE] [--merge]`
- `awerouter config path`
- `awerouter config show [PROFILE]`
- `awerouter config edit`
- `awerouter list`
- `awerouter usage stats`
- `awerouter usage log`
- `awerouter usage tokens`
- `awerouter usage calibrate`
- `awerouter usage savings`

Do not run these inside the agent:
- `awerouter serve run` (blocks the session)
- `awerouter add` (interactive wizard)
- `awerouter config login [claude]` (opens a browser and waits for a pasted code — the user runs it)
- `awerouter config logout [claude]` (deletes a stored credential)
- `awerouter config restore` (overwrites a config file from its `.bak`)
- `awerouter usage clean` (deletes saved request logs)

## Config Structure

### providers.json

```json
{
  "anthropic": {
    "stepfun":   { "base_url": "https://api.stepfun.com/step_plan", "auth": "${STEPFUN_AUTH_TOKEN}" },
    "anthropic": { "base_url": "https://api.anthropic.com",          "auth": "${ANTHROPIC_KEY}" },
    "claude":    { "base_url": "https://api.anthropic.com",          "auth": "claude" },
    "ollama":    { "base_url": "http://127.0.0.1:11434" }
  },
  "openai-chat": {
    "stepfun": { "base_url": "https://api.stepfun.com/step_plan/v1", "auth": "${STEPFUN_AUTH_TOKEN}" },
    "ollama":  { "base_url": "http://127.0.0.1:11434/v1" }
  },
  "openai-responses": {
    "openai": { "base_url": "https://api.openai.com/v1", "auth": "${OPENAI_API_KEY}" },
    "codex":  { "base_url": "https://chatgpt.com/backend-api/codex", "auth": "codex" }
  }
}
```

Rules:
- `base_url` uses each native client's convention. Copy it from the client config.
- `auth` supports `${ENV_VAR}` references. It may be omitted for no-auth upstreams (local model servers: Ollama, LM Studio, llama.cpp, vLLM) — requests then go out with no auth header. Local servers sit under `openai-chat` with a `/v1` base_url; Ollama ≥ 0.14 also speaks the anthropic protocol.
- `auth: "codex"` (openai-responses group only) rides the local Codex CLI login (`$CODEX_HOME/auth.json`): per-request Bearer access_token + `chatgpt-account-id`, honors `https_proxy`/`all_proxy`, 401 re-reads the login once; a 401 surviving the re-read falls back flash→pro when pro has its own key (pro on the same login surfaces the 401). Body normalized for backend quirks: `store` forced false, `max_output_tokens` dropped, and non-streaming requests go upstream as a stream and return buffered as one JSON response. No refresh by design — the CLI owns refresh; missing login = 503 with a `codex login` hint (deliberately no fallback there: a missing login is a config error, and silently serving it from a paid pro would hide both). Codex model names drift (current: `gpt-5.6-luna`), so the destination's `model` may need a one-line update over time.
- `auth: "claude"` (anthropic group only) routes through a Claude Pro/Max subscription OAuth login that awerouter itself owns — no local Claude Code CLI login needed or used. The user logs in out-of-band with `awerouter config login claude` (browser PKCE flow, paste-the-code); tokens live in `~/.config/awerouter/claude-auth.json` (0600). Per-request Bearer access_token + `anthropic-beta: oauth-2025-04-20`; honors `https_proxy`/`all_proxy`; access tokens auto-refresh (rotating refresh token, rotation-safe against concurrent requests/processes). 401 forces one refresh and retries; a 401 surviving it falls back flash→pro when pro has its own key (same as codex); missing login = 503 with an `awerouter config login claude` hint. No body normalization. Note: reverse-engineered public endpoints that can drift, and Anthropic's 2026 ToS restricts third-party use of subscription OAuth tokens — user rides their own subscription at their own risk.
- `auth_header` is optional. If omitted, awerouter auto-detects:
  - `anthropic.com` -> `x-api-key`
  - others -> `Authorization` with auto `Bearer ` prefix when needed.
- `models` (optional, gateway-only): a list of model ids the provider may serve directly. Under `awerouter serve all`, each becomes a fixed `<provider>/<model>` forward that bypasses routing; an undeclared model is rejected with a 400 naming the declared list. Declare `models` separately in each protocol group where the provider appears.

### routing.json

```json
{
  "settings": {
    "backgroundModel": "flash",
    "thinkModel": "pro",
    "toolRouting": { "webSearch": "pro", "edit": "pro" }
  },
  "cc-router-1": {
    "protocol": "anthropic",
    "longContextThreshold": 8000,
    "destinations": {
      "flash": "stepfun,step-3.7-flash",
      "pro":   "anthropic,claude-opus-5"
    }
  }
}
```

Rules:
- `settings` is optional. Tool-keyed rules live in `settings.toolRouting` (`webSearch`, `edit`); the legacy top-level `webSearchModel` still works. `imageModel` (default pro) re-aims the image guard; `defaultModel` (default flash) flips the fall-through. `imageBridge` (default false, opt-in) has the `imageModel` destination transcribe history images to text so a text-only pro keeps the session — fresh uploads still route to `imageModel` natively; transcription failures keep the image route (prefer scoping it to the one profile with a multimodal `imageModel`). `longContextThreshold` is an integer or `"auto"` (calibrated from recent traffic per `settings.longContextAuto`).
- Every settings key may also be written directly in a profile body, flat next to `protocol`/`destinations` — it overrides the global value for that profile alone; missing keys (and missing fields inside `toolRouting`/`longContextAuto`) inherit from the global block. `config show <profile>` prints the effective values; the serve banner lists overrides on an `overrides -> ...` line.
- Each profile needs `protocol`, `longContextThreshold`, and `destinations`. `protocol` accepts one id or a list (`["anthropic", "openai-chat"]`) — a list serves several wire protocols on one port (clients pick by endpoint path); every destination provider must exist in each served providers.json group.
- Supported protocols: `anthropic`, `openai-chat`, `openai-responses`.
- Optional `"rtk": true` enables RTK tool-result compression (default off): verbose tool output (git diff/status/log, grep, listings, build logs) is compressed before forwarding. Fail-open, deterministic; error results and short content pass through. Per-request opt-out header: `X-Awerouter-Token-Saver: off`. After enabling, re-run `awerouter usage calibrate` (thresholds tuned on uncompressed traffic over-trigger pro).
- Optional `"odcp": true` (or `{"dedup": bool, "purgeErrors": bool | {"turns": int}}`) enables context pruning beside rtk (default off): repeated identical tool calls (same tool + arguments) keep only the newest output; errored calls older than `turns` user messages (default 4) lose their input strings, error text stays (anthropic only). Editors and task/planning tools protected; trailing turn untouched; runs before rtk. Same fail-open, `X-Awerouter-Token-Saver: off` bypass, and `usage calibrate` advice.

### Gateway mode (serve all)

`awerouter serve all` serves every routing profile and every declared `<provider>/<model>` on one port; the request's model name selects:
- `<profile>/auto` — that profile's smart route (L1–L4).
- `<profile>/flash` / `<profile>/pro` — force a tier; a profile's own L2 tier labels also work.
- `<provider>/<model>` — fixed forward to a model declared in that provider's `models` list; bypasses routing. Requires at least one profile serving that wire protocol.

Bare `auto`/`flash`/`pro` resolve via routing.json's top-level `defaultProfile`. The endpoint path picks the wire protocol among those a profile serves; `GET /v1/models` lists all names; unknown names return descriptive 400s. Never run `serve all` inside the agent — the server belongs in the user's terminal (`serve all --install` exists as a resident service for them).

## Routing Logic

awerouter evaluates requests in first-match-wins order:

| Layer | Signal | Result |
|-------|--------|--------|
| L1 | `web_search` tool present | `toolRouting.webSearch` (default pro; legacy `webSearchModel` works) |
| L1 | image present | `settings.imageModel` (default pro; flash for multimodal-sidekick profiles). With `imageBridge` on, only images in the FINAL message route here — history images are transcribed to text by the imageModel destination and the request falls through (codex subscription logins skip the bridge) |
| L2 | tier model label (`c1/flash`, `c1/think`, or equivalent model mapping) | flash or pro |
| L3 | long context (token count over all request content) | pro if above threshold |
| L4 | trailing tool batch changed code (`edit`/`write`/`apply_patch`/...) | `toolRouting.edit` (default pro, `null` disables) |
| — | nothing matched | `settings.defaultModel` (default flash) |

Notes:
- The image guard outranks tier labels and long context: a model that cannot see images must never receive them.
- For Anthropic-style clients, tier labels come from the model id mapping.
- For OpenAI-style clients, tier labels usually do not apply; routing is mostly L1 + L3 with the `defaultModel` fall-through.
- `longContextThreshold` compares against all request content (messages, system prompt, tool definitions, tool I/O); calibrate from real traffic.

## Common Tasks

### Init config from a bundled template

Run:
```bash
awerouter init                    # 'default' template
awerouter init step-glm           # key-only combo: flash=StepFun step_plan, pro=GLM coding plan
awerouter init glm-codex          # flash=GLM coding plan, pro=ChatGPT subscription (auth: "codex")
awerouter init step-glm-mm        # multimodal sidekick, dual-protocol (anthropic+openai-chat on one port): pro=GLM glm-5.3 does everything, flash=StepFun step-3.7-flash takes images only; imageBridge on — follow-up text turns return to pro carrying flash's transcriptions of history images
awerouter init step-glm-mm --merge  # add a template to an EXISTING config: fills missing providers/profiles/settings, never overwrites (warns when imageModel/defaultModel/imageBridge are newly set)
```

Templates are `<name>.providers.json` + `<name>.routing.json` pairs; an unknown name fails with the list of available ones. This creates `providers.json` and `routing.json` if missing. With `--merge` on an existing config, only missing entries are added (skipped: providers/profiles already present, settings keys already set) and the merged config is validated before the command finishes.

### Inspect current config

Run:
```bash
awerouter config path
awerouter config show            # everything, secrets redacted
awerouter config show <profile>  # just that profile: its providers + routing entry
awerouter list
```

### Edit a routing profile

1. Read the config.
2. Update `routing.json` only for strategy changes.
3. Update `providers.json` only for endpoint/auth changes.
4. For auth values, scan the shell config first for API-key variables the user already exports (`~/.zshrc`, `~/.bashrc`, `~/.bash_profile`: `export <NAME>=...` matching `*_API_KEY` / `*_AUTH_TOKEN` / `*_KEY` / `*_TOKEN`; on Windows, the user environment) and reuse them as `${VAR_NAME}` — report variable names only, never values, and only ask for keys that are missing. Omit `auth` entirely for local no-auth providers.
5. Validate with:
```bash
awerouter config show
awerouter list
```

### Tune longContextThreshold

1. Collect traffic.
2. Run:
```bash
awerouter usage calibrate
```
3. Update `longContextThreshold` in `routing.json`.

### Review cost routing behavior

Use:
```bash
awerouter usage stats
awerouter usage savings
```

Explain the output plainly; do not promise exact billing because output tokens and cache semantics vary by provider.

### Explain token composition

Use `awerouter usage tokens` to show input-token totals by content type (messages, system prompt, tools, tool I/O); `awerouter usage log --tokens` shows the same breakdown per request.

### Run a resident service (start at login / survive reboot)

Use:
```bash
awerouter serve run <profile> --install   # install launchd/systemd service and start it now
awerouter serve status                    # shows svc:launchd or svc:systemd for resident instances
awerouter serve stop <profile>            # stops the running instance (returns at next login)
awerouter serve stop <profile> --purge    # stops and removes the service (no more auto-start)
awerouter serve restart <profile>         # re-install/re-spawn from this shell (applies changed secrets)
awerouter serve all --install             # gateway mode resident service
```

Notes:
- `--install` implies background mode. Re-running it updates host/port/env and restarts the service.
- The service manager starts the daemon without your shell environment. awerouter bakes `${VAR}` values referenced by `providers.json` (plus `AWEROUTER_*` and proxy vars) into the service file (mode 0600). If a variable the target needs is unset, install fails up front; set it, reload the shell, and re-run `--install`.
- `serve restart` is how a changed secret/env var gets applied: a resident service is re-installed from the current shell (same command line, port and host, env baked fresh; an installed-but-stopped service starts too), a plain `-d` instance is stopped and re-spawned from the current shell. Config-file edits hot-reload and never need it. The user runs it in their own terminal.
- `serve stop` routes resident instances through the service manager (plain SIGTERM would be instantly undone by the restart policy). `--purge` removes the service file; without it, the instance comes back at the next login.
- These commands are POSIX-only.

## Pitfalls

- Loopback proxy hijack: shell proxy env without `no_proxy=127.0.0.1,localhost` can cause empty `502` errors.
- Long-lived sessions: never run `awerouter serve run` inside the agent.
- OpenAI clients: they do not behave like Claude Code tier labels.
- Auth mismatch: if upstream expects a different header, set `auth_header` explicitly.

## Troubleshooting

- Empty `502` right after launch -> likely shell proxy hijacking loopback.
- Profile not found -> check `routing.json` profile id spelling and `AWEROUTER_CONFIG_DIR`.
- Provider not found -> check protocol group name and provider spelling in `providers.json`.
- Missing token env var -> set the shell variable before starting `awerouter serve run`.
- Serve-start warning "no auth set for off-machine providers" -> either a forgotten `auth` entry (cloud APIs need one) or a legitimate LAN no-auth server; the warning is informational, not fatal.
- Requests 503 with "awerouter config login claude" -> the stored subscription login is missing/invalid; the user runs `awerouter config login claude` in their own terminal (browser + paste the code).
- Config broken after an edit -> `config edit` and the `add` wizard write a `.bak` before every change; tell the user to run `awerouter config restore [providers|routing]` in their own terminal.
