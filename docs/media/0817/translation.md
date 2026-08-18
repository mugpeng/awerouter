# awerouter：一句话让AI 用智能路由给你省钱

![awerouter](../../../logo/logo.png)

用大模型有一笔让人不舒服的账：每个请求都发给同一个模型。十行代码的重构、横跨五个文件的重命名、"修个错别字"——全都按前沿模型的价格，按最贵的价格死死扣你的钱包。可智能体一天里干的活，大多只是例行公事。真正需要强模型的请求——12,000 token 的并发 bug、图像分析、网页搜索的、思考的等等诸如此类——只占少数。你是在用 pro 的价格，干 flash 的活儿。

awerouter 给出的答案是智能路由。你在每个路由档案（routing profile）里配两个"提供商-模型"组合：一个 **flash**——快而便宜——接高频、轻量的任务；一个 **pro** ——强而准——接复杂推理和关键决策。每个请求一到手就被分流，依据的是请求本身的结构。不靠关键词。不用 LLM 分类器。不为"怎么路由"多花一个 token。

awerouter 正是诞生于这份憋屈。而且awerouter 他做的更多：路由器要有人配置、有人维护才有用——阈值要调、提供商要加、用量要解读。但这个人不必是你。awerouter 是为 AI 时代而造的工具：端到端都由智能体来打理。它出厂自带一份给智能体读的 README、一个给智能体用的技能、一个给智能体跑的 CLI。工具的学习成本，从你身上挪到了智能体身上。你只说要什么，智能体去想怎么干。

于是，我把整套安装交给了我的智能体。

我对它说："Read https://github.com/mugpeng/awerouter/blob/main/README.ai.md and follow it."（读一下这个 README 并照着做。）然后我去喝了杯咖啡。

回来的时候，awerouter 已经装好，技能已注册，两个配置文件躺在 `~/.config/awerouter/` 里，环境变量写进了 `~/.zshrc`。它读了模板配置，配好了三个提供商——便宜干 flash 活儿的 StepFun、管 pro 的 Anthropic，再加一个给 Opencode 会话用的 OpenAI 兼容端——并把它们接进了一个路由档案。

然后它说："Run `awerouter serve` in your terminal. I will not start the daemon for you."（在你的终端里运行 `awerouter serve`。守护进程我不会替你启动。）

这就是安装智能体工具的新形态。安装是一个任务。智能体就是干任务的。所以我把任务交给了智能体。

