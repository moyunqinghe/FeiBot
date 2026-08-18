# MCP tools/call（真实远程调用，第二步）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 MCP 发现基座补 `tools/call`，并把插件的占位 handler 换成真实远程调用，让装入的工具能被真正执行。

**Architecture:** 基座新增公共入口 `call_tool()`（镜像 `discover()`，每次新开连接：initialize → tools/call → 归一化提取 → 关闭）与结果归一化 `_extract_tool_result`、错误码 `TOOL_ERROR`。插件 `_register_tools` 增配置参数，handler 闭包捕获归一化配置并在被调用时发起 `call_tool`、stringify 结果。engine/calls/registry/store/main 零改动。

**Tech Stack:** Python 3.11+，`mcp_discovery`（editable 安装），pytest。基座测试在 `app/agent/mcp/tests/`（conftest 已注入包路径），插件测试在 `backend/tests/`（conftest 已把 db 指到临时目录）。

**参考 spec:** `docs/superpowers/specs/2026-08-18-mcp-tools-call-design.md`

---

## 文件职责总览

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `backend/app/agent/mcp/mcp_discovery/client.py` | 修改 | 加 `TOOL_ERROR`、`_coerce_config`、`_Session.call_tool`、`call_tool()`、`_content_text`、`_extract_tool_result` |
| `backend/app/agent/mcp/mcp_discovery/__init__.py` | 修改 | 导出 `call_tool`、`TOOL_ERROR` |
| `backend/app/agent/mcp/tests/stdio_mock_server.py` | 修改 | `handle_jsonrpc` 加 `tools/call` 分支（echo 文本 / fail isError） |
| `backend/app/agent/mcp/tests/test_call.py` | 新建 | 基座 call 相关测试（提取/coerce/stdio 调用） |
| `backend/app/agent/tools/mcp_plugins.py` | 修改 | `_stringify`、`_make_handler`、`_register_tools` 增 config、四条注册路径传 config、删占位 handler、调用超时常量 |
| `backend/tests/test_mcp_plugins.py` | 修改 | handler 单测；集成测试从占位断言改为真实调用 |
| `backend/app/agent/mcp/README.md`、`backend/README.md` | 修改 | 文档补 tools/call |

**关键签名（后续任务必须一致）：**
- 基座：`call_tool(config, tool_name, arguments=None, *, timeout_seconds=30.0, protocol_version=PROTOCOL_VERSION, client_info=None) -> Any`；`_coerce_config(config) -> McpServerConfig`；`_extract_tool_result(raw) -> Any`；`_content_text(content) -> str`；`_Session.call_tool(self, name, arguments) -> Any`。
- 插件：`MCP_CALL_TIMEOUT_SECONDS = 30.0`；`_stringify(value) -> str`；`_make_handler(config, tool_name)`；`_register_tools(self, name, tools, config) -> set[str]`。

---

### Task 1: 基座加 TOOL_ERROR + 结果提取

**Files:**
- Modify: `backend/app/agent/mcp/mcp_discovery/client.py`（错误码区加 `TOOL_ERROR`；文件末尾共享工具区加 `_content_text`、`_extract_tool_result`）
- Modify: `backend/app/agent/mcp/mcp_discovery/__init__.py`（导出 `TOOL_ERROR`）
- Test: `backend/app/agent/mcp/tests/test_call.py`（新建，先只放提取测试）

- [ ] **Step 1: 写失败测试** — 新建 `backend/app/agent/mcp/tests/test_call.py`：

```python
"""call_tool 的结果归一化与配置收敛测试(真实 stdio 调用在后续任务补)。"""

from __future__ import annotations

import pytest

from mcp_discovery import TOOL_ERROR, McpDiscoveryError
from mcp_discovery.client import _content_text, _extract_tool_result


def test_extract_text_content():
    raw = {"content": [{"type": "text", "text": "hello"}], "isError": False}
    assert _extract_tool_result(raw) == "hello"


def test_extract_multiple_text_blocks_joined():
    raw = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
    assert _extract_tool_result(raw) == "a\nb"


def test_extract_prefers_structured_content():
    raw = {"structuredContent": {"k": 1}, "content": [{"type": "text", "text": "x"}]}
    assert _extract_tool_result(raw) == {"k": 1}


def test_extract_non_text_content_fallback_returns_raw():
    raw = {"content": [{"type": "image", "data": "..."}]}
    assert _extract_tool_result(raw) == raw


def test_extract_non_mapping_passthrough():
    assert _extract_tool_result("plain") == "plain"
    assert _extract_tool_result(42) == 42


def test_extract_is_error_raises_tool_error():
    raw = {"content": [{"type": "text", "text": "bad input"}], "isError": True}
    with pytest.raises(McpDiscoveryError) as ei:
        _extract_tool_result(raw)
    assert ei.value.code == TOOL_ERROR
    assert "bad input" in str(ei.value)


def test_content_text_ignores_non_text_and_handles_none():
    assert _content_text([{"type": "image"}, {"type": "text", "text": "x"}]) == "x"
    assert _content_text(None) == ""
```

