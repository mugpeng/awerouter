---
name: awerouter
description: "Use when helping users set up or edit awerouter routing, inspect routing profiles, interpret usage logs, or tune flash/pro split behavior for coding-agent traffic. 中文触发词：awerouter、路由配置、flash/pro、长文本阈值、Anthropic代理、OpenAI代理、用量统计。"
---

# awerouter

This skill covers **configuring** awerouter routing, inspecting profiles, and interpreting usage/calibration output.

## Do Not Run Long-Lived Servers

**Never start `awerouter serve` for the user inside this agent.** It blocks the session. Tell the user to run it in their own terminal.

## Language Behavior

- Reply in the user's language when possible.
- If the user asks in Chinese, continue in Chinese.
- If the user asks in English, continue in English.

## Core Concepts

awerouter is a transparent same-protocol proxy for coding-agent traffic. It does not rewrite request bodies or translate between protocols.

Key config dir: `~/.config/awerouter/` (override with `AWEROUTER_CONFIG_DIR`).
Request log dir: `~/.local/state/awerouter/` (override with `AWEROUTER_LOG_DIR`).

Main config files:
- `providers.json` — endpoints and auth, grouped by protocol.
- `routing.json` — routing profiles and global routing strategy.

## Safe Commands

You may run these read-only or non-interactive commands:
- `awerouter init`
- `awerouter config path`
- `awerouter config show`
- `awerouter config edit`
- `awerouter list`
- `awerouter usage stats`
- `awerouter usage tail`
- `awerouter usage calibrate`
- `awerouter usage savings`

Do not run these inside the agent:
- `awerouter serve`
- `awerouter add`

## Config Structure

### providers.json

```json
{
  "anthropic": {
    "stepfun":   { "base_url": "https://api.stepfun.com/step_plan", "auth": "${STEPFUN_AUTH_TOKEN}" },
    "anthropic": { "base_url": "https://api.anthropic.com",          "auth": "${ANTHROPIC_KEY}" }
  },
  "openai-chat": {
    "stepfun": { "base_url": "https://api.stepfun.com/step_plan/v1", "auth": "${STEPFUN_AUTH_TOKEN}" }
  },
  "openai-responses": {
    "openai": { "base_url": "https://api.openai.com/v1", "auth": "${OPENAI_API_KEY}" }
  }
}
```

Rules:
- `base_url` uses each native client's convention. Copy it from the client config.
- `auth` supports `${ENV_VAR}` references.
- `auth_header` is optional. If omitted, awerouter auto-detects:
  - `anthropic.com` -> `x-api-key`
  - others -> `Authorization` with auto `Bearer ` prefix when needed.

### routing.json

```json
{
  "settings": {
    "backgroundModel": "flash",
    "thinkModel": "pro",
    "webSearchModel": "pro"
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
- `settings` is optional.
- Each profile needs `protocol`, `longContextThreshold`, and `destinations`.
- Supported protocols: `anthropic`, `openai-chat`, `openai-responses`.

## Routing Logic

awerouter evaluates requests in first-match-wins order:

| Layer | Signal | Result |
|-------|--------|--------|
| L1 | `web_search` tool present | `settings.webSearchModel` (default pro) |
| L2 | tier model label (`c1/flash`, `c1/think`, or equivalent model mapping) | flash or pro |
| L3 | long context or image-heavy content | pro if above threshold or image present |

Notes:
- For Anthropic-style clients, tier labels come from the model id mapping.
- For OpenAI-style clients, tier labels usually do not apply; routing is mostly L1 + L3 with a flash default.
- `longContextThreshold` is message-token-centric; calibrate from real traffic.

## Common Tasks

### Init default config

Run:
```bash
awerouter init
```

This creates template `providers.json` and `routing.json` if missing.

### Inspect current config

Run:
```bash
awerouter config path
awerouter config show
awerouter list
```

### Edit a routing profile

1. Read the config.
2. Update `routing.json` only for strategy changes.
3. Update `providers.json` only for endpoint/auth changes.
4. Keep `${ENV_VAR}` for secrets.
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

## Pitfalls

- Loopback proxy hijack: shell proxy env without `no_proxy=127.0.0.1,localhost` can cause empty `502` errors.
- Long-lived sessions: never run `awerouter serve` inside the agent.
- OpenAI clients: they do not behave like Claude Code tier labels.
- Auth mismatch: if upstream expects a different header, set `auth_header` explicitly.

## Troubleshooting

- Empty `502` right after launch -> likely shell proxy hijacking loopback.
- Profile not found -> check `routing.json` profile id spelling and `AWEROUTER_CONFIG_DIR`.
- Provider not found -> check protocol group name and provider spelling in `providers.json`.
- Missing token env var -> set the shell variable before starting `awerouter serve`.
