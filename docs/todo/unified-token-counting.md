# 统一三协议 token 计数

## 目标

让 `InspectResult.token_count` 统一反映请求中所有"随轮重发、上游计费"的内容，消除三个协议之间以及不同 agent 之间的统计口径差异。

## 动机

当前三个协议的计数口径不一致：

| 请求内容 | anthropic | openai-chat | openai-responses |
|---|---|---|---|
| 正文 prose | ✓ | ✓ | ✓ |
| 系统提示词 | ✗ | ✓（messages 成员） | ✗ |
| 工具结果 | ✗ | ✓（role=tool） | ✗ |
| 工具定义 | ✗ | ✗ | ✗ |
| 工具调用参数 | ✗ | ✗ | ✗ |
| thinking 块 | ✗ | 不存在 | ✗ |

这导致两个问题：
1. **路由语义不统一**：openai-chat 的 L3 阈值比的是"正文+系统提示词+工具结果"，anthropic 只比正文
2. **跨 agent 数字不可比**：opencode（openai-chat）账面 token 显著高于 claude-code（anthropic），主要是口径差异

## 变更范围

### 1. `src/awerouter/protocols.py` — 核心：扩展三个 extractor

统一扩展为六项（图片维持不变，只置 `has_image` 标志不计 token）：

| 内容 | anthropic | openai-chat | openai-responses |
|---|---|---|---|
| 系统提示词 | `body["system"]`（str 或 list 块） | messages 中 role=system | `body["instructions"]` |
| 正文 prose | 现有逻辑不变 | 现有逻辑不变 | 现有逻辑不变 |
| 工具定义 | `body["tools"]` json.dumps | `body["tools"]` json.dumps | `body["tools"]` json.dumps |
| 工具结果 | `tool_result` 块的 `content` | role=tool 的 content（已在计入） | `function_call_output` 的 `output` |
| 工具调用参数 | `tool_use` 块的 `input` dict | `tool_calls[].function.arguments` str | `function_call` 的 `arguments` str |
| thinking 块 | `type: "thinking"` 的 `thinking` 字段 | 无此结构 | `type: "reasoning"` 的 `summary` 文本 |

新增辅助函数：
- `_count_tool_defs(tools)`: tools 数组 json.dumps → estimate_tokens
- `_count_system(text_or_blocks)`: 处理 str 和 list 两种格式的系统提示词
- `_count_tool_use_input(input_val)`: 处理 dict 和 str

### 2. `tests/test_router.py` — 更新断言 + 新增用例

需修改的现有断言（token_count 会因新计数项而改变）：
- `test_empty_messages` (line 49): `== 0` → 空 body 仍为 0，不用改
- `TestExtractAnthropic.test_text_extraction` (line 72): 图片 body 的 token_count
- `TestExtractOpenAIChat`: line 83 (`== 2`), line 133 (`== 0`)
- `TestExtractOpenAIResponses.test_non_message_items_skipped` (line 158): function_call_output 目前断言计 0，改后会计入
- `TestResolveAcrossProtocols`: line 128 (`== 2`), line 133 (`== 0`)

新增测试用例：
- 系统提示词计入（三种协议各一个）
- 工具结果计入（三种协议各一个，openai-chat 确认不变）
- 工具定义计入
- tool_use 输入计入
- thinking 块计入（anthropic）
- 混合场景（正文 + 工具结果 + 系统提示词 + 工具定义）

### 3. `src/awerouter/cli.py` — 更新措辞

删除 4 处 "messages only — system prompt & tools excluded" 的 caveat，替换为准确描述：
- line 334: stats 的 `total_tokens` 说明
- line 340-342: offload 说明
- line 385-387: calibrate 的 distribution 说明
- line 425: savings 的 heading

### 4. 文档更新

**`README.md` + `README_cn.md`：**
- L3 表格行：token 描述改为 "token count (all request content) > threshold"
- calibrate 段落：删除 "messages only — system prompt and tools are excluded"
- savings 段落：对应更新

**`docs/CHANGELOG.md`（v0.3.7, `### Changed`）：**
> Token counting now includes system prompt, tool definitions, tool results, tool-call inputs, and thinking blocks across all three protocols. Previously only message prose was counted (and inconsistently across protocols). `longContextThreshold` values need recalibration via `usage calibrate` after upgrading.

**`resources/skills/awerouter/SKILL.md`：**
- L3 表格行和 `longContextThreshold` 描述同步更新

**`docs/CONTRIBUTING.md`：**
- 架构描述中 "message tokens" → "request tokens"

**`docs/todo/code-quality.md`：**
- 标记 "system/tools 不计入" 的旧决策已推翻

### 5. 版本号

`src/awerouter/__init__.py`: `0.3.6` → `0.3.7`

### 6. 默认阈值

`src/awerouter/default-routing.json`：**保持 8000 不变**。模板只在 `awerouter init` 新装时使用，新用户上线后 `usage calibrate` 定值。

## 不做的事

- 不加新的 `InspectResult` 字段：`token_count` 语义升级即可
- 不加工具分类/搜索折扣：留到下一轮，先有统一口径再谈分配
- 不改 `router.py` 的路由逻辑：L3 `token_count > threshold` 不变，变的是喂给它的数字
- 不改 `estimate_tokens` 公式：非中文÷4、中文÷1.5 够用
- 不迁移已有日志：用户通过 `--since` 控制校准范围

## 实施顺序

1. 改 `protocols.py`（核心逻辑）
2. 改 `tests/test_router.py`（更新旧断言 + 加新用例）
3. 跑测试确认全绿
4. 改 `cli.py` 措辞
5. 改文档（README×2 + CHANGELOG + SKILL + CONTRIBUTING + code-quality todo）
6. 改版本号
7. 跑全量测试

## 升级注意事项

- anthropic/responses profile 的 token_count 会显著增大，旧阈值会把更多流量送 pro
- 升级后立即跑 `usage calibrate` 重新定阈值，或用 `--since` 只看新口径流量
- 新旧口径日志混在一起时 calibrate 分布会呈双峰——注意用 `--since` 切割

## 相关文档

- `l3-complexity.md` — L3 智能度的边界（工具分类/搜索折扣属于此文档的后续方向）
- `usage-scanning.md` — savings/token 统计相关
- `code-quality.md` — 记录了"system/tools 不计入"的旧决策（本 todo 推翻该决策）
