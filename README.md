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
    <img src="https://img.shields.io/github/stars/wehuman01/awerouter?style=flat-square" alt="Stars">
  </p>
</div>

> Transparent proxy that splits coding-agent traffic across providers by cost and capability. Same-protocol passthrough — no translation. Optional per-profile token saving (RTK compression + ODCP pruning, off by default).

## Quick Start

### 1. Install and use awerouter

If you are working in Claude Code, Codex, Cursor, or another coding agent, tell it:

```text
Read https://github.com/wehuman01/awerouter/blob/main/README.ai.md and follow it to install and configure awerouter.
```

The agent will install the CLI, init the config (a bundled template, or merged into what you already have), configure providers and routing, and install the awerouter skill via [aweskill](https://aweskill.webioinfo.top/) for ongoing routing management. For auth it scans your shell config for API-key variables you already export (`GLM_API_KEY`, `STEPFUN_AUTH_TOKEN`, ...) and references them as `${VAR}` in `providers.json` — it reports variable names only, never values, and only asks for keys you don't already have.

**After setup, you can tell the agent things like:**

> "Add a stepfun flash provider and a pro profile."
> "List my awerouter profiles."
> "Tune longContextThreshold from my usage."
> "Explain my usage savings."

The agent can run read-only commands (`list`, `config show`, `usage stats` / `calibrate` / `savings`) and edit `providers.json` (endpoints/auth) and `routing.json` (strategy) directly — see [step 3](#3-manage-routing-through-natural-language).

<details>
<summary>Manual install and config</summary>

Install from PyPI:

```bash
pip install awerouter
```

Quick Start:

```bash
# 1. Init config (creates ~/.config/awerouter/{providers,routing}.json)
awerouter init                # or pick a bundled combo: awerouter init step-glm / glm-codex / step-glm-mm (see "Common setup templates")
#    already have a config? awerouter init <template> --merge fills in the template's
#    missing providers, profiles, and settings — existing entries are never overwritten

# 2. Add a profile (writes both files, references stay consistent)
awerouter add                 # interactive wizard — run it in your own terminal
#    or edit by hand: providers.json for keys (${ENV_VAR}), routing.json for flash/pro

# 3. Install the awerouter skill for ongoing natural-language management
aweskill install wehuman01/awerouter
aweskill agent add skill awerouter --global --agent <agent-id>   # claude-code, codex, cursor, ...
```

</details>

### 2. Start the router in your terminal — two serving modes

`awerouter serve` is a long-lived daemon — the one thing the agent will not run for you. It has two serving modes, and they compose: run a smart-routing profile for day-to-day coding traffic, and a gateway alongside it when you also want every model callable by name.

#### Smart routing — `awerouter serve run <profile>`

One profile, flash/pro split by structural signals: cheap drafting goes to flash, hard work to pro. Typical combinations (from a real config — a StepFun step_plan key, a GLM coding plan, a Codex subscription):

- flash `stepfun-1,step-router-v1` / pro `glm,glm-5.3` — StepFun's step_plan router soaks up the cheap traffic, the GLM coding plan takes the hard calls (the step-glm combo)
- flash `stepfun-1,step-3.7-flash` / pro `glm,glm-5.3` plus `imageModel: "flash"` and `imageBridge: true` — same pair, but the text-only flagship gains a pair of eyes (step-glm-mm; see [Image Bridge](#image-bridge))
- flash `stepfun-1,step-router-v1` / pro `codex,gpt-5.6-terra` — a ChatGPT subscription rides pro while a keyed plan drafts (step-codex)

```bash
awerouter serve run step-glm       # profile name optional when only one exists
#    -d runs it in the background (survives the terminal; log: ~/.local/state/awerouter/serve-<profile>.log)
#    --install runs it as a resident service: starts at login, survives reboots and crashes
```

The serve banner prints the client line to export:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:20128      # Claude Code
export OPENAI_BASE_URL=http://127.0.0.1:20128/v1      # openai-compatible clients
# aweswitch profile env: ANTHROPIC_MODEL=auto, _HAIKU_=flash, _OPUS_=pro
```

Config edits don't need a restart: serve watches `routing.json` / `providers.json` and hot-reloads changes (a broken file keeps the previous config serving until it parses again — see [Background serving & hot reload](#background-serving--hot-reload)).

**Or launch through [aweswitch](https://github.com/Webioinfo01/aweswitch)** — an aweswitch profile points a client's `BASE_URL` at the daemon, so routing is applied on launch. Run it in your terminal — it starts a new agent session:

```bash
aweswitch oc-awerouter
```

<details>
<summary>Example: an OpenCode profile pointing at awerouter</summary>

Add an aweswitch OpenCode profile pointing at the daemon:

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

With `OPENCODE_MODEL` set to `auto`, awerouter routes each request by structural signals — the upstream provider receives the actual model id from `routing.json` destinations, not `auto`. Claude Code works the same way via an `anthropic` profile (`ANTHROPIC_MODEL=auto`).

</details>

#### Local integrated gateway — `awerouter serve all`

One port serving everything you configured: every routing profile (`<profile>/auto` runs its smart route, `/flash` and `/pro` force a tier), plus every model a provider declares in its `models` list as a fixed `<provider>/<model>` forward that bypasses routing. All your different providers behind one local OpenAI/Anthropic-compatible endpoint — a personal mini-OpenRouter. From the same real config: StepFun's `step-router-v1` and `step-explore`, GLM's `glm-5.3` and `glm-5.3-flash`, Doubao's `Kimi-K2.7-Code` and `Doubao-Seed-Evolving`, a Codex subscription's `gpt-5.6-luna` — all callable through one port; and one provider may appear several times under different keys (stepfun-1/2/3: same models, three keys) to pool quota across accounts:

```bash
awerouter serve all               # one port for every profile and declared model
```

Point a client at the port and pick by model name:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:20128/v1
# then call, e.g.: step-glm/auto   step-deepseek/pro   doubao/Kimi-K2.7-Code   codex/gpt-5.6-luna
```

To make a provider's models directly callable, declare them in `providers.json` (per protocol group):

```json
{
  "openai-chat": {
    "stepfun": {
      "base_url": "https://api.stepfun.com/step_plan/v1",
      "auth": "${STEPFUN_AUTH_TOKEN}",
      "models": ["step-3.7-flash", "step-router-v1"]
    }
  }
}
```

`GET /v1/models` lists every available name; unknown names return a descriptive 400. `serve all` takes only its own `--port` (or scans from 20128), and the optional `defaultProfile` maps bare `auto`/`flash`/`pro` names to one profile — full details in the [gateway note under routing.json](#config).

The agent's remaining boundaries: it also won't run `awerouter add` (interactive wizard), `awerouter config restore` (overwrites config), `awerouter usage clean` (deletes logs), or `awerouter self-update` (upgrades the installation) — those stay in your terminal.

### 3. Manage routing through natural language

Day-to-day routing management goes through your agent — it lists and inspects profiles, edits `providers.json` (endpoints/auth) and `routing.json` (strategy) separately, reads usage, and suggests threshold changes (the full CLI reference is in [Commands](#commands)):

#### List and inspect

You can tell your agent:

```text
List my awerouter profiles and show the routing entry for cc-router-1.
```

<details>
<summary>Equivalent CLI commands</summary>

```bash
awerouter list
awerouter config show cc-router-1   # redacted; the profile's providers + routing entry only
```

</details>

#### Add a provider or edit destinations

You can tell your agent:

```text
Add a GLM provider for the openai-chat group and use it as the pro destination.
```

The agent edits the two config files directly — a provider entry in the `openai-chat` group of `providers.json` (auth as `${GLM_API_KEY}` if you already export it), and the profile's `destinations.pro` in `routing.json` pointing at it. The interactive `awerouter add` wizard remains available in your own terminal.

#### Tune the threshold from real usage

You can tell your agent:

```text
Tune longContextThreshold from my usage.
```

<details>
<summary>Equivalent CLI commands</summary>

```bash
awerouter usage calibrate    # L3 token distribution + suggested thresholds at p90/p95/p99
awerouter usage stats        # per-profile breakdowns, errors, latency percentiles
# then edit routing.json — or set the threshold to "auto" and let each serve start calibrate it
```

</details>

#### See what routing saved

You can tell your agent:

```text
Explain my usage savings.
```

<details>
<summary>Equivalent CLI commands</summary>

```bash
awerouter usage savings      # pro tokens offloaded to flash vs a pro-only baseline, with ready-to-fill price formulas
```

</details>

## Support Tools

awerouter works best alongside two companion tools:

- **[aweskill](https://aweskill.webioinfo.top/)** — CLI skill package manager for AI agents. Installs the awerouter skill so your agent can manage routing in natural language.
- **[aweswitch](https://github.com/Webioinfo01/aweswitch)** — Agent profile switcher. Launches Claude Code, Codex, or OpenCode sessions with a profile that points `BASE_URL` at the awerouter daemon.

aweskill lets the agent **manage** routing by operating skills; aweswitch lets you **launch** sessions through it. Configure awerouter once, then start any agent against it with `aweswitch <profile>`.

## Config

Two files in `~/.config/awerouter/` (override with `AWEROUTER_CONFIG_DIR`):

### providers.json

Endpoints + keys, grouped by wire protocol (redacted in `config show`):

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

In gateway mode, a provider can also declare the models that may be selected directly:

```json
"openai-chat": {
  "stepfun": {
    "base_url": "https://api.stepfun.com/step_plan/v1",
    "auth": "${STEPFUN_AUTH_TOKEN}",
    "models": ["step-3.7-flash", "step-router-v1"]
  }
}
```

`awerouter serve all` then exposes `stepfun/step-3.7-flash` as a fixed forward. It bypasses automatic routing, and undeclared models are rejected. Declare `models` separately in each protocol group; existing configurations do not need changes.

A provider may also declare `"multimodal": true`. Failover queues are its only consumer: an image-bearing request never fails over to a provider that has not declared it — the image guard holds during failover, and undeclared means no (the safe direction).

A provider may also carry a `"pool": "stepfun"` tag. Entries sharing a tag within one protocol group are the same vendor's accounts (same models, different keys): a gateway `provider/<model>` forward that 429s fails over to the **same model** on the next pool member — declaration order wrapping around from the named entry, so each account's overflow flows to the next account. One tag per entry replaces the mutual-backups lists a multi-account setup would otherwise need; untagged entries stay pinned (a named model surfaces its 429, it never swaps horses — pools only swap who serves the model you named). Cooldowns, `→fb:` labels, `fallback_hops`, and the `multimodal` image guard all apply exactly as for routing-profile failover; a member that did not declare the model is simply not a candidate, and serve-start warns when a pool's members declare different model sets.

Three protocols are supported. `base_url` uses each native client's convention — copy it verbatim from your client config; awerouter appends the endpoint path the same way the native client would:

| Protocol id         | `base_url` style | Endpoint |
|---------------------|------------------|----------|
| `anthropic`         | `ANTHROPIC_BASE_URL` (no `/v1`) | `base_url + /v1/messages` |
| `openai-chat`       | `OPENAI_BASE_URL` (includes version segment) | `base_url + /chat/completions` |
| `openai-responses`  | `OPENAI_BASE_URL` (includes version segment) | `base_url + /responses` |

The same provider often uses a different path per protocol — GLM for instance: `https://open.bigmodel.cn/api/anthropic` for Claude-protocol clients but `https://open.bigmodel.cn/api/coding/paas/v4` for chat completions and `https://open.bigmodel.cn/api/v1` for responses. That's why each protocol group carries its own `base_url` — and why a multi-protocol profile lists the provider in each group it serves.

The auth header is **auto-detected from `base_url`**: `anthropic.com` → `x-api-key` (bare token); everyone else → `Authorization` (auto-prefixes `Bearer `). No `auth_header` field needed unless the heuristic is wrong.

### Local models (no auth)

Local model servers don't need a key — just omit `auth` and the request goes upstream with no auth header at all:

```json
{
  "anthropic": {
    "ollama":    { "base_url": "http://127.0.0.1:11434" },
    "anthropic": { "base_url": "https://api.anthropic.com", "auth": "${ANTHROPIC_KEY}" }
  },
  "openai-chat": {
    "ollama": { "base_url": "http://127.0.0.1:11434/v1" }
  }
}
```

Works with any OpenAI-compatible server (Ollama, LM Studio, llama.cpp, vLLM) under `openai-chat`; Ollama ≥ 0.14 also speaks the Anthropic protocol, so it can sit in the `anthropic` group next to Claude Code. Note the path convention: `openai-chat` base_urls carry the `/v1` segment, `anthropic` ones don't — same as their cloud counterparts above.

Local and cloud mix freely in one profile — cheap drafting on local, hard calls on cloud:

```json
"destinations": {
  "flash": "ollama,qwen3-coder:30b",
  "pro":   "anthropic,claude-opus-5"
}
```

End to end with Ollama, flash=local / pro=cloud:

```bash
ollama pull qwen3-coder:30b      # local server listens on 127.0.0.1:11434 by default
awerouter serve run cc-router-1
export ANTHROPIC_BASE_URL=http://127.0.0.1:20128
```

Default ports for the common local servers (all `openai-chat`, all no-auth): LM Studio `http://127.0.0.1:1234/v1`, llama.cpp `http://127.0.0.1:8080/v1`, vLLM `http://127.0.0.1:8000/v1`.

If the local server is down, the flash→pro fallback kicks in on connection errors and requests transparently go to the cloud — local-first with a cloud safety net, no extra config.

Guard against a forgotten key: a provider with no `auth` whose `base_url` is **not** on localhost gets a warning at serve start (`awerouter add` asks for confirmation in the same case). LAN servers without auth are legitimate — the warning is informational, not fatal.

### Codex account (subscription login)

`"auth": "codex"` in the `openai-responses` group rides the local Codex CLI login (`~/.codex/auth.json`) instead of an API key — the subscription's own models mix into flash/pro routing next to key-based providers:

```json
"openai-responses": {
  "stepfun": { "base_url": "https://api.stepfun.com/step_plan/v1", "auth": "${STEPFUN_AUTH_TOKEN}" },
  "codex":   { "base_url": "https://chatgpt.com/backend-api/codex", "auth": "codex" }
}
```

```json
"destinations": {
  "flash": "stepfun,step-router-v1",
  "pro":   "codex,gpt-5.6-luna"
}
```

Any Responses-speaking client points at awerouter with a dummy key; the real auth headers are injected per request, and a few backend quirks are normalized: `store` forced to `false`, `max_output_tokens` dropped, non-streaming requests run upstream as SSE and come back buffered into a single JSON reply.

How it behaves:

- **Read-only login, no refresh.** Refreshing here would invalidate the CLI's login, so refresh stays with the CLI; awerouter re-reads `auth.json` per request and once more on a 401. The access token lives ~10 days — keep using `codex` (or awewarm).
- **Expired login.** A 401 that survives the re-read: flash falls back to a keyed pro (`401-retry` marker in `usage log`); the 401 surfaces to the client only when pro rides the same login.
- **Proxy-aware.** Honors `https_proxy`/`all_proxy` — chatgpt.com often needs the proxy; every other provider always connects directly.
- **Model names drift.** The current name is whatever the CLI uses (`gpt-5.6-luna` at the time of writing); a rename is a one-line routing.json edit.
- **No login yet?** Requests return a 503 with a `run: codex login` hint, plus a serve-start warning — deliberately no fallback: a missing login is a config error best fixed immediately, and silently serving it from a paid pro would hide both the error and the bill.

The sentinel only loads in the `openai-responses` group — the ChatGPT Codex backend speaks the Responses protocol.

### Claude account (subscription login)

`"auth": "claude"` in the `anthropic` group routes through a Claude Pro/Max subscription login that awerouter itself owns (`awerouter config login claude` runs the same authorization flow the CLI uses; tokens live in `~/.config/awerouter/claude-auth.json`, mode 0600) — the local Claude Code CLI login is never used. The subscription's own models mix into flash/pro routing next to key-based providers:

```json
"anthropic": {
  "stepfun": { "base_url": "https://api.stepfun.com/step_plan", "auth": "${STEPFUN_AUTH_TOKEN}" },
  "claude":  { "base_url": "https://api.anthropic.com", "auth": "claude" }
}
```

```json
"destinations": {
  "flash": "stepfun,step-router-v1",
  "pro":   "claude,claude-opus-4-5"
}
```

```bash
awerouter config login claude    # opens the browser; paste the code shown on the callback page
awerouter config logout claude   # removes the stored login
```

Point any Anthropic-protocol client at awerouter with a dummy key (Claude Code via `ANTHROPIC_BASE_URL` — the CLI's own login is never touched); auth headers and the OAuth flag are injected per request, with no body rewriting. How it behaves:

- **awerouter owns refresh.** The inverse of codex: this login belongs to awerouter, so it renews automatically with the refresh token, persisting the new one before the request proceeds.
- **Dead login.** A 401 forces one refresh and retry; a 401 that survives means flash falls back to a keyed pro. A missing login is a 503 with a `run: awerouter config login claude` hint — same deliberate no-fallback as codex.
- **Proxy-aware**, like codex — api.anthropic.com often needs the proxy.
- **ToS caveat.** The endpoints and header set are the reverse-engineered public contract shared by community clients — they can break without notice; and per Anthropic's 2026 policy, third-party use of subscription OAuth tokens is ToS-restricted. This rides your own subscription, at your own risk.

The sentinel only loads in the `anthropic` group — the subscription backend speaks the Messages protocol.

### Common setup templates

`awerouter init` takes an optional bundled template name and generates a matching `providers.json` + `routing.json` pair in one shot (no name: `default`). Three ready-made combos ship out of the box; hand-copying any snippet below into your own config works just as well. Keys are `${ENV_VAR}` placeholders — a missing env var dies at startup with a clear message. To fold a template into an existing config instead, append `--merge`: it fills in only what is missing and never touches what you already have.

**step-glm** — key-only Chinese two-tier combo: flash on StepFun step_plan, pro on the GLM coding plan. For agents speaking the openai-chat protocol:

```json
"openai-chat": {
  "stepfun": { "base_url": "https://api.stepfun.com/step_plan/v1",        "auth": "${STEPFUN_AUTH_TOKEN}" },
  "glm":     { "base_url": "https://open.bigmodel.cn/api/coding/paas/v4", "auth": "${GLM_API_KEY}" }
}
```

```json
"destinations": {
  "flash": "stepfun,step-3.7-flash",
  "pro":   "glm,glm-5.3"
}
```

```bash
awerouter init step-glm      # needs STEPFUN_AUTH_TOKEN and GLM_API_KEY
```

**glm-codex** — the GLM coding plan soaks up flash traffic, a ChatGPT subscription (`"auth": "codex"`, see above) handles pro:

```json
"openai-responses": {
  "glm":   { "base_url": "https://open.bigmodel.cn/api/v1",       "auth": "${GLM_API_KEY}" },
  "codex": { "base_url": "https://chatgpt.com/backend-api/codex", "auth": "codex" }
}
```

```json
"destinations": {
  "flash": "glm,glm-5.3-flash",
  "pro":   "codex,gpt-5.6-terra"
}
```

```bash
awerouter init glm-codex     # needs GLM_API_KEY, plus a ChatGPT subscription via codex login
```

**step-glm-mm** — a flagship with a pair of eyes: glm-5.3 does all the text work, while image-bearing requests go to the multimodal step-3.7-flash, which looks and reports back; with the template's `imageBridge` on, the follow-up text turns return to glm-5.3 carrying flash's transcriptions of those images. Dual-protocol (`["anthropic", "openai-chat"]`): one port takes Claude Code and openai-chat agents alike — each protocol rides its own provider entry:

```json
"anthropic": {
  "stepfun": { "base_url": "https://api.stepfun.com/step_plan",      "auth": "${STEPFUN_AUTH_TOKEN}" },
  "glm":     { "base_url": "https://open.bigmodel.cn/api/anthropic", "auth": "${GLM_API_KEY}" }
},
"openai-chat": {
  "stepfun": { "base_url": "https://api.stepfun.com/step_plan/v1",        "auth": "${STEPFUN_AUTH_TOKEN}" },
  "glm":     { "base_url": "https://open.bigmodel.cn/api/coding/paas/v4", "auth": "${GLM_API_KEY}" }
}
```

```json
"cc-router-1": {
  "protocol": ["anthropic", "openai-chat"],
  "destinations": {
    "flash": "stepfun,step-3.7-flash",
    "pro":   "glm,glm-5.3"
  }
}
```

```json
"settings": {
  "imageModel": "flash",
  "defaultModel": "pro",
  "imageBridge": true
}
```

```bash
awerouter init step-glm-mm        # needs STEPFUN_AUTH_TOKEN and GLM_API_KEY
# Claude Code:    export ANTHROPIC_BASE_URL=http://127.0.0.1:20128        (no /v1)
# openai-chat:    export OPENAI_BASE_URL=http://127.0.0.1:20128/v1       (with /v1)
```

> **Don't need smart routing — just want your flagship to see images?** Those two settings are the whole trick: `imageModel: "flash"` sends image-bearing requests to the multimodal model, `defaultModel: "pro"` sends everything else to the flagship. Copy the two lines into any existing config; you don't need the full template. GLM-only shop? Swap flash for GLM's own `glm-5.3-flash`. Add `"imageBridge": true` to let follow-up text turns go back to the flagship carrying flash's transcriptions of the earlier images (see "Image bridge").

Backend model names drift (see the Codex account section) — renaming is a one-line change in routing.json.

### routing.json

Strategy, no secrets (safe to commit):

```json
{
  "settings": {
    "backgroundModel": "flash",
    "thinkModel": "pro",
    "toolRouting": {
      "webSearch": "pro",
      "edit": "pro"
    },
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
    },
    "backups": {
      "flash": ["glm,glm-4.7-flash", "anthropic,claude-opus-5"],
      "pro":   ["glm,glm-5.3"]
    }
  },
  "cc-pro-first": {
    "protocol": "anthropic",
    "longContextThreshold": 8000,
    "destinations": {
      "flash": "stepfun,step-3.7-flash",
      "pro":   "anthropic,claude-opus-5"
    },
    "defaultModel": "pro"
  }
}
```

`settings` is optional (defaults: `flash`/`pro`). It maps the model ids CC sends for the background (Haiku) and think (Opus) tiers; every tool-keyed routing rule (L1 `webSearch` included) lives in `settings.toolRouting` — the legacy top-level `webSearchModel` still works as a fallback. `imageModel` re-aims the image guard (default `pro`; `flash` when pro is a non-multimodal flagship — see the step-glm-mm template) and `defaultModel` flips the cost-first fall-through (default `flash`; `pro` for pro-first profiles). The main loop uses `auto` — routed by difficulty by L3. Set these in your aweswitch profile: `ANTHROPIC_DEFAULT_HAIKU_MODEL=flash`, `ANTHROPIC_MODEL=auto`, `ANTHROPIC_DEFAULT_OPUS_MODEL=pro`.

Every settings key can also be written **directly in a profile body**, flat next to `protocol`/`destinations` (`cc-pro-first` above flips only `defaultModel`). It then applies to that profile alone; missing keys — and, inside `toolRouting`/`longContextAuto`, missing fields — inherit from the global `settings` block, so each profile re-tunes exactly what differs. `config show <profile>` prints the profile's effective values with its override keys in the entry, and the serve banner lists them on an `overrides -> ...` line.

Settings keys, their defaults, and what each one steers (all of them settable globally and per profile):

| Key | Values | Default | Controls |
|-----|--------|---------|----------|
| `backgroundModel` | model id (e.g. `flash`, `c1/flash`) | `flash` | L2: requests carrying this tier label route to flash |
| `thinkModel` | model id | `pro` | L2: requests carrying this tier label route to pro |
| `webSearchModel` | `flash` / `pro` | `pro` | L1: destination for requests declaring `web_search` (legacy — `toolRouting.webSearch` wins when set) |
| `imageModel` | `flash` / `pro` | `pro` | L1: destination for image-bearing requests (the capability guard — outranks tiers and length) |
| `defaultModel` | `flash` / `pro` | `flash` | fall-through destination when no layer matches |
| `searchResultDiscount` | number 0–1 | `0.3` | L3: weight of file-search (Grep/Glob/LS) result tokens; `1` = off |
| `imageBridge` | `true` / `false` | `false` | flash transcribes history images to text, so a text-only pro keeps the session (opt-in; see "Image bridge") |
| `toolRouting.webSearch` | `flash` / `pro` / `null` | `null` | overrides `webSearchModel` for the web_search guard |
| `toolRouting.edit` | `flash` / `pro` / `null` | `pro` | L4: destination for the turn after code changed; `null` disables the checkpoint |
| `longContextAuto.percentile` | 1–99 | `95` | which percentile of the L3 distribution becomes the `"auto"` threshold |
| `longContextAuto.windowDays` | ≥ 1 | `7` | trailing window for auto calibration |
| `longContextAuto.minSamples` | ≥ 1 | `50` | below this many L3 samples, the fallback applies |
| `longContextAuto.fallbackThreshold` | ≥ 0 | `8000` | threshold until enough samples exist (backs `"auto"` before serve resolves it) |

`backgroundModel`/`thinkModel` take free-form model ids (whatever the client actually sends for those tiers), not `flash`/`pro`. Unknown keys — in `settings` or in a profile body — die at load naming the offender, so a typo never silently inherits the global value.

`longContextThreshold` is an integer, or `"auto"` to calibrate it from this profile's own traffic: at every `serve` start, awerouter takes the `percentile` of the profile's L3 effective-token distribution over the trailing `windowDays` as the threshold. With fewer than `minSamples` L3 requests in the window (fresh profile, quiet week) the `fallbackThreshold` applies instead. All four knobs live in `settings.longContextAuto` and are optional — the banner always prints what was picked and why. Note the percentile sets the flash/pro *split*, not flash's capability ceiling: if your flash model degrades on very long contexts, keep a manual threshold.

> **Failover queues (`backups`, quota-aware):** each destination key may declare an ordered failover queue — same `"provider,model"` strings as `destinations` (a bare string is a one-element queue). When the primary answers 429/408/5xx or a network error *before the first streamed byte*, the request walks its queue in order; smart routing itself never changes (the tier is the request's property — failover only swaps who serves it). Zero config, each tier carries one implicit cross-tier hop — flash→pro and pro→flash, answer over error, loudly stamped into the label. An explicit list **replaces** the implicit hop: what you wrote is exactly what runs (want pro at the end of the flash queue, list it). A 429 — or any response carrying `Retry-After` — puts that candidate on an in-process cooldown (Retry-After honored when parseable, else 30s; capped at 60s), so a dead quota window sticks traffic on the backup instead of taxing every request with a doomed first hit; it is advisory, and a restart clears it. An exhausted queue passes the last upstream response through untouched; a network-error 502 names every candidate tried. Every hop is stamped into the label (`background→fb:glm,glm-4.7-flash`) and counted as `fallback_hops` in the usage log. Image-bearing requests filter their queue on the provider's declared `multimodal` flag (see providers.json) — the image guard holds during failover too — and `provider/<model>` direct forwards stay pinned unless the provider pools its accounts (the `pool` tag in providers.json): a named model that 429s surfaces the 429, it never swaps horses.

Keys reference `${ENV_VAR}` syntax. Missing env vars die with a clear message at startup.

> **Profile-based routing:** `routing.json` groups configs under profile ids (like aweswitch). `awerouter serve run <profile>` starts one; with a single profile it auto-selects. `protocol` maps the profile to a providers.json group and decides which endpoint it serves — the serve banner prints the matching client env (`ANTHROPIC_BASE_URL` for Claude Code, `OPENAI_BASE_URL` / Codex `wire_api` for the openai protocols). It accepts a single id or a list: `"protocol": ["anthropic", "openai-chat"]` serves both wire protocols on one port — clients pick by endpoint path (`/v1/messages` vs `/v1/chat/completions`), each protocol forwards through its own provider group, and every destination provider must exist in each served group (per-protocol `base_url`s). Note: openai clients are single-model, so L2 tier labels effectively never fire for them — openai traffic routes by L1 + L3 with a flash default.

> **Gateway mode (many profiles, one port):** `awerouter serve all` serves **every** routing.json profile on one port. Instead of configuring a separate OpenCode/SDK provider and port for each combination, the request's model name selects it: `<profile>/auto` runs the full L1–L4 smart route; `<profile>/flash` and `<profile>/pro` force a tier — for example `step-glm/auto` or `step-deepseek/pro`. Providers may also expose declared `<provider>/<model>` fixed forwards, such as `stepfun/step-3.7-flash`; these bypass automatic routing. `GET /v1/models` lists all names; endpoint path still selects the wire protocol among the ones a profile serves. An unknown profile, bad tier, or protocol mismatch returns a descriptive 400 rather than silently falling into another combination. Gateway mode has no profile-specific ports: it takes only its own `--port`, or scans from 20128.
>
> ```json
> {
>   "defaultProfile": "step-glm",
>   "settings": { "...": "..." },
>   "step-glm": { "...": "..." },
>   "step-deepseek": { "...": "..." }
> }
> ```
>
> The optional top-level `defaultProfile` maps bare `auto` / `flash` / `pro` names to one profile, so existing Claude Code three-tier environment variables keep working. With multiple profiles and no `defaultProfile`, bare names fail clearly and callers must use `<profile>/...`; with just one profile, it naturally becomes the default. `defaultProfile` affects gateway mode only and cannot go inside a profile; it and all profiles hot-reload. The existing single-profile, multi-port `serve run <profile>` workflow remains intact and can run alongside the gateway.

> **Ports:** single-profile mode's optional `port` field pins a profile's listen port (`awerouter list` shows it); precedence: `--port` flag > profile `port` > 20128 default. An explicitly chosen port that is already in use fails loudly — clients hardcode it, it must not silently move. Without one, serve takes the first free port scanning up from 20128: the first instance gets 20128, the next 20129, and so on — the assignment follows start order, not the profile. For the one-instance-at-a-time swap workflow, leave profiles portless and point clients at 20128. Gateway mode ignores profile `port` fields and uses only its own `--port` or the default.

## How It Routes

First-match-wins pipeline, evaluated per request:

| Layer | Signal | Decision |
|-------|--------|----------|
| L1 Capability | `web_search` tool in body; image content present | `toolRouting.webSearch` (default **pro**; legacy `webSearchModel` still works); `settings.imageModel` (default **pro**; with `imageBridge` on, only *fresh* images route here — bridged history falls through the normal pipeline) |
| L2 Tier label | `model == c1/flash` or `c1/think` | flash / pro respectively |
| L3 Difficulty | token count (all request content) > threshold | **pro**; else fall through |
| L4 Edit checkpoint | trailing tool batch changed code (`edit`/`write`/`apply_patch`/...) | `toolRouting.edit` (default **pro**, `null` disables) |

CC's `/model` picker sets the tier model id (c1/flash / c1/pro / c1/think). awerouter reads it and routes accordingly — no keyword parsing, no LLM classifier. The image guard is a capability rule, not a difficulty guess: it sits above tier labels and long context, so with `imageModel: flash` an image-bearing request reaches the multimodal model no matter what tier it carries or how long it is (a model that cannot see images must never get them). Everything that matches no layer falls through to `settings.defaultModel` (default flash — cost-first).

L4 is a consequence checkpoint, not a difficulty guess: the turn right *after* code changed is the review turn (verify, continue, report), so it goes to pro — flash drafts, pro reviews. The signal is the trailing parallel tool batch; any edit-class call in it marks the batch (`Edit`/`Write`/`NotebookEdit`/`apply_patch` etc., case-insensitive; shell-wrapped calls classified by command text). L4 sits below L3 on purpose: sessions above `longContextThreshold` stay pro, keeping the long-context crossing one-way flash→pro.

All tool-keyed rules live in one block — `settings.toolRouting` (`webSearch`/`edit`) — and the serve banner prints the active mapping on one `tool -> ...` line.

Failover queues sit under the pipeline, not in it: `resolve()` picks the tier, then that tier's queue — `[primary, backups...]`, or one implicit cross-tier hop (flash→pro, pro→flash) under zero config — decides who serves the request when the primary dies mid-attempt (429/quota, 5xx, network error; pre-stream only, since SSE cannot be undone). Routing decisions never re-run on failover; every hop is visible in the label. See "Failover queues" under routing.json.

## Image Bridge

The canonical combination is the step-glm-mm template: **glm-5.3 on the GLM coding plan is a strong text-only flagship — no eyes; StepFun's step-3.7-flash can see.** Put both in one profile — `destinations.pro = "glm,glm-5.3"`, `destinations.flash = "stepfun,step-3.7-flash"`, plus `imageModel: "flash"`, `defaultModel: "pro"`, `imageBridge: true` — and glm-5.3 does all the text work while step-3.7-flash supplies the eyes: image-bearing requests go to the multimodal flash (the L1 capability guard), and follow-up text turns return to glm-5.3 carrying flash's transcriptions of those images. One port, dual protocol — a flagship with a pair of eyes.

`settings.imageBridge: true` (opt-in; the step-glm-mm template turns it on) gives a text-only pro model a second-hand pair of eyes. When a request carries images only in *history* — the final message is text — awerouter first has the multimodal flash destination (`imageModel`) transcribe each distinct image once, then replaces the image blocks with the transcription text before routing, so the request keeps routing normally — typically landing on `defaultModel` (pro) — instead of the session being pinned to flash forever. A fresh upload this turn still routes to flash natively, and `/v1/messages/count_tokens` sees the same rewritten body, so estimates match what is sent. Transcriptions are cached by image content for the process lifetime (a restart re-transcribes each image once); if any caption call fails, the request keeps its original images and the L1 image guard routes it as before. Codex subscription logins skip the bridge (their SSE-only backend cannot serve a non-streaming caption call).

`imageBridge` is a settings key like any other, so it can also live in a single profile's body — the right place when only that profile has a multimodal `imageModel` (a global switch makes every profile transcribe via its own `imageModel`; a text-only one fails every caption call and falls back, one wasted upstream call per attempt). Each distinct image costs one extra flash call (caption capped at 2048 output tokens); the first bridged turn pays its latency, later ones hit the cache. The serve banner prints `image bridge -> on (...)` naming the transcribing destination.

## Token Saver (RTK + ODCP)

> **⚠️ Experimental — that's why it's off by default.** Both layers are lossy. RTK compression: long file reads keep head + tail plus a skeleton of signature lines (the marker names the offset to re-read the middle), grep keeps 10 matches per file, diffs are line-capped; format detection is heuristic and can misfire on unusual content. ODCP pruning removes content outright: a deduplicated older output is gone, not truncated. The model usually notices and re-reads, costing an extra turn. If an agent starts behaving oddly (re-reading the same files, missing detail), turn the layer off or send `X-Awerouter-Token-Saver: off` for that session. Check real savings with `awerouter usage log`.

Coding agents resubmit the whole conversation every turn, and most of it is tool output — git diffs, grep hits, directory listings, build logs. A profile can opt into two complementary token savers, applied before routing and forwarding: **RTK** rewrites each tool output's text in place, and **ODCP** drops whole superseded content ([RTK](#rtk-compression), [ODCP](#odcp-pruning-dedup--purgeerrors)):

```json
"cc-router-1": {
  "protocol": "anthropic",
  "longContextThreshold": 8000,
  "rtk": true,
  "destinations": { "flash": "stepfun,step-3.7-flash", "pro": "anthropic,claude-opus-5" }
}
```

### RTK compression

- **What it touches:** `tool_result` / tool-message content only — never user prompts or model replies. Rule-based filters (git diff/status/log, grep, find, tree, ls, build output, …) auto-detect the format and compress it; unrecognized content, anything under 500 chars, and error results (`is_error`) pass through untouched.
- **Fail-open:** any failure (exception, filter error) leaves the body as-is — never a broken request. Note this guards against crashes, not heuristic misjudgment (see the warning above).
- **Deterministic:** the same history compresses to the same bytes every turn, so provider prompt-cache prefixes survive.
- **Per-request escape hatch:** send `X-Awerouter-Token-Saver: off` to forward one request uncompressed (e.g. debugging an agent that needs full diff/log detail).
- Compression runs before routing, and `/v1/messages/count_tokens` is compressed too, so L3 decisions and usage logs match what is actually billed. After enabling RTK, re-run `usage calibrate` — a threshold tuned on uncompressed traffic over-triggers pro (`"auto"` self-corrects after its window).

Compression is inspired by [rtk](https://github.com/rtk-ai/rtk) (Apache 2.0) and [9router](https://github.com/decolua/9router)'s JS port (MIT); this implementation is a from-scratch Python rewrite. The request log records the estimated saved tokens per request.

### ODCP pruning (dedup + purgeErrors)

RTK shrinks each tool output's size; ODCP drops content that no longer earns its tokens. One pairing pass over the history applies two rules:

- **Dedup** — repeated identical tool calls (same tool, same normalized arguments) keep only the newest output; older ones become a one-line placeholder. Re-reading a file or re-running a command costs one line instead of N full outputs.
- **purgeErrors** — once a failed tool call is `purgeErrors.turns` user messages old (default 4), its input strings become a placeholder; the error text itself stays, so the model still sees what failed. Anthropic only — the other protocols carry no error mark on the wire.

```json
"cc-router-1": {
  "protocol": "anthropic",
  "longContextThreshold": 8000,
  "odcp": true,
  "destinations": { "flash": "stepfun,step-3.7-flash", "pro": "anthropic,claude-opus-5" }
}
```

`"odcp": true` enables both strategies with defaults; an object tunes them — `"odcp": {"dedup": false, "purgeErrors": {"turns": 2}}`. The guardrails:

- Editors (Edit/Write/apply_patch/…) and task/planning tools are never rewritten; replacements that wouldn't shrink the body are skipped; the trailing turn is always untouched (dedup keeps the newest, purge requires age).
- Same fail-open contract and `X-Awerouter-Token-Saver: off` escape hatch as RTK. ODCP runs **before** RTK: dropped outputs never reach the compressor, and what remains gets compressed as usual.
- Unlike RTK, pruning is not prompt-cache-stable across turns by design: a new duplicate rewrites an earlier message (was verbatim, becomes a placeholder) and an error crossing the age threshold rewrites once. Each such event trades a one-time cache miss for the tokens saved.

Behavior follows [Opencode-DCP](https://github.com/Opencode-DCP/opencode-dynamic-context-pruning)'s public documentation (AGPL-3.0); this is an independent implementation for awerouter's wire protocols, not a translation of its source. Savings are logged per request (`odcp_saved`) and reported by `awerouter usage` next to rtk's.

## Background serving & hot reload

`awerouter serve run <profile> -d` runs one profile detached — it keeps serving after the terminal closes, logging to `~/.local/state/awerouter/serve-<profile>.log` (append; one file per profile). `awerouter serve all -d` does the same for the gateway (log: `serve-gateway.log`; stop it with `awerouter serve stop gateway`). Both commands wait for the daemon to bind and print its pid, port, and log path; an already-running target is noted (another instance still starts).

`--install` (instead of or alongside `-d`) goes one step further and runs the daemon as a **resident service** — a launchd agent on macOS (`~/Library/LaunchAgents/com.awerouter.serve.<profile>.plist`) or a systemd user unit on Linux (`~/.config/systemd/user/awerouter-<profile>.service`). It starts at login, so a reboot needs no re-run, and relaunches after a crash. The service replaces any running instance of the same profile first. Since the service manager starts the daemon without your shell environment, install bakes the `${VAR}` values your providers reference (plus `AWEROUTER_*` overrides and proxy variables) into the service file — mode 0600, secrets included — and dies up front if a referenced variable the target needs has no value in the current shell; after changing those variables, re-run `--install` to refresh them (re-installing is the update path for host/port/env too).

Every serve instance — foreground or background — registers itself under `~/.local/state/awerouter/run/` at bind time, so one command sees them all:

```bash
awerouter serve status           # profile, fg/bg/svc, pid, host:port, protocol, uptime
awerouter serve stop [PROFILE]   # SIGTERM all instances, or one profile's (graceful shutdown)
awerouter serve restart [PROFILE]  # apply changed env/secrets: re-install / re-spawn from this shell
```

Resident instances show as `svc:launchd` / `svc:systemd`. `serve stop` stops them through the service manager (a plain SIGTERM would be instantly undone by the restart policy) — they return at the next login; `awerouter serve stop [PROFILE] --purge` also removes the service file so they never start again. An installed-but-stopped service is listed by `serve status`. Registration files are keyed by pid; entries whose process no longer exists are pruned automatically, and `serve stop` refuses to signal a pid whose command line no longer looks like awerouter (a reused pid after an unclean kill). `-d`/`--install`/`serve stop`/`serve restart` are POSIX-only.

`serve restart` is the way to apply a changed environment variable or secret the daemon was started with: a resident service is re-installed from the current shell (same command line, port and host as installed, env baked fresh — this also starts an installed-but-stopped service), a plain background instance is stopped and re-spawned from the current shell on the same port, and foreground instances are skipped (they belong to their own terminal). Config-file edits never need it — see hot reload below.

Serve also watches `routing.json` and `providers.json` (1s mtime poll) and hot-reloads changes: destinations, thresholds, tool routing, settings overrides, provider entries — even switching the profile's providers — apply to the next request without a restart. A file that fails to load (mid-save partial write, broken JSON) is announced once and the previous config keeps serving until the file parses again; the one thing a reload cannot do is rebind the listen port — change the `port` field and serve prints a restart hint instead.

## Commands

```bash
awerouter init [TEMPLATE]             # create config from a bundled template (default / step-glm / glm-codex / step-glm-mm); --merge fills it into an existing config
awerouter add                         # interactively add a profile (pick category and providers)
awerouter list                        # list profiles (name, protocol, port, flash, pro, threshold)
awerouter serve run [PROFILE] [--port N] [--host 127.0.0.1] [-d] [--install]  # one profile; port: --port > profile 'port' > 20128
awerouter serve all [--port N] [--host 127.0.0.1] [-d] [--install]            # gateway: every profile on one port, model = <profile>/auto|flash|pro
awerouter serve status                # running serve instances (foreground + background + resident)
awerouter serve stop [PROFILE] [--purge]  # stop all running instances, or one profile's; --purge also removes the resident service
awerouter <PROFILE>                   # shorthand for serve run PROFILE (also takes -d)
awerouter self-update [--check]        # upgrade to the latest PyPI release (--check: versions only)
awerouter config path                 # print both config file paths
awerouter config show [PROFILE]       # redacted config; PROFILE = its providers + entry only
awerouter config edit [providers|routing]  # open one file in $EDITOR (backs up to .bak first)
awerouter config login [claude|codex]      # log in a subscription account (claude: browser PKCE)
awerouter config logout [claude|codex]     # remove a stored subscription login
awerouter config restore [providers|routing]  # restore a config file from its .bak backup
awerouter usage stats [--since ..] [--profile ..]
awerouter usage clean                 # delete saved request logs (asks to confirm)
awerouter usage log [--lines 20] [--all] [--tokens] [--since ..] [--profile ..]
awerouter usage tokens [--since ..] [--profile ..]
awerouter usage calibrate [--since ..] [--profile ..]
awerouter usage savings [--since ..] [--profile ..]
```

All `usage` subcommands read the same request log. `log`, `stats`, `tokens`, `calibrate`, and `savings` take `--since` (`today`, `yesterday`, `7d`, or `YYYY-MM-DD`, local time) and `--profile` directly — e.g. `awerouter usage stats --since today --profile cc-1`; `clean` deletes everything and takes no window options.

`usage stats` aggregates the log per profile (with its wire protocol): label/agent/destination/provider/model breakdowns with percentages, error and fallback counts, latency percentiles (first byte and total) per destination/provider/model, and estimated request tokens (all request content: messages, system prompt, tools, tool I/O). `usage clean` deletes the saved logs (`requests.jsonl` + rotated backup) after a confirmation prompt. `usage log` shows entries verbatim — the last 20 by default, or every entry with `--all`; requests that went through a codex login re-read carry a `401-retry` marker; each line includes the protocol served and the calling agent, detected from the client's `User-Agent` header (`claude-cli/...` → `claude-code`, `codex_cli_rs/...` → `codex`, `opencode/...` → `opencode`). `--tokens` swaps the status/latency/model-in columns for the per-type token breakdown of each request (`msg/sys/tools/results/calls/think`); entries logged before per-type counting show only the total.

`usage tokens` aggregates those per-type breakdowns: input-token totals and share by content type (messages, system prompt, tool definitions, tool results, tool-call arguments, thinking) — useful for seeing how much of a request's tokens are environment constants (system prompt + tool definitions) versus conversation.

`config edit` and the `add` wizard snapshot the target file to `<name>.json.bak` before every write; `awerouter config restore [providers|routing]` copies a backup back (with confirmation, then validates the restored config). `config path` prints the two config file paths; `config show [PROFILE]` shows the redacted full config, or just one profile's providers and routing entry. The pre-move top-level spellings (`awerouter login` / `logout` / `restore`) keep working as hidden aliases.

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
git clone https://github.com/wehuman01/awerouter
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

## Awesome Ecosystem

awerouter is part of a growing family of "awesome" tools — CLI-first, local-first, and operable by AI agents.

### CLI Tools

- **[aweskill](https://aweskill.webioinfo.top/)** — CLI-first skill package manager supporting 47+ AI coding agents.
- **[aweswitch](https://github.com/Webioinfo01/aweswitch)** — Agent profile switcher for Claude Code, Codex, and OpenCode.
- **[awerouter](https://github.com/wehuman01/awerouter)** — Smart router that splits requests between Flash and Pro models using structural signals, cutting unnecessary model spend.
- **[aweshelf](https://github.com/Webioinfo01/aweshelf)** — Bookmark, categorize, and restore AI coding sessions; pairs with aweswitch to save profiles and launch with one command.
- **[aweshare](https://github.com/wehuman01/aweshare)** — Share local Ollama/vLLM backends, domestic coding plans, or authorized OpenAI/Anthropic subscriptions through a self-hosted hub — a sharing economy for tokens.
- **[awewarm](https://github.com/wehuman01/awewarm)** — Subscription window warmer that keeps AI coding-plan windows active, for local setups and through a remote hub server.
- **[awescholar](https://github.com/Webioinfo01/awescholar)** — AI-agent-operable scientific literature discovery and curation.

### Desktop Apps

- **[awedot](https://awedot.wehuman.top/)** — A floating orb at your screen edge keeps track of the current AI session: bookmark it in one click, resume anytime, and pair with aweswitch to pin the agent's config (e.g., relaunch with the GLM model).

### Project Collections

- **[Awesome AI Meets Biology](https://github.com/Webioinfo01/Awesome-AI-Meets-Biology)** — A curated survey of AI applications in biology, bioinformatics, and biomedical research. Powered by awescholar.
- **[Awesome AI Virtual Tumor](https://github.com/Webioinfo01/Awesome-AI-Virtual-Tumor)** — A curated collection of state-of-the-art AI systems for virtual tumor modeling and simulation: static models, dynamic models, agents, benchmarks, and reviews.
