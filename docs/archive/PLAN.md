# awerouter MVP — 结构信号驱动的确定性路由代理（Anthropic / CC）

## Context（为什么做）

awerouter 是 aweswitch 的兄弟工具：一个本地常驻 daemon，做 aweswitch 做不到的事——**在一个端口上，把 Claude Code 的请求按规则路由到不同 provider/model**。CC 的 `ANTHROPIC_BASE_URL` 是全局唯一的，唯一能按请求携带的分流信号是 `model` 字段。所以 aweswitch 给 CC 设一个指向 awerouter 的 profile、把各 tier 设成不同 model-id（`c1/flash`、`c1/pro`、`c1/think`），awerouter 据此 + 请求特征路由。

本 MVP 的两个用户决策：

1. **先做 CC**：协议范围只做 Anthropic `/v1/messages`（同协议透传，不做跨协议翻译）。通过一个端口（默认 20128），让 `aweswitch <profile>` 能启动。
2. **结构信号路由（无关键词）**：路由依据是**可观测的请求结构信号**（入站 model 档位、token 量、是否含图、是否用 web_search 工具），不猜最后一句话的语义。

**关键设计决定（三层管线 + 两目的地）**：分类输出只有两个目的地 `flash`（便宜）/ `pro`（强）；决策由三层 first-match-wins 管线产生，每层用不同性质的信号：

- **L1 能力护栏**：请求要的能力 flash 供应商给不了 → 强制走能给的 provider（不看难度）。
- **L2 档位覆盖**：入站 model 是已知档位标签 → 直接用该档位目的地（CC 自己已判好，免费且精确）。
- **L3 难度评分**：其余普通流量按 token 量 / 是否含图判 flash/pro。

六个决策标签仅用于日志/统计：`background / think / longContext / image / webSearch / default`。

**诚实的边界**：L3 的难度是 token 量的近似，会误判。对策：① L1/L2 用最可靠的结构信号兜住"能力"和"档位"两类硬情况；② L3 默认偏 flash（省钱），仅 token 超阈值或含图才升 pro；③ 阈值靠日志画像校准（CC 基线 token 就几千，别照搬 8K/32K）；④ 每次决策落结构化日志。

## 分类器：三层管线

`router.inspect(body)` 一次性提取特征（只读一次请求体）：

- `token_count`：估算 token（文本字符 ÷ ~4 英文 / ~1.5 中文；粗略，阈值当旋钮）
- `has_image`：messages 是否含 image block
- `has_web_search`：`body.tools` 是否含 `web_search_*` 工具
- `message_count`：消息条数（v1 仅入日志/备用，不进决策；token_count 已与之强相关）

`resolve(model, body) → (destination, provider, real_model, label)`，first-match-wins：

```
L1 能力护栏
  has_web_search                       → pro     # 能力约束：flash 供应商多不支持 web_search server tool
L2 档位覆盖（入站 model 精确匹配档位标签）
  model == backgroundModel(c1/flash)   → flash   # CC 后台杂活，最可靠
  model == thinkModel(c1/think)        → pro     # 用户手选 opus / /fast，最可靠
L3 难度评分（其余普通流量，默认 flash）
  token_count > longContextThreshold   → pro     # 长上下文是便宜模型最容易崩/截断的地方
  has_image                            → pro     # 视觉，强模型更稳
  否则                                  → flash   # default
```

标签 → 目的地：`flash = {background, default}`；`pro = {think, longContext, image, webSearch}`。

> 说明：① web_search 是**能力**不是难度——短查询也必须走支持该工具的 provider，故放 L1、不看分数。② background/think 用档位 model id 检测，比用 messageCount/systemText 猜准得多，且免费。③ L3 砍掉了 `toolNames 多样性`（CC 每次发整套工具清单 + MCP，是环境常量、不反映单轮难度）和 `systemText 长度`（多为固定 system prompt + CLAUDE.md，反映项目不反映单轮难度）。④ 灰区即 L3 的 default → flash。

## 配置：两个文件（密钥 vs 策略）

拆开的实在收益：`routing.json` 无密钥、可贴可进 git；只有 `providers.json` 需 redact。两者同放 `~/.config/awerouter/`，单个 `AWEROUTER_CONFIG_DIR` env 覆盖目录。`config show` 对 providers redact、对 routing 全量显示。