- [ ] **Step 2: 运行确认失败**
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/mcp && ../../../../.venv/bin/python -m pytest tests/test_call.py -q`
Expected: FAIL（`ImportError: cannot import name 'TOOL_ERROR'`）

- [ ] **Step 3: 实现** — 在 `client.py` 错误码区（`PROTOCOL_ERROR` 之后）加：
```python
TOOL_ERROR = "TOOL_ERROR"              # 工具自身返回 isError(协议本身正常)
```
在文件末尾共享工具区（`_stderr_text` 附近）加：
```python
def _content_text(content: Any) -> str:
    """从 tools/call 的 content 列表里抽取 type==text 的文本,换行拼接。"""
    if not isinstance(content, list):
        return ""
    parts = [
        str(item.get("text") or "")
        for item in content
        if isinstance(item, Mapping) and item.get("type") == "text"
    ]
    return "\n".join(part for part in parts if part)


def _extract_tool_result(raw: Any) -> Any:
    """归一化 tools/call 的 result:isError 抛 TOOL_ERROR;优先 structuredContent,否则取文本。"""
    if not isinstance(raw, Mapping):
        return raw
    if raw.get("isError"):
        message = _content_text(raw.get("content")) or "MCP 工具返回 isError=true"
        raise McpDiscoveryError(message, code=TOOL_ERROR)
    structured = raw.get("structuredContent")
    if structured is not None:
        return structured
    text = _content_text(raw.get("content"))
    if text:
        return text
    return dict(raw)
```
在 `__init__.py` 的 import 块与 `__all__` 中加入 `TOOL_ERROR`。

- [ ] **Step 4: 运行确认通过**
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/mcp && ../../../../.venv/bin/python -m pytest tests/test_call.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: 提交**
```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/mcp/mcp_discovery/client.py backend/app/agent/mcp/mcp_discovery/__init__.py backend/app/agent/mcp/tests/test_call.py
git commit -m "feat(mcp): 基座加 TOOL_ERROR 与 tools/call 结果归一化" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 基座抽 _coerce_config（discover 复用）

**Files:**
- Modify: `backend/app/agent/mcp/mcp_discovery/client.py`
- Test: `backend/app/agent/mcp/tests/test_call.py`（追加）

- [ ] **Step 1: 写失败测试** — 在 `test_call.py` 顶部 import 块加入 `from mcp_discovery import CONFIG_INVALID` 与 `from mcp_discovery.client import _coerce_config`，文件末尾追加：
```python
def test_coerce_config_accepts_mapping_and_model():
    from mcp_discovery import McpServerConfig
    cfg = _coerce_config({"transport": "http", "url": "http://x"})
    assert cfg.url == "http://x"
    assert _coerce_config(cfg) is cfg


def test_coerce_config_rejects_non_mapping():
    with pytest.raises(McpDiscoveryError) as ei:
        _coerce_config("not a mapping")
    assert ei.value.code == CONFIG_INVALID


def test_coerce_config_rejects_invalid_mapping():
    with pytest.raises(McpDiscoveryError) as ei:
        _coerce_config({"transport": "stdio"})  # 缺 command
    assert ei.value.code == CONFIG_INVALID
```

