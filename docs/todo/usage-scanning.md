# 响应 usage 旁路扫描（savings 真值化）

来源：savings cache sensitivity 讨论（2026-08-16）。目标版本：v0.3.x。

## 问题

`savings` 目前只能估：输入 token 是代理自己的请求估算（v0.3.7 起含 system prompt 与工具定义等全部请求内容），
输出 token 完全不可见，缓存命中/写入只能按 0.1×/1.25×/TTL 假设折算。三块盲区里
缓存恰恰是影响节省估算准确度的最大项——pro-only 基线大部分输入本会按缓存读价计费。

## 观察

Anthropic 协议的 SSE 流自带精确账单，响应里就有：

- `message_start` 事件：`usage.input_tokens` + `cache_read_input_tokens` + `cache_creation_input_tokens`
- `message_delta` 事件：`usage.output_tokens`（随流更新，最后一个为准）

也就是说，不做任何建模，每个请求的真实分级 token 都可以从响应里拿到。

## 设计

- **字节保持透传，只读不改**：在 `handle_messages` 的流转发循环里，对每个 chunk
  做旁路扫描（累积一个小 buffer，识别 `data: {...}` 行中的 `message_start` /
  `message_delta` usage 字段），写出缓冲与转发字节完全一致。SSE 语义不受影响。
- **扫描状态挂在 `_RoutingState` 或独立的轻量 scanner 对象**，请求结束写入日志新字段：
  `in_tokens`、`cache_read`、`cache_write`、`out_tokens`（来自上游响应，真值）。
- `token_count`（请求侧估算）保留——响应缺失 usage 的上游（不合规实现）仍有 fallback。
- `savings` / `stats` 改为优先用响应真值：per-provider 的缓存命中分布、输出 token、
  "pro-equivalent" 折算从区间变成实测；cache sensitivity 的 0.1×/1.25× 假设退役，
  仅 TTL 结构分析保留（或同样由实测 cache_read 序列推断冷热）。

## 代价与豁免理由

CONTRIBUTING 写明「任何需要解析响应的改动必须论证破例」。破例理由：

1. 只读旁路，不改一个字节，客户端拿到的流与不扫描时逐位相同；
2. 换来的是钱的真值（缓存分布 + 输出 token），是本项目核心卖点（省钱）的地基；
3. 失败模式安全：usage 解析失败/字段缺失 → 退回估算，不影响转发。

## 实现要点

- SSE 行可能跨 chunk 分裂，scanner 需要跨 chunk buffer，按 `\n` 切完整行再解析；
- 只关心两种事件类型，JSON 解析失败静默跳过（上游可能是非标准 SSE）；
- 性能：每 chunk 一次字符串查找（`"message_start"` / `"message_delta"` 前缀粗筛），
  命中才 json.loads，流式大响应开销可忽略；
- 测试：fake 上游返回带 usage 的 message_start/message_delta，断言日志字段与透传
  字节不变（对转发输出做快照对比）。