**providers.json**（端点 + 密钥）：

```json
{
  "stepfun":   { "base_url": "https://api.stepfun.com/anthropic", "auth": "${STEPFUN_KEY}",   "auth_header": "authorization" },
  "anthropic": { "base_url": "https://api.anthropic.com",         "auth": "${ANTHROPIC_KEY}", "auth_header": "x-api-key" }
}
```

**routing.json**（策略，无密钥）：

```json
{
  "backgroundModel": "c1/flash",
  "thinkModel": "c1/think",
  "longContextThreshold": 32000,
  "destinations": {
    "flash": "stepfun,step-3.5-flash",
    "pro":   "anthropic,claude-opus-5"
  }
}
```

> 目的地格式 `"provider,model"`，provider 名必须存在于 providers.json（加载时校验，缺失即 `die`）。阈值 / model 名为占位，按真实可用值改；`longContextThreshold` 先打日志看 CC 真实 token 分布再定（基线已几千，勿照搬 8K/32K）。

## 目录结构（镜像 aweswitch 惯用法，不复用代码）

```
product/tools/awerouter/
  pyproject.toml              # name=awerouter; deps: click>=8.1, aiohttp>=3.9; requires-python>=3.9; MPL-2.0; package-data default-providers.json + default-routing.json
  README.md
  LICENSE                     # MPL-2.0（与 aweswitch 一致）
  src/awerouter/
    __init__.py               # __version__
    cli.py                    # click 命令面
    config.py                 # 两文件加载/校验/${VAR} 展开/redact/die + 目的地解析
    router.py                 # inspect(body)→特征；resolve(model,body)→(dest,provider,model,label)
    server.py                 # aiohttp app：POST 转发 + 流式透传 + 每请求日志
    logging.py                # 追加结构化日志 + 读取/聚合 stats
    default-providers.json
    default-routing.json
  tests/
    test_config.py
    test_router.py
    test_server_streaming.py
```

## 复用 aweswitch 的惯用法（来自 product/tools/aweswitch/src/aweswitch/cli.py）

- `ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")` + `expand_value(value, env)`（缺 env 即 `die`）——只作用于 providers.json 的 `auth` 字段
- `SECRET_RE = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|AUTH)", re.IGNORECASE)` + `redact(data)`——只作用于 providers.json
- `die(message)` → `raise SystemExit(f"awerouter: {message}")`
- 路径：`AWEROUTER_CONFIG_DIR` env 或 `~/.config/awerouter/`；内含 `providers.json` + `routing.json`
- `TEMPLATE_PROVIDERS / TEMPLATE_ROUTING = Path(__file__).parent / "default-*.json"`；`init_config` 拷贝两模板
- `load_config(dir)`：读两 JSON + 校验（providers 顶层 dict；routing 含 `destinations`/`backgroundModel`/`longContextThreshold`）+ 校验目的地引用的 provider 存在
- click：`@click.group` + 子命令；`context_settings={"help_option_names":["-h","--help"]}`；`@click.version_option`
- pyproject：setuptools、`[tool.setuptools.package-data]` 含两个 default json、`[project.scripts] awerouter = "awerouter.cli:main"`

## server.py 请求流（路径保留 + 只改 model）

1. 收任意 `POST /v1/messages*`（含 `/v1/messages/count_tokens`，CC 会调）。
2. `body = await request.json()`；若 body 含 `model` → `resolve`；否则用 default 目的地只转发。
3. 协议校验：MVP 仅 anthropic；硬编码假设 anthropic-compat（不再逐 upstream 配 `protocol`）。
4. 改写：`body["model"] = real_model` 后重序列化；**丢弃入站 auth 头**，按目的地 provider 设 `{auth_header}: expand_value(auth)`；保留 `anthropic-version`、`content-type` 等非鉴权头。
5. 转发：`upstream_url = base_url.rstrip('/') + request.path`（路径保留，透传 count_tokens 等）。
6. **响应当不透明字节流**：`async with session.post(...) as up: resp=StreamResponse(status=up.status, headers=过滤后的上游头); resp.content_type=up.content_type; await resp.prepare(request); async for chunk in up.content.iter_any(): await resp.write(chunk)`。**不解析、不缓冲 SSE**。
7. 上游非 2xx：原样透传 status + body，不伪造。
8. `logging.append(...)`：ts、model_in、label、destination、provider、model_out、status、ms、bytes、token_count。

