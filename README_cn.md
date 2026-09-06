<div align="center">
  <img src="logo/logo.webp" alt="awerouter" width="860">
  <h1>awerouter: 智能 LLM 路由 <a href="https://github.com/Webioinfo01/aweskill"><img src="https://raw.githubusercontent.com/Webioinfo01/aweskill/main/logo/aweskill-badge2.svg" alt="aweskill companion"></a></h1>
  <p><strong>轻量任务走 Flash，复杂决策走 Pro。</strong></p>
  <p>按请求结构信号做确定性路由的同协议透明代理——不猜语义、不用关键词、不跑分类器。支持 Anthropic Messages、OpenAI Chat Completions、OpenAI Responses 三种协议。</p>
  <p>
    <a href="./README.md">English</a> ·
    <strong>简体中文</strong>
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

> 按结构信号把编码 agent 流量拆分到不同 provider，省钱不降质。同协议透传，不做协议转换。可选的 profile 级 token 节省（RTK 压缩 + ODCP 裁剪，默认关闭）。

## 快速开始

### 1. 安装和使用 awerouter

如果你在 Claude Code、Codex、Cursor 等 coding agent 中工作，直接告诉它：

```text
Read https://github.com/wehuman01/awerouter/blob/main/README.ai.md and follow it to install and configure awerouter.
```

