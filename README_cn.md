<div align="center">
  <h1>awerouter: 智能 LLM 路由</h1>
  <p><strong>轻量任务走 Flash，复杂决策走 Pro。</strong></p>
  <p>按请求结构信号做确定性路由的 Anthropic 透明代理——不猜语义、不用关键词、不跑分类器。</p>
  <p>
    <a href="./README.md">English</a> ·
    <strong>简体中文</strong>
  </p>
  <p>
    <img src="https://img.shields.io/badge/version-0.1.0-7C3AED?style=flat-square" alt="Version">
    <img src="https://img.shields.io/badge/python-%E2%89%A53.9-0EA5E9?style=flat-square" alt="Python">
  </p>
  <p>
    <img src="https://img.shields.io/badge/status-alpha-c96a3d?style=flat-square" alt="Status">
    <img src="https://img.shields.io/badge/install-pip-22C55E?style=flat-square" alt="pip">
    <img src="https://img.shields.io/badge/platform-terminal-334155?style=flat-square" alt="Platform">
    <img src="https://img.shields.io/pypi/dm/awerouter?style=flat-square" alt="Downloads">
    <img src="https://img.shields.io/github/stars/owner/awerouter?style=flat-square" alt="Stars">
  </p>
</div>

> 按结构信号把 Claude Code 流量拆分到不同 provider，省钱不降质。

## 安装

```bash
pip install awerouter
```

## 快速开始

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

**providers.json** — 端点 + 密钥，按 agent 分组（`config show` 自动脱敏）：

```json
{
  "claude": {
    "stepfun":   { "base_url": "https://api.stepfun.com/step_plan", "auth": "${STEPFUN_AUTH_TOKEN}" },
    "anthropic": { "base_url": "https://api.anthropic.com",          "auth": "${ANTHROPIC_KEY}" }
  },
  "codex": {
    "stepfun": { "base_url": "https://api.stepfun.com/v1", "auth": "${STEPFUN_AUTH_TOKEN}" }
  }
}
```

鉴权头**根据 `base_url` 自动判断**：`anthropic.com` → `x-api-key`（裸 token）；其他 → `Authorization`（自动补 `Bearer `）。除非启发式判断错了，否则不需要填 `auth_header`。

**routing.json** — 路由策略，不含密钥（可以进 git）：

```json
{
  "settings": {
    "backgroundModel": "flash",
    "thinkModel": "pro"
  },
  "cc-router-1": {
    "agent": "claude",
    "longContextThreshold": 8000,
    "destinations": {
      "flash": "stepfun,step-3.7-flash",
      "pro":   "anthropic,claude-opus-5"
    }
  }
}
```

`settings` 可省（默认 `flash`/`pro`）。它定义 CC 发送的档位 model id：background（Haiku 档）和 think（Opus 档）。主循环用 `auto`——由 L3 按难度路由。在 aweswitch profile 里设：`ANTHROPIC_DEFAULT_HAIKU_MODEL=flash`、`ANTHROPIC_MODEL=auto`、`ANTHROPIC_DEFAULT_OPUS_MODEL=pro`。

密钥用 `${ENV_VAR}` 引用。缺失的环境变量在启动时报错退出。

> **基于 profile 的路由：** `routing.json` 用 profile id 分组（类似 aweswitch）。`awerouter serve <profile>` 启动其中一个；只有一个 profile 时自动选择。`agent` 字段把 profile 映射到 providers.json 的分组。

## 路由逻辑

三层 first-match-wins 管线，逐请求评估：

| 层 | 信号 | 决策 |
|----|------|------|
| L1 能力护栏 | body 含 `web_search` 工具 | **pro**（flash 不支持） |
| L2 档位匹配 | `model == c1/flash` 或 `c1/think` | flash / pro |
| L3 难度评分 | token 超阈值，或含图片 | **pro**；否则 **flash** |

CC 的 `/model` 选择器设置 tier model id（c1/flash / c1/pro / c1/think）。awerouter 直接读取该字段做路由——不猜语义、不用关键词、不跑分类器。

## 命令

```bash
awerouter init                        # 创建默认配置（= config init）
awerouter add                         # 交互式添加 profile（含新 provider）
awerouter list                        # 列出 profile（名字、agent、flash、pro、阈值）
awerouter show [PROFILE]              # 查看单个 profile 或全部配置（脱敏）
awerouter serve [PROFILE] [--port 20128] [--host 127.0.0.1]
awerouter <PROFILE>                   # serve 的简写
awerouter config path | show | edit | init
awerouter log [--lines 20]
awerouter stats
awerouter calibrate
```

`calibrate` 展示 L3 流量（受阈值影响的层）的消息 token 分布（仅统计 messages，不含 system prompt 与 tools 定义），并在 p90/p95/p99 处建议 `longContextThreshold` 候选值。跑一段真实流量后执行，再编辑 `routing.json`。

## 开发

```bash
git clone <repo-url>
cd awerouter
pip install -e ".[dev]"
pytest
```
