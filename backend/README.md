# feibot backend

微信个人助理的最小 agent harness:渠道接入 + agent 编排,分层单向依赖。

## 目录结构

- `app/main.py` — 入口:装配 agent 回调,启动渠道 ingress(阻塞循环)
- `app/config.py` — 路径、密钥(env `FEIBOT_CHANNEL_SECRET`)、渠道常量
- `app/api/` — HTTP API 层(占位,后续引入 FastAPI)
- `app/channels/` — 渠道协议层:微信扫码登录 + 长轮询收发(`ingress.py`),协议封装在 `channels/wechat/`(wechat-ilink 包)
- `app/agent/` — agent 编排层:引擎、slash 指令、上下文、记忆、会话、提示词、skills、tools(内置工具 + 白名单门控)、MCP(占位)
- `app/llm/` — LLM 层:`client.py` 对上层只暴露 `generate(messages)`(经 `llm_protocols/` 通用协议包真实调用,无配置回退 EchoLLM);`registry.py` 管模型配置存取/加解密;`manage.py` 是配置 CLI;`llm_protocols/` 为独立协议包(勿改)
- `app/db/` — sqlite 持久化:kv 表(渠道 token/游标、当前模型)+ sessions 表 + model_configs 表 + messages 表(会话历史)
- `app/jobs/` — 后台任务层(占位:outbox 投递、定时任务)
- `USER.md` — 用户画像,由助理根据对话自动维护(见下)
- `.feibot/` — 运行时数据目录，不进 git；sqlite 数据库位于 `.feibot/feibot.db`，已安装 Skill 位于 `.feibot/skills/<skill-id>/`
- `tests/` — 回归测试

依赖方向:`channels` → `agent` → `llm` / `db`;agent 层零渠道依赖,engine 返回文本、ingress 负责分片发送。

## 用户画像与长期记忆(自动形成,用户只聊天不写文件)

每轮对话结束后,`agent/memory/distill.py` 用当前模型做一次提炼,一次调用产出两类结果:

- **profile** — 用户的稳定属性(称呼/角色/工作习惯/沟通偏好/时区),写入 `backend/USER.md` 对应小节;
- **facts** — 其他值得长期记住的事实(项目背景、正在进行的工作等),追加进 `backend/MEMORY.md`。

两者都会在下一轮作为 system 消息注入上下文(画像前缀"以下是用户画像,请在回复中参考:",记忆前缀"以下是你记住的关于用户的事:")。因此用户只需跟助理聊天,画像和记忆自动积累,无需手写任何文件。

- `USER.md` / `MEMORY.md` 都是项目级内容文件(进 git,仿 AGENTS.md 惯例),可读可手改;手工修改会被保留,直到对应内容下次被对话更新。
- 提炼为 best-effort:失败只记日志,不影响主回复。提炼 prompt 里带已有画像与记忆原文,用于去重和覆盖旧值。
- `/记忆` 查看当前画像与记忆;`/遗忘` 一键清空两者。

## 工具(仅白名单可用)

agent 可以在对话里调用工具拿实时信息、执行操作。采用提示词 JSON 约定(`llm_protocols` 是纯协议层不支持原生 function calling):白名单会话的上下文注入工具说明,模型输出一行 `{"tool": "...", "args": {...}}`,engine 执行后把结果回填给模型,如此循环直到给出自然语言回复(上限 3 轮)。

内置工具(`agent/tools/builtin.py`):`current_time` 当前时间、`pwd` 工作目录、`list_dir` 列目录、`shell` 执行命令(30s 超时、输出截断、无 stdin)。加新工具:写 handler + `register_tool(ToolSpec(...))`。

**安全**:任何能给 bot 发消息的微信用户都进得了对话,因此工具只对白名单开放(`config.is_tool_admin`,默认只有主人,可用 env `FEIBOT_TOOL_ADMINS` 逗号分隔覆盖)。非白名单会话连工具说明都看不到,纯聊天。`shell` 是高危能力——bot 进程有什么权限它就能做什么,务必守住白名单。

### MCP 插件(装/卸远端工具)

MCP server 以插件形式接入(`agent/tools/mcp_plugins.py`):`discover()` 取回的工具
清单加 `{插件名}__` 前缀注册进同一注册表,与内置工具统一被 engine 注入与调用;
配置持久化在 sqlite `mcp_plugins` 表,启动自动重载启用的插件。handler 通过基座
`call_tool` 发起真实远程调用(每次新开连接),工具报错时以「工具执行失败:…」
回填给模型。

```python
from app.agent.tools.mcp_plugins import plugin_manager
plugin_manager.install("12306-mcp", {"type": "streamable_http", "url": "https://.../mcp"})
plugin_manager.list(); plugin_manager.disable("12306-mcp"); plugin_manager.uninstall("12306-mcp")
```

管理员可在微信里用 `/mcp` 系列指令管理 MCP 插件:`/mcp` 或 `/mcp list` 查看已装插件,`/mcp add <名称> <url>` 装入(仅支持 url/streamable_http 类),`/mcp remove <名称>` 卸下,`/mcp enable|disable <名称>` 启停。非管理员不可用。

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