Agent 会安装 CLI、初始化配置（内置模板，或合并进你已有的配置）、配置 providers 和路由，并通过 [aweskill](https://aweskill.webioinfo.top/) 安装 awerouter skill 用于后续路由管理。认证方面，它会扫描你的 shell 配置，找出你已经导出的 key 变量（`GLM_API_KEY`、`STEPFUN_AUTH_TOKEN` 等），在 `providers.json` 里以 `${VAR}` 引用——只报告变量名、不碰值，只有缺失的 key 才会问你要。

**配置完成后你可以这样告诉 agent：**

> "加一个 stepfun 的 flash provider 和一个 pro profile。"
> "列出我的 awerouter profile。"
> "根据 usage 帮我调一下 longContextThreshold。"
> "解释一下我的 usage savings。"

Agent 可以直接运行只读命令（`list`、`config show`、`usage stats` / `calibrate` / `savings`）并编辑 `providers.json`（端点/密钥）和 `routing.json`（策略）——见[第 3 步](#3-用自然语言管理路由)。

<details>
<summary>手动安装与配置</summary>

从 PyPI 安装：

```bash
pip install awerouter
```

快速开始：

```bash
# 1. 初始化配置（生成 ~/.config/awerouter/{providers,routing}.json）
awerouter init                # 也可选内置搭配：awerouter init step-glm / glm-codex / step-glm-mm（见「常见搭配模版」）
#    已经有配置了？awerouter init <模板> --merge 把模板里缺的 provider、profile 和 settings
#    补进现有配置——已有条目一律不覆盖

# 2. 添加 profile（自动写入两个文件，保证引用一致）
awerouter add                 # 交互式向导——在你自己的终端运行
#    或者手改：编辑 providers.json 填密钥（${ENV_VAR}），编辑 routing.json 映射 flash/pro

# 3. 安装 awerouter skill，用于后续自然语言管理
aweskill install wehuman01/awerouter
aweskill agent add skill awerouter --global --agent <agent-id>   # claude-code、codex、cursor 等
```

</details>

### 2. 在自己的终端启动路由 —— 两种服务模式

`awerouter serve` 是常驻 daemon——这是唯一一件 agent 不会替你做的事。它有两种服务模式，而且可以并存：日常编码流量跑一个智能路由 profile，需要所有模型按名字调用时，在旁边再开一个集成网关。

#### 智能路由 —— `awerouter serve run <profile>`

单个 profile，按结构信号切分 flash/pro：便宜的起草走 flash，难活走 pro。常见组合（取自一份真实配置——StepFun step_plan 的 key + GLM coding plan + Codex 订阅）：

- flash `stepfun-1,step-router-v1` / pro `glm,glm-5.3`——StepFun 的 step_plan router 承接廉价流量，GLM coding plan 处理难活（step-glm 组合）
- flash `stepfun-1,step-3.7-flash` / pro `glm,glm-5.3`，再加 `imageModel: "flash"`、`imageBridge: true`——同一对模型，但纯文本旗舰长出了眼睛（step-glm-mm，见[图片桥接](#图片桥接)）
- flash `stepfun-1,step-router-v1` / pro `codex,gpt-5.6-terra`——ChatGPT 订阅扛 pro，计费 plan 起草（step-codex）

```bash
awerouter serve run step-glm       # 只有一个 profile 时名字可省
#    加 -d 后台常驻运行（终端关掉也不停；日志：~/.local/state/awerouter/serve-<profile>.log）
#    或 --install 装成常驻系统服务：开机自启，重启、崩溃都自动恢复
```

serve 启动横幅会直接打印客户端要 export 的行：

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:20128      # Claude Code
export OPENAI_BASE_URL=http://127.0.0.1:20128/v1      # openai 兼容客户端
# aweswitch profile 环境变量：ANTHROPIC_MODEL=auto, _HAIKU_=flash, _OPUS_=pro
```

改配置不用重启：serve 会监听 `routing.json` / `providers.json` 的修改并热加载（文件写坏时继续用上一份可用配置服务，见[后台运行与热加载](#后台运行与热加载)）。

**或者通过 [aweswitch](https://github.com/Webioinfo01/aweswitch) 启动**——用一个 aweswitch profile 把客户端的 `BASE_URL` 指向 daemon，启动即走路由。在你的终端运行（它会启动新的 agent 会话）：

```bash
aweswitch oc-awerouter
```

<details>
<summary>示例：指向 awerouter 的 OpenCode profile</summary>

在 aweswitch 配置里加一个指向 daemon 的 OpenCode profile：

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

`OPENCODE_MODEL` 设为 `auto` 时，awerouter 按结构信号逐请求路由——上游 provider 收到的是 `routing.json` destinations 里配置的实际 model id，而不是 `auto`。Claude Code 同理，用一个 `anthropic` profile（`ANTHROPIC_MODEL=auto`）即可。

</details>

#### 本地集成路由 —— `awerouter serve all`

一个端口服务你配置的所有内容：每个路由 profile（`<profile>/auto` 跑它的智能路由，`/flash`、`/pro` 强制指定档位），以及 provider 在自己的 `models` 列表里声明的每个模型——以 `<provider>/<model>` 固定转发直达、不走路由。把你所有不同的 provider 聚到一个本地 OpenAI/Anthropic 兼容端点后面——一个属于自己的迷你 OpenRouter。同样取自那份真实配置：StepFun 的 `step-router-v1` 和 `step-explore`、GLM 的 `glm-5.3` 和 `glm-5.3-flash`、豆包的 `Kimi-K2.7-Code` 和 `Doubao-Seed-Evolving`、Codex 订阅的 `gpt-5.6-luna`——一个端口全部可调；同一个 provider 还可以配多个条目（stepfun-1/2/3：同一组模型、三把 key），用多账号摊平额度——给这几个条目加上相同的 `"pool"` 标记后，固定转发撞 429 还会自动换下一个账号、模型不变（见 providers.json 一节）：

```bash
awerouter serve all               # 一个端口服务所有 profile 和已声明的模型
```

客户端指向这个端口，按模型名选择：

```bash
export OPENAI_BASE_URL=http://127.0.0.1:20128/v1
# 然后就可以调用，例如：step-glm/auto   step-deepseek/pro   doubao/Kimi-K2.7-Code   codex/gpt-5.6-luna
```

要让某个 provider 的模型可以直接调用，在 `providers.json` 里声明（每个协议分组单独声明）：

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

`GET /v1/models` 列出所有可用名称；未知名称返回描述清晰的 400。`serve all` 只认自己的 `--port`（否则从 20128 起扫描），可选的 `defaultProfile` 把裸 `auto`/`flash`/`pro` 名字映射到某个 profile——完整细节见[配置一节的 gateway 注记](#配置)。

Agent 的其余边界：它也不会运行 `awerouter add`（交互式向导）、`awerouter config restore`（覆盖配置文件）、`awerouter usage clean`（删除日志）或 `awerouter self-update`（升级安装）——这些都留在你的终端。

### 3. 用自然语言管理路由

日常路由管理都通过 agent 进行——列出和查看 profile、分别编辑 `providers.json`（端点/密钥）和 `routing.json`（策略）、读取 usage 并给出阈值建议（完整 CLI 参考见[命令](#命令)）：

#### 查看与检查

可以直接对 agent 说：

```text
列出我的 awerouter profile，并给我看 cc-router-1 的路由条目。
```

<details>
<summary>等价的 CLI 命令</summary>

```bash
awerouter list
awerouter config show cc-router-1   # 脱敏输出；只看该 profile 的 providers 和路由条目
```

</details>

#### 新增 provider 或调整目的地

可以直接对 agent 说：

```text
给 openai-chat 分组加一个 GLM provider，并把它设为 pro 目的地。
```

Agent 会直接编辑两个配置文件——在 `providers.json` 的 `openai-chat` 分组加 provider 条目（如果你已导出 `GLM_API_KEY`，auth 就写成 `${GLM_API_KEY}`），并把 `routing.json` 里该 profile 的 `destinations.pro` 指向它。交互式的 `awerouter add` 向导仍然可以在你自己的终端使用。

#### 按真实用量调阈值

可以直接对 agent 说：

```text
根据 usage 帮我调一下 longContextThreshold。
```

<details>
<summary>等价的 CLI 命令</summary>

```bash
awerouter usage calibrate    # L3 token 分布 + p90/p95/p99 候选阈值
awerouter usage stats        # 按 profile 的明细、错误、延迟分位
# 然后编辑 routing.json——或把阈值设为 "auto"，让每次 serve 启动时自动校准
```

</details>

#### 看看省了多少

可以直接对 agent 说：

```text
解释一下我的 usage savings。
```

<details>
<summary>等价的 CLI 命令</summary>

```bash
awerouter usage savings      # 被卸载到 flash 的 pro token 量（对比纯 pro 基线），附可直接代入价格的公式
```

</details>

## 支持工具

awerouter 与两个配套工具配合最佳：

- **[aweskill](https://aweskill.webioinfo.top/)** — 面向 AI agent 的 CLI skill 包管理器。安装 awerouter skill，让你的 agent 用自然语言管理路由。
- **[aweswitch](https://github.com/Webioinfo01/aweswitch)** — Agent profile 切换器。用指向 awerouter daemon 的 profile 启动 Claude Code、Codex 或 OpenCode 会话。

aweskill 通过管理skills，让 agent **管理**路由；aweswitch 让你**启动**走路由的会话。配置一次 awerouter，之后就能用 `aweswitch <profile>` 把任意 agent 启动到它上面。

## 配置

`~/.config/awerouter/` 下两个文件（`AWEROUTER_CONFIG_DIR` 环境变量覆盖目录）：

### providers.json

端点 + 密钥，按线上协议分组（`config show` 自动脱敏）：

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

在网关模式下，provider 还可以声明允许直接切换的模型：

```json
"openai-chat": {
  "stepfun": {
    "base_url": "https://api.stepfun.com/step_plan/v1",
    "auth": "${STEPFUN_AUTH_TOKEN}",
    "models": ["step-3.7-flash", "step-router-v1"]
  }
}
```

这样 `awerouter serve all` 会额外暴露 `stepfun/step-3.7-flash`。它是固定转发，绕过自动路由；未声明的模型不会被接受。`models` 按协议分别声明，旧配置不需要修改。

provider 还可以声明 `"multimodal": true`。它的唯一消费方是回退队列：带图请求永远不会回退到没声明多模态的 provider——图片护栏在故障转移期间同样成立，未声明即视为否（安全方向）。

provider 还可以带一个 `"pool": "stepfun"` 标记。同一协议组内共享标记的条目就是同一家厂商的多个账号（同一组模型、不同的 key）：网关的 `provider/<model>` 固定转发撞 429 时，会换**同一个模型**的下一个池成员重试——按声明顺序从被点名的条目循环往后，每个账号的溢出流向下一个账号。每个条目一个标记，就取代了多账号场景下本要互相写一堆 backups 的做法；不打标记的条目保持锁定（点名的模型 429 就返回 429，绝不偷换——池只换谁来服务你点名的模型，不换模型本身）。冷却、`→fb:` 标签、`fallback_hops`、`multimodal` 图片护栏都与路由 profile 的回退完全一致；没声明该模型的成员不进队列，池内 `models` 集合不一致时 serve 启动会给出警告。

支持三种协议。`base_url` 沿用各原生客户端的写法——从客户端配置里原样抄过来即可，awerouter 按原生客户端同样的规则拼接端点路径：

| 协议 id | `base_url` 写法 | 端点 |
|---------|----------------|------|
| `anthropic` | `ANTHROPIC_BASE_URL` 风格（不带 `/v1`） | `base_url + /v1/messages` |
| `openai-chat` | `OPENAI_BASE_URL` 风格（含版本段） | `base_url + /chat/completions` |
| `openai-responses` | `OPENAI_BASE_URL` 风格（含版本段） | `base_url + /responses` |

同一家 provider 的两个协议路径往往不同——比如 GLM：Claude 协议客户端用 `https://open.bigmodel.cn/api/anthropic`，chat completions 是 `https://open.bigmodel.cn/api/coding/paas/v4`，responses 是 `https://open.bigmodel.cn/api/v1`。所以每个协议分组各配各的 `base_url`——这也是多协议 profile 要在每个被服务的分组里都列一份 provider 的原因。

鉴权头**根据 `base_url` 自动判断**：`anthropic.com` → `x-api-key`（裸 token）；其他 → `Authorization`（自动补 `Bearer `）。除非启发式判断错了，否则不需要填 `auth_header`。

### 本地模型（免认证）

本地推理服务不需要密钥——`auth` 直接省略，请求会以无认证头的干净形态发往上游：

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

任何 OpenAI 兼容服务（Ollama、LM Studio、llama.cpp、vLLM）都能挂在 `openai-chat` 组；Ollama ≥ 0.14 原生支持 Anthropic 协议，可以和 Claude Code 同在 `anthropic` 组。注意路径约定与云端一致：`openai-chat` 的 base_url 带 `/v1` 段，`anthropic` 的不带。

本地和云端在同一个 profile 里随意混排——便宜的活给本地，难啃的给云端：

```json
"destinations": {
  "flash": "ollama,qwen3-coder:30b",
  "pro":   "anthropic,claude-opus-5"
}
```

以 Ollama 为例的端到端部署（flash=本地 / pro=云端）：

```bash
ollama pull qwen3-coder:30b      # 本地服务默认监听 127.0.0.1:11434
awerouter serve run cc-router-1
export ANTHROPIC_BASE_URL=http://127.0.0.1:20128
```

常见本地服务的默认端口（都走 `openai-chat`、都免认证）：LM Studio `http://127.0.0.1:1234/v1`、llama.cpp `http://127.0.0.1:8080/v1`、vLLM `http://127.0.0.1:8000/v1`。

本地服务没启动时，flash→pro 回退在连接错误时触发，请求透明地落到云端——本地优先、云端兜底，不需要任何额外配置。

防呆：`auth` 为空但 `base_url` **不在**本机的 provider，serve 启动时会打一行警告（`awerouter add` 向导在同情况下会当场确认）。局域网免认证服务是合法场景——警告只提示、不拦截。

### Codex 账号（订阅登录）

`openai-responses` 组里的 `"auth": "codex"` 表示用本地 Codex CLI 的登录态（`~/.codex/auth.json`）代替 API key——订阅自带的模型就能和 key 计费的模型混在同一个 flash/pro 路由里：

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

任何说 Responses 协议的客户端把 base URL 指到 awerouter、填个哑 key 即可；真正的鉴权头由 awerouter 每请求现盖，并为后端怪癖做少量归一化：`store` 强制 `false`、剥掉 `max_output_tokens`、非流式请求以 SSE 跑完再收敛成单个 JSON 回复。

行为要点：

- **只读登录，不刷新。** 在这里刷新会把 CLI 的登录踢失效，所以刷新归 CLI；awerouter 每请求重读 `auth.json`，遇 401 再重读重试一次。access token 约十天有效，照常用 `codex`（或 awewarm 保温）即可。
- **登录过期。** 重读后仍 401：flash 兜底到带 key 的 pro（`usage log` 带 `401-retry` 标记）；只有 pro 也用同一登录时才把 401 透传。
- **感知系统代理。** 遵循 `https_proxy`/`all_proxy`——chatgpt.com 常需要代理才通；其他 provider 永远直连。
- **模型名会漂移。** 当前可用的是 CLI 在用的名字（写作本文时是 `gpt-5.6-luna`），改名只需动 routing.json 一行。
- **没登录？** 返回 503 并提示 `run: codex login`，serve 启动时也打警告——刻意不兜底：登录缺失是配置错误，应该立刻暴露，静默落到付费 pro 会把错误和账单一起藏起来。

该哨兵值只允许出现在 `openai-responses` 组——ChatGPT Codex 后端只说 Responses 协议。

### Claude 账号（订阅登录）

`anthropic` 组里的 `"auth": "claude"` 表示用 Claude Pro/Max 订阅登录作为上游。这个登录由 awerouter 自己持有（`awerouter config login claude` 走 Claude Code 同款授权流程，token 存 `~/.config/awerouter/claude-auth.json`，权限 0600），不借用本机 CLI 的登录态。订阅自带的模型就这样和 key 计费的模型混进同一个 flash/pro 路由：

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
awerouter config login claude    # 打开浏览器授权，把回调页显示的 code 粘回来
awerouter config logout claude   # 删除本地登录
```

任何说 Anthropic 协议的客户端把 base URL 指到 awerouter、填个哑 key 即可（Claude Code 设 `ANTHROPIC_BASE_URL`，CLI 自己的登录完全不受影响）；鉴权头和 OAuth 标记由 awerouter 每请求现盖，请求体不做改写。行为要点：

- **刷新归 awerouter。** 与 codex 正相反：这个登录属于 awerouter，由它用 refresh token 自动续期，新 token 落盘后请求才继续。
- **登录失效。** 401 先强制刷新重试一次；刷新后仍 401 则 flash 兜底到带 key 的 pro。登录缺失返回 503 并提示 `run: awerouter config login claude`——和 codex 一样刻意不兜底。
- **感知系统代理。** 同 codex——api.anthropic.com 常需要代理才通。
- **注意 ToS。** 端点和头集合是社区逆向的公开契约，随时可能失效；且 Anthropic 2026 年政策限制第三方工具使用订阅 OAuth token——骑你自己的订阅，风险自担。

该哨兵值只允许出现在 `anthropic` 组——订阅后端说的是 Messages 协议。

### 常见搭配模版

`awerouter init` 支持内置模板名，一次生成配套的 `providers.json` + `routing.json`（不传名字即 `default`）。下面三套开箱即用；不想跑命令的话，手抄任意一段到自己的配置里也一样。密钥一律 `${ENV_VAR}` 占位，缺失的环境变量在启动时报错。想把模板并进已有配置，加 `--merge`：只补缺失项，已有的内容一律不碰。

**step-glm** —— 纯 API key 的国产双档：flash 走 StepFun step_plan，pro 走 GLM coding plan。面向说 openai-chat 协议的 agent：

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
awerouter init step-glm      # 需要设置 STEPFUN_AUTH_TOKEN 和 GLM_API_KEY
```

**glm-codex** —— GLM coding plan 消化 flash 流量，ChatGPT 订阅（`"auth": "codex"`，见上文）消化 pro：

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
awerouter init glm-codex     # 需要 GLM_API_KEY，且 codex login 登录过 ChatGPT 订阅
```

**step-glm-mm** —— 旗舰模型配一双眼睛：glm-5.3 做全部文本工作，带图的请求交给多模态的 step-3.7-flash 看图转述；模板开启 `imageBridge` 后，后续纯文本轮会带着 flash 的图片转述回到 glm-5.3 继续。双协议（`["anthropic", "openai-chat"]`）：一个端口同时接 Claude Code 和 openai-chat 系 agent——每种协议走自己的 provider 条目：

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
awerouter init step-glm-mm        # 需要设置 STEPFUN_AUTH_TOKEN 和 GLM_API_KEY
# Claude Code:    export ANTHROPIC_BASE_URL=http://127.0.0.1:20128        （不带 /v1）
# openai-chat:    export OPENAI_BASE_URL=http://127.0.0.1:20128/v1       （带 /v1）
```

> **不需要智能路由，只想让旗舰模型看得见图？** 上面这两个 settings 就是全部秘密：`imageModel: "flash"` 让带图请求交给多模态模型，`defaultModel: "pro"` 让其余请求全部走旗舰。把这两行抄进任何已有配置即可，不需要使用整个模板。想只买 GLM 一家？flash 换成 GLM 自家的 `glm-5.3-flash` 即可。再加一行 `"imageBridge": true`，后续纯文本轮就能带着 flash 转述的图片内容回到旗舰（见「图片桥接」）。

后端模型名会漂移（见「Codex 账号」一节）——改名只需动 routing.json 一行。

### routing.json

路由策略，不含密钥（可以进 git）：

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

`settings` 可省（默认 `flash`/`pro`）。它定义 CC 发送的档位 model id：background（Haiku 档）、think（Opus 档）；所有按工具路由的规则（含 L1 的 `webSearch`）统一放在 `settings.toolRouting`——旧顶层 `webSearchModel` 仍作为兜底兼容。`imageModel` 重定向图片护栏（默认 `pro`；旗舰不支持多模态时改 `flash`，见 step-glm-mm 模版），`defaultModel` 翻转兜底去向（默认 `flash`；pro 优先的配置改 `pro`）。主循环用 `auto`——由 L3 按难度路由。在 aweswitch profile 里设：`ANTHROPIC_DEFAULT_HAIKU_MODEL=flash`、`ANTHROPIC_MODEL=auto`、`ANTHROPIC_DEFAULT_OPUS_MODEL=pro`。

settings 的每个键也都可以**直接写在 profile 体内**，与 `protocol`/`destinations` 平级（上面的 `cc-pro-first` 只翻转了 `defaultModel`），且只对这一个 profile 生效。缺的键——包括 `toolRouting`/`longContextAuto` 里缺的字段——逐键继承全局 `settings`，所以每个 profile 只调自己不同的部分。`config show <profile>` 会打印该 profile 的生效值（覆盖键平铺在条目里），serve 横幅用一行 `overrides -> ...` 列出覆盖项。

全部 settings 键、默认值与作用（每个键都既可全局配置、也可在 profile 里配置）：

| 键 | 取值 | 默认 | 作用 |
|----|------|------|------|
| `backgroundModel` | model id（如 `flash`、`c1/flash`） | `flash` | L2：带此档位标签的请求走 flash |
| `thinkModel` | model id | `pro` | L2：带此档位标签的请求走 pro |
| `webSearchModel` | `flash` / `pro` | `pro` | L1：声明 `web_search` 工具的请求去向（旧键——设了 `toolRouting.webSearch` 时以它为准） |
| `imageModel` | `flash` / `pro` | `pro` | L1：带图请求去向（能力护栏——优先级高于档位和长度） |
| `defaultModel` | `flash` / `pro` | `flash` | 所有层都不匹配时的兜底去向 |
| `searchResultDiscount` | 0–1 数字 | `0.3` | L3：文件搜索（Grep/Glob/LS）结果 token 的权重；`1` = 关闭 |
| `imageBridge` | `true` / `false` | `false` | flash 把历史图片转写成文字，纯文本的 pro 接管图片会话（opt-in；见「图片桥接」） |
| `toolRouting.webSearch` | `flash` / `pro` / `null` | `null` | 覆盖 `webSearchModel` 的 web_search 护栏去向 |
| `toolRouting.edit` | `flash` / `pro` / `null` | `pro` | L4：代码刚被修改后的那一轮去向；`null` 关闭该检查点 |
| `longContextAuto.percentile` | 1–99 | `95` | `"auto"` 阈值取 L3 分布的哪个分位 |
| `longContextAuto.windowDays` | ≥ 1 | `7` | 自动校准的回看窗口（天） |
| `longContextAuto.minSamples` | ≥ 1 | `50` | 窗口内 L3 样本不足此数时用兜底阈值 |
| `longContextAuto.fallbackThreshold` | ≥ 0 | `8000` | 样本不足时的阈值（serve 解析 `"auto"` 前也用它） |

`backgroundModel`/`thinkModel` 填的是自由格式的 model id（客户端实际发送的档位名），不是 `flash`/`pro`。未知键——无论写在 `settings` 还是 profile 体内——加载时直接报错并指明是哪个键，拼错不会静默继承全局值。

`longContextThreshold` 可以是整数，也可以写 `"auto"`：每次 `serve` 启动时，awerouter 取该 profile 自己最近 `windowDays` 天 L3 有效 token 分布的 `percentile` 分位值作为阈值。窗口内 L3 请求数不足 `minSamples`（新 profile、流量清淡）时改用 `fallbackThreshold`。四个参数都在 `settings.longContextAuto` 里，全部可选——横幅每次都会打印选了什么、依据是什么。注意：分位值决定的是 flash/pro 的*分配比例*，不代表 flash 的能力上限——如果你的 flash 模型在超长上下文上明显退化，请继续用固定阈值。

> **回退队列（`backups`，配额感知）：** 每个 destination 键都可以声明一条有序回退队列——写法与 `destinations` 相同的 `"provider,model"` 字符串（单个字符串等价于单元素队列）。主选在**首个流式字节之前**返回 429/408/5xx 或网络错误时，请求按队列顺序换下一个候选；智能路由本身不变（档位是请求的属性——回退只换服务者，不换档位）。零配置时每个档位各有一条隐式跨档跳转——flash→pro 与 pro→flash，宁可降级也要给出答案，且每次跳转都大声音地写进 label。显式列表**替换**隐式跳转：你写的就 exactly 是运行的（想让 flash 队列末尾兜底到 pro，就自己写上）。429——或任何带 `Retry-After` 的响应——会让该候选进入进程内冷却（可解析的 Retry-After 优先，否则 30 秒；上限 60 秒），配额耗尽的窗口期内流量会粘在备选上，而不是每个请求都先白撞一次死配额；冷却是建议性的，重启即清零。队列耗尽时把最后一个上游响应原样透传，绝不吞掉；网络错误的 502 会点名每一个试过的候选。每一跳都盖进 label（`background→fb:glm,glm-4.7-flash`）并计入用量日志的 `fallback_hops`。带图请求的队列会按 provider 声明的 `multimodal` 标志过滤（见 providers.json）——图片护栏在回退期间同样成立；`provider/<model>` 固定转发默认保持锁定（见 providers.json 的 `pool` 标记，多账号可换账号不换模型）：点名的模型 429 就返回 429，绝不偷换。

密钥用 `${ENV_VAR}` 引用。缺失的环境变量在启动时报错退出。

> **基于 profile 的路由：** `routing.json` 用 profile id 分组（类似 aweswitch）。`awerouter serve run <profile>` 启动其中一个；只有一个 profile 时自动选择。`protocol` 字段把 profile 映射到 providers.json 的分组，并决定它服务哪个端点——serve 横幅按协议打印对应客户端的环境变量（anthropic → Claude Code 的 `ANTHROPIC_BASE_URL`；openai 协议 → `OPENAI_BASE_URL` / Codex `wire_api`）。它可以写单个 id，也可以写列表：`"protocol": ["anthropic", "openai-chat"]` 让一个端口同时服务两种线协议——客户端按端点路径自选（`/v1/messages` 还是 `/v1/chat/completions`），每种协议走自己的 provider 组，且每个 destination provider 必须在每个被服务分组里都存在（各自协议的 `base_url`）。注意：openai 客户端是单 model 配置，L2 档位匹配基本不触发——openai 流量走 L1 + L3，默认 flash。

> **网关模式（多 profile，一个端口）：** `awerouter serve all` 在**一个**端口服务 routing.json 的全部 profile；不再给每个组合配不同的 OpenCode/SDK provider 和端口。请求的 model 名选择 profile：`<profile>/auto`（完整 L1–L4 智能路由）、`<profile>/flash` 或 `<profile>/pro`（强制档位），例如 `step-glm/auto`、`step-deepseek/pro`。也可以使用 providers.json 中声明的 `<provider>/<model>` 固定模型，例如 `stepfun/step-3.7-flash`；固定模型绕过自动路由。`GET /v1/models` 会列出所有可选名；同一 profile 支持的每种协议依旧由请求端点决定。未知 profile、错误档位或协议不匹配会返回 400 并说明可用 profile，不会静默落到其他组合。网关模式没有 profile 专属端口：它只认 `--port`，否则从 20128 起找空闲端口。
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
> 顶层可选的 `defaultProfile` 让裸名 `auto` / `flash` / `pro` 路由到指定 profile——这让 Claude Code 现有的三档环境变量无需改变。没有 `defaultProfile` 且有多个 profile 时，裸名会明确报错，必须使用 `<profile>/...`；只有一个 profile 时它自然就是默认值。`defaultProfile` 只影响网关模式，不能写在 profile 内；修改它和任意 profile 都会热加载。旧的 `serve run <profile>` 单 profile、多端口用法原样保留，可与网关同时运行。

> **端口分配：** 单 profile 的可选 `port` 字段为 profile 固定监听端口（`awerouter list` 会显示）；优先级：`--port` 参数 > profile `port` > 默认 20128。显式指定的端口被占用时直接报错退出——客户端写死了它，不能悄悄漂移。不配置端口时，serve 从 20128 起向上找第一个空闲端口：第一个实例拿 20128，下一个 20129，依次顺延——按启动顺序分配，与 profile 无关。一次只跑一个实例的热切换用法：profile 不配端口、客户端固定指向 20128 即可。网关模式不读取 profile 的 `port`，只使用自己的 `--port` 或默认端口。

## 路由逻辑

first-match-wins 管线，逐请求评估：

| 层 | 信号 | 决策 |
|----|------|------|
| L1 能力护栏 | body 含 `web_search` 工具；含图片内容 | `toolRouting.webSearch`（默认 **pro**；旧顶层 `webSearchModel` 仍兼容）；`settings.imageModel`（默认 **pro**；开 `imageBridge` 后只有*本轮新传*的图才走这里——已转写的历史图片照常走后续管线） |
| L2 档位匹配 | `model == c1/flash` 或 `c1/think` | flash / pro |
| L3 难度评分 | token（全部请求内容）超阈值 | **pro**；否则继续 |
| L4 编辑检查点 | 尾部工具批次改写了代码（`edit`/`write`/`apply_patch` 等） | `toolRouting.edit`（默认 **pro**，`null` 关闭） |

CC 的 `/model` 选择器设置 tier model id（c1/flash / c1/pro / c1/think）。awerouter 直接读取该字段做路由——不猜语义、不用关键词、不跑分类器。图片护栏是能力规则而非难度猜测：排在档位标签和长文本之上，`imageModel: flash` 时带图请求无论档位多高、上下文多长都到多模态模型（看不见图的模型绝不能收到图）。所有层都不匹配的请求落到 `settings.defaultModel`（默认 flash——cost-first）。

L4 是后果检查点，不是难度猜测：代码刚被改写的**下一轮**是审查轮（验证、继续、交代），送 pro——flash 起草，pro 审查。信号取尾部并行工具批次，其中任何一个编辑类调用（`Edit`/`Write`/`NotebookEdit`/`apply_patch` 等，大小写不敏感；shell 包装的调用按命令文本分类）都会标记该批次。它排在 L3 之下是刻意的：超过 `longContextThreshold` 的会话留在 pro，flash→pro 的长上下文跨越保持单向。

所有按工具路由的规则（含 L1 的 `webSearch`）统一放在 `settings.toolRouting`（`webSearch`/`edit`），serve 横幅用一行 `tool -> ...` 打印生效的映射。

回退队列叠在流水线之下、不在流水线之内：`resolve()` 定档位，然后该档位的队列——`[主选, backups...]`，零配置时一条隐式跨档跳转（flash→pro、pro→flash）——在主选中途挂掉时（429/配额、5xx、网络错误；仅限流式开始前，SSE 无法撤回）决定谁来服务这个请求。回退不重跑路由决策；每一跳都体现在 label 里。详见 routing.json 一节的「回退队列」。

## 图片桥接

经典组合就是 step-glm-mm 模板：**GLM coding plan 上的 glm-5.3 是强悍的纯文本旗舰——没有眼睛；StepFun 的 step-3.7-flash 看得见。** 把两者配进同一个 profile——`destinations.pro = "glm,glm-5.3"`、`destinations.flash = "stepfun,step-3.7-flash"`，再加 `imageModel: "flash"`、`defaultModel: "pro"`、`imageBridge: true`——glm-5.3 包揽所有文字工作，step-3.7-flash 负责看：带图请求走多模态 flash（L1 能力守卫），后续文字轮次带着 flash 的图片转写回到 glm-5.3。一个端口、双协议——一个长着眼睛的旗舰。

`settings.imageBridge: true`（opt-in，step-glm-mm 模版已开启）给纯文本的 pro 模型一双二手的眼睛：当请求里的图片只存在于*历史*——最后一条消息是纯文本——awerouter 先让多模态的 flash（`imageModel`）把每张不同的图片转写一次，再把图片块替换成转写文本后照常路由，请求回到正常管线——典型情况落到 `defaultModel`（pro）——而不是把会话永远钉死在 flash 上。本轮新传的图片照旧原生路由给 flash；`/v1/messages/count_tokens` 看到的是同样的改写后请求体，估算与实发一致。转写按图片内容缓存在进程内存（重启后每张图重新转写一次）；任何一次转写失败，请求体保持原样，L1 图片护栏照旧路由。codex 订阅登录跳过桥接（其 SSE-only 后端无法服务非流式转写调用）。

`imageBridge` 和其他 settings 键一样，也可以只写在某个 profile 体内——当只有个别 profile 拥有多模态 `imageModel` 时就应该这么用（全局开启会让每个 profile 都用自己的 `imageModel` 去转写：纯文本的 imageModel 每次转写都会失败再回退，白白多付一次上游调用）。每张不同的图片多付一次 flash 调用（转写输出上限 2048 token）；第一个桥接轮承担延迟，之后命中缓存。serve 横幅会打印 `image bridge -> on (...)`，注明负责转写的 destination。

## Token Saver（RTK + ODCP 省流）

> **⚠️ 试验性功能——所以默认关闭。** 两层都是有损的。RTK 压缩：超长文件读取只保留头尾 + 函数签名等骨架行（截断标记会注明 offset，模型可据此重读中段）；grep 每文件最多保留 10 条命中；diff 有行数上限；格式识别是启发式的，偶尔会在特殊内容上误判。ODCP 裁剪则是整段删除：被去重的旧输出直接消失，不是截断。模型通常会察觉并重读，代价是多一轮。如果发现 agent 行为异常（反复重读同一批文件、漏掉细节），关掉对应层，或对该会话发送 `X-Awerouter-Token-Saver: off`。实际省了多少可用 `awerouter usage log` 查看。

编码 agent 每轮都重发全部对话历史，其中大头是工具输出——git diff、grep 命中、目录列表、构建日志。profile 可以开启两个互补的省流层，都在路由和转发之前生效：**RTK** 原位压缩每个工具输出的文本，**ODCP** 整段删掉被取代的内容（[RTK](#rtk-压缩)、[ODCP](#odcp-裁剪去重--清错误)）：

```json
"cc-router-1": {
  "protocol": "anthropic",
  "longContextThreshold": 8000,
  "rtk": true,
  "destinations": { "flash": "stepfun,step-3.7-flash", "pro": "anthropic,claude-opus-5" }
}
```

### RTK 压缩

- **只动 tool result：** 仅压缩 `tool_result` / tool 消息内容，绝不碰用户 prompt 和模型回复。规则式 filter（git diff/status/log、grep、find、tree、ls、构建输出等）自动识别格式并压缩；无法识别的内容、500 字符以下的短输出、错误结果（`is_error`）原样放行。
- **fail-open：** 任何失败（异常、filter 报错）都保持 body 原样，绝不会弄坏请求。注意这只防崩溃，不防启发式误判（见上面的警告）。
- **确定性：** 同样的历史每轮压出同样的字节，provider 的 prompt cache prefix 不会失效。
- **请求级逃生口：** 发送 `X-Awerouter-Token-Saver: off` 可让单个请求不压缩转发（排障、需要完整 diff/日志时用）。
- 压缩发生在路由之前，`/v1/messages/count_tokens` 同样压缩，因此 L3 决策和用量日志与实际计费一致。开启 RTK 后建议重跑 `usage calibrate`——按未压缩流量校准的阈值会过多地触发 pro（`"auto"` 在窗口期后自愈）。

压缩算法设计源自 [rtk](https://github.com/rtk-ai/rtk)（Apache 2.0）及 [9router](https://github.com/decolua/9router) 的 JS 移植版（MIT），本模块为 Python 从零重构版；请求日志会记录每个请求估算省下的 token。

### ODCP 裁剪（去重 + 清错误）

RTK 压缩每个工具输出的大小；ODCP 删整个不再值那些 token 的内容。对历史做一遍配对遍历，套两条规则：

- **去重（dedup）**——同样的工具、同样的参数调用多次时，只保留最新一次的输出，旧的那次换成一行占位符。重读同一个文件、重跑同一个命令，从 N 份完整输出变成一行。
- **清错误（purgeErrors）**——失败的工具调用过了 `purgeErrors.turns` 轮用户消息（默认 4）后，其输入字符串换成占位符；错误信息本身保留，模型仍然知道哪里失败过。仅对 anthropic 生效——其他协议在线上不带错误标记。

```json
"cc-router-1": {
  "protocol": "anthropic",
  "longContextThreshold": 8000,
  "odcp": true,
  "destinations": { "flash": "stepfun,step-3.7-flash", "pro": "anthropic,claude-opus-5" }
}
```

`"odcp": true` 两条规则全开、用默认值；对象写法可细调——`"odcp": {"dedup": false, "purgeErrors": {"turns": 2}}`。护栏：

- 编辑类工具（Edit/Write/apply_patch/…）和 task/规划类工具绝不改写；替换后不会变小的跳过；最后一轮永远不动（去重留最新，清错误要求足够老）。
- 与 RTK 相同的 fail-open 契约和 `X-Awerouter-Token-Saver: off` 逃生口。ODCP 在 RTK **之前**运行：被删的内容不会到压缩器手里，剩下的照常压缩。
- 与 RTK 不同，裁剪在设计上不做跨轮的 prompt-cache 前缀稳定：新重复出现时会改写更早的消息（原文本变占位符），错误到达年龄阈值时会改写一次。每次事件用一次性 cache miss 换省下的 token，这正是这个功能存在的取舍。

行为参考 [Opencode-DCP](https://github.com/Opencode-DCP/opencode-dynamic-context-pruning) 的公开文档（AGPL-3.0）；本模块是面向 awerouter 三种协议的独立实现，非其源码翻译。节省量按请求记录（`odcp_saved`），`awerouter usage` 与 rtk 并列展示。

## 后台运行与热加载

`awerouter serve run <profile> -d` 让单 profile daemon 脱离终端常驻运行——终端关掉也不停，输出追加到 `~/.local/state/awerouter/serve-<profile>.log`（每个 profile 一个文件）。`awerouter serve all -d` 同样后台运行网关，日志为 `serve-gateway.log`，用 `awerouter serve stop gateway` 停止。两种命令都会等 daemon 绑定端口后打印 pid、端口和日志路径；已经在跑时会提示（仍然照常再起一个实例）。

`--install`（代替或配合 `-d`）再进一步：把 daemon 装成**常驻系统服务**——macOS 是 launchd 代理（`~/Library/LaunchAgents/com.awerouter.serve.<profile>.plist`），Linux 是 systemd 用户服务（`~/.config/systemd/user/awerouter-<profile>.service`）。开机登录自动启动（重启电脑不用重新运行），进程崩溃自动拉活。安装前会先停掉同 profile 的现有实例（一个端口一个属主）。服务管理器启动的 daemon 没有你的 shell 环境，而 `${VAR}` 认证在请求时才从环境展开——所以安装时会把 providers 引用的 `${VAR}` 当前值（连同 `AWEROUTER_*` 覆盖和代理变量）烘焙进服务文件（权限 0600，含密钥）；目标 profile 需要的变量在当前 shell 没有值时安装直接报错。改了这些变量后重跑一次 `--install` 即可刷新（host/port/env 的更新也是重跑 `--install`）。

每个 serve 实例——无论前台还是后台——绑定端口时都会在 `~/.local/state/awerouter/run/` 注册自己，因此一条命令就能看到全部实例：

```bash
awerouter serve status           # profile、fg/bg/svc、pid、host:port、协议、运行时长
awerouter serve stop [PROFILE]   # SIGTERM 全部实例，或只停某个 profile 的（优雅退出）
```

常驻实例显示为 `svc:launchd` / `svc:systemd`。`serve stop` 对它们走服务管理器停止（直接 SIGTERM 会被重启策略立刻拉回来）——停的是当次，下次登录还会自启；`awerouter serve stop [PROFILE] --purge` 连服务文件一起删掉，从此不再自启。已安装但当前没在跑的服务也会列在 `serve status` 里。注册文件按 pid 命名；进程已不存在的条目会自动清理。`serve stop` 拒绝向命令行已不像 awerouter 的 pid 发信号（进程被 -9 杀死后 pid 被复用的保护）。`-d`/`--install`/`serve stop` 仅支持 POSIX 系统。

serve 还会监听 `routing.json` 和 `providers.json` 的修改（每秒轮询 mtime）并热加载：destinations、阈值、tool routing、settings 覆盖、provider 条目——包括整体换掉 profile 的 provider——都无需重启、下一条请求即生效。文件加载失败（保存到一半、JSON 写坏）只提示一次，上一份可用配置继续服务，直到文件重新可解析；唯一不能热加载的是监听端口——改了 `port` 字段，serve 会打印需要重启的提示而不是重绑。

## 命令

```bash
awerouter init [TEMPLATE]             # 从内置模板创建配置（default / step-glm / glm-codex / step-glm-mm）；--merge 把模板补进已有配置
awerouter add                         # 交互式添加 profile（先选类别再选 provider）
awerouter list                        # 列出 profile（名字、协议、端口、flash、pro、阈值）
awerouter serve run [PROFILE] [--port N] [--host 127.0.0.1] [-d] [--install]  # 单 profile；端口：--port > profile 'port' > 20128
awerouter serve all [--port N] [--host 127.0.0.1] [-d] [--install]            # 网关：所有 profile 共用一个端口，模型名为 <profile>/auto|flash|pro
awerouter serve status                # 查看运行中的 serve 实例（前台 + 后台 + 常驻服务）
awerouter serve stop [PROFILE] [--purge]  # 停止全部运行实例，或只停某个 profile 的；--purge 连常驻服务一起移除
awerouter <PROFILE>                   # serve run 的简写（同样支持 -d）
awerouter self-update [--check]        # 升级到最新 PyPI 版本（--check：只看版本不升级）
awerouter config path                 # 打印两个配置文件路径
awerouter config show [PROFILE]       # 脱敏全量配置；带 PROFILE 只看它的 provider 和条目
awerouter config edit [providers|routing]  # 在 $EDITOR 中打开某个文件（先备份 .bak）
awerouter config login [claude|codex]      # 登录订阅账号（claude：浏览器 PKCE 授权）
awerouter config logout [claude|codex]     # 删除已保存的订阅登录
awerouter config restore [providers|routing]  # 从 .bak 备份恢复配置文件
awerouter usage stats
awerouter usage clean                 # 删除已保存的请求日志（需确认）
awerouter usage log [--lines 20] [--all] [--tokens]
awerouter usage tokens
awerouter usage calibrate
awerouter usage savings
```

所有 `usage` 子命令读的是同一份请求日志。`log`、`stats`、`tokens`、`calibrate` 和 `savings` 直接接受 `--since`（`today`、`yesterday`、`7d` 或 `YYYY-MM-DD`，本地时间）和 `--profile`——例如 `awerouter usage stats --since today --profile cc-1`；`clean` 删除全部日志，不接受窗口选项。

`usage stats` 按 profile 汇总：label/destination/provider/model 分组（带百分比）、错误与降级计数、各 destination/provider/model 的延迟分位数（首字节与总时长）、估算请求 token（全部请求内容：messages、system prompt、工具定义与工具 I/O）。`--since` 接受 `today`、`yesterday`、`7d` 或 `YYYY-MM-DD`（本地时间）；`--profile` 只看单个 profile。`usage clean` 确认后删除已保存的日志（`requests.jsonl` 及轮转备份）。`usage log` 原样显示条目——默认最后 20 条，加 `--all` 显示全部；codex 登录重读重试过的请求带 `401-retry` 标记；`--tokens` 把 status/延迟/入站 model 三列换成每条请求的分类型 token 明细（`msg/sys/tools/results/calls/think`），分类型计数之前记录的条目只显示总数。

`config edit` 和 `add` 向导在每次写入前把目标文件快照为 `<名称>.json.bak`；`awerouter config restore [providers|routing]` 确认后把备份拷回并校验恢复后的配置。`config path` 打印两个配置文件路径；`config show [PROFILE]` 显示脱敏全量配置，或单个 profile 用到的 provider 与路由条目。移动前的顶层拼法（`awerouter login` / `logout` / `restore`）作为隐藏别名继续可用。

`self-update` 升级已安装的包——pipx 安装走 `pipx upgrade awerouter`，其余走 `pip install --upgrade`；升级后需重启运行中的 serve。每条命令还会在后台线程检查 PyPI（至多一天一次，缓存在配置目录的 `update-check.json`），发现新版本时在命令结束后输出一行提醒——提醒同样一天至多一次；`AWEROUTER_NO_UPDATE_CHECK=1` 可完全关闭检查。serve 启动横幅也会基于缓存检查结果显示更新提示。

`usage calibrate` 展示 L3 流量（受阈值影响的层）的请求 token 分布（统计全部请求内容：messages、system prompt、工具定义与工具 I/O），并在 p90/p95/p99 处建议 `longContextThreshold` 候选值，末尾给出按 `settings.longContextAuto` 策略 `"auto"` 会选的值。跑一段真实流量后执行，再编辑 `routing.json`，或把 profile 改成 `"auto"` 交给 serve 每次启动时自动校准。

`usage tokens` 按内容类型（messages、system prompt、工具定义、工具结果、工具调用参数、thinking）汇总输入 token 的总量与占比——可以看出请求 token 里多少是环境常量（system prompt + 工具定义）、多少是真实对话。

`usage savings` 是 token 记账视图：各档消化了多少输入请求 token、相对「全部直连 pro」的基线卸载了多少 pro 输入 token。cache sensitivity 小节给出卸载量的上下界（Anthropic 体系按缓存读 ~0.1×、写 ~1.25×、TTL 5 分钟折算），并展示你的换档节奏与 TTL 的关系——pro-only 基线若缓存常热，那些 token 本会按缓存读价计费。输出末尾给出代入式金额公式（token 数为实测值）——把你的输入单价（每百万 token）代入 pro/flash 即可直接算出节省金额（输出 token、flash 侧缓存、能力错配导致的额外轮次均未建模）。

## 故障排查

**CC 启动后立刻报 `502 status code (no body)`** —— shell 代理（Clash 等）劫持了回环流量。发往 `127.0.0.1:20128` 的请求被送进代理，而代理的 `127.0.0.1` 是它自己，端口上没人监听，代理就返回空的 502。`serve` 检测到这种情况会在启动时打印警告；在 shell 配置里豁免回环地址即可：

```bash
export no_proxy=127.0.0.1,localhost NO_PROXY=127.0.0.1,localhost
```

然后开新终端、重新启动 CC。

## 开发

```bash
git clone https://github.com/wehuman01/awerouter
cd awerouter
pip install -e ".[dev]"
pytest
```

架构说明、配置语义和发布流程见 [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)。

## 赞助与支持

如果 awerouter 帮你省了钱，欢迎支持一下：

- ⭐ 给项目点个 Star — 让更多人看到它。
- ☕ [Ko-fi](https://ko-fi.com/mugpeng) — 请我喝杯咖啡。
- 💬 微信 — 扫描下方收款码。

<p align="center">
  <img src="assets/images/wechat-pay.jpg" alt="微信收款码" width="240">
</p>

> awerouter 是免费开源的，你的支持让它持续维护下去 — 谢谢。

## Awesome 软件生态

awerouter 是一个不断壮大的 "awesome" 工具家族中的一员 — 围绕 AI 编程 agent 打造，local-first、可被 agent 直接操作。

### CLI 工具

- **[aweskill](https://aweskill.webioinfo.top/)** — CLI 优先的技能包管理器，支持 47+ AI 编程 agent。
- **[aweswitch](https://github.com/Webioinfo01/aweswitch)** — Claude Code、Codex、OpenCode 的 agent 配置切换器。
- **[awerouter](https://github.com/wehuman01/awerouter)** — 智能路由器，用结构信号把请求分给 Flash 或 Pro 模型，减少不必要的模型开销。
- **[aweshelf](https://github.com/Webioinfo01/aweshelf)** — 收藏、分类、恢复 AI 编程会话，还能搭配 aweswitch 实现保存配置，一键启动。
- **[aweshare](https://github.com/wehuman01/aweshare)** — 通过自建 Hub 共享本地 Ollama/vLLM，或国产厂商 coding plan，或已授权的 OpenAI/Anthropic 帐号订阅，实现 token 的共享经济。
- **[awewarm](https://github.com/wehuman01/awewarm)** — 订阅窗口保持器，让 AI 编程套餐的窗口持续激活，无论是本地设置，还是通过远程连接的服务器。
- **[awescholar](https://github.com/Webioinfo01/awescholar)** — AI agent 可自主执行的科学文献发现与策展，搜索、标注、筛选和报告学术论文。

### 桌面应用

- **[awedot](https://awedot.wehuman.top/)** — 悬浮球驻留屏幕边缘，实时追踪当前 AI 会话；一键收藏、随时恢复，并可搭配 aweswitch 固定 agent 配置（比如用 GLM 模型启动）。

### Project Collections

- **[Awesome AI Meets Biology](https://github.com/Webioinfo01/Awesome-AI-Meets-Biology)** — AI 在生物学、生物信息学和生物医学研究中应用的精选综述。由 awescholar 驱动。
- **[Awesome AI Virtual Tumor](https://github.com/Webioinfo01/Awesome-AI-Virtual-Tumor)** — 面向虚拟肿瘤建模与仿真的前沿 AI 系统精选合集：静态模型、动态模型、agent、基准与综述。
