# awerouter: I Let My Agent Set Up a Traffic Cop

![awerouter](../../logo/logo.png)

I told my agent: *"Read https://github.com/mugpeng/awerouter/blob/main/README.ai.md and follow it."* Then I went to get a coffee.

When I came back, awerouter was installed, the skill was registered, two config files sat in `~/.config/awerouter/`, and the env vars were in `~/.zshrc`. It had read the template configs, set up three providers — StepFun for cheap flash work, Anthropic for pro, and an OpenAI-compatible one for Codex sessions — and wired them into a routing profile.

Then it said: *"Run `awerouter serve` in your terminal. I will not start the daemon for you."*

That is the new shape of installing an agent tool. The install is a task. The agent does tasks. So I gave the task to the agent.

GitHub: [github.com/mugpeng/awerouter](https://github.com/mugpeng/awerouter)

## The Install: A README the Agent Reads

Most agent tools ship a `README.md` for humans and a separate `README.ai.md` for agents. The split is honest: humans want a marketing story, agents want a procedure. awerouter leans into this.

The `README.ai.md` is a seven-step install contract written for the agent, not the user:

1. `pip install awerouter` and verify with `awerouter --version`
2. Install the `awerouter` skill via [aweskill](https://aweskill.webioinfo.top/) (Option A) or direct curl of `SKILL.md` (Option B)
3. `awerouter init` to create `~/.config/awerouter/providers.json` and `routing.json`
4. Read the existing config, add providers under `anthropic`, `openai-chat`, or `openai-responses`
5. Append the matching `export` lines to `~/.zshrc` (or `~/.bashrc`)
6. Point the client at the awerouter daemon port
7. Tell the user to invoke skills and look for `awerouter` in the skill list

### The 30-Second Version

In Claude Code, Codex, OpenCode, or any of the 47+ agents supported by aweskill, the prompt is the same:

> "Read https://github.com/mugpeng/awerouter/blob/main/README.ai.md and follow it."

The agent does the rest. It runs `pip install`, sets up the skill, initializes the config, edits `~/.zshrc`, and reports back. If something fails — Python too old, missing `pip`, an existing config with profiles you care about — it stops and asks, instead of silently breaking things.

### What the Agent Will Not Do

This is the most important boundary. awerouter is a **daemon**. It runs as `awerouter serve` and sits in front of the upstream provider, proxying every request through a three-layer routing pipeline. The agent will never run `serve`. It will never start, stop, or restart the proxy.

The `README.ai.md` says so explicitly. The skill says so explicitly. The safety rule is unambiguous: the daemon is the user's terminal. The agent's job is configuration — `init`, `config show`, `usage stats`, `usage calibrate`, editing `providers.json` and `routing.json`. The agent lives in the text file world. The daemon lives in the socket world. They do not cross.

This means the day-to-day routing management — list profiles, show config, check usage, tune thresholds, add providers — is all agent-runnable. Only the actual proxy is yours to start in your own terminal. The same rule applies to `awerouter add` (interactive wizard) and `awerouter restore` (overwrites a config from backup).

## A Day in Practice

It is Tuesday. The daemon is running on port 20128. Claude Code is pointed at it via `ANTHROPIC_BASE_URL=http://127.0.0.1:20128`.

**7:42 AM.** You start the day. `awerouter serve` is already running from yesterday. You want to see how the routing went overnight:

```
awerouter usage stats
```

The agent reads the JSONL request log and reports: 68% of requests went to flash (StepFun step-3.7-flash), 32% to pro (Anthropic claude-opus-5). Three fallbacks — flash returned 429s and the proxy failed over to pro before streaming a single byte. You make a note to check the StepFun quota later.

**9:15 AM.** You hit a tricky concurrency bug. The agent opens a large context — 12,000 tokens of code, stack traces, and prior conversation. awerouter's L3 difficulty check kicks in: token count across all request content exceeds `longContextThreshold` (8,000). The request is routed to pro. Claude Opus gets the full context and diagnoses the deadlock in one pass. You did not have to think about the routing. It happened automatically, based on the shape of the request.

**11:30 AM.** You want to add a new provider. You tell the agent:

> "Add GLM as a provider in the openai-chat group. I have `GLM_API_KEY` in my zshrc."

The agent reads `providers.json`, adds the GLM entry under `openai-chat`, and reports the new block:

```json
"openai-chat": {
  "glm": {
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "auth": "${GLM_API_KEY}"
  }
}
```

Then it asks: "Do you want to add GLM as a destination in a routing profile?" You say yes, and it updates `routing.json` to set `destinations.flash` to `glm,glm-4-flash`. No copy-paste. No "let me find the docs."

**1:00 PM.** You are curious whether `longContextThreshold` is set right. You run:

```
awerouter usage calibrate
```

The agent shows the token distribution from the last 500 requests: p50 at 3,200 tokens, p75 at 6,800, p90 at 11,000, p95 at 18,000. It suggests thresholds at 4,000, 6,000, 8,000, and 12,000, with the resulting flash/pro split for each. You pick 6,000 — it keeps 75% of requests on the cheap tier while still catching the genuinely long ones. The agent edits `routing.json` and says: "Restart `awerouter serve` to pick up the change."

**3:00 PM.** You want to compare costs. You run:

```
awerouter usage savings
```

The agent shows a token accounting table: 2.4 million input tokens to flash, 1.1 million to pro. Against a pro-only baseline, the savings bracket lands between 47% and 62% depending on cache sensitivity. It does not promise exact money — output tokens and cache semantics vary by provider — but the order of magnitude is clear. The tool paid for itself in a single morning.

**6:00 PM.** You are done. Four providers, two routing profiles, three protocol groups, one threshold tuned from real traffic. The agent handled every config change. The daemon handled every request. You never opened `~/.config/awerouter/providers.json` by hand.

## The Stack: What the Skill Can Reach

The `awerouter` skill is intentionally small. It does not try to be a general agent framework. It is a thin procedural layer over the `awerouter` CLI, with an intent router that maps natural language to commands.

| You say | The skill runs |
|---|---|
| "List my awerouter profiles." | `awerouter list` |
| "Show me cc-router-1." | `awerouter config show cc-router-1` |
| "Add a GLM provider for openai-chat." | edits `~/.config/awerouter/providers.json` |
| "Change flash destination to glm-4-flash." | edits `routing.json`, verifies with `awerouter config show` |
| "How did yesterday's routing go?" | `awerouter usage stats` |
| "What should my longContextThreshold be?" | `awerouter usage calibrate` |
| "How much am I saving?" | `awerouter usage savings` |
| "Set up `GLM_API_KEY` in my zshrc." | appends `export` line, asks you to paste the token |

The last row is the one that gets the most surprise. Most users have at least one key sitting in their head — not in any file — because they set it up once, on a different machine, and never quite got around to persisting it. The agent will read `~/.zshrc`, identify what's missing, and walk you through adding it. The key stays in the env var. Never in the config file.

## Claude Code, Codex, OpenCode, and the Long Tail

The same profile works across clients.

**Claude Code** is the primary user. `ANTHROPIC_BASE_URL=http://127.0.0.1:20128` points the CLI at the awerouter daemon. Tier labels like `c1/flash` and `c1/think` (Claude Code's background and think model hints) map directly to flash and pro tiers. The `web_search` tool — a signal that the request needs a capable model — routes to `settings.webSearchModel` (default: pro). The user experience is transparent: you keep working, routing happens silently in the background.

**Codex** profiles use the `openai-chat` or `openai-responses` protocol group. awerouter writes `OPENAI_BASE_URL` for you — either through aweswitch profile launch, or by telling the agent. Codex does not have tier labels, so routing is mostly L1 (web_search) plus L3 (token count and images). Flash by default, pro when the request demands it.

**OpenCode** profiles use `openai-chat` or `openai-responses` the same way. OpenCode's `@`-agent calling then lets you route sub-tasks to different models in the same conversation — awerouter manages the upstream split, OpenCode manages the agent selection. The boundary is intentional: awerouter handles the *connections*, OpenCode handles the *agents*.

**Cursor, Gemini CLI, Windsurf** — any client that speaks Anthropic Messages, OpenAI Chat Completions, or OpenAI Responses works. The protocol layer detects mismatches and returns a clear 400 instead of silently garbling the body.

## Usage Analytics: The Other Half

Routing is half the problem. The other half is knowing whether the routing is any good.

awerouter writes every proxied request to a JSONL log at `~/.local/state/awerouter/requests.jsonl` (50MB rotation). Four read commands let you inspect it:

- **`usage stats`** — per-profile breakdowns by agent, destination, provider, and model. Error counts, fallback counts, latency percentiles (first-byte and total) per dimension.
- **`usage calibrate`** — token count distribution (p50–p99) with candidate `longContextThreshold` values and the resulting flash/pro split. This is how you answer "is my threshold too aggressive or too conservative?"
- **`usage savings`** — token accounting against a pro-only baseline, with a cache-sensitivity bracket (Anthropic-style ~0.1× read / 1.25× write / 5-min TTL) and switch-cadence analysis. It ends in ready-to-fill money formulas.
- **`usage tokens`** — input tokens by content type: messages, system prompt, tools, tool results, tool calls, thinking. Shows you what is actually consuming tokens.

Agent identification comes from the User-Agent header: `claude-cli/...` → Claude Code, `codex_cli_rs` → Codex, `opencode/...` → OpenCode. No user action required.

## Why It Matters

The first wave of agent tools assumed a human operator. Configure meant editing JSON. Install meant `pip install` and a checklist. Most users tolerated it because they only had one tool to install.

The second wave assumes an agent operator. Install is a task. Configure is a task. Both can be delegated. The artifact that gets delegated is not the binary — it is a readable spec the agent can execute.

`README.ai.md` is that spec. It is written for the agent to read and follow, with explicit "do not run this command" boundaries, fallback paths for missing dependencies, and verification steps at every stage. The user does not need to understand it. The agent does.

awerouter has a second design constraint that makes it different from most proxy tools: the response path is **opaque**. Response bytes are never parsed. They are never buffered. They stream from upstream to client untouched. This is not an implementation shortcut — it is a design invariant. It means the proxy never sees enough of the conversation to build a feedback loop on quality. So awerouter does not try. It does not use an LLM classifier. It does not guess from keywords. It routes from structure — model tier, token count, image presence, web_search tool — and nothing else. The moat is "automatic + client-agnostic + zero classification cost," not accuracy.

This is the test I now apply to every routing tool I evaluate:

1. **Can an agent install it from a single prompt?**
2. **Can an agent tune it from natural language after install?**
3. **Does the routing add latency I can feel?**

awerouter passes all three. The first prompt is the README. The second is the skill. The third is negligible: the proxy is a thin aiohttp relay with zero request-body parsing. The first-byte latency overhead is a single HTTP round-trip on the local loopback.

The future of agent tooling is not "tools that work well with agents." It is "tools that the agent itself can install, configure, and operate on your behalf." awerouter is one of the first to ship with that as the primary install path, not a workaround.

## Try It

Tell your agent:

> "Read https://github.com/mugpeng/awerouter/blob/main/README.ai.md and follow it."

Then start the daemon in your own terminal:

```bash
awerouter serve
```

And point your client at it:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:20128
```

From there, the questions become ordinary:

- "Add a GLM provider for openai-chat."
- "What is my flash/pro split this week?"
- "Tune longContextThreshold from my usage."

The agent already knows the commands. You just had not given it the README yet.

## More from mugpeng

awerouter is part of the aweteam ecosystem:

- **[aweskill](https://aweskill.webioinfo.top/)** — CLI-first skill package manager for 47+ AI coding agents
- **[aweswitch](https://github.com/Webioinfo01/aweswitch)** — Agent profile switcher for Claude Code, Codex, and OpenCode; launches sessions pointing at the awerouter daemon
- **[aweshelf](https://github.com/Webioinfo01/aweshelf)** — AI coding session manager with profile-aware restoration
