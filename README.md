<div align="center">
  <h1>awerouter: Smart LLM Router <a href="https://github.com/Webioinfo01/aweskill"><img src="https://raw.githubusercontent.com/Webioinfo01/aweskill/main/logo/aweskill-badge2.svg" alt="aweskill companion"></a></h1>
  <p><strong>Route cheap/fast tasks to Flash, hard decisions to Pro.</strong></p>
  <p>Transparent same-protocol proxy that routes coding-agent requests by structural signals — no keyword guessing, no LLM classifier. Speaks Anthropic Messages, OpenAI Chat Completions, and OpenAI Responses.</p>
  <p>
    <strong>English</strong> ·
    <a href="./README_cn.md">简体中文</a>
  </p>
  <p>
    <a href="https://ko-fi.com/mugpeng"><img src="https://img.shields.io/badge/Ko--fi-Buy%20me%20a%20coffee-FF5E5B?style=flat-square&logo=ko-fi&logoColor=white" alt="Ko-fi"></a>
  </p>
  <p>
    <img src="https://img.shields.io/pypi/v/awerouter?style=flat-square&color=7C3AED" alt="Version">
    <img src="https://img.shields.io/badge/python-%E2%89%A53.9-0EA5E9?style=flat-square" alt="Python">
    <img src="https://img.shields.io/badge/license-MPL--2.0-22C55E?style=flat-square" alt="License">
  </p>
  <p>
    <img src="https://img.shields.io/badge/status-alpha-c96a3d?style=flat-square" alt="Status">
    <img src="https://img.shields.io/badge/install-pip-22C55E?style=flat-square" alt="pip">
    <img src="https://img.shields.io/badge/platform-terminal-334155?style=flat-square" alt="Platform">
    <img src="https://img.shields.io/pypi/dm/awerouter?style=flat-square" alt="Downloads">
    <img src="https://img.shields.io/github/stars/mugpeng/awerouter?style=flat-square" alt="Stars">
  </p>
</div>

> Transparent proxy that splits coding-agent traffic across providers by cost and capability. Same-protocol passthrough — no translation.

## Support Tools

awerouter works best alongside two companion tools:

- **[aweskill](https://aweskill.webioinfo.top/)** — CLI skill package manager for AI agents. Installs the awerouter skill so your agent can manage routing in natural language.
- **[aweswitch](https://github.com/Webioinfo01/aweswitch)** — Agent profile switcher. Launches Claude Code, Codex, or OpenCode sessions with a profile that points `BASE_URL` at the awerouter daemon.

aweskill lets the agent **manage** routing; aweswitch lets you **launch** sessions through it. Configure awerouter once, then start any agent against it with `aweswitch <profile>`.

## Install

```bash
pip install awerouter
```

## Quick Start

```bash
# 1. Init config (creates ~/.config/awerouter/{providers,routing}.json)
awerouter init

# 2. Interactively add a profile (writes both files, references stay consistent)
awerouter add
#    or edit by hand: providers.json for keys (${ENV_VAR}), routing.json for flash/pro

# 3. Start the daemon (profile name optional when only one exists)
awerouter serve [cc-router-1]     # shorthand: awerouter cc-router-1

# 4. Point CC at it — the serve banner prints both lines below
export ANTHROPIC_BASE_URL=http://127.0.0.1:20128
# aweswitch profile env: ANTHROPIC_MODEL=auto, _HAIKU_=flash, _OPUS_=pro
```

## Let AI Agent Configure

If you are working in Claude Code, Codex, Cursor, or another coding agent, tell it:

```text
Read https://github.com/mugpeng/awerouter/blob/main/README.ai.md and follow it to install and configure awerouter.
```

The agent will install the CLI, init config, help you add profiles, and install the awerouter skill via [aweskill](https://aweskill.webioinfo.top/) for ongoing routing management.

**After setup, you can tell the agent things like:**

> "Add a stepfun flash provider and a pro profile."
> "List my awerouter profiles."
> "Tune longContextThreshold from my usage."
> "Explain my usage savings."

The agent can run read-only commands (`list`, `config show`, `usage stats`, `usage calibrate`, `usage savings`) and edit config directly, but it will **not** run `awerouter serve` — that would start a long-lived daemon inside the agent. To start the daemon, run it in your own terminal:

```bash
awerouter serve cc-router-1
```

### awerouter skill

Install the [awerouter skill](https://github.com/mugpeng/awerouter/blob/main/resources/skills/awerouter/SKILL.md) via [aweskill](https://aweskill.webioinfo.top/) to let AI agents manage routing with natural language:

- List, inspect, add, and edit routing profiles
- Edit `providers.json` (endpoints/auth) and `routing.json` (strategy) separately
- Read `usage stats` / `usage calibrate` / `usage savings` and suggest threshold changes
- Guide environment-variable setup for `${ENV_VAR}` auth references

After install, you can tell the agent things like "Add a GLM provider for the openai-chat group", "Raise longContextThreshold to 12000", or "Show me which provider handles my web_search traffic". The agent reads the config, makes changes, and verifies with `awerouter config show` / `awerouter list`.

### Launch through aweswitch

Once awerouter is configured, launch any agent through it by pointing an aweswitch profile at the daemon.

**Example: launch OpenCode through awerouter**

Start the daemon with an openai-chat profile in one terminal:

```bash
awerouter serve oc-router-1
```

Add an aweswitch OpenCode profile pointing at it:

```json
{
  "profiles": {
    "opencode": {
      "oc-awerouter": {
        "env": {
          "OPENCODE_BASE_URL": "http://127.0.0.1:20128/v1",
          "OPENCODE_API_KEY": "sk-any-non-empty-value",
          "OPENCODE_NAME": "awerouter",
          "OPENCODE_MODEL": "auto"
        }
      }
    }
  }
}
```

```bash
aweswitch oc-awerouter
```

With `OPENCODE_MODEL` set to `auto`, awerouter routes each request by structural signals — the upstream provider receives the actual model id from `routing.json` destinations, not `auto`. Claude Code works the same way via an `anthropic` profile (`ANTHROPIC_MODEL=auto`).

## Config

Two files in `~/.config/awerouter/` (override with `AWEROUTER_CONFIG_DIR`):

**providers.json** — endpoints + keys, grouped by wire protocol (redacted in `config show`):

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

Three protocols are supported. `base_url` uses each native client's convention — copy it verbatim from your client config; awerouter appends the endpoint path the same way the native client would:

| Protocol id         | `base_url` style | Endpoint |
|---------------------|------------------|----------|
| `anthropic`         | `ANTHROPIC_BASE_URL` (no `/v1`) | `base_url + /v1/messages` |
| `openai-chat`       | `OPENAI_BASE_URL` (includes version segment) | `base_url + /chat/completions` |
| `openai-responses`  | `OPENAI_BASE_URL` (includes version segment) | `base_url + /responses` |

The same provider often uses a different path per protocol — GLM for instance: `https://open.bigmodel.cn/api/coding/paas/v4` for chat completions but `https://open.bigmodel.cn/api/v1` for responses. That's why each protocol group carries its own `base_url`.

The auth header is **auto-detected from `base_url`**: `anthropic.com` → `x-api-key` (bare token); everyone else → `Authorization` (auto-prefixes `Bearer `). No `auth_header` field needed unless the heuristic is wrong.

**routing.json** — strategy, no secrets (safe to commit):

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

`settings` is optional (defaults: `flash`/`pro`). It maps the model ids CC sends for the background (Haiku) and think (Opus) tiers, plus the `webSearchModel` destination for L1 web_search traffic (default `pro`). The main loop uses `auto` — routed by difficulty by L3. Set these in your aweswitch profile: `ANTHROPIC_DEFAULT_HAIKU_MODEL=flash`, `ANTHROPIC_MODEL=auto`, `ANTHROPIC_DEFAULT_OPUS_MODEL=pro`.

Keys reference `${ENV_VAR}` syntax. Missing env vars die with a clear message at startup.

> **Profile-based routing:** `routing.json` groups configs under profile ids (like aweswitch). `awerouter serve <profile>` starts one; with a single profile it auto-selects. `protocol` maps the profile to a providers.json group and decides which endpoint it serves — the serve banner prints the matching client env (`ANTHROPIC_BASE_URL` for Claude Code, `OPENAI_BASE_URL` / Codex `wire_api` for the openai protocols). Note: openai clients are single-model, so L2 tier labels effectively never fire for them — openai traffic routes by L1 + L3 with a flash default.

## How It Routes

Three-layer first-match-wins pipeline, evaluated per request:

| Layer | Signal | Decision |
|-------|--------|----------|
| L1 Capability | `web_search` tool in body | `settings.webSearchModel` (default **pro**) |
| L2 Tier label | `model == c1/flash` or `c1/think` | flash / pro respectively |
| L3 Difficulty | token count > threshold, or has image | **pro**; else **flash** |

CC's `/model` picker sets the tier model id (c1/flash / c1/pro / c1/think). awerouter reads it and routes accordingly — no keyword parsing, no LLM classifier.

## Commands

```bash
awerouter init                        # create default config (= config init)
awerouter add                         # interactively add a profile (and new providers)
awerouter list                        # list profiles (name, protocol, flash, pro, threshold)
awerouter serve [PROFILE] [--port 20128] [--host 127.0.0.1]
awerouter <PROFILE>                   # shorthand for serve PROFILE
awerouter config path | show | edit | init
awerouter usage stats [--clean]
awerouter usage tail [--lines 20]
awerouter usage calibrate
awerouter usage savings
```

All `usage` subcommands read the same request log; window options sit between `usage` and the subcommand (`awerouter usage --since today savings`).

`usage stats` aggregates the log per profile: label/destination/provider/model breakdowns with percentages, error and fallback counts, latency percentiles (first byte and total) per destination/provider/model, and estimated message tokens. `--since` accepts `today`, `yesterday`, `7d`, or `YYYY-MM-DD` (local time); `--profile` restricts to one profile; `--clean` deletes the saved logs after a confirmation prompt. `usage tail` shows recent entries verbatim.

`usage calibrate` shows the message-token distribution of L3 traffic (the threshold-sensitive layer; messages only — system prompt and tools are excluded) and suggests candidate `longContextThreshold` values at p90/p95/p99. Run it after some real traffic, then edit `routing.json`.

`usage savings` is the token accounting view: how many message-input tokens each tier consumed and how many pro input tokens routing offloaded to flash vs a pro-only baseline. A cache-sensitivity section brackets the offload between "all cache reads" and "all full price" (Anthropic-style ~0.1x read / 1.25x write / 5-min TTL) and shows your switch cadence vs the TTL — a cache-warm pro-only baseline would have billed those tokens at cache-read prices. The output ends with ready-to-fill formulas using the measured token counts — substitute your providers' input prices (per 1M tokens) and read off the saved amount (output tokens, flash-side caching, and capability-mismatch turns are not modeled).

## Troubleshooting

**CC shows `502 status code (no body)` right after launch** — a shell proxy (Clash etc.) is hijacking loopback traffic. Requests to `127.0.0.1:20128` go into the proxy, whose `127.0.0.1` is itself, so nothing is listening and the proxy returns an empty 502. `serve` prints a warning when it detects this; fix it by exempting loopback in your shell config:

```bash
export no_proxy=127.0.0.1,localhost NO_PROXY=127.0.0.1,localhost
```

Then open a new terminal and relaunch CC.

## Development

```bash
git clone https://github.com/mugpeng/awerouter
cd awerouter
pip install -e ".[dev]"
pytest
```

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for architecture notes, config semantics, and the release process.

## Support

If awerouter saves you money, consider supporting it:

- ⭐ Star the repo — it helps others find it.
- ☕ [Ko-fi](https://ko-fi.com/mugpeng) — buy me a coffee.
- 💬 WeChat — scan the QR code below.

<p align="center">
  <img src="assets/images/wechat-pay.jpg" alt="WeChat Pay" width="240">
</p>

> awerouter is free and open source. Sponsors keep it maintained — thank you.