- [ ] **Step 2: 运行确认失败**
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/mcp && ../../../../.venv/bin/python -m pytest tests/test_call.py -q`
Expected: FAIL（`ImportError: cannot import name '_coerce_config'`）

- [ ] **Step 3: 实现** — 在 `discover()` 之前加：
```python
def _coerce_config(config: McpServerConfig | Mapping[str, Any]) -> McpServerConfig:
    """把配置收敛为 McpServerConfig;非法配置抛 McpDiscoveryError(CONFIG_INVALID)。"""
    if isinstance(config, McpServerConfig):
        return config
    if not isinstance(config, Mapping):
        raise McpDiscoveryError(
            f"MCP server 配置必须是 mapping 或 McpServerConfig,实际是 {type(config).__name__}。",
            code=CONFIG_INVALID,
        )
    try:
        return McpServerConfig.model_validate(dict(config))
    except ValidationError as exc:
        raise McpDiscoveryError(f"MCP server 配置无效：{exc}", code=CONFIG_INVALID, cause=exc) from exc
```
然后把 `discover()` 开头那段 `if not isinstance(config, McpServerConfig): ...` 整体替换为一行 `config = _coerce_config(config)`（保持后续 `session = _build_session(...)` 不变）。

- [ ] **Step 4: 运行确认通过**（discover 相关既有测试也须保持绿）
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/mcp && ../../../../.venv/bin/python -m pytest tests/ -q`
Expected: PASS（含新增 3 个；既有 discover 测试不回归）

- [ ] **Step 5: 提交**
```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/mcp/mcp_discovery/client.py backend/app/agent/mcp/tests/test_call.py
git commit -m "refactor(mcp): discover 配置校验抽为 _coerce_config" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: stdio mock server 支持 tools/call

**Files:**
- Modify: `backend/app/agent/mcp/tests/stdio_mock_server.py`

说明：`tools/call` 与 `tools/list` 相互独立，给 `tools/call` 增加 echo/fail 处理**不改 TOOLS 列表**，因此既有发现类测试（断言 3 个工具）不受影响。

- [ ] **Step 1: 实现** — 在 `handle_jsonrpc` 中、`tools/list` 分支之后、兜底 `_error` 之前加入：
```python
    if method == "tools/call":
        params = request.get("params") or {}
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        if name == "echo":
            text = str(args.get("text") or "")
            return _result(
                request_id,
                {"content": [{"type": "text", "text": f"echo: {text}"}], "isError": False},
            )
        if name == "fail":
            return _result(
                request_id,
                {"content": [{"type": "text", "text": "boom: tool failed"}], "isError": True},
            )
        return _error(request_id, -32601, f"Unknown tool: {name}")
```

- [ ] **Step 2: 快速验证 mock 本身**（不新增测试文件，直接跑既有发现测试确认没破坏 + 手工跑一次 tools/call）
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/mcp && ../../../../.venv/bin/python -m pytest tests/ -q`
Expected: PASS（既有测试全绿）
Run: `../../../../.venv/bin/python -c "import json,subprocess,sys; p=subprocess.Popen([sys.executable,'tests/stdio_mock_server.py'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True); p.stdin.write(json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'echo','arguments':{'text':'hi'}}})+'\n'); p.stdin.flush(); print(p.stdout.readline()); p.kill()"`
Expected: 输出含 `"text": "echo: hi"`

- [ ] **Step 3: 提交**
```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/mcp/tests/stdio_mock_server.py
git commit -m "test(mcp): stdio mock 支持 tools/call(echo/fail)" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 基座 call_tool() 公共入口

**Files:**
- Modify: `backend/app/agent/mcp/mcp_discovery/client.py`（`_Session` 加 `call_tool`；`discover` 之后加公共 `call_tool()`）
- Modify: `backend/app/agent/mcp/mcp_discovery/__init__.py`（导出 `call_tool`）
- Test: `backend/app/agent/mcp/tests/test_call.py`（追加真实 stdio 调用）

- [ ] **Step 1: 写失败测试** — 在 `test_call.py` 顶部 import 块加入 `import sys`、`from pathlib import Path`、`from mcp_discovery import call_tool`，并加常量 `_STDIO_MOCK = Path(__file__).resolve().parent / "stdio_mock_server.py"`；文件末尾追加：
```python
def test_call_tool_stdio_echo():
    config = {"transport": "stdio", "command": sys.executable, "args": [str(_STDIO_MOCK)]}
    result = call_tool(config, "echo", {"text": "你好"}, timeout_seconds=15)
    assert result == "echo: 你好"


def test_call_tool_stdio_is_error_raises_tool_error():
    config = {"transport": "stdio", "command": sys.executable, "args": [str(_STDIO_MOCK)]}
    with pytest.raises(McpDiscoveryError) as ei:
        call_tool(config, "fail", {}, timeout_seconds=15)
    assert ei.value.code == TOOL_ERROR


