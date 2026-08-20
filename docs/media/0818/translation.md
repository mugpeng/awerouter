# awerouter 更新: 数据看板告诉你省了多少

![awerouter](../../../logo/logo.png)

awerouter 是一个智能大语言模型（LLM）路由器，位于你的编程智能体（coding agent）与上游提供商之间。每个请求首先到达路由器。它依据结构——而非猜测——决定将其发送到廉价的轻量模型（flash model），还是能力更强的旗舰模型（pro model）。这一决策瞬间完成，响应不经修改地直接透传，路由过程对你完全透明。

但解决路由只做了一半。另一半是知道路由到底好不好用：你的请求流量中有多少真正流向了轻量模型？`longContextThreshold` 是否过于激进？你是在省钱，还是只是在增加延迟？awerouter 随附一套用量分析工具，可以回答这些问题——还有一套技能体系，让智能体替你回答。

GitHub：[github.com/mugpeng/awerouter](https://github.com/mugpeng/awerouter)

## 技能：让智能体替你管理路由器

awerouter 的设计理念是由智能体来操作。你只需说出想要什么，智能体会自行找出对应的命令行接口（CLI）命令。这座桥梁就是 awerouter 技能——一个轻薄的过程层，将自然语言映射为 awerouter CLI 命令。

### 通过 aweskill 安装

[aweskill](https://aweskill.webioinfo.top/) 是一个以 CLI 为核心的技能包管理器，支持 47 款以上的 AI 编程智能体。通过它安装 awerouter 技能只需一条命令——而且智能体可以自主完成安装。

```bash
npm install -g aweskill
aweskill init
aweskill install mugpeng/awerouter
aweskill agent add skill awerouter --global --agent claude-code
```

将 `claude-code` 替换为你的智能体 ID：`codex`、`opencode`、`cursor`、`gemini-cli`、`windsurf`，或任何其他受支持的智能体。智能体可以通过运行 `aweskill agent supported` 自动查找受支持的 ID。

### 直接安装（不使用 aweskill）

如果没有 Node.js 环境，或更偏好直接安装，只需将技能文件复制到智能体的技能目录即可：

```bash
mkdir -p ~/.claude/skills/awerouter/
curl -fsSL https://raw.githubusercontent.com/mugpeng/awerouter/main/resources/skills/awerouter/SKILL.md -o ~/.claude/skills/awerouter/SKILL.md
```

### 技能能做什么

该技能将自然语言转换为 CLI 命令：

| 你说 | 技能执行 |
|---|---|
| "列出我的 awerouter 路由方案。" | `awerouter list` |
| "给我看看 cc-router-1。" | `awerouter config show cc-router-1` |
| "为 openai-chat 添加一个 GLM 提供商。" | 编辑 `~/.config/awerouter/providers.json` |
| "把轻量模型目标改成 glm-4-flash。" | 编辑 `routing.json`，并用 `awerouter config show` 校验 |
| "昨天的路由情况怎么样？" | `awerouter usage stats` |
| "我的 longContextThreshold 应该设为多少？" | `awerouter usage calibrate` |
| "我省了多少钱？" | `awerouter usage savings` |

智能体永远不会运行 `awerouter serve`。守护进程由你在自己的终端中启动。技能涵盖配置、检查与调优——唯独不包括代理服务本身。

## 用量分析：五条必知命令

awerouter 将所有经过代理的请求写入 `~/.local/state/awerouter/requests.jsonl` 的 JSONL 日志中。五条只读命令让你检查这些日志。每条命令都接受 `--since`（按时间窗口过滤）和 `--profile`（按路由方案过滤）参数。你完全可以调用aweskill 替你装好的awerouter 技能，直接在agent 里去解读这些数值。

### `usage stats`——路由总览

总览全局：总请求数、错误率、降级次数，以及按标签、智能体、目标、提供商、模型拆分的各方案明细——附带延迟百分位。

```bash
awerouter usage stats --since 7d
```

输出：

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

这就是你的总览仪表盘。每天刷一眼。

### `usage calibrate`——阈值校准

展示 L3 流量的 token 分布，并推荐 `longContextThreshold` 值及其对应的轻量/旗舰模型分流比例。

```bash
awerouter usage calibrate --since 7d
```

输出：

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

`longContextAuto` 块控制 `"auto"` 模式如何选取阈值：

```json
"longContextAuto": {
  "percentile": 75,
  "windowDays": 7,
  "minSamples": 10,
  "fallbackThreshold": 10000
}
```

| 字段 | 默认值 | 作用 |
|---|---|---|
| `percentile` | `75` | 使用观察到的 token 分布的哪个百分位作为阈值。`75` 表示 75% 的 L3 请求保留在轻量模型。 |
| `windowDays` | `7` | 守护进程启动时分析多少天的流量。 |
| `minSamples` | `10` | 信任自动校准结果所需的最少 L3 请求数。如果窗口内的样本数低于此值，则使用 `fallbackThreshold`。 |
| `fallbackThreshold` | `10000` | 样本不足或窗口为空时的静态回退值。防止冷启动时阈值突然跳变。 |

有了它，你就能回答"阈值是过于激进还是过于保守？"这个问题。在 `routing.json` 中将其设为 `"auto"`，awerouter 会在每次启动守护进程时根据你的实际流量重新校准。

### `usage savings`——Token 经济账

展示有多少输入 token 被分流到了轻量模型，以及如果全部使用旗舰模型会消耗多少成本，同时给出缓存敏感度区间。

```bash
awerouter usage savings --since 7d
```

输出：

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

它不会给出精确的金额——输出 token 和缓存机制因提供商而异——但量级一目了然。

### `usage tokens`——Token 都消耗在哪

按内容类型分解输入 token：消息（messages）、系统提示词（system prompt）、工具（tools）、工具返回结果（tool results）、工具调用（tool calls）、思考过程（thinking）。

```bash
awerouter usage tokens --since 7d
```

输出：

```
input tokens by type (842 requests, total 2,410,000  search 120,000  effective 2,350,800):
  messages      1,100,000    46%  avg 1,307/req
  system          680,000    28%  avg 807/req
  tool_results    320,000    13%  avg 380/req  (includes 120,000 search at 30% weight)
  tool_calls      180,000     8%  avg 214/req
  tools           130,000     5%  avg 154/req
```

用它来检查你的系统提示词是否过于臃肿、工具返回结果是否侵占了上下文、或文件搜索结果是否在膨胀你的 token 计数（它们会在路由前按 30% 权重计算）。

### `usage log`——原始请求记录

默认显示最近 20 条原始 JSONL 记录，包含每条请求的关键字段：时间戳、智能体、目标、提供商、模型、状态、延迟、token 数量。

```bash
awerouter usage log --lines 50
awerouter usage log --tokens    # 显示每条记录的各类型 token 明细
awerouter usage log --all       # 显示过滤窗口内的所有记录
```

这是你的审计追踪。当你需要确切查看某次请求发生了什么时使用。

## 这一切如何串联

典型工作流程如下：

1. **安装 awerouter**——`pip install awerouter`，然后运行 `awerouter init` 创建配置文件。
2. **安装技能**——通过 aweskill 或直接 curl 下载。
3. **启动守护进程**——在你自己的终端中运行 `awerouter serve`。
4. **将客户端指向它**——设置 `ANTHROPIC_BASE_URL=http://127.0.0.1:20128`（或客户端对应的环境变量）。
5. **其余交给智能体**——添加提供商、调整阈值、查看用量——全部通过自然语言完成。

智能体读取 `README.ai.md` 并照此执行。技能将你的表述映射为命令。守护进程路由每一条请求。JSONL 日志记录一切。五条 `usage` 命令让你查看所有数据。

你也可以通过一句话让智能体完成 awerouter 的安装：

> "阅读 https://github.com/mugpeng/awerouter/blob/main/README.ai.md 并按其执行。"

智能体会运行 pip install、安装技能、初始化配置、编辑 `~/.zshrc`，然后向你汇报。`awerouter serve` 则由你自己启动。



## awerouter 系列文章

- [awerouter：不怕deepseek 涨价，一句话让智能路由给你省钱](https://mp.weixin.qq.com/s/8jucVeQWQRjCIUEXxj-fHQ)
- [awerouter 更新: 数据看板告诉你省了多少](https://mp.weixin.qq.com/s/V1tPgz-jEekAMRdLMzGZGQ)



## 更多来自 mugpeng

awerouter 是 aweteam 生态的一部分：

- **[aweskill](https://aweskill.webioinfo.top/)** — 以 CLI 为核心的技能包管理器，支持 47 款以上的 AI 编程智能体
- **[aweswitch](https://github.com/Webioinfo01/aweswitch)** — AI 编程智能体配置方案切换器，支持 Claude Code、Codex 和 OpenCode；启动会话时自动指向 awerouter 守护进程
- **[aweshelf](https://github.com/Webioinfo01/aweshelf)** — 支持配置方案感知恢复的 AI 编程会话管理器
- **[awerouter](https://github.com/mugpeng/awerouter)** — 智能大语言模型路由器，自动利用结构信号将智能体请求分配到快速低价的轻量模型或能力更强的旗舰提供商，平衡成本、延迟与推理质量
- **[awescholar](https://github.com/Webioinfo01/awescholar)** — 面向 Awesome 列表的自动化科学文献发现与整理工具
