# MCP 插件管理聊天指令 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让白名单（管理员）会话在微信里用 `/mcp list/add/remove/enable/disable` 管理 MCP 插件，复用现有 slash 解析与 `is_tool_admin` 门控。

**Architecture:** `slash.py` 识别 `mcp` 指令；`engine.py` 把 `conv_key` 透传进命令分发，新增 `_handle_mcp`（先门控、再解析子命令、调用模块级 `plugin_manager` 单例、格式化文本输出、捕获异常）。插件管理器、基座、存储零改动。

**Tech Stack:** Python 3.11+，pytest。测试在 `backend/tests/`（conftest 已把 db 指到临时目录）。

**参考 spec:** `docs/superpowers/specs/2026-08-19-mcp-chat-commands-design.md`

---

## 文件职责总览

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `backend/app/agent/slash.py` | 修改 | `parse_command` 识别 `mcp` kind |
| `backend/app/agent/engine.py` | 修改 | `_handle_command` 增 `conv_key`；新增 `MCP_HELP`、`_handle_mcp` 及子命令助手；`HELP_TEXT` 加 /mcp；导入 `plugin_manager`/`PluginError`/`McpDiscoveryError` |
| `backend/tests/test_slash.py` | 修改 | /mcp 解析测试 |
| `backend/tests/test_engine.py` | 修改 | /mcp 门控与各子命令测试 |

**关键签名（前后一致）：**
- `parse_command` 新增分支返回 `ChannelCommand(kind="mcp", query=query)`
- `_handle_command(conv_key: str, cmd: ChannelCommand) -> str`
- `_handle_mcp(conv_key: str, query: str) -> str`
- 复用：`plugin_manager.install(name, config) -> int`、`.uninstall(name) -> bool`、`.enable(name) -> int`、`.disable(name) -> bool`、`.list() -> list[dict]`（行键 `name/enabled/registered/status`）

---

### Task 1: slash.py 识别 /mcp

**Files:**
- Modify: `backend/app/agent/slash.py`
- Test: `backend/tests/test_slash.py`

- [ ] **Step 1: 写失败测试** — 在 `test_slash.py` 末尾追加：
```python
def test_mcp_command_no_args() -> None:
    cmd = parse_command("/mcp")
    assert cmd is not None
    assert cmd.kind == "mcp"
    assert cmd.query == ""


def test_mcp_command_list() -> None:
    cmd = parse_command("/mcp list")
    assert cmd is not None
    assert cmd.kind == "mcp"
    assert cmd.query == "list"


def test_mcp_command_add_with_args() -> None:
    cmd = parse_command("/mcp add foo http://x/mcp")
    assert cmd is not None
    assert cmd.kind == "mcp"
    assert cmd.query == "add foo http://x/mcp"
```

- [ ] **Step 2: 运行确认失败**
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend && .venv/bin/python -m pytest tests/test_slash.py -q`
Expected: FAIL（新测试断言 `kind == "mcp"`，但当前 `/mcp` 落入 help 兜底）

- [ ] **Step 3: 实现** — 在 `slash.py` 的 `parse_command` 中、`遗忘/forget` 分支之后、兜底 `return ChannelCommand(kind="help", ...)` 之前加入：
```python
    if name == "mcp":
        return ChannelCommand(kind="mcp", query=query)
```

- [ ] **Step 4: 运行确认通过**
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend && .venv/bin/python -m pytest tests/test_slash.py -q`
Expected: PASS（含新增 3 个，既有不回归）

- [ ] **Step 5: 提交**
```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/slash.py backend/tests/test_slash.py
git commit -m "feat(slash): parse_command 识别 /mcp 指令" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: engine /mcp 处理器 + 门控

**Files:**
- Modify: `backend/app/agent/engine.py`
- Test: `backend/tests/test_engine.py`

- [ ] **Step 1: 写失败测试** — 在 `test_engine.py` 顶部 import 区加入 `from mcp_discovery import McpDiscoveryError`；文件末尾追加：
```python
def test_mcp_denied_for_non_admin(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: False)
    assert "仅限管理员" in engine.handle_message("conv-1", "/mcp list")