def test_call_tool_rejects_invalid_config():
    with pytest.raises(McpDiscoveryError) as ei:
        call_tool({"transport": "stdio"}, "echo", {})
    assert ei.value.code == CONFIG_INVALID
```

- [ ] **Step 2: 运行确认失败**
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/mcp && ../../../../.venv/bin/python -m pytest tests/test_call.py -q`
Expected: FAIL（`ImportError: cannot import name 'call_tool'`）

- [ ] **Step 3: 实现** — 在 `_Session` 类的 `list_tools` 之后加：
```python
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return self._request("tools/call", {"name": name, "arguments": arguments})
```
在 `discover()` 之后加公共入口：
```python
def call_tool(
    config: McpServerConfig | Mapping[str, Any],
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    timeout_seconds: float = 30.0,
    protocol_version: str = PROTOCOL_VERSION,
    client_info: Mapping[str, Any] | None = None,
) -> Any:
    """连接 MCP server,initialize 后调用单个工具,返回归一化结果数据。

    每次调用新开连接(与 discover 一致)。工具 isError 抛 TOOL_ERROR;
    连接/协议失败抛对应 code 的 McpDiscoveryError。
    """
    config = _coerce_config(config)
    session = _build_session(config, timeout_seconds, protocol_version, client_info)
    try:
        with session:
            session.initialize()
            raw = session.call_tool(tool_name, dict(arguments or {}))
    except McpDiscoveryError:
        raise
    except Exception as exc:  # 兜底:收敛为单一错误类型
        raise McpDiscoveryError(f"MCP 工具调用失败：{exc}", code=PROTOCOL_ERROR, cause=exc) from exc
    return _extract_tool_result(raw)
```
在 `__init__.py` 的 import 块与 `__all__` 加入 `call_tool`。

- [ ] **Step 4: 运行确认通过**
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/mcp && ../../../../.venv/bin/python -m pytest tests/ -q`
Expected: PASS（含新增 3 个；既有不回归）

- [ ] **Step 5: 提交**
```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/mcp/mcp_discovery/client.py backend/app/agent/mcp/mcp_discovery/__init__.py backend/app/agent/mcp/tests/test_call.py
git commit -m "feat(mcp): 基座新增 call_tool 公共入口" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 插件接线真实调用 handler（含集成测试更新，单次提交全绿）

**Files:**
- Modify: `backend/app/agent/tools/mcp_plugins.py`
- Test: `backend/tests/test_mcp_plugins.py`（追加 handler 单测 + 改集成测试占位断言）

- [ ] **Step 1: 写失败测试** — 在 `backend/tests/test_mcp_plugins.py` 末尾追加（复用 `manager` 夹具、`_result` 构造器；`McpDiscoveryError`/`json`/`store`/`TOOL_REGISTRY`/`mcp_plugins` 顶部已有）：
```python
def test_stringify_str_passthrough_and_dict():
    from app.agent.tools.mcp_plugins import _stringify
    assert _stringify("hello") == "hello"
    assert json.loads(_stringify({"a": 1})) == {"a": 1}


def test_install_handler_calls_call_tool_with_config(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["alpha"]))
    captured = {}

    def fake_call(config, tool_name, arguments=None, **kw):
        captured.update({"config": config, "tool": tool_name, "args": arguments})
        return {"ok": True, "echo": arguments}

    monkeypatch.setattr(mcp_plugins, "call_tool", fake_call)
    manager.install("p1", {"transport": "http", "url": "http://x/mcp"})
    out = TOOL_REGISTRY["p1__alpha"].handler(a="1")
    assert captured["tool"] == "alpha"
    assert captured["args"] == {"a": "1"}
    assert captured["config"]["url"] == "http://x/mcp"
    assert json.loads(out) == {"ok": True, "echo": {"a": "1"}}


def test_handler_raises_on_call_error(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["alpha"]))

    def boom(config, tool_name, arguments=None, **kw):
        raise McpDiscoveryError("工具报错", code="TOOL_ERROR")

    monkeypatch.setattr(mcp_plugins, "call_tool", boom)
    manager.install("p1", {"transport": "http", "url": "http://x"})
    with pytest.raises(McpDiscoveryError):
        TOOL_REGISTRY["p1__alpha"].handler()
```