GitHub：[github.com/mugpeng/awerouter](https://github.com/mugpeng/awerouter)

## 安装：一份写给智能体的 README

大多数智能体工具会发布一份给人看的 `README.md`，再单独发一份给智能体看的 `README.ai.md`。这个拆分很诚实：人想听营销故事，智能体只想要操作步骤。awerouter 把这件事做到了底。

`README.ai.md` 是一份七步安装契约，写给智能体，不是写给用户的：

1. `pip install awerouter`，用 `awerouter --version` 验证
2. 通过 [aweskill](https://aweskill.webioinfo.top/) 安装 `awerouter` 技能（方案 A），或直接 curl 拉取 `SKILL.md`（方案 B）
3. 运行 `awerouter init`，创建 `~/.config/awerouter/providers.json` 和 `routing.json`
4. 读取现有配置，在 `anthropic`、`openai-chat` 或 `openai-responses` 下添加提供商
5. 把对应的 `export` 行追加到 `~/.zshrc`（或 `~/.bashrc`）
6. 把客户端指向 awerouter 守护进程的端口
7. 告诉用户调用技能，在技能列表里确认有 `awerouter`

### 30 秒版本

在 Claude Code、Codex、OpenCode，或者 aweskill 支持的 47+ 种智能体里的任何一个，提示词都是同一句：

> "Read https://github.com/mugpeng/awerouter/blob/main/README.ai.md and follow it."

剩下的智能体自己搞定。它跑 `pip install`、装技能、初始化配置、改 `~/.zshrc`，然后回来汇报。如果哪里出了问题——Python 版本太老、缺 `pip`、已有配置里有你在乎的档案——它会停下来问你，而不是闷声把东西搞坏。

### 智能体不做什么

这是最重要的一条边界。awerouter 是个**守护进程**（daemon）。它以 `awerouter serve` 的方式运行，挡在上游提供商前面，让每个请求都经过一条三层路由管线。智能体永远不会运行 `serve`，永远不会启动、停止或重启这个代理。

`README.ai.md` 把话说得很明白，技能也把话说得很明白。安全规则毫不含糊：守护进程是用户终端里的事。智能体的活儿是配置——`init`、`config show`、`usage stats`、`usage calibrate`，以及编辑 `providers.json` 和 `routing.json`。智能体活在文本文件的世界里，守护进程活在套接字的世界里，两者互不越界。

这意味着日常的路由管理——列档案、看配置、查用量、调阈值、加提供商——全部可以交给智能体跑。只有代理本身，要由你自己在自己的终端里启动。同样的规则也适用于 `awerouter add`（交互式向导）和 `awerouter restore`（用备份覆盖配置）。

## 实战一天

这天是周二。守护进程跑在 20128 端口上，Claude Code 通过 `ANTHROPIC_BASE_URL=http://127.0.0.1:20128` 指向它。

**早上 7:42。** 新的一天开始。`awerouter serve` 还是昨天起的那个进程。你想看看一夜之间路由跑得怎么样：

```
awerouter usage stats
```

智能体读了 JSONL 请求日志，报告：68% 的请求走了 flash（StepFun step-3.7-flash），32% 走了 pro（Anthropic claude-opus-5）。三次回退——flash 连吃 429（**限流**），代理在流出哪怕一个字节之前就故障转移到了 pro。你记下一笔：回头查查 StepFun 的配额。

**早上 9:15。** 你撞上一个棘手的并发 bug。智能体打开了一个巨大的上下文——12,000 token 的代码、堆栈跟踪和之前的对话。awerouter 的 L3 难度检查生效：全部请求内容的 token 数超过了 `longContextThreshold`（8,000）。请求被路由到 pro。Claude Opus 拿到完整上下文，一遍就诊断出死锁。你根本没为路由费过脑筋——它基于请求的模样，自动发生了。

**上午 11:30。** 你想加一个新提供商。你对智能体说：

> "Add GLM as a provider in the openai-chat group. I have `GLM_API_KEY` in my zshrc."

智能体读了 `providers.json`，在 `openai-chat` 下加了 GLM 的条目，然后把新的配置块报给你：

```json
"openai-chat": {
  "glm": {
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "auth": "${GLM_API_KEY}"
  }
}
```

然后它问："要不要把 GLM 加进某个路由档案的目的地？"你说要，它就更新 `routing.json`，把 `destinations.flash` 设成 `glm,glm-4-flash`。没有复制粘贴，没有"让我先翻翻文档"。

**下午 1:00。** 你有点好奇 `longContextThreshold` 设得对不对。你运行：

```
awerouter usage calibrate
```

智能体给出最近 500 个请求的 token 分布：p50 在 3,200 token，p75 在 6,800，p90 在 11,000，p95 在 18,000。它给出 4,000、6,000、8,000、12,000 四档候选阈值，并附上每档对应的 flash/pro 分流比。你选了 6,000——它能让 75% 的请求留在便宜档位，同时又不漏掉真正超长的那些。智能体改好 `routing.json`，说："重启 `awerouter serve` 让改动生效。"

**下午 3:00。** 你想算算省了多少钱。你运行：

```
awerouter usage savings
```

智能体亮出一张 token 记账表：flash 收了 240 万输入 token，pro 收了 110 万。对照纯 pro 基线，节省幅度落在 47% 到 62% 之间，具体取决于缓存敏感度。它不承诺精确的金额——输出 token 和缓存语义各家提供商不同——但量级一目了然。这工具一上午就把自己的成本赚回来了。

**晚上 6:00。** 收工。四个提供商、两个路由档案、三个协议组、一个从真实流量里调出来的阈值。每一次配置变更都是智能体经手的，每一个请求都是守护进程接住的。你从头到尾没手动打开过 `~/.config/awerouter/providers.json`。

## 技术栈：技能够得着什么

`awerouter` 技能刻意做得很小。它不打算当通用智能体框架，只是 `awerouter` CLI 上一层薄薄的流程封装，外加一个把自然语言映射到命令的意图路由器。

| 你说 | 技能执行 |
|---|---|
| "列一下我的 awerouter 档案。" | `awerouter list` |
| "给我看看 cc-router-1。" | `awerouter config show cc-router-1` |
| "给 openai-chat 加个 GLM 提供商。" | 编辑 `~/.config/awerouter/providers.json` |
| "把 flash 目的地换成 glm-4-flash。" | 编辑 `routing.json`，用 `awerouter config show` 验证 |
| "昨天的路由跑得怎么样？" | `awerouter usage stats` |
| "我的 longContextThreshold 该设多少？" | `awerouter usage calibrate` |
| "我省了多少？" | `awerouter usage savings` |
| "帮我在 zshrc 里配好 `GLM_API_KEY`。" | 追加 `export` 行，让你把 token 粘贴进去 |

最后一行最让人意外。大多数用户至少有一把密钥存在脑子里——不在任何文件里——因为是在另一台机器上配过一次，之后就再没腾出手把它落盘。智能体会读 `~/.zshrc`，找出缺什么，然后手把手带你补上。密钥只待在环境变量里，永远不进配置文件。

## Claude Code、Codex、OpenCode，和其他agent 平台

同一个档案，跨客户端通用。

**Claude Code** 是主力用户。`ANTHROPIC_BASE_URL=http://127.0.0.1:20128` 把 CLI 指向 awerouter 守护进程。`c1/flash`、`c1/think` 这类档位标签（Claude Code 的后台模型/思考模型提示）直接映射到 flash 和 pro 档。`web_search` 工具——一个"这个请求需要更强模型"的信号——被路由到 `settings.webSearchModel`（默认：pro）。用户体验是透明的：你继续干活，路由在后台悄悄发生。

**Codex** 档案走 `openai-chat` 或 `openai-responses` 协议组。`OPENAI_BASE_URL` 由 awerouter 替你写好——要么通过 aweswitch 档案启动，要么让智能体来配。Codex 没有档位标签，所以路由主要靠 L1（web_search）加 L3（token 数和图片）。默认走 flash，请求确有需要时才上 pro。

**OpenCode** 档案以同样的方式走 `openai-chat` 或 `openai-responses`。OpenCode 的 `@` 智能体调用则让你在同一场对话里把子任务派给不同模型——awerouter 管上游分流，OpenCode 管智能体选择。这条边界是有意为之：awerouter 管*连接*，OpenCode 管*智能体*。

**Cursor、Gemini CLI、Windsurf**——任何会说 Anthropic Messages、OpenAI Chat Completions 或 OpenAI Responses 的客户端都能用。协议层会发现不匹配，返回一个明确的 400，而不是闷声把报文搞乱。

## 用量分析：问题的另一半

路由只解决了一半问题。另一半是知道路由到底好不好。

awerouter 会把每个代理过的请求写进 JSONL 日志 `~/.local/state/awerouter/requests.jsonl`（50MB 轮转）。四条只读命令随时可查：

- **`usage stats`**——按档案细分到智能体、目的地、提供商和模型。每个维度都有错误数、回退数和延迟分位数（首字节与总耗时）。
- **`usage calibrate`**——token 数分布（p50–p99），附候选 `longContextThreshold` 值和相应的 flash/pro 分流比。想问"我的阈值是太激进还是太保守"，用这条命令回答。
- **`usage savings`**——对照纯 pro 基线的 token 记账，含缓存敏感度区间（Anthropic 式约 0.1× 读 / 1.25× 写 / 5 分钟 TTL）和切换频次分析，最后以现成的金额计算公式收尾，只差往里填数。
- **`usage tokens`**——按内容类型拆输入 token：消息、系统提示词、工具、工具结果、工具调用、思考。让你看清到底是什么在吃 token。

智能体识别靠 User-Agent 请求头：`claude-cli/...` → Claude Code，`codex_cli_rs` → Codex，`opencode/...` → OpenCode。用户什么都不用做。

## 为什么这很重要

第一波智能体工具假设操作者是人。配置意味着手改 JSON，安装意味着 `pip install` 加一张检查清单。大多数用户忍了，因为要装的工具也就一个。

第二波假设操作者是智能体。安装是一个任务，配置是一个任务，两个都可以外包。被外包的不是那个二进制——而是一份智能体能照着执行的、可读的规格说明。

`README.ai.md` 就是那份规格。它是写给智能体读、照着做的：带明确的"不要运行这条命令"的边界，带依赖缺失时的回退路径，每个阶段都带验证步骤。用户不需要看懂它，智能体需要。

awerouter 还有一条设计约束，把它和大多数代理工具区分开：响应路径是**不透明的**。响应字节永不被解析，永不被缓冲，从上游原封不动地流到客户端。这不是实现上的偷懒——这是设计上的不变量。它意味着代理永远看不到足够多的对话内容，也就无法围绕质量建反馈回路。所以 awerouter 干脆不试。它不用 LLM 分类器，不靠关键词瞎猜。它只按结构路由——模型档位、token 数、有没有图片、有没有 web_search 工具——仅此而已。护城河是"全自动 + 客户端无关 + 零分类成本"，而不是准确率。

如今我评估任何路由工具，都过这三关：

1. **智能体能不能凭一句提示词就装好它？**
2. **装好之后，智能体能不能用自然语言调优它？**
3. **路由会不会带来我感觉得到的延迟？**

awerouter 三关全过。第一关靠 README，第二关靠技能，第三关的延迟开销可以忽略不计：代理只是一层薄薄的 aiohttp 中继，对请求体零解析，首字节延迟的开销只是本地回环上的一次 HTTP 往返。

智能体工具的未来，不是"跟智能体配合得好的工具"，而是"智能体能替你安装、配置、运营的工具"。awerouter 是第一批把这当作主要安装路径来发布的工具——而不是当权宜之计。

## 试一试

对你的智能体说：

> "Read https://github.com/mugpeng/awerouter/blob/main/README.ai.md and follow it."

然后在你自己的终端里启动守护进程：

```bash
awerouter serve
```

再把客户端指向它：

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:20128
```

从这里开始，问题都变得稀松平常：

- "给 openai-chat 加个 GLM 提供商。"
- "我这周的 flash/pro 分流是多少？"
- "根据我的用量调一下 longContextThreshold。"

智能体早就认识这些命令了，只是你还没把那份 README 交给它。

## 其他有意思的作品

awerouter 是我的awesome 工具生态的一部分：

- **[aweskill](https://aweskill.webioinfo.top/)** ——面向 47+ 种 AI 编程智能体的 CLI 优先技能包管理器
- **[aweswitch](https://github.com/Webioinfo01/aweswitch)** ——Claude Code、Codex、OpenCode 的智能体档案切换器；启动的会话直接指向 awerouter 守护进程
- **[aweshelf](https://github.com/Webioinfo01/aweshelf)** ——支持档案感知恢复的 AI 编程会话管理器
- **[awescholar](https://github.com/Webioinfo01/awescholar)** — 自动化科学文献发现
- **[awerouter](https://github.com/mugpeng/awerouter)** — 智能 LLM 路由器：基于请求的结构化信号，在 Flash（低成本）与 Pro（高能力）模型提供商之间自动分流，为 Agent 兼顾成本、速度与推理质量。

