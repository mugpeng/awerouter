# Shadow 模式（路由规则影子验证）

## 背景

规则类改动（`toolRouting` 各键、`searchResultDiscount`、新增 phase 规则如 test-run→pro）
现在只能"改了再看"：

1. **看不到反事实** —— 规则上线后只有新规则的结果，不知道"如果没改会怎样"；
   效果变差也不知道差了多少、差在哪。
2. **试错直接暴露给真实流量** —— 规则判错，错误立刻发生在正在干活的会话上。

阈值这块已经有 `calibrate`（看分布、有证据地定值），规则这块没有对应的验证手段。
shadow 模式补的就是这块。

## 是什么

请求进来时**按两套规则各算一次路由**：

- 实际转发用**当前生效的规则**，行为完全不变；
- **影子规则**只计算、只记日志（每行多存影子 label + 影子目的地），不影响请求。

跑几天后一条命令看对比：

```
shadow vs live (last 7d):
  一致     512 (84%)
  分歧      96 (16%)   live=flash shadow=pro 71 | live=pro shadow=flash 25
  分歧集中的 label: default→toolEdit 39, toolSearch→pro 22 ...
```

"test-run → pro 该不该开"从"改了再看"变成"先看影子数据再决定"。

## 适用 / 不适用

- ✅ 任何**路由行为**变更：新规则、改规则目标档位、调 search 折扣、调 L4 阶段判定。
- ✗ provider/端点变更（非路由决策，影子算不了）。
- ✗ **历史重放做不了**：`requests.jsonl` 只存聚合数字，不存请求体（也不该存——体积和
  隐私都不允许）。所以影子必须**实时标注**，这就是为什么需要"跑几天"而不是"跑一遍历史"。

## 成本

- 每请求多一次 `resolve()` 计算：纯内存、微秒级，可忽略。
- 日志每行 +2 字段（shadow_label / shadow_destination）。

## 设计要点（做的时候）

- **影子规则从哪来**：`routing.json` 加一个可选 `shadow` 块，存一套覆盖后的
  settings（如 `{"toolRouting": {...}, "searchResultDiscount": 0.5}`）。
  块不存在 = shadow 关闭，零开销零行为差异。
- **公平对比**：影子计算复用同一个 `InspectResult` 调 `resolve()` 第二次，
  两套规则看的是同一份请求特征，差异只来自规则本身。
- **查看**：`usage shadow` 子命令出上面对比视图（复用 `--since` / `--profile`）。
- **标注语义**：影子目的地如果与实际相同也照记（一致率本身是要看的数据）。

## 建议执行

单独一轮做，约一天。改动面：config（shadow 块解析）、server（第二次 resolve + 日志透传）、
logging（两个新字段）、cli（`usage shadow`）。
