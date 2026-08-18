# feibot backend

微信个人助理的最小 agent harness:渠道接入 + agent 编排,分层单向依赖。

## 目录结构

- `app/main.py` — 入口:装配 agent 回调,启动渠道 ingress(阻塞循环)
- `app/config.py` — 路径、密钥(env `FEIBOT_CHANNEL_SECRET`)、渠道常量
- `app/api/` — HTTP API 层(占位,后续引入 FastAPI)
- `app/channels/` — 渠道协议层:微信扫码登录 + 长轮询收发(`ingress.py`),协议封装在 `channels/wechat/`(wechat-ilink 包)
- `app/agent/` — agent 编排层:引擎、slash 指令、上下文、记忆、会话、提示词、skills、tools、MCP(部分为占位)
- `app/llm/` — LLM 层:`client.py` 对上层只暴露 `generate(messages)`(经 `llm_protocols/` 通用协议包真实调用,无配置回退 EchoLLM);`registry.py` 管模型配置存取/加解密;`manage.py` 是配置 CLI;`llm_protocols/` 为独立协议包(勿改)
- `app/db/` — sqlite 持久化:kv 表(渠道 token/游标、当前模型)+ sessions 表 + model_configs 表
- `app/jobs/` — 后台任务层(占位:outbox 投递、定时任务)
- `skills/` — 项目根级 skill 内容目录(每个子目录一个 SKILL.md)
- `USER.md` — 用户画像(见下)
- `.feibot/` — 运行时数据目录(sqlite 库),不进 git
- `tests/` — 回归测试

依赖方向:`channels` → `agent` → `llm` / `db`;agent 层零渠道依赖,engine 返回文本、ingress 负责分片发送。

## USER.md 用户画像

`backend/USER.md` 是项目级内容文件(进 git,仿 AGENTS.md 惯例)。首次运行时若不存在,自动创建中文模板;填写后内容会作为 system 消息注入每轮对话(前缀"以下是用户画像,请在回复中参考:")。HTML 注释和留空的小节不会被注入。

## 模型配置

模型配置存 sqlite `model_configs` 表,api_key 加密落盘(密钥与渠道 token 同源于 env `FEIBOT_CHANNEL_SECRET`)。协议取值见 `llm_protocols.ModelApiProtocol`:`openai_chat_completions` / `openai_responses` / `anthropic_messages` / `gemini_generate_content`。

CLI 管理:

```bash
cd backend
.venv/bin/python -m app.llm.manage add --name claude --protocol anthropic_messages \
    --model claude-sonnet-4-5 --base-url https://api.anthropic.com --api-key sk-xxx
.venv/bin/python -m app.llm.manage list          # key 脱敏,* 为当前
.venv/bin/python -m app.llm.manage use claude    # 切换当前模型
.venv/bin/python -m app.llm.manage remove claude
```

微信里也可以发 `/模型` 查看全部配置、`/模型 <名称>` 切换。未配置模型时回复走 echo 桩并附提示。

## 运行

```bash
cd backend
.venv/bin/python -m app.main   # 首次运行扫码登录,Ctrl+C 退出
```

## 测试

```bash
cd backend
.venv/bin/python -m pytest tests -q
```