## CLI 命令面（极简，aweswitch 风格）

- `awerouter serve [--port 20128] [--host 127.0.0.1]` — 启动 daemon
- `awerouter config path|show|edit|init` — 镜像 aweswitch（show 对 providers redact、routing 全量）
- `awerouter log` — 尾随最近请求；`awerouter stats` — 按 label/destination/provider 聚合计数与字节

## aweswitch 接线（零改动 aweswitch）

用户在 aweswitch config 手写一个指向 awerouter 的 claude profile：

```json
"cc-awerouter": { "env": {
  "ANTHROPIC_BASE_URL": "http://127.0.0.1:20128",
  "ANTHROPIC_AUTH_TOKEN": "awerouter",
  "ANTHROPIC_MODEL": "c1/pro",
  "ANTHROPIC_DEFAULT_HAIKU_MODEL": "c1/flash",
  "ANTHROPIC_DEFAULT_SONNET_MODEL": "c1/pro",
  "ANTHROPIC_DEFAULT_OPUS_MODEL": "c1/think",
  "ANTHROPIC_DEFAULT_FABLE_MODEL": "c1/think"
}}
```

`aweswitch cc-awerouter` 启动 CC → Haiku 档 `c1/flash` 命中 L2 background→flash；Opus 档 `c1/think` 命中 L2 think→pro；主循环 `c1/pro` 走 L3，长上下文/含图升 pro、其余 default→flash；带 web_search 的请求 L1 强制 pro。awerouter 自带配置，不读写 aweswitch 文件。

## 明确不做（MVP 范围外）

- OpenAI `/v1/chat/completions`（Codex）端点 —— 下一步，同套路
- 跨协议翻译、fallback/重试/负载均衡、配额
- dashboard UI、读取 aweswitch 配置
- 关键词分类器、LLM 分类器（当前全结构信号；不够再上，先靠日志画像评估）

## 风险与对策

| 风险 | 对策 |
|---|---|
| L3 token 阈值误判难度 | 默认偏 flash 省钱；阈值靠日志画像校准；CC 基线 token 已几千，阈值按真实分布定 |
| web_search 路由到不支持的供应商 | L1 能力护栏：`has_web_search` 强制走支持该工具的 provider（=pro） |
| flash 供应商不支持视觉 | `has_image` → pro（强模型稳）；若 flash 支持视觉再优化 |
| 档位信号丢失（改用特征猜 background/think） | L2 直接用入站 model 精确匹配档位标签，免费且准 |
| SSE 流式透传（主要崩点） | 响应不透明字节管道，不解析不缓冲；真机 e2e 验证 |
| Anthropic-compat auth 头不一 | 目的地 provider 可配 `auth_header`（authorization / x-api-key） |
| `anthropic-version` 等头丢失 | 显式白名单透传非鉴权头 |

## 验证

1. **单测**：`test_config`（两文件加载、`${VAR}` 展开、缺 provider 报错、目的地引用校验、redact 只动 providers）；`test_router`（三层命中顺序：webSearch > background > think > longContext > image > default、`inspect` 特征提取、token 阈值边界）；`test_server_streaming`（本地 echo 上游，断言流式透传不截断、model 被改写、count_tokens 路径透传）。
2. **集成**：`awerouter serve` + `curl POST /v1/messages`：model=`c1/flash`→flash；model=`c1/think`→pro；model=`c1/pro` + token>阈值→pro；body 含 `web_search`→pro（无论多短）。
3. **真机 e2e**：写 `cc-awerouter` profile → `aweswitch cc-awerouter` → 跑真实 CC 会话（① 琐碎查找 ② 改文件 ③ 长上下文 ④ 让它 web_search）→ `awerouter log` 确认：各 label 命中预期目的地、token 流式无截断、`tool_use` 往返正常；用日志画像回头校准 `longContextThreshold`。
