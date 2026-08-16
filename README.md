<div align="center">
  <h1>awerouter: Smart LLM Router</h1>
  <p><strong>Route cheap/fast tasks to Flash, hard decisions to Pro.</strong></p>
  <p>Transparent Anthropic proxy that routes Claude Code requests by structural signals — no keyword guessing, no LLM classifier.</p>
  <p>
    <strong>English</strong> ·
    <a href="./README_cn.md">简体中文</a>
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
  <p>
    <a href="https://ko-fi.com/mugpeng"><img src="https://img.shields.io/badge/Ko--fi-Buy%20me%20a%20coffee-FF5E5B?style=flat-square&logo=ko-fi&logoColor=white" alt="Ko-fi"></a>
  </p>
</div>

> Transparent proxy that splits Claude Code traffic across providers by cost and capability.

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

## Config

Two files in `~/.config/awerouter/` (override with `AWEROUTER_CONFIG_DIR`):

**providers.json** — endpoints + keys, grouped by agent (redacted in `config show`):

```json
{
  "claude": {
    "stepfun":   { "base_url": "https://api.stepfun.com/step_plan", "auth": "${STEPFUN_AUTH_TOKEN}" },
    "anthropic": { "base_url": "https://api.anthropic.com",          "auth": "${ANTHROPIC_KEY}" }
  },
  "codex": {
    "stepfun": { "base_url": "https://api.stepfun.com/v1", "auth": "${STEPFUN_AUTH_TOKEN}" }
  }
}
```

The auth header is **auto-detected from `base_url`**: `anthropic.com` → `x-api-key` (bare token); everyone else → `Authorization` (auto-prefixes `Bearer `). No `auth_header` field needed unless the heuristic is wrong.

**routing.json** — strategy, no secrets (safe to commit):

```json
{
  "settings": {
    "backgroundModel": "flash",
    "thinkModel": "pro"
  },
  "cc-router-1": {
    "agent": "claude",
    "longContextThreshold": 8000,
    "destinations": {
      "flash": "stepfun,step-3.7-flash",
      "pro":   "anthropic,claude-opus-5"
    }
  }
}
```

`settings` is optional (defaults: `flash`/`pro`). It defines the model ids CC sends for background (Haiku) and think (Opus) tiers. The main loop uses `auto` — routed by difficulty by L3. Set these in your aweswitch profile: `ANTHROPIC_DEFAULT_HAIKU_MODEL=flash`, `ANTHROPIC_MODEL=auto`, `ANTHROPIC_DEFAULT_OPUS_MODEL=pro`.

Keys reference `${ENV_VAR}` syntax. Missing env vars die with a clear message at startup.

> **Profile-based routing:** `routing.json` groups configs under profile ids (like aweswitch). `awerouter serve <profile>` starts one; with a single profile it auto-selects. `agent` maps the profile to a providers.json group.

## How It Routes

Three-layer first-match-wins pipeline, evaluated per request:

| Layer | Signal | Decision |
|-------|--------|----------|
| L1 Capability | `web_search` tool in body | **pro** (flash can't run it) |
| L2 Tier label | `model == c1/flash` or `c1/think` | flash / pro respectively |
| L3 Difficulty | token count > threshold, or has image | **pro**; else **flash** |

CC's `/model` picker sets the tier model id (c1/flash / c1/pro / c1/think). awerouter reads it and routes accordingly — no keyword parsing, no LLM classifier.

## Commands

```bash
awerouter init                        # create default config (= config init)
awerouter add                         # interactively add a profile (and new providers)
awerouter list                        # list profiles (name, agent, flash, pro, threshold)
awerouter show [PROFILE]              # show one profile or all config (redacted)
awerouter serve [PROFILE] [--port 20128] [--host 127.0.0.1]
awerouter <PROFILE>                   # shorthand for serve PROFILE
awerouter config path | show | edit | init
awerouter log [--lines 20]
awerouter stats
awerouter calibrate
```

`calibrate` shows the message-token distribution of L3 traffic (the threshold-sensitive layer; messages only — system prompt and tools are excluded) and suggests candidate `longContextThreshold` values at p90/p95/p99. Run it after some real traffic, then edit `routing.json`.

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
