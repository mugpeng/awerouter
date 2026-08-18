<div align="center">
  <img src="logo/logo.webp" alt="awerouter" width="860">
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

aweskill lets the agent **manage** routing by operating skills; aweswitch lets you **launch** sessions through it. Configure awerouter once, then start any agent against it with `aweswitch <profile>`.

## Install & Usage

### Let AI agent install and configure

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

The agent can run read-only commands (`list`, `config show`, `usage stats`, `usage calibrate`, `usage savings`) and edit config directly, but it will **not** run `awerouter serve` (long-lived daemon), `awerouter add` (interactive wizard), `awerouter restore` (overwrites config), `awerouter usage clean` (deletes logs), or `awerouter self-update` (upgrades the installation). To start the daemon, run it in your own terminal:

```bash
awerouter serve cc-router-1
```

#### awerouter skill

Install the [awerouter skill](https://github.com/mugpeng/awerouter/blob/main/resources/skills/awerouter/SKILL.md) via [aweskill](https://aweskill.webioinfo.top/) to let AI agents manage routing with natural language:

- List, inspect, add, and edit routing profiles
- Edit `providers.json` (endpoints/auth) and `routing.json` (strategy) separately
- Read `usage stats` / `usage calibrate` / `usage savings` and suggest threshold changes
- Guide environment-variable setup for `${ENV_VAR}` auth references

After install, you can tell the agent things like "Add a GLM provider for the openai-chat group", "Raise longContextThreshold to 12000", or "Show me which provider handles my web_search traffic". The agent reads the config, makes changes, and verifies with `awerouter config show` / `awerouter list`.

#### Launch through aweswitch

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

### Manual install and usage

Install from PyPI:

```bash
pip install awerouter
```

Quick Start:

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
    "webSearchModel": "pro",
    "longContextAuto": {
      "percentile": 95,
      "windowDays": 7,
      "minSamples": 50,
      "fallbackThreshold": 8000
    }
  },
  "cc-router-1": {
    "protocol": "anthropic",
    "port": 20128,
    "longContextThreshold": 8000,
    "destinations": {
      "flash": "stepfun,step-3.7-flash",
      "pro":   "anthropic,claude-opus-5"
    }
  }
}
```

`settings` is optional (defaults: `flash`/`pro`). It maps the model ids CC sends for the background (Haiku) and think (Opus) tiers, plus the `webSearchModel` destination for L1 web_search traffic (default `pro`). The main loop uses `auto` — routed by difficulty by L3. Set these in your aweswitch profile: `ANTHROPIC_DEFAULT_HAIKU_MODEL=flash`, `ANTHROPIC_MODEL=auto`, `ANTHROPIC_DEFAULT_OPUS_MODEL=pro`.

`longContextThreshold` is an integer, or `"auto"` to calibrate it from this profile's own traffic: at every `serve` start, awerouter takes the `percentile` of the profile's L3 effective-token distribution over the trailing `windowDays` as the threshold. With fewer than `minSamples` L3 requests in the window (fresh profile, quiet week) the `fallbackThreshold` applies instead. All four knobs live in `settings.longContextAuto` and are optional — the banner always prints what was picked and why. Note the percentile sets the flash/pro *split*, not flash's capability ceiling: if your flash model degrades on very long contexts, keep a manual threshold.

Keys reference `${ENV_VAR}` syntax. Missing env vars die with a clear message at startup.

> **Profile-based routing:** `routing.json` groups configs under profile ids (like aweswitch). `awerouter serve <profile>` starts one; with a single profile it auto-selects. `protocol` maps the profile to a providers.json group and decides which endpoint it serves — the serve banner prints the matching client env (`ANTHROPIC_BASE_URL` for Claude Code, `OPENAI_BASE_URL` / Codex `wire_api` for the openai protocols). Note: openai clients are single-model, so L2 tier labels effectively never fire for them — openai traffic routes by L1 + L3 with a flash default.

> **Ports:** the optional `port` field pins a profile's listen port (`awerouter list` shows it); precedence: `--port` flag > profile `port` > 20128 default. An explicitly chosen port that is already in use fails loudly — clients hardcode it, it must not silently move. Without one, serve takes the first free port scanning up from 20128: the first instance gets 20128, the next 20129, and so on — the assignment follows start order, not the profile. For the one-instance-at-a-time swap workflow, leave profiles portless and point clients at 20128.

## How It Routes

Three-layer first-match-wins pipeline, evaluated per request:

| Layer | Signal | Decision |
|-------|--------|----------|
| L1 Capability | `web_search` tool in body | `settings.webSearchModel` (default **pro**) |
| L2 Tier label | `model == c1/flash` or `c1/think` | flash / pro respectively |
| L3 Difficulty | token count (all request content) > threshold, or has image | **pro**; else **flash** |

CC's `/model` picker sets the tier model id (c1/flash / c1/pro / c1/think). awerouter reads it and routes accordingly — no keyword parsing, no LLM classifier.

## Commands

```bash
awerouter init                        # create default config from templates
awerouter add                         # interactively add a profile (pick category and providers)
awerouter list                        # list profiles (name, protocol, port, flash, pro, threshold)
awerouter serve [PROFILE] [--port N] [--host 127.0.0.1]  # port: --port > profile 'port' > 20128
awerouter <PROFILE>                   # shorthand for serve PROFILE
awerouter restore [providers|routing] # restore a config file from its .bak backup
awerouter self-update [--check]        # upgrade to the latest PyPI release (--check: versions only)
awerouter config path                 # print both config file paths
awerouter config show [PROFILE]       # redacted config; PROFILE = its providers + entry only
awerouter config edit [providers|routing]  # open one file in $EDITOR (backs up to .bak first)
awerouter usage stats [--since ..] [--profile ..]
awerouter usage clean                 # delete saved request logs (asks to confirm)
awerouter usage log [--lines 20] [--all] [--tokens] [--since ..] [--profile ..]
awerouter usage tokens [--since ..] [--profile ..]
awerouter usage calibrate [--since ..] [--profile ..]
awerouter usage savings [--since ..] [--profile ..]
```

All `usage` subcommands read the same request log. `log`, `stats`, `tokens`, `calibrate`, and `savings` take `--since` (`today`, `yesterday`, `7d`, or `YYYY-MM-DD`, local time) and `--profile` directly — e.g. `awerouter usage stats --since today --profile cc-1`; `clean` deletes everything and takes no window options.

`usage stats` aggregates the log per profile (with its wire protocol): label/agent/destination/provider/model breakdowns with percentages, error and fallback counts, latency percentiles (first byte and total) per destination/provider/model, and estimated request tokens (all request content: messages, system prompt, tools, tool I/O). `usage clean` deletes the saved logs (`requests.jsonl` + rotated backup) after a confirmation prompt. `usage log` shows entries verbatim — the last 20 by default, or every entry with `--all`; each line includes the protocol served and the calling agent, detected from the client's `User-Agent` header (`claude-cli/...` → `claude-code`, `codex_cli_rs/...` → `codex`, `opencode/...` → `opencode`). `--tokens` swaps the status/latency/model-in columns for the per-type token breakdown of each request (`msg/sys/tools/results/calls/think`); entries logged before per-type counting show only the total.

`usage tokens` aggregates those per-type breakdowns: input-token totals and share by content type (messages, system prompt, tool definitions, tool results, tool-call arguments, thinking) — useful for seeing how much of a request's tokens are environment constants (system prompt + tool definitions) versus conversation.

`config edit` and the `add` wizard snapshot the target file to `<name>.json.bak` before every write; `awerouter restore [providers|routing]` copies a backup back (with confirmation, then validates the restored config). `config path` prints the two config file paths; `config show [PROFILE]` shows the redacted full config, or just one profile's providers and routing entry.

`self-update` upgrades the installed package — pipx installs use `pipx upgrade awerouter`, everything else `pip install --upgrade`; restart running serve instances afterwards. Every command also checks PyPI in a background thread (at most once a day, cached as `update-check.json` in the config dir) and prints a one-line reminder after the command when a newer release exists — also throttled to once a day; `AWEROUTER_NO_UPDATE_CHECK=1` disables the check entirely. The serve banner shows the same update hint from the cached check.

`usage calibrate` shows the request-token distribution of L3 traffic (the threshold-sensitive layer; all request content — messages, system prompt, tools, tool I/O) and suggests candidate `longContextThreshold` values at p90/p95/p99, plus what `"auto"` would pick under `settings.longContextAuto`. Run it after some real traffic, then either edit `routing.json` or switch the profile to `"auto"` and let serve calibrate on each start.

`usage savings` is the token accounting view: how many request-input tokens each tier consumed and how many pro input tokens routing offloaded to flash vs a pro-only baseline. A cache-sensitivity section brackets the offload between "all cache reads" and "all full price" (Anthropic-style ~0.1x read / 1.25x write / 5-min TTL) and shows your switch cadence vs the TTL — a cache-warm pro-only baseline would have billed those tokens at cache-read prices. The output ends with ready-to-fill formulas using the measured token counts — substitute your providers' input prices (per 1M tokens) and read off the saved amount (output tokens, flash-side caching, and capability-mismatch turns are not modeled).

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
