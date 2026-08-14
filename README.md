<div align="center">
  <h1>awerouter: Smart LLM Router</h1>
  <p><strong>Route cheap/fast tasks to Flash, hard decisions to Pro.</strong></p>
  <p>Transparent Anthropic proxy that routes Claude Code requests by structural signals — no keyword guessing, no LLM classifier.</p>
  <p>
    <strong>English</strong> ·
    <a href="./README_cn.md">简体中文</a>
  </p>
  <p>
    <img src="https://img.shields.io/badge/version-0.1.0-7C3AED?style=flat-square" alt="Version">
    <img src="https://img.shields.io/badge/python-%E2%89%A53.9-0EA5E9?style=flat-square" alt="Python">
  </p>
  <p>
    <img src="https://img.shields.io/badge/status-alpha-c96a3d?style=flat-square" alt="Status">
    <img src="https://img.shields.io/badge/install-pip-22C55E?style=flat-square" alt="pip">
    <img src="https://img.shields.io/badge/platform-terminal-334155?style=flat-square" alt="Platform">
    <img src="https://img.shields.io/pypi/dm/awerouter?style=flat-square" alt="Downloads">
    <img src="https://img.shields.io/github/stars/owner/awerouter?style=flat-square" alt="Stars">
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
awerouter config init

# 2. Edit providers.json — set your API keys via ${ENV_VAR}
# 3. Edit routing.json — map flash/pro to your providers/models

# 4. Start the daemon (profile name optional when only one exists)
awerouter serve [cc-router-1]

# 5. Point CC at it (in aweswitch or directly)
export ANTHROPIC_BASE_URL=http://127.0.0.1:20128
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
  "cc-router-1": {
    "agent": "claude",
    "backgroundModel": "c1/flash",
    "thinkModel": "c1/think",
    "longContextThreshold": 8000,
    "destinations": {
      "flash": "stepfun,step-3.7-flash",
      "pro":   "anthropic,claude-opus-5"
    }
  }
}
```

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
awerouter serve [PROFILE] [--port 20128] [--host 127.0.0.1]
awerouter config path | show | edit | init
awerouter log [--lines 20]
awerouter stats
awerouter calibrate
```

`calibrate` shows the token distribution of L3 traffic (the threshold-sensitive layer) and suggests candidate `longContextThreshold` values at p90/p95/p99. Run it after some real traffic, then edit `routing.json`.

## Development

```bash
git clone <repo-url>
cd awerouter
pip install -e ".[dev]"
pytest
```
