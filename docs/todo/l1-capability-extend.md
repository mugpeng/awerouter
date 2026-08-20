# L1 Capability Guard：web_search 之外还应覆盖什么

> 2026-08-20 · 来源：媒体稿件复核 + 四层设计讨论

## 现状

`router.py:39-47`：L1 只检查 `has_web_search`（工具名以 `web_search_` 开头），命中则路由到 `web_search_model`（默认 pro）。

```python
if feat.has_web_search:
    dest_key = web_search_model   # 默认 "pro"
    return ResolveResult(..., label="webSearch", ...)
```

`protocols.py` 里 `has_image` 的检测逻辑在三套协议中都已实现，但放在了 L3（`router.py:76-82`），label 为 `image`，与 `longContext` 并列。

## 问题

### 1. L1 仅覆盖 web search，能力约束不完整

L1 的设计语义是"flash 根本做不到"，是一个硬约束。web search 是其中一种，但不是唯一一种。其他可能的能力断层：

- **图像输入（vision）**：部分 cheap provider 完全不支持图像输入，API 会直接报错
- **函数调用的 schema 复杂度**：某些 provider 对 tool_use 的参数结构有限制
- **system prompt 长度**：某些 provider 对 system prompt 有硬上限

### 2. `has_image` 放在 L3，语义错位

L3 是"难度估计"，逻辑是"flash 能做但质量差所以不值得"。

但实际上存在两类情况：

| 情况 | 当前处理 | 问题 |
|------|----------|------|
| cheap provider 支持图像但质量差 | L3 `image` → pro | 正确，是难度问题 |
| cheap provider 根本不支持图像 | L3 `image` → pro | 错，这是能力硬约束 |

两种情况代码里无法区分——`has_image` 只检测"请求里有没有图"，不检测 provider 是否支持。

### 3. 媒体稿件中的 L2 范围问题

`docs/media/0819/awerouter-four-layer-routing.md` 说 L2 是"客户端已经决定"。

实际代码（`types.py:69`）：L2 只对 Anthropic 协议有效——OpenAI 客户端是单模型，tier label 机制不会触发。

## 讨论方向

### 方案 A：L1 新增 `has_vision` 检查（低风险）

在 L1 增加独立的图像能力检查，由配置决定是否触发：

```
if feat.has_web_search:   → webSearch → pro（硬编码）
if feat.has_vision:       → vision  → pro（可配置，默认关闭）
```

改动：
- `types.py`：新增 `vision_model: str = ""`（空 = 禁用）
- `router.py`：L1 增加 `if feat.has_image and dests.get(vision_model): ...`
- 现有 `has_image` → L3 逻辑保留不动，行为零变化

好处：不改现有行为，按需启用。配置设为 `vision_model: "pro"` 即可让图像请求在 L1 就被拦截。

### 方案 B：把 `has_image` 从 L3 提升到 L1（行为变更）

label 从 `image` 改为 `vision`，明确表示这是能力检查而非难度估计。

改动：
- `router.py`：`has_image` 判断从 L3 移到 L1
- L3 移除 image 分支
- 标签 `image` 弃用，改为 `vision`

代价：行为变化——之前图像请求会经过 L1（失败）→ L2（可能命中 background/think）→ L3（image 判断），提升后在第一层就决定。对大部分流量（L2 命中的 background 请求）结果可能不同。

### 需要先回答的问题

1. **各 provider 的图像支持现状**：flash tier provider 是否真的有不支持图像输入的？还是只是质量差？
2. **L2 的 Anthropic 专属限制要不要在文档里说明**：媒体稿件需要加一句限定，避免读者以为所有客户端都有 tier label。
3. **L1 要不要做成可扩展的"能力清单"**：目前是硬编码两个检查（web_search, image），未来如果加 function calling schema 限制、system prompt 上限等，是每个加一个 if 还是抽象成 `feat.capability_missing` 列表？

## 建议执行顺序

1. 先确认 provider 图像支持现状 → 决定 A 还是 B
2. 同步修媒体稿件 L2 的 Anthropic 限定说明
3. 如选 A，实现 `vision_model` 配置项
4. 如选 B，移动 `has_image` 判断并更新标签命名
