# awerouter Bootstrap Protocol

This document is for AI coding agents. Help the user install and configure `awerouter`, a transparent LLM router that splits coding-agent traffic across cheap/fast and strong/accurate providers.

## Language Behavior

- Reply in the user's language when possible.
- If the user asks in Chinese, continue in Chinese.
- If the user asks in English, continue in English.

## Do Not Launch Servers

**Never run `awerouter serve` inside this agent.** It starts a long-lived proxy session. Always tell the user to run it in their own terminal.

You may run these read-only or non-interactive commands: `awerouter init`, `awerouter config path`, `awerouter config show`, `awerouter config edit`, `awerouter list`, `awerouter usage stats`, `awerouter usage log`, `awerouter usage calibrate`, `awerouter usage savings`.

Do not run `awerouter add` inside the agent — it is interactive and would block the session.

## Step 1: Install awerouter CLI

### Prerequisites

- Python >= 3.9 (`python3 --version`)
- pip available (`pip --version`)

If Python is missing, tell the user to install it from https://www.python.org/.

### Steps

#### 1. Install awerouter

```bash
pip install awerouter
```

#### 2. Verify awerouter CLI

```bash
awerouter --version
```

Expected output: `awerouter X.Y.Z`

---

## Step 2: Install awerouter skill

Install the skill so the agent can help manage routing in this and future sessions. Choose one of the following options.

### Option A: Via aweskill (recommended if aweskill is available)

Use this option if the user already has aweskill installed, or is willing to install it. This gives full skill management — install, update, projection, backup.

#### Prerequisites

- Node.js >= 20 (`node --version`) — required by aweskill
- npm available (`npm --version`)

If Node.js is missing, tell the user to install it from https://nodejs.org/.

#### Steps

##### A1. Install aweskill (if not already installed)

```bash
npm install -g aweskill
```

##### A2. Initialize the aweskill central store (if not already done)

```bash
aweskill store init
```

##### A3. Install awerouter skill from GitHub

```bash
aweskill install mugpeng/awerouter
```

##### A4. Identify the current agent

```bash
aweskill agent supported
```

Look for lines marked with `✓`. Common agent ids: `claude-code`, `codex`, `cursor`, `gemini-cli`, `windsurf`, `opencode`, `qwen-code`.

If you cannot determine the agent id, ask the user.

##### A5. Project awerouter skill to this agent

```bash
aweskill agent add skill awerouter --global --agent <agent-id>
```

##### A6. Verify

```bash
aweskill agent list --global --agent <agent-id>
```

Expected: `awerouter` shows as `linked`.

---

### Option B: Direct copy (no aweskill needed)

Use this option if the user does not have aweskill and does not want to install Node.js. This copies the SKILL.md file directly into the agent's skill directory.

#### Prerequisites

- `curl` or `wget` available

#### Steps

##### B1. Identify the current agent's skill directory

Determine which agent is running and its global skill directory:

| Agent | Skill directory |
|---|---|
| Claude Code | `~/.claude/skills/awerouter/` |
| Codex | `~/.codex/skills/awerouter/` |
| Cursor | `.cursor/skills/awerouter/` (project-level) |
| Gemini CLI | `~/.gemini/skills/awerouter/` |
| Windsurf | `~/.windsurf/skills/awerouter/` |
| OpenCode | `~/.opencode/skills/awerouter/` |
| Qwen Code | `~/.qwen/skills/awerouter/` |

If the agent is not in this list, ask the user where to place the skill file.

##### B2. Download and place SKILL.md

```bash
mkdir -p <skill-directory>
curl -fsSL https://raw.githubusercontent.com/mugpeng/awerouter/main/resources/skills/awerouter/SKILL.md -o <skill-directory>/SKILL.md
```

Replace `<skill-directory>` with the path from step B1.

---

## Step 3: Initialize config

```bash
awerouter init
```

This creates default config files in `~/.config/awerouter/`:
- `providers.json`
- `routing.json`

Override with `AWEROUTER_CONFIG_DIR` if needed.

---

## Step 4: Configure providers and routing

Tell the user the difference between the two files:

- `providers.json` stores endpoints and auth. It contains secrets or secret references.
- `routing.json` stores routing strategy. It should usually be the file you edit to change behavior.

### Edit providers

1. Read `providers.json`.
2. Update only the protocol group you need: `anthropic`, `openai-chat`, or `openai-responses`.
3. Use `${ENV_VAR}` for auth values.
4. Keep `base_url` exactly as the client expects.

### Edit routing

