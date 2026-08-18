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
    <img src="https://img.shields.io/github/stars/mugpeng/awerouter?style=flat-square" alt="Stars">
  </p>
</div>

> 按结构信号把编码 agent 流量拆分到不同 provider，省钱不降质。同协议透传，不做协议转换。可选的 profile 级 tool-result 压缩（RTK，默认关闭）。

## 支持工具

awerouter 与两个配套工具配合最佳：

- **[aweskill](https://aweskill.webioinfo.top/)** — 面向 AI agent 的 CLI skill 包管理器。安装 awerouter skill，让你的 agent 用自然语言管理路由。
- **[aweswitch](https://github.com/Webioinfo01/aweswitch)** — Agent profile 切换器。用指向 awerouter daemon 的 profile 启动 Claude Code、Codex 或 OpenCode 会话。

aweskill 通过管理skills，让 agent **管理**路由；aweswitch 让你**启动**走路由的会话。配置一次 awerouter，之后就能用 `aweswitch <profile>` 把任意 agent 启动到它上面。

## 安装与使用

### 让 AI agent 安装和配置

如果你在 Claude Code、Codex、Cursor 等 coding agent 中工作，直接告诉它：

```text
Read https://github.com/mugpeng/awerouter/blob/main/README.ai.md and follow it to install and configure awerouter.
```

Agent 会安装 CLI、初始化配置、帮你添加 profile，并通过 [aweskill](https://aweskill.webioinfo.top/) 安装 awerouter skill，用于后续路由管理。

**配置完成后你可以这样告诉 agent：**

> "加一个 stepfun 的 flash provider 和一个 pro profile。"
> "列出我的 awerouter profile。"
> "根据 usage 帮我调一下 longContextThreshold。"
> "解释一下我的 usage savings。"

Agent 可以直接运行只读命令（`list`、`config show`、`usage stats`、`usage calibrate`、`usage savings`）并编辑配置，但**不会**运行 `awerouter serve`（常驻 daemon）、`awerouter add`（交互式向导）、`awerouter restore`（覆盖配置文件）、`awerouter usage clean`（删除日志）或 `awerouter self-update`（升级安装）。要启动 daemon，请在你自己的终端运行：

```bash
awerouter serve cc-router-1
```

#### awerouter skill

通过 [aweskill](https://aweskill.webioinfo.top/) 安装 [awerouter skill](https://github.com/mugpeng/awerouter/blob/main/resources/skills/awerouter/SKILL.md)，可以让 AI agent 用自然语言管理路由：

- 列出、查看、添加、编辑路由 profile
- 分别编辑 `providers.json`（端点/密钥）和 `routing.json`（策略）
- 读取 `usage stats` / `usage calibrate` / `usage savings` 并给出阈值调整建议
- 引导配置 `${ENV_VAR}` 引用所需的环境变量

安装后你可以直接告诉 agent："给 openai-chat 分组加一个 GLM provider"、"把 longContextThreshold 调到 12000"、"看看我的 web_search 流量走哪个 provider"，agent 会读取配置、做修改、用 `awerouter config show` / `awerouter list` 验证。

#### 通过 aweswitch 启动

awerouter 配置好后，用一个指向 daemon 的 aweswitch profile，就能启动走智能路由的编码 agent。

**示例：通过 awerouter 启动 OpenCode**

先在一个终端用 openai-chat profile 启动 daemon：

```bash
awerouter serve oc-router-1
```

然后在 aweswitch 配置里加一个指向它的 OpenCode profile：

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

`OPENCODE_MODEL` 设为 `auto` 时，awerouter 按结构信号逐请求路由——上游 provider 收到的是 `routing.json` destinations 里配置的实际 model id，而不是 `auto`。Claude Code 同理，用一个 `anthropic` profile（`ANTHROPIC_MODEL=auto`）即可。

### 手动安装和使用

从 PyPI 安装：

```bash
pip install awerouter
```

快速开始：

```bash
# 1. 初始化配置（生成 ~/.config/awerouter/{providers,routing}.json）
awerouter init

# 2. 交互式添加 profile（自动写入两个文件，保证引用一致）
awerouter add
#    或者手改：编辑 providers.json 填密钥（${ENV_VAR}），编辑 routing.json 映射 flash/pro

# 3. 启动 daemon（只有一个 profile 时名字可省）
awerouter serve [cc-router-1]     # 等价简写：awerouter cc-router-1

# 4. 让 CC 指向它 —— serve 启动横幅会直接打印下面这两行
export ANTHROPIC_BASE_URL=http://127.0.0.1:20128
# aweswitch profile 环境变量：ANTHROPIC_MODEL=auto, _HAIKU_=flash, _OPUS_=pro
```

## 配置

`~/.config/awerouter/` 下两个文件（`AWEROUTER_CONFIG_DIR` 环境变量覆盖目录）：

**providers.json** — 端点 + 密钥，按线上协议分组（`config show` 自动脱敏）：

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

支持三种协议。`base_url` 沿用各原生客户端的写法——从客户端配置里原样抄过来即可，awerouter 按原生客户端同样的规则拼接端点路径：

| 协议 id | `base_url` 写法 | 端点 |
|---------|----------------|------|
| `anthropic` | `ANTHROPIC_BASE_URL` 风格（不带 `/v1`） | `base_url + /v1/messages` |
| `openai-chat` | `OPENAI_BASE_URL` 风格（含版本段） | `base_url + /chat/completions` |
| `openai-responses` | `OPENAI_BASE_URL` 风格（含版本段） | `base_url + /responses` |

同一家 provider 的两个协议路径往往不同——比如 GLM：chat completions 是 `https://open.bigmodel.cn/api/coding/paas/v4`，responses 是 `https://open.bigmodel.cn/api/v1`。所以每个协议分组各配各的 `base_url`。

鉴权头**根据 `base_url` 自动判断**：`anthropic.com` → `x-api-key`（裸 token）；其他 → `Authorization`（自动补 `Bearer `）。除非启发式判断错了，否则不需要填 `auth_header`。

**routing.json** — 路由策略，不含密钥（可以进 git）：

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
    }
  }
}
```

`settings` 可省（默认 `flash`/`pro`）。它定义 CC 发送的档位 model id：background（Haiku 档）、think（Opus 档）；所有按工具路由的规则（含 L1 的 `webSearch`）统一放在 `settings.toolRouting`——旧顶层 `webSearchModel` 仍作为兜底兼容。主循环用 `auto`——由 L3 按难度路由。在 aweswitch profile 里设：`ANTHROPIC_DEFAULT_HAIKU_MODEL=flash`、`ANTHROPIC_MODEL=auto`、`ANTHROPIC_DEFAULT_OPUS_MODEL=pro`。

`longContextThreshold` 可以是整数，也可以写 `"auto"`：每次 `serve` 启动时，awerouter 取该 profile 自己最近 `windowDays` 天 L3 有效 token 分布的 `percentile` 分位值作为阈值。窗口内 L3 请求数不足 `minSamples`（新 profile、流量清淡）时改用 `fallbackThreshold`。四个参数都在 `settings.longContextAuto` 里，全部可选——横幅每次都会打印选了什么、依据是什么。注意：分位值决定的是 flash/pro 的*分配比例*，不代表 flash 的能力上限——如果你的 flash 模型在超长上下文上明显退化，请继续用固定阈值。

密钥用 `${ENV_VAR}` 引用。缺失的环境变量在启动时报错退出。

> **基于 profile 的路由：** `routing.json` 用 profile id 分组（类似 aweswitch）。`awerouter serve <profile>` 启动其中一个；只有一个 profile 时自动选择。`protocol` 字段把 profile 映射到 providers.json 的分组，并决定它服务哪个端点——serve 横幅按协议打印对应客户端的环境变量（anthropic → Claude Code 的 `ANTHROPIC_BASE_URL`；openai 协议 → `OPENAI_BASE_URL` / Codex `wire_api`）。注意：openai 客户端是单 model 配置，L2 档位匹配基本不触发——openai 流量走 L1 + L3，默认 flash。

> **端口分配：** 可选的 `port` 字段为 profile 固定监听端口（`awerouter list` 会显示）；优先级：`--port` 参数 > profile `port` > 默认 20128。显式指定的端口被占用时直接报错退出——客户端写死了它，不能悄悄漂移。不配置端口时，serve 从 20128 起向上找第一个空闲端口：第一个实例拿 20128，下一个 20129，依次顺延——按启动顺序分配，与 profile 无关。一次只跑一个实例的热切换用法：profile 不配端口、客户端固定指向 20128 即可。

## 路由逻辑

first-match-wins 管线，逐请求评估：

| 层 | 信号 | 决策 |
|----|------|------|
| L1 能力护栏 | body 含 `web_search` 工具 | `toolRouting.webSearch`（默认 **pro**；旧顶层 `webSearchModel` 仍兼容） |
| L2 档位匹配 | `model == c1/flash` 或 `c1/think` | flash / pro |
| L3 难度评分 | token（全部请求内容）超阈值，或含图片 | **pro**；否则继续 |
| L4 编辑检查点 | 尾部工具批次改写了代码（`edit`/`write`/`apply_patch` 等） | `toolRouting.edit`（默认 **pro**，`null` 关闭） |

CC 的 `/model` 选择器设置 tier model id（c1/flash / c1/pro / c1/think）。awerouter 直接读取该字段做路由——不猜语义、不用关键词、不跑分类器。

L4 是后果检查点，不是难度猜测。结构信号看不到*决定*编辑的那一轮——那一轮按它之前的信号路由——但代码刚被改写的**下一轮**是审查轮（验证、继续、交代），所以送 pro：flash 起草，pro 审查。信号取**尾部并行批次**：其中任何一个编辑类调用都会标记该批次（`[Grep, Edit]` 和 `[Edit, Grep]` 结果一致）。shell 包装的调用（codex 的 `exec_command`/`shell`）按命令文本分类——`apply_patch` 算编辑。它排在 L3 之下是刻意的——已超过 `longContextThreshold` 的会话无论刚跑了什么工具都留在 pro，flash 不会拿到可能退化的超长上下文，长上下文这一跨越也保持 flash→pro 单向（阈值以下：编辑检查点轮次走 pro，其余轮次回落 flash）。编辑类覆盖 `Edit`/`Write`/`NotebookEdit`/`apply_patch`/`replace_in_file` 等，大小写不敏感匹配。早期版本在这里还把搜索/机械阶段路由到 flash；由于 flash 本来就是兜底默认，这些规则不改变任何行为，v0.4.8 已移除。

所有按工具路由的规则（含 L1 的 `webSearch`）统一放在 `settings.toolRouting`（`webSearch`/`edit`），serve 横幅用一行 `tool -> ...` 打印生效的映射。

## Token Saver（RTK 省流）

> **⚠️ 试验性功能——所以默认关闭。** 压缩是有损的：超长文件读取只保留头尾 + 函数签名等骨架行（截断标记会注明 offset,模型可据此重读中段）;grep 每文件最多保留 10 条命中;diff 有行数上限。格式识别是启发式的，偶尔会在特殊内容上误判造成信息损失——模型通常会察觉并重读，代价是多一轮。如果发现 agent 行为异常(反复重读同一批文件、漏掉细节),关掉 RTK 或对该会话发送 `X-Awerouter-Token-Saver: off`。实际省了多少可用 `awerouter usage log` 查看。

编码 agent 每轮都重发全部对话历史，其中大头是工具输出——git diff、grep 命中、目录列表、构建日志。profile 可以开启 RTK 压缩，在路由和转发之前原位改写这些文本：

```json
"cc-router-1": {
  "protocol": "anthropic",
  "longContextThreshold": 8000,
  "rtk": true,
  "destinations": { "flash": "stepfun,step-3.7-flash", "pro": "anthropic,claude-opus-5" }
}
```

- **只动 tool result：** 仅压缩 `tool_result` / tool 消息内容，绝不碰用户 prompt 和模型回复。规则式 filter（git diff/status/log、grep、find、tree、ls、构建输出等）自动识别格式并压缩；无法识别的内容、500 字符以下的短输出、错误结果（`is_error`）原样放行。
- **fail-open：** 任何失败（异常、filter 报错）都保持 body 原样，绝不会弄坏请求。注意这只防崩溃，不防启发式误判（见上面的警告）。
- **确定性：** 同样的历史每轮压出同样的字节，provider 的 prompt cache prefix 不会失效。
- **请求级逃生口：** 发送 `X-Awerouter-Token-Saver: off` 可让单个请求不压缩转发（排障、需要完整 diff/日志时用）。
- 压缩发生在路由之前，`/v1/messages/count_tokens` 同样压缩，因此 L3 决策和用量日志与实际计费一致。开启 RTK 后建议重跑 `usage calibrate`——按未压缩流量校准的阈值会过多地触发 pro（`"auto"` 在窗口期后自愈）。

压缩算法设计源自 [rtk](https://github.com/rtk-ai/rtk)（Apache 2.0）及 9router 的 JS 移植版（MIT），本模块为 Python 从零重构版；请求日志会记录每个请求估算省下的 token。

## 命令

```bash
awerouter init                        # 从模板创建默认配置
awerouter add                         # 交互式添加 profile（先选类别再选 provider）
awerouter list                        # 列出 profile（名字、协议、端口、flash、pro、阈值）
awerouter serve [PROFILE] [--port N] [--host 127.0.0.1]  # 端口优先级：--port > profile 'port' > 20128
awerouter <PROFILE>                   # serve 的简写
awerouter restore [providers|routing] # 从 .bak 备份恢复配置文件
awerouter self-update [--check]        # 升级到最新 PyPI 版本（--check：只看版本不升级）
awerouter config path                 # 打印两个配置文件路径
awerouter config show [PROFILE]       # 脱敏全量配置；带 PROFILE 只看它的 provider 和条目
awerouter config edit [providers|routing]  # 在 $EDITOR 中打开某个文件（先备份 .bak）
awerouter usage stats
awerouter usage clean                 # 删除已保存的请求日志（需确认）
awerouter usage log [--lines 20] [--all] [--tokens]
awerouter usage tokens
awerouter usage calibrate
awerouter usage savings
```

所有 `usage` 子命令读的是同一份请求日志。`log`、`stats`、`tokens`、`calibrate` 和 `savings` 直接接受 `--since`（`today`、`yesterday`、`7d` 或 `YYYY-MM-DD`，本地时间）和 `--profile`——例如 `awerouter usage stats --since today --profile cc-1`；`clean` 删除全部日志，不接受窗口选项。

`usage stats` 按 profile 汇总：label/destination/provider/model 分组（带百分比）、错误与降级计数、各 destination/provider/model 的延迟分位数（首字节与总时长）、估算请求 token（全部请求内容：messages、system prompt、工具定义与工具 I/O）。`--since` 接受 `today`、`yesterday`、`7d` 或 `YYYY-MM-DD`（本地时间）；`--profile` 只看单个 profile。`usage clean` 确认后删除已保存的日志（`requests.jsonl` 及轮转备份）。`usage log` 原样显示条目——默认最后 20 条，加 `--all` 显示全部；`--tokens` 把 status/延迟/入站 model 三列换成每条请求的分类型 token 明细（`msg/sys/tools/results/calls/think`），分类型计数之前记录的条目只显示总数。

`config edit` 和 `add` 向导在每次写入前把目标文件快照为 `<名称>.json.bak`；`awerouter restore [providers|routing]` 确认后把备份拷回并校验恢复后的配置。`config path` 打印两个配置文件路径；`config show [PROFILE]` 显示脱敏全量配置，或单个 profile 用到的 provider 与路由条目。

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
git clone https://github.com/mugpeng/awerouter
cd awerouter
pip install -e ".[dev]"
pytest
```

架构说明、配置语义和发布流程见 [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)。

## 支持一下

如果 awerouter 帮你省了钱，欢迎支持一下：

- ⭐ 给项目点个 Star — 让更多人看到它。
- ☕ [Ko-fi](https://ko-fi.com/mugpeng) — 请我喝杯咖啡。
- 💬 微信 — 扫描下方收款码。

<p align="center">
  <img src="assets/images/wechat-pay.jpg" alt="微信收款码" width="240">
</p>

> awerouter 是免费开源的，你的支持让它持续维护下去 — 谢谢。