- [ ] **Step 2: 运行确认失败**
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py::test_stringify_str_passthrough_and_dict tests/test_mcp_plugins.py::test_install_handler_calls_call_tool_with_config -q`
Expected: FAIL（`ImportError: cannot import name '_stringify'` 或 handler 仍为占位导致断言失败）

- [ ] **Step 3: 实现** — 修改 `mcp_plugins.py`：
  1. import 行改为 `from mcp_discovery import McpDiscoveryError, McpServerConfig, call_tool, discover`
  2. 在 `_PLUGIN_NAME_RE` 之后加常量 `MCP_CALL_TIMEOUT_SECONDS = 30.0`
  3. 在类外（模块级，`PluginError` 之后）加：
```python
def _stringify(value) -> str:
    """把 call_tool 返回的数据值转成回填给模型的字符串。"""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)
```
  4. 删除 `_placeholder_handler`，在类内原位置加：
```python
    @staticmethod
    def _make_handler(config: dict, tool_name: str):
        def handler(**args) -> str:
            result = call_tool(config, tool_name, args, timeout_seconds=MCP_CALL_TIMEOUT_SECONDS)
            return _stringify(result)
        return handler
```
  5. `_register_tools` 签名改为 `def _register_tools(self, name: str, tools, config: dict) -> set[str]:`，其中 `handler=self._placeholder_handler(name, t.name)` 改为 `handler=self._make_handler(config, t.name)`。
  6. 四条注册路径传 config：
     - `install`：`keys = self._register_tools(name, result.tools, cfg)`
     - `enable`：先 `cfg = json.loads(row["config_json"])`，`result = discover(cfg)`，`keys = self._register_tools(name, result.tools, cfg)`
     - `reload`：同 enable 的取 cfg 方式，`keys = self._register_tools(name, result.tools, cfg)`
     - `load_enabled`：`cfg = json.loads(row["config_json"])`，`result = discover(cfg)`，`keys = self._register_tools(name, result.tools, cfg)`
  7. 模块 docstring 末句"第一步 handler 为占位(远程执行 tools/call 是第二步)"改为"handler 通过基座 call_tool 发起真实远程调用"。

  **同一提交内**把集成测试 `test_integration_stdio_install_list_uninstall` 的占位断言：
```python
    # 占位 handler 可调用且不炸
    out = TOOL_REGISTRY["mocksrv__echo"].handler()
    assert "远程执行尚未接入" in out
```
  替换为真实调用（stdio mock 的 echo 返回 `echo: {text}`），并把函数名改为 `test_integration_stdio_install_call_uninstall`：
```python
    # 真实远程调用 echo
    out = TOOL_REGISTRY["mocksrv__echo"].handler(text="hi")
    assert out == "echo: hi"
```

- [ ] **Step 4: 运行确认通过**
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: PASS（新增 3 个 + 更新后的集成测试 + 既有单测，全绿）

- [ ] **Step 5: 提交**
```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/tools/mcp_plugins.py backend/tests/test_mcp_plugins.py
git commit -m "feat(mcp): 插件 handler 接入真实 tools/call" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: 文档 + 全量回归

**Files:**
- Modify: `backend/app/agent/mcp/README.md`（说明 call_tool）
- Modify: `backend/README.md`（MCP 插件子节把"第一步为定义的装/卸,远程执行为下一步"更新为已支持真实调用）

- [ ] **Step 1: 更新文档** — 在 `backend/app/agent/mcp/README.md` API 摘要处补 `call_tool` 条目；把 `backend/README.md` 中 MCP 插件子节的"远程执行(`tools/call`)为下一步"改为"handler 通过基座 `call_tool` 发起真实远程调用（每次新开连接）"。

- [ ] **Step 2: 全量回归**
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend && .venv/bin/python -m pytest tests app/agent/mcp/tests -q`
Expected: 全部通过（记录总数）

- [ ] **Step 3: 提交**
```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/mcp/README.md backend/README.md
git commit -m "docs(mcp): 补充 tools/call 说明" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 完成标准

- `call_tool(config, tool, args)` 真实调用 stdio/HTTP/SSE server 并返回归一化数据；isError → `TOOL_ERROR`。
- 装入的 MCP 工具被模型调用时真正执行并回填结果；报错回填「工具执行失败:<工具错误>」。
- engine/calls/registry/store/main 零改动；装/卸/启停/重载仍正常。
- 全量回归绿。