1. Read `routing.json`.
2. Set a `profile` id for each routing setup.
3. Set `protocol` to the matching provider group.
4. Set `longContextThreshold` to an integer, or `"auto"` to calibrate it from the profile's own traffic at each serve start (policy in `settings.longContextAuto`: `percentile`/`windowDays`/`minSamples`/`fallbackThreshold`, all optional).
5. Use `destinations.flash` for cheap/fast tasks and `destinations.pro` for hard tasks.

If the user is unsure, recommend starting from `awerouter init` and changing one profile at a time.

---

## Step 5: Set up environment variables

Provider auth uses `${ENV_VAR}` references that expand from the shell environment. These must be set before starting `awerouter serve`.

### Where to put them

| Platform | Target | Scope |
|----------|--------|-------|
| zsh (default on macOS) | `~/.zshrc` | all zsh shells |
| bash | `~/.bashrc` or `~/.bash_profile` | all bash shells |
| Windows | `setx` (writes user environment variables) | cmd and PowerShell both |

On Windows, prefer `setx` — it persists to the user environment, so both cmd and PowerShell pick it up.

### Format

bash/zsh — append to the shell config file:

```bash
export STEPFUN_AUTH_TOKEN="..."
export GLM_API_KEY="..."
export OPENAI_API_KEY="..."
```

Windows — run `setx` (works from cmd and PowerShell):

```bat
setx STEPFUN_AUTH_TOKEN "..."
setx GLM_API_KEY "..."
setx OPENAI_API_KEY "..."
```

Do not pass `/M` to `setx` — that targets machine scope and requires admin.

### Steps

1. Read `providers.json` to find which `${ENV_VAR}` names are referenced.
2. Check which env vars are already set to avoid duplicates.
3. Set them using the platform-appropriate method.
4. Tell the user to reload: `source ~/.zshrc` (bash/zsh) or open a new terminal (Windows `setx` — it does not affect the current one).

---

## Step 6: Point the client at awerouter

Set the client's base URL to the awerouter daemon port shown by `awerouter serve`.

Common setups:
- Claude Code -> `ANTHROPIC_BASE_URL=http://127.0.0.1:20128`
- OpenAI-compatible clients -> `OPENAI_BASE_URL=http://127.0.0.1:20128/v1`

Tell the user to start the daemon themselves:
```bash
awerouter serve [profile-name]
```

If only one routing profile exists, the profile name is optional.

---

## Step 7: Verify and tune

Run these checks:
```bash
awerouter list
awerouter config show
awerouter usage stats
awerouter usage calibrate
```

If the user wants cheaper routing without losing accuracy:
1. Start from `awerouter usage calibrate`.
2. Adjust `longContextThreshold`, or set it to `"auto"` so each serve start picks the percentile of the profile's own recent L3 traffic (the calibrate output ends with the value auto would pick).
3. Review `awerouter usage savings`.

## Final Step

After setup, tell the user to invoke skills (`/` in Claude Code, `$` in Codex, or the equivalent in other agents) and check if `awerouter` appears in the list. If it does, the skill is ready to use immediately. If not, the user should restart the agent.

> awerouter is installed and configured. Invoke skills (type `/` or `$` depending on your agent) and look for `awerouter` — if it appears, you're good to go. If not, restart the agent. Then you can ask me things like:
>
> - "Add a GLM provider for the openai-chat group."
> - "List my awerouter profiles."
> - "Tune longContextThreshold from my usage."

If the user is speaking Chinese, use this version instead:

> awerouter 已安装并配置完成。请调用 skills（输入 `/` 或 `$`，取决于你的 agent），看看列表中是否出现了 `awerouter`。如果出现了，说明已就绪可以直接使用。如果没有，请重启 agent 后再试。然后你可以继续问我，例如：
>
> - "给 openai-chat 分组加一个 GLM provider。"
> - "列出我的 awerouter profile。"
> - "根据 usage 调一下 longContextThreshold。"

---

## Next Steps

### aweswitch — profile-based launching

awerouter pairs naturally with [aweswitch](https://github.com/Webioinfo01/aweswitch), the agent profile switcher. An aweswitch profile can point `ANTHROPIC_BASE_URL` (plus `ANTHROPIC_MODEL=auto` and the `_HAIKU_`/`_OPUS_` tier vars) at the awerouter daemon so routing is applied on launch.

If the user agrees, read the aweswitch AI install guide:

```
https://github.com/Webioinfo01/aweswitch/blob/main/README.ai.md
```

## Safety Rules

- Do not run `awerouter serve` inside the agent.
- Do not hardcode secrets into config files.
- Do not edit `providers.json` and `routing.json` in the same step unless the user explicitly asks.
- If a command fails, report the exact command and error message.
- If the user uses a non-default config directory, always use `AWEROUTER_CONFIG_DIR`.
