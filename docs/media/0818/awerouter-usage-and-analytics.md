# awerouter: Usage, Skills, and the Analytics That Tell You If It's Working

![awerouter](../../../logo/logo.png)

awerouter is a smart LLM router that sits between your coding agent and the upstream providers. Every request hits the router first. It decides — from structure, not from guessing — whether to send it to a cheap flash model or a capable pro model. The decision is instant, the response streams through untouched, and the routing is invisible to you.

But routing is only half the problem. The other half is knowing whether the routing is any good. How much of your traffic actually goes to flash? Is your `longContextThreshold` too aggressive? Are you saving money or just adding latency? awerouter ships with a usage analytics suite that answers these questions — and a skill system that lets your agent answer them for you.

GitHub: [github.com/mugpeng/awerouter](https://github.com/mugpeng/awerouter)

## The Skill: Let Your Agent Run the Router

awerouter is designed to be operated by your agent. You say what you want; the agent figures out the CLI command. The bridge is the `awerouter` skill — a thin procedural layer that maps natural language to the `awerouter` CLI.

### Installing via aweskill

[aweskill](https://aweskill.webioinfo.top/) is a CLI-first skill package manager for 47+ AI coding agents. Installing the awerouter skill through it takes one command — and the agent can run it.

```bash
npm install -g aweskill
aweskill store init
aweskill install mugpeng/awerouter
aweskill agent add skill awerouter --global --agent claude-code
```

Replace `claude-code` with your agent id: `codex`, `opencode`, `cursor`, `gemini-cli`, `windsurf`, or any of the supported agents. The agent can figure this out by running `aweskill agent supported`.

### Direct Install (No aweskill)

If you don't have Node.js or prefer a direct approach, copy the skill file into your agent's skill directory:

```bash
mkdir -p ~/.claude/skills/awerouter/
curl -fsSL https://raw.githubusercontent.com/mugpeng/awerouter/main/resources/skills/awerouter/SKILL.md -o ~/.claude/skills/awerouter/SKILL.md
```

### What the Skill Can Do

The skill translates natural language into CLI commands:

| You say | The skill runs |
|---|---|
| "List my awerouter profiles." | `awerouter list` |
| "Show me cc-router-1." | `awerouter config show cc-router-1` |
| "Add a GLM provider for openai-chat." | edits `~/.config/awerouter/providers.json` |
| "Change flash destination to glm-4-flash." | edits `routing.json`, verifies with `awerouter config show` |
| "How did yesterday's routing go?" | `awerouter usage stats` |
| "What should my longContextThreshold be?" | `awerouter usage calibrate` |
| "How much am I saving?" | `awerouter usage savings` |

The agent never runs `awerouter serve`. The daemon is yours to start in your own terminal. The skill is for configuration, inspection, and tuning — everything except the proxy itself.

## Usage Analytics: Five Commands to Know

awerouter writes every proxied request to a JSONL log at `~/.local/state/awerouter/requests.jsonl`. Five read commands let you inspect it. Each accepts `--since` (filter by time window) and `--profile` (filter by routing profile).

### `usage stats` — Routing Summary

Shows the big picture: total requests, error rate, fallback count, and per-profile breakdowns by label, agent, destination, provider, and model — with latency percentiles.

```bash
awerouter usage stats --since 7d
```

Output:

```
total_requests : 842
~total_tokens  : 2,410,000
errors         : 3 (0.4%)
fallbacks      : 5 (flash failed -> pro)

profile cc-router-1 [anthropic]  (842 requests, ~1,680,000 flash tokens, 3 errors, 5 fallbacks):
  by_label:
    default                   590 (70%)
    longContext                120 (14%)
    webSearch                   80 (10%)
    background                  30 ( 4%)
    toolSearch                  12 ( 1%)
    image                       10 ( 1%)
  by_destination:
    flash                     680 (81%)  p50 120ms  p95 450ms
    pro                       162 (19%)  p50 380ms  p95 1200ms
```

This is your dashboard. Check it daily.

### `usage calibrate` — Threshold Tuning

Shows the token distribution of your L3 traffic and suggests `longContextThreshold` values with the resulting flash/pro split.

```bash
awerouter usage calibrate --since 7d
```

Output:

```
L3 request-token distribution (542 requests):
  min:     500   p50:   2,000   p75:   4,000
  p90:     8,000   p95:  12,000   p99:  25,000   max:  50,000

if you set longContextThreshold to:
    8,000   → 72% flash, 28% pro
   12,000   → 90% flash, 10% pro
   25,000   → 99% flash,  1% pro

'auto' would set: 12,000  (p95 of 142 L3 requests, last 7d)
```

The `longContextAuto` block controls how `"auto"` mode picks its threshold:

```json
"longContextAuto": {
  "percentile": 75,
  "windowDays": 7,
  "minSamples": 10,
  "fallbackThreshold": 10000
}
```

| Field | Default | What it does |
|---|---|---|
| `percentile` | `75` | Which percentile of the observed token distribution to use as the threshold. `75` means 75% of L3 requests stay on flash. |
| `windowDays` | `7` | How many days of traffic to analyze when the daemon starts. |
| `minSamples` | `10` | Minimum L3 requests in the window required to trust the auto-calibrated value. If the window has fewer samples, `fallbackThreshold` is used instead. |
| `fallbackThreshold` | `10000` | Static fallback when there aren't enough samples or the window is empty. Prevents sudden threshold swings on cold starts. |

This is how you answer "is my threshold too aggressive or too conservative?" Set it to `"auto"` in `routing.json` and awerouter recalibrates from your own traffic at every serve start.

### `usage savings` — Token Economics

Shows how many input tokens were offloaded to flash vs. what a pro-only baseline would have cost, with a cache-sensitivity bracket.

```bash
awerouter usage savings --since 7d
```

Output:

```
requests: 842  (flash 680 / pro 162, 81% flash, fallback 5)

request input tokens:
  flash        1,680,000   avg 2,471/req
  pro            730,000   avg 4,506/req
  total        2,410,000

vs a pro-only setup:
  pro input billed   2,410,000 → 730,000
  offloaded to flash 1,680,000  (70% of input tokens)

cache sensitivity (Anthropic-style: read ~10%, write ~125%, TTL 5 min):
  offload worth 168,000–1,680,000 pro-equivalent input tokens
  (lower = all would have been cache reads; a cache-warm pro-only baseline sits near it)

plug in your input prices (per 1M tokens) to get money saved:
  upper       = (1,680,000 × pro − 1,680,000 × flash) / 1,000,000
  cache-aware = (168,000 × pro − 1,680,000 × flash) / 1,000,000
```

It does not promise exact money — output tokens and cache semantics vary by provider — but the order of magnitude is clear.

### `usage tokens` — What Is Actually Consuming Tokens

Breaks down input tokens by content type: messages, system prompt, tools, tool results, tool calls, thinking.

```bash
awerouter usage tokens --since 7d
```

Output:

```
input tokens by type (842 requests, total 2,410,000  search 120,000  effective 2,350,800):
  messages      1,100,000    46%  avg 1,307/req
  system          680,000    28%  avg 807/req
  tool_results    320,000    13%  avg 380/req  (includes 120,000 search at 30% weight)
  tool_calls      180,000     8%  avg 214/req
  tools           130,000     5%  avg 154/req
```

Use this to find out whether your system prompt is bloated, whether tool results are eating context, or whether file-search results are inflating your token count (they are discounted at 30% before routing).

### `usage log` — Raw Request Entries

Shows the last 20 raw JSONL entries by default, with the key fields per request: timestamp, agent, destination, provider, model, status, latency, token count.

```bash
awerouter usage log --lines 50
awerouter usage log --tokens    # show per-type token breakdown per entry
awerouter usage log --all       # show every entry in the filtered window
```

This is your audit trail. Use it when you want to see exactly what happened on a specific request.

## How It All Connects

The typical workflow looks like this:

1. **Install awerouter** — `pip install awerouter`, then `awerouter init` to create configs.
2. **Install the skill** — via aweskill or direct curl.
3. **Start the daemon** — `awerouter serve` in your own terminal.
4. **Point your client at it** — set `ANTHROPIC_BASE_URL=http://127.0.0.1:20128` (or the matching env var for your client).
5. **Let the agent handle the rest** — add providers, tune thresholds, check usage — all through natural language.

The agent reads `README.ai.md` and follows it. The skill maps your words to commands. The daemon routes every request. The JSONL log records everything. The five `usage` commands let you inspect it.

You can also install awerouter in one prompt by telling your agent:

> "Read https://github.com/mugpeng/awerouter/blob/main/README.ai.md and follow it."

The agent runs `pip install`, sets up the skill, initializes the config, edits `~/.zshrc`, and reports back. You start `awerouter serve` yourself.

## More from mugpeng

awerouter is part of the aweteam ecosystem:

- **[aweskill](https://aweskill.webioinfo.top/)** — CLI-first skill package manager for 47+ AI coding agents
- **[aweswitch](https://github.com/Webioinfo01/aweswitch)** — Agent profile switcher for Claude Code, Codex, and OpenCode; launches sessions pointing at the awerouter daemon
- **[aweshelf](https://github.com/Webioinfo01/aweshelf)** — AI coding session manager with profile-aware restoration
- **[awerouter](https://github.com/mugpeng/awerouter)** — A smart LLM router that automatically directs agent requests to fast, low-cost Flash models or more capable Pro providers using structural signals, balancing cost, latency, and reasoning quality.
- **[awescholar](https://github.com/Webioinfo01/awescholar)** — Automated scientific literature discovery and curation for Awesome lists.