def test_mcp_list_empty_for_admin(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    reply = engine.handle_message("conv-1", "/mcp")
    assert "还没有装入 MCP 插件" in reply


def test_mcp_add_and_remove_flow(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    monkeypatch.setattr(engine.plugin_manager, "install", lambda name, cfg: 3)
    reply = engine.handle_message("conv-1", "/mcp add foo http://x/mcp")
    assert "已装入插件 foo" in reply and "3 个工具" in reply
    monkeypatch.setattr(engine.plugin_manager, "uninstall", lambda name: True)
    assert "已卸下插件 foo" in engine.handle_message("conv-1", "/mcp remove foo")


def test_mcp_add_missing_args_shows_usage(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    assert "用法" in engine.handle_message("conv-1", "/mcp add foo")


def test_mcp_add_install_failure(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)

    def boom(name, cfg):
        raise McpDiscoveryError("连不上", code="CONNECT_FAILED")

    monkeypatch.setattr(engine.plugin_manager, "install", boom)
    assert "装入失败" in engine.handle_message("conv-1", "/mcp add foo http://x")


def test_mcp_enable_and_disable(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    monkeypatch.setattr(engine.plugin_manager, "enable", lambda name: 2)
    monkeypatch.setattr(engine.plugin_manager, "disable", lambda name: True)
    assert "已启用插件 foo" in engine.handle_message("conv-1", "/mcp enable foo")
    assert "已停用插件 foo" in engine.handle_message("conv-1", "/mcp disable foo")


def test_mcp_remove_missing(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    monkeypatch.setattr(engine.plugin_manager, "uninstall", lambda name: False)
    assert "没有名为" in engine.handle_message("conv-1", "/mcp remove ghost")


def test_mcp_unknown_subcommand_shows_usage(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    assert "用法" in engine.handle_message("conv-1", "/mcp wat")
```

- [ ] **Step 2: 运行确认失败**
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend && .venv/bin/python -m pytest tests/test_engine.py -q`
Expected: FAIL（`/mcp list` 走 help 兜底，无"仅限管理员"等；或 engine 无 plugin_manager/is_tool_admin 门控逻辑）

- [ ] **Step 3: 实现** — 修改 `engine.py`：
  1. import 区加入：
```python
from mcp_discovery import McpDiscoveryError
from app.agent.tools.mcp_plugins import PluginError, plugin_manager
```
  2. 在 `HELP_TEXT` 中、`"其他消息直接发给助理。"` 之前加入这几行：
```python
    "/mcp(/mcp list) - 列出已装 MCP 插件(仅管理员)\n"
    "/mcp add <名称> <url> - 装入 MCP 插件\n"
    "/mcp remove <名称> - 卸下 MCP 插件\n"
    "/mcp enable|disable <名称> - 启用/停用 MCP 插件\n"
```
  3. `handle_message` 中 `return _handle_command(cmd)` 改为 `return _handle_command(conv_key, cmd)`。
  4. `_handle_command` 签名改为 `def _handle_command(conv_key: str, cmd: ChannelCommand) -> str:`，并在 ping 分支之前加：
```python
    if cmd.kind == "mcp":
        return _handle_mcp(conv_key, cmd.query)
```
  5. 在 `_handle_model` 之后新增（含子命令助手）：
```python
MCP_HELP = (
    "用法:\n"
    "/mcp 或 /mcp list - 列出已装插件\n"
    "/mcp add <名称> <url> - 装入插件\n"
    "/mcp remove <名称> - 卸下插件\n"
    "/mcp enable <名称> 或 /mcp disable <名称> - 启用/停用插件"
)


def _handle_mcp(conv_key: str, query: str) -> str:
    """/mcp:管理 MCP 插件(仅管理员)。list/add/remove/enable/disable。"""
    if not is_tool_admin(conv_key):
        return "MCP 插件管理仅限管理员使用。"
    sub, _, arg = query.partition(" ")
    sub = sub.lower().strip()
    arg = arg.strip()
    if sub in {"", "list"}:
        return _mcp_list()
    if sub == "add":
        return _mcp_add(arg)
    if sub == "remove":
        return _mcp_remove(arg)
    if sub == "enable":
        return _mcp_enable(arg)
    if sub == "disable":
        return _mcp_disable(arg)
    return MCP_HELP


def _mcp_list() -> str:
    rows = plugin_manager.list()
    if not rows:
        return f"还没有装入 MCP 插件。\n{MCP_HELP}"
    lines = ["已装 MCP 插件(* 为启用):"]
    for row in rows:
        mark = "*" if row["enabled"] else " "
        lines.append(f"{mark} {row['name']}({len(row['registered'])} 个工具) {row['status']}")
    return "\n".join(lines)


def _mcp_add(arg: str) -> str:
    name, _, url = arg.partition(" ")
    name, url = name.strip(), url.strip()
    if not name or not url:
        return "用法:/mcp add <名称> <url>"
    try:
        n = plugin_manager.install(name, {"type": "streamable_http", "url": url})
    except (McpDiscoveryError, PluginError) as exc:
        return f"装入失败:{exc}"
    return f"已装入插件 {name},发现 {n} 个工具。"


def _mcp_remove(arg: str) -> str:
    name = arg.strip()
    if not name:
        return "用法:/mcp remove <名称>"
    if plugin_manager.uninstall(name):
        return f"已卸下插件 {name}。"
    return f"没有名为「{name}」的插件,/mcp 查看已装插件。"


def _mcp_enable(arg: str) -> str:
    name = arg.strip()
    if not name:
        return "用法:/mcp enable <名称>"
    try:
        n = plugin_manager.enable(name)
    except (McpDiscoveryError, PluginError) as exc:
        return f"启用失败:{exc}"
    return f"已启用插件 {name}({n} 个工具)。"


def _mcp_disable(arg: str) -> str:
    name = arg.strip()
    if not name:
        return "用法:/mcp disable <名称>"
    if plugin_manager.disable(name):
        return f"已停用插件 {name}(配置保留,/mcp enable 可恢复)。"
    return f"没有名为「{name}」的插件,/mcp 查看已装插件。"
```

- [ ] **Step 4: 运行确认通过**
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend && .venv/bin/python -m pytest tests/test_engine.py -q`
Expected: PASS（新增 8 个 + 既有不回归）

- [ ] **Step 5: 提交**
```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/engine.py backend/tests/test_engine.py
git commit -m "feat(engine): /mcp 插件管理指令(仅管理员)" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 文档 + 全量回归

**Files:**
- Modify: `backend/README.md`（工具/指令相关章节补 /mcp）

- [ ] **Step 1: 更新文档** — 在 `backend/README.md` 提到斜杠指令或工具的部分补一句：管理员可用 `/mcp` 系列指令装入/卸下/启停 MCP 插件。措辞与现有风格一致。

- [ ] **Step 2: 全量回归**
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend && .venv/bin/python -m pytest tests app/agent/mcp/tests -q`
Expected: 全部通过（记录总数）

- [ ] **Step 3: 提交**
```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/README.md
git commit -m "docs(mcp): 补充 /mcp 管理指令说明" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 完成标准

- 管理员会话 `/mcp add <名称> <url>` 装入后即可在对话真实调用；`/mcp list` 可见；`/mcp remove/enable/disable` 生效。
- 非管理员任何 /mcp 指令被拒。
- 插件管理器/基座/存储/registry/calls/main 零改动；既有行为不回归；全量测试绿。
