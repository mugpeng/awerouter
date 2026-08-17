# 代码质量收尾

来源：dev 分支 code review（2026-08-14）。按严重度排序，都是小改动、消除隐式契约或提升体验。

> **状态：已完成（2026-08-15）。** 高/中/低优先级 7 项全部落地，测试从 72 增至 84 个全绿。calibrate 输出已标注 message token。各项实现细节见 CHANGELOG「Unreleased」；下文保留原始分析作为记录。

## 高优先级（半小时内、纯消除隐患）

### 1. `_proxy_request` 原地改 body — 消除隐式副作用

**位置**：`src/awerouter/server.py:78`
```python
body["model"] = dest.model
```

**问题**：这是 retry 循环里唯一一个就地修改入参的地方。`body` 在 fallback 重试时会被反复改写。当前无 bug（每次重写），但契约太脆弱——任何未来新增的 body 读取逻辑（如"日志里记原始 model"）都会踩到已被改写的 body。

**修法**：在 `_proxy_request` 入口做浅拷贝：
```python
body = dict(body)
body["model"] = dest.model
```
成本：一行。收益：消除整个文件里最危险的隐式契约。

### 2. `Destination.provider` 两阶段初始化 — 类型不安全

**位置**：`src/awerouter/types.py:21`、`src/awerouter/config.py:202-203`

**问题**：`Destination.provider` 类型是 `Provider | None = None`，在 `_parse_destination` 构造时是 None，在 `load_for_profile` 里用副作用赋值补全。这造成半初始化状态——类型系统无法阻止你在未填充时用它。而 `ResolveResult.provider`（`types.py:52`）类型标注是 `Provider`（非 Optional），但 `resolve()` 里传的是 `dest.provider`，如果没填充就传了 None，类型签名骗了人。

**修法**（二选一）：
- A：`Destination` 只持 `provider_name`，provider 解析在 `resolve()` 调用处查 dict
- B：`_parse_destination` 时就传入完整 Provider 对象（要改 load 顺序）

倾向 A——Destinations 是纯数据，provider 解析是 config 的职责，分开更干净。

### 3. `detect_auth_header` 子串匹配脆弱

**位置**：`src/awerouter/config.py:34`
```python
return "x-api-key" if "anthropic.com" in base_url else "authorization"
```

**问题**：`https://evil.com/anthropic.com/proxy` 会误判成 x-api-key。

**修法**：
```python
from urllib.parse import urlparse
netloc = urlparse(base_url).netloc.lower()
return "x-api-key" if netloc == "api.anthropic.com" or netloc.endswith(".anthropic.com") else "authorization"
```

## 中优先级（体验提升）

### 4. config show 不校验 dest provider 存在

**位置**：`src/awerouter/config.py:107-114`（`_parse_destination`）、`:183`（`resolve_provider`）

**问题**：`_parse_destination` 只做字符串 split，provider 存在性检查在 `load_for_profile:202`。routing.json 写错 provider 名时，`awerouter config show` 成功，`awerouter serve` 才崩——错误反馈延迟。

**修法**：把 provider 存在性校验提前到 `load_routing`（或 `load_for_profile` 的 profile 解析阶段），让 `config show` 也能报错。

### 5. 无 request id

**位置**：`src/awerouter/server.py:handle_messages`

**问题**：日志有 `ts` 但无 `request_id`，无法把入站请求、上游调用、下游响应关联起来。流式请求调试时特别痛。

**修法**：handle_messages 入口生成 UUID（或复用客户端传的 `x-request-id`），写入日志、透传给上游。

### 6. 日志全量读

**位置**：`src/awerouter/logging.py` 的 `stats()`、`tail()`、`token_distribution()`

**问题**：daemon 跑 30 天、每秒 1 请求，文件约 2.5GB。CLI 每次执行都 `read_text().splitlines()` 全量读。

**修法**（渐进）：
- 短期：加 `AWEROUTER_LOG_MAX_BYTES` 环境变量，append 时检查大小、超限则轮转
- 长期：`tail()` 用 seek-from-end 只读尾部；`stats()`/`token_distribution()` 可接受全量读（低频调用）

## 低优先级（防 drift）

### 7. resolve 逻辑重复

**位置**：`src/awerouter/server.py:236-244`（handle_count_tokens）与 `handle_messages` 里的 resolve 调用重复。

**修法**：抽 `_resolve_for_request(body, profile, settings)` 函数。

## 明确不做（review 提出但不采纳）

### `_extract_text` 不含 system + tools

**review 建议**：把 system prompt 和 tools 定义也算进 token_count。

**不采纳**：这是**有意的设计**（PLAN.md:47）。CC 的 system prompt 多为固定 prompt + CLAUDE.md，反映项目规模不反映单轮难度；tools 定义是整套 MCP 工具清单，每次都发，是环境常量。token_count 是"单轮消息内容难度"代理，不是"请求总 token"。

**但 calibrate 的文档/输出要说清这一点**——当前 `L3 token distribution` 字样会让人误以为是总 token。改输出标注为 "message token"。

**2026-08-17 已推翻**（v0.3.7，见 `unified-token-counting.md`）：为统一三协议的 L3 阈值语义与跨 agent 可比性，token_count 现已计入 system prompt、工具定义、工具结果、工具调用参数与 thinking 块。升级后需用 `usage calibrate` 重新定 `longContextThreshold`。

### 并发 / 断连测试

每请求独立的 `_RoutingState`，无共享可变状态，并发测试 ROI 低。断连路径有 try/finally，已 e2e 验证过。不补单测。

### Graceful shutdown / SIGTERM

aiohttp AppRunner 自带基础信号处理，当前够用。真要做 daemon 化（systemd/launchd）时再加。
