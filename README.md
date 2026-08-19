# FeiBot

微信个人助理 agent harness：微信渠道接入 + agent 编排 + LLM + 工具/MCP，
分层单向依赖、协议层可复用。

## 仓库布局

| 目录 | 说明 |
| --- | --- |
| [`backend/`](backend/) | 核心实现：渠道、agent 编排、LLM、工具、MCP。详见 [`backend/README.md`](backend/README.md) |
| `frontend/` | 前端（占位，暂未实现） |
| [`docs/superpowers/`](docs/superpowers/) | 设计文档（`specs/`）与实现计划（`plans/`） |

## 分层与依赖方向

```
channels（微信 iLink 协议）→ agent（编排）→ llm / db
```

agent 层零渠道依赖；engine 只产出文本，渠道 ingress 负责分片发送。
工具（内置 + MCP）由白名单门控，仅管理员会话可见。

## 可复用的纯协议层基座

两个自包含、可 `pip install` 的协议包，零业务/数据库/Web 框架耦合：

- `backend/app/llm/llm_protocols/`（`llm-protocols`）— LLM 提供商协议层
  （OpenAI `chat.completions`/`responses`、Anthropic `messages`、Gemini `generateContent`）。
- `backend/app/agent/mcp/`（`mcp-discovery`）— MCP server 工具发现协议层
  （stdio / Streamable HTTP / legacy HTTP+SSE），见其 [`README`](backend/app/agent/mcp/README.md)。

## 快速开始

见 [`backend/README.md`](backend/README.md#运行)：

```bash
cd backend
.venv/bin/python -m app.main   # 首次运行扫码登录，Ctrl+C 退出
```

## 测试

```bash
cd backend
.venv/bin/python -m pytest tests -q
```
