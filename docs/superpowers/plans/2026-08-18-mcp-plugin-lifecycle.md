# MCP 插件生命周期（第一步：工具定义的装/卸）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把一个 MCP Server 当作插件装入/卸下 feibot：discover 取回的工具清单加前缀注册进现有工具注册表，支持持久化、启动重载、启停与刷新；第一步不做远程执行。

**Architecture:** 方案 A——新增单一宿主模块 `app/agent/tools/mcp_plugins.py`（`McpPluginManager`），持有「插件名→已注册工具 key」归属映射；给 `registry.py` 加 `unregister_tool`，给 `store.py` 加 `mcp_plugins` 表，`main.py` 启动时调 `load_enabled()`。基座 `mcp_discovery` 零改动，engine/calls 零改动。

**Tech Stack:** Python 3.11+，标准库 sqlite3，`mcp_discovery`（已 editable 安装），pytest。测试复用 `backend/tests/conftest.py` 的临时 db 夹具。

**参考 spec:** `docs/superpowers/specs/2026-08-18-mcp-plugin-lifecycle-design.md`

---

## 文件职责总览

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `backend/app/db/store.py` | 修改 | 加 `mcp_plugins` 表 + 5 个 CRUD 函数 |
| `backend/app/agent/tools/registry.py` | 修改 | 加 `unregister_tool(name)->bool` |
| `backend/app/agent/tools/mcp_plugins.py` | 新建 | `McpPluginManager` + 默认实例 + `load_enabled_plugins()` |
| `backend/app/main.py` | 修改 | 启动时调 `load_enabled_plugins()` |
| `backend/tests/test_mcp_plugins.py` | 新建 | 管理器 + store 全部测试 |
| `backend/tests/test_mcp_plugins_store.py` | 新建 | store 层 CRUD 测试 |
| `backend/README.md` | 修改 | 工具章节补 MCP 插件说明 |

**关键常量/签名（后续任务必须一致）：**
- store：`upsert_plugin(name, config_json, enabled)`、`get_plugin(name)->dict|None`、`list_plugins()->list[dict]`、`delete_plugin(name)->bool`、`set_plugin_enabled(name, enabled)->bool`；行 dict 键：`name/config_json/enabled/added_at/updated_at`。
- registry：`unregister_tool(name)->bool`。
- 管理器：`install(name, config)->int`、`uninstall(name)->bool`、`enable(name)->int`、`disable(name)->bool`、`reload(name)->int`、`list()->list[dict]`、`load_enabled()->None`、`install_from_mcp_servers(payload)->dict`。
- 前缀分隔符 `__`；插件名正则 `^[A-Za-z0-9_-]+$` 且不得含 `__`。

---

### Task 1: store 加 `mcp_plugins` 表与 CRUD

**Files:**
- Modify: `backend/app/db/store.py`（`_SCHEMA` 内加表；文件末尾加函数）
- Test: `backend/tests/test_mcp_plugins_store.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_mcp_plugins_store.py
from app.db import store


def test_upsert_and_get_plugin():
    store.upsert_plugin("p1", '{"transport":"http","url":"http://x/mcp"}', 1)
    row = store.get_plugin("p1")
    assert row["name"] == "p1"
    assert row["config_json"] == '{"transport":"http","url":"http://x/mcp"}'
    assert row["enabled"] == 1


def test_upsert_overwrites_same_name():
    store.upsert_plugin("p1", '{"a":1}', 1)
    store.upsert_plugin("p1", '{"a":2}', 0)
    row = store.get_plugin("p1")
    assert row["config_json"] == '{"a":2}'
    assert row["enabled"] == 0


def test_get_missing_plugin_returns_none():
    assert store.get_plugin("nope") is None


def test_list_plugins_sorted_and_roundtrip():
    store.upsert_plugin("b", "{}", 1)
    store.upsert_plugin("a", "{}", 0)
    names = [r["name"] for r in store.list_plugins()]
    assert names == ["a", "b"]


def test_delete_plugin_returns_whether_deleted():
    store.upsert_plugin("p1", "{}", 1)
    assert store.delete_plugin("p1") is True
    assert store.delete_plugin("p1") is False
    assert store.get_plugin("p1") is None


def test_set_plugin_enabled():
    store.upsert_plugin("p1", "{}", 1)
    assert store.set_plugin_enabled("p1", 0) is True
    assert store.get_plugin("p1")["enabled"] == 0
    assert store.set_plugin_enabled("nope", 1) is False
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins_store.py -q`
Expected: FAIL（`AttributeError: module 'app.db.store' has no attribute 'upsert_plugin'`）

- [ ] **Step 3: 实现——在 `_SCHEMA` 里加表**

在 `store.py` 的 `_SCHEMA` 字符串中、`CREATE INDEX ...` 之前加入：

```sql
CREATE TABLE IF NOT EXISTS mcp_plugins (
    name        TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    added_at    REAL NOT NULL,
    updated_at  REAL NOT NULL
);
```

- [ ] **Step 4: 实现——文件末尾加 CRUD 函数**

```python
# ---- MCP 插件(mcp_plugins 表;config_json 为连接配置的 JSON 文本)----


def upsert_plugin(name: str, config_json: str, enabled: int) -> None:
    """写入 MCP 插件(同名覆盖);首次写入记 added_at,每次刷新 updated_at。"""
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO mcp_plugins (name, config_json, enabled, added_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET"
            " config_json = excluded.config_json, enabled = excluded.enabled,"
            " updated_at = excluded.updated_at",
            (name, config_json, enabled, now, now),
        )


def get_plugin(name: str) -> dict | None:
    """按名字取插件行(dict);不存在返回 None。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT name, config_json, enabled, added_at, updated_at"
            " FROM mcp_plugins WHERE name = ?",
            (name,),
        ).fetchone()
    return _plugin_row_to_dict(row) if row else None


def list_plugins() -> list[dict]:
    """列出全部插件行(按名字排序)。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT name, config_json, enabled, added_at, updated_at"
            " FROM mcp_plugins ORDER BY name"
        ).fetchall()
    return [_plugin_row_to_dict(row) for row in rows]


def delete_plugin(name: str) -> bool:
    """删除插件;返回是否确实删掉了行。"""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM mcp_plugins WHERE name = ?", (name,))
        return cur.rowcount > 0


def set_plugin_enabled(name: str, enabled: int) -> bool:
    """切换插件启用状态;返回是否命中已有行。"""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE mcp_plugins SET enabled = ?, updated_at = ? WHERE name = ?",
            (enabled, time.time(), name),
        )
        return cur.rowcount > 0


def _plugin_row_to_dict(row: tuple) -> dict:
    return {
        "name": row[0],
        "config_json": row[1],
        "enabled": row[2],
        "added_at": row[3],
        "updated_at": row[4],
    }
```

- [ ] **Step 5: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins_store.py -q`
Expected: PASS（6 passed）

- [ ] **Step 6: 提交**

```bash
git add backend/app/db/store.py backend/tests/test_mcp_plugins_store.py
git commit -m "feat(db): 新增 mcp_plugins 表与 CRUD" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: registry 加 `unregister_tool`

**Files:**
- Modify: `backend/app/agent/tools/registry.py`（`register_tool` 之后）
- Test: `backend/tests/test_mcp_plugins.py`（先建文件，放注册表测试）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_mcp_plugins.py
from app.agent.tools.registry import (
    TOOL_REGISTRY,
    ToolSpec,
    register_tool,
    unregister_tool,
)


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description="d", parameters={}, handler=lambda: "ok")


def test_unregister_tool_removes_and_reports():
    register_tool(_spec("tmp_tool"))
    assert "tmp_tool" in TOOL_REGISTRY
    assert unregister_tool("tmp_tool") is True
    assert "tmp_tool" not in TOOL_REGISTRY


def test_unregister_missing_tool_returns_false():
    assert unregister_tool("never_registered") is False
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: FAIL（`ImportError: cannot import name 'unregister_tool'`）

- [ ] **Step 3: 实现**

在 `registry.py` 的 `register_tool` 函数之后加入：

```python
def unregister_tool(name: str) -> bool:
    """注销工具;返回是否确实移除了已注册的工具。"""
    return TOOL_REGISTRY.pop(name, None) is not None
```

并在 `app/agent/tools/__init__.py` 的 import 与 `__all__` 中加入 `unregister_tool`（`from app.agent.tools.registry import (... unregister_tool ...)`）。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/app/agent/tools/registry.py backend/app/agent/tools/__init__.py backend/tests/test_mcp_plugins.py
git commit -m "feat(tools): 注册表支持 unregister_tool" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 管理器骨架 + install（发现→前缀注册→落库）

**Files:**
- Create: `backend/app/agent/tools/mcp_plugins.py`
- Test: `backend/tests/test_mcp_plugins.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `test_mcp_plugins.py` 顶部加入夹具与构造器，再追加测试：

```python
import json

import pytest

from mcp_discovery import DiscoveredTool, DiscoveryResult, McpDiscoveryError

from app.agent.tools import mcp_plugins
from app.agent.tools.mcp_plugins import McpPluginManager, PluginError
from app.db import store


@pytest.fixture
def clean_registry():
    """清空注册表做隔离,测试后恢复(内置工具在导入时已注册)。"""
    saved = dict(TOOL_REGISTRY)
    TOOL_REGISTRY.clear()
    yield TOOL_REGISTRY
    TOOL_REGISTRY.clear()
    TOOL_REGISTRY.update(saved)


@pytest.fixture
def manager(clean_registry):
    return McpPluginManager()


def _tool(name: str) -> DiscoveredTool:
    return DiscoveredTool(
        name=name,
        title="",
        description=f"{name} 描述",
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "string", "description": "参数 x"}},
        },
        output_schema={},
        annotations={},
        meta={},
    )


def _result(names):
    return DiscoveryResult(
        tools=tuple(_tool(n) for n in names),
        protocol_version="2024-11-05",
        capabilities={},
        server_info={"name": "fake", "version": "1"},
    )


def test_install_registers_prefixed_tools_and_persists(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["alpha", "beta"]))
    count = manager.install("p1", {"transport": "http", "url": "http://x/mcp"})
    assert count == 2
    assert "p1__alpha" in TOOL_REGISTRY
    assert "p1__beta" in TOOL_REGISTRY
    assert TOOL_REGISTRY["p1__alpha"].description == "alpha 描述"
    assert TOOL_REGISTRY["p1__alpha"].parameters == {"x": "参数 x"}
    row = store.get_plugin("p1")
    assert row is not None and row["enabled"] == 1
    assert json.loads(row["config_json"])["url"] == "http://x/mcp"


def test_install_invalid_name_rejected(manager):
    with pytest.raises(PluginError):
        manager.install("bad name!", {"transport": "http", "url": "http://x"})
    with pytest.raises(PluginError):
        manager.install("a__b", {"transport": "http", "url": "http://x"})


def test_install_discover_failure_registers_nothing(manager, monkeypatch):
    def boom(cfg, **kw):
        raise McpDiscoveryError("连不上", code="CONNECT_FAILED")
    monkeypatch.setattr(mcp_plugins, "discover", boom)
    with pytest.raises(McpDiscoveryError):
        manager.install("p1", {"transport": "http", "url": "http://x"})
    assert TOOL_REGISTRY == {}
    assert store.get_plugin("p1") is None


def test_install_maps_type_to_transport(manager, monkeypatch):
    seen = {}
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: (seen.update(cfg), _result(["t"]))[1])
    manager.install("p1", {"type": "streamable_http", "url": "http://x/mcp"})
    assert seen["transport"] == "http"
    assert "type" not in seen
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: FAIL（`ImportError: cannot import name 'McpPluginManager'`）

- [ ] **Step 3: 实现 `mcp_plugins.py`（骨架 + install）**

```python
# backend/app/agent/tools/mcp_plugins.py
"""MCP 插件生命周期管理:把一个 MCP server 当作一个插件装入/卸下 agent。

装 = discover() 取清单 → 加前缀注册进 TOOL_REGISTRY → 落库;
卸 = 按归属把工具移出注册表 → 删库。归属映射在本模块维护,卸载不依赖
server 在线。第一步 handler 为占位(远程执行 tools/call 是第二步)。
"""

from __future__ import annotations

import json
import logging
import re

from mcp_discovery import McpDiscoveryError, McpServerConfig, discover

from app.agent.tools.registry import ToolSpec, register_tool, unregister_tool
from app.db import store

logger = logging.getLogger(__name__)

_SEPARATOR = "__"
_PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class PluginError(Exception):
    """插件管理层错误(非法名、插件不存在等)。"""


class McpPluginManager:
    def __init__(self) -> None:
        self._provenance: dict[str, set[str]] = {}  # 插件名 -> 已注册工具 key
        self._status: dict[str, str] = {}           # 插件名 -> 最近加载状态

    # ---- 内部 ----
    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or not _PLUGIN_NAME_RE.match(name):
            raise PluginError(f"非法插件名:{name!r}(仅允许字母/数字/下划线/连字符)")
        if _SEPARATOR in name:
            raise PluginError(f"插件名不能包含 {_SEPARATOR!r}:{name!r}")

    @staticmethod
    def _normalize_config(config) -> dict:
        if isinstance(config, McpServerConfig):
            cfg = config.model_dump()
        else:
            cfg = dict(config)
        if "type" in cfg and "transport" not in cfg:
            cfg["transport"] = cfg.pop("type")
        return cfg

    @staticmethod
    def _placeholder_handler(plugin: str, tool: str):
        def handler(**args) -> str:
            return f"工具 {plugin}{_SEPARATOR}{tool} 来自 MCP 插件 {plugin},远程执行尚未接入。"
        return handler

    def _register_tools(self, name: str, tools) -> set[str]:
        keys: set[str] = set()
        for t in tools:
            key = f"{name}{_SEPARATOR}{t.name}"
            params = {
                pname: str(pdef.get("description") or pdef.get("type") or "")
                for pname, pdef in (t.input_schema.get("properties") or {}).items()
                if isinstance(pdef, dict)
            }
            register_tool(ToolSpec(
                name=key,
                description=t.description or f"MCP 工具 {t.name}",
                parameters=params,
                handler=self._placeholder_handler(name, t.name),
            ))
            keys.add(key)
        return keys

    def _remove_tools(self, name: str) -> int:
        removed = 0
        for key in list(self._provenance.get(name, ())):
            if unregister_tool(key):
                removed += 1
        self._provenance.pop(name, None)
        return removed

    # ---- 公共 API ----
    def install(self, name: str, config) -> int:
        """发现并装入插件;同名幂等重装。返回注册的工具数。"""
        self._validate_name(name)
        cfg = self._normalize_config(config)
        result = discover(cfg)  # 失败抛 McpDiscoveryError,不注册不落库
        self._remove_tools(name)
        keys = self._register_tools(name, result.tools)
        self._provenance[name] = keys
        self._status[name] = f"ok, {len(keys)} tools"
        store.upsert_plugin(name, json.dumps(cfg, ensure_ascii=False), 1)
        return len(keys)


# 模块级默认实例与 load_enabled_plugins() 在 Task 7 一并添加。
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: PASS（含 Task 2 的 2 个 + 本任务 4 个）

- [ ] **Step 5: 提交**

```bash
git add backend/app/agent/tools/mcp_plugins.py backend/tests/test_mcp_plugins.py
git commit -m "feat(mcp): 插件管理器 install——发现→前缀注册→落库" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: uninstall + 幂等重装

**Files:**
- Modify: `backend/app/agent/tools/mcp_plugins.py`
- Test: `backend/tests/test_mcp_plugins.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def test_uninstall_removes_tools_and_record(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a", "b"]))
    manager.install("p1", {"transport": "http", "url": "http://x"})
    assert manager.uninstall("p1") is True
    assert "p1__a" not in TOOL_REGISTRY and "p1__b" not in TOOL_REGISTRY
    assert store.get_plugin("p1") is None


def test_uninstall_missing_returns_false(manager):
    assert manager.uninstall("ghost") is False


def test_install_idempotent_reinstall(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a"]))
    manager.install("p1", {"transport": "http", "url": "http://x"})
    manager.install("p1", {"transport": "http", "url": "http://x"})
    assert list(TOOL_REGISTRY) == ["p1__a"]
    assert len(store.list_plugins()) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: FAIL（`AttributeError: ... no attribute 'uninstall'`）

- [ ] **Step 3: 实现 `uninstall`**

在 `McpPluginManager` 中 `install` 之后加入：

```python
    def uninstall(self, name: str) -> bool:
        """卸下插件:移出其工具并删除库记录。不联网;不存在返回 False。"""
        existed_db = store.delete_plugin(name)
        removed = self._remove_tools(name)
        self._status.pop(name, None)
        return existed_db or removed > 0
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/agent/tools/mcp_plugins.py backend/tests/test_mcp_plugins.py
git commit -m "feat(mcp): 插件 uninstall——纯本地移除工具与记录" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: enable / disable

**Files:**
- Modify: `backend/app/agent/tools/mcp_plugins.py`
- Test: `backend/tests/test_mcp_plugins.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def test_disable_keeps_record_removes_tools(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a"]))
    manager.install("p1", {"transport": "http", "url": "http://x"})
    assert manager.disable("p1") is True
    assert "p1__a" not in TOOL_REGISTRY
    row = store.get_plugin("p1")
    assert row is not None and row["enabled"] == 0


def test_disable_missing_returns_false(manager):
    assert manager.disable("ghost") is False


def test_enable_rediscovers_and_registers(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a"]))
    manager.install("p1", {"transport": "http", "url": "http://x"})
    manager.disable("p1")
    assert manager.enable("p1") == 1
    assert "p1__a" in TOOL_REGISTRY
    assert store.get_plugin("p1")["enabled"] == 1


def test_enable_missing_raises(manager):
    with pytest.raises(PluginError):
        manager.enable("ghost")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: FAIL（`AttributeError: ... 'disable'`）

- [ ] **Step 3: 实现 `disable` / `enable`**

```python
    def disable(self, name: str) -> bool:
        """停用:移出工具但保留配置。不存在返回 False。"""
        if store.get_plugin(name) is None:
            return False
        self._remove_tools(name)
        store.set_plugin_enabled(name, 0)
        self._status[name] = "disabled"
        return True

    def enable(self, name: str) -> int:
        """启用:重新 discover 并注册。不存在抛 PluginError。"""
        row = store.get_plugin(name)
        if row is None:
            raise PluginError(f"插件不存在:{name}")
        result = discover(json.loads(row["config_json"]))
        self._remove_tools(name)
        keys = self._register_tools(name, result.tools)
        self._provenance[name] = keys
        store.set_plugin_enabled(name, 1)
        self._status[name] = f"ok, {len(keys)} tools"
        return len(keys)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/agent/tools/mcp_plugins.py backend/tests/test_mcp_plugins.py
git commit -m "feat(mcp): 插件 enable/disable" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: reload + list

**Files:**
- Modify: `backend/app/agent/tools/mcp_plugins.py`
- Test: `backend/tests/test_mcp_plugins.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def test_reload_updates_tool_set(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a", "b"]))
    manager.install("p1", {"transport": "http", "url": "http://x"})
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a", "c", "d"]))
    assert manager.reload("p1") == 3
    assert set(TOOL_REGISTRY) == {"p1__a", "p1__c", "p1__d"}


def test_reload_missing_raises(manager):
    with pytest.raises(PluginError):
        manager.reload("ghost")


def test_list_reports_plugins(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a"]))
    manager.install("p1", {"transport": "http", "url": "http://x"})
    manager.install("p2", {"transport": "stdio", "command": "node"})
    manager.disable("p2")
    info = {row["name"]: row for row in manager.list()}
    assert info["p1"]["enabled"] is True
    assert info["p1"]["registered"] == ["p1__a"]
    assert info["p2"]["enabled"] is False
    assert info["p2"]["registered"] == []
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: FAIL（`AttributeError: ... 'reload'`）

- [ ] **Step 3: 实现 `reload` / `list`**

```python
    def reload(self, name: str) -> int:
        """重新 discover 并 diff 更新已注册工具。不存在抛 PluginError。"""
        row = store.get_plugin(name)
        if row is None:
            raise PluginError(f"插件不存在:{name}")
        result = discover(json.loads(row["config_json"]))
        self._remove_tools(name)
        keys = self._register_tools(name, result.tools)
        self._provenance[name] = keys
        store.upsert_plugin(name, row["config_json"], row["enabled"])
        self._status[name] = f"ok, {len(keys)} tools"
        return len(keys)

    def list(self) -> list[dict]:
        """列出全部插件及状态(启用、已注册工具、最近加载结果)。"""
        out = []
        for row in store.list_plugins():
            name = row["name"]
            out.append({
                "name": name,
                "enabled": bool(row["enabled"]),
                "registered": sorted(self._provenance.get(name, ())),
                "status": self._status.get(name, ""),
            })
        return out
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/agent/tools/mcp_plugins.py backend/tests/test_mcp_plugins.py
git commit -m "feat(mcp): 插件 reload 与 list" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: load_enabled（启动重载，单个失败不拖累）

**Files:**
- Modify: `backend/app/agent/tools/mcp_plugins.py`（补 `load_enabled`；填 `load_enabled_plugins`）
- Test: `backend/tests/test_mcp_plugins.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def test_load_enabled_registers_enabled_only_and_skips_failures(manager, monkeypatch):
    # 预置三条库记录:p1 启用可连,p2 停用,p3 启用但 discover 失败
    store.upsert_plugin("p1", json.dumps({"transport": "http", "url": "http://ok"}), 1)
    store.upsert_plugin("p2", json.dumps({"transport": "http", "url": "http://off"}), 0)
    store.upsert_plugin("p3", json.dumps({"transport": "http", "url": "http://bad"}), 1)

    def fake_discover(cfg, **kw):
        if cfg["url"] == "http://bad":
            raise McpDiscoveryError("连不上", code="CONNECT_FAILED")
        return _result(["a"])

    monkeypatch.setattr(mcp_plugins, "discover", fake_discover)
    manager.load_enabled()  # 不应抛异常

    assert "p1__a" in TOOL_REGISTRY
    assert "p2__a" not in TOOL_REGISTRY
    assert "p3__a" not in TOOL_REGISTRY
    status = {row["name"]: row["status"] for row in manager.list()}
    assert status["p1"].startswith("ok")
    assert "load failed" in status["p3"]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: FAIL（`AttributeError: ... 'load_enabled'`）

- [ ] **Step 3: 实现 `load_enabled`**

在 `McpPluginManager` 中加入，并把模块底部 `load_enabled_plugins` 指到它：

```python
    def load_enabled(self) -> None:
        """启动钩子:重载所有启用插件;单个失败只记录,不拖累其他与启动。"""
        for row in store.list_plugins():
            if not row["enabled"]:
                continue
            name = row["name"]
            try:
                result = discover(json.loads(row["config_json"]))
                self._remove_tools(name)
                keys = self._register_tools(name, result.tools)
                self._provenance[name] = keys
                self._status[name] = f"ok, {len(keys)} tools"
            except Exception as exc:  # noqa: BLE001 — 单个插件失败不影响整体
                logger.warning("启动加载 MCP 插件 %s 失败:%s", name, exc)
                self._status[name] = f"load failed: {exc}"
```

并在文件末尾补模块级默认实例与入口函数：

```python
# 模块级默认实例,供 main.py / 编程式调用
plugin_manager = McpPluginManager()


def load_enabled_plugins() -> None:
    plugin_manager.load_enabled()
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/agent/tools/mcp_plugins.py backend/tests/test_mcp_plugins.py
git commit -m "feat(mcp): 启动 load_enabled 重载启用插件" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: install_from_mcp_servers（标准信封适配）

**Files:**
- Modify: `backend/app/agent/tools/mcp_plugins.py`
- Test: `backend/tests/test_mcp_plugins.py`（追加）

- [ ] **Step 1: 写失败测试**

```python
def test_install_from_mcp_servers_batch(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["t"]))
    payload = {
        "mcpServers": {
            "train": {"type": "streamable_http", "url": "http://x/mcp"},
            "bad name!": {"type": "stdio", "command": "node"},
        }
    }
    results = manager.install_from_mcp_servers(payload)
    assert results["train"] == 1
    assert str(results["bad name!"]).startswith("failed")
    assert "train__t" in TOOL_REGISTRY


def test_install_from_mcp_servers_empty(manager):
    assert manager.install_from_mcp_servers({}) == {}
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: FAIL（`AttributeError: ... 'install_from_mcp_servers'`）

- [ ] **Step 3: 实现**

```python
    def install_from_mcp_servers(self, payload: dict) -> dict:
        """吃标准 {"mcpServers": {name: {...}}} 信封批量装;逐项记录成败。"""
        servers = payload.get("mcpServers") or {}
        results: dict[str, object] = {}
        for name, cfg in servers.items():
            try:
                results[name] = self.install(name, cfg)
            except (PluginError, McpDiscoveryError) as exc:
                results[name] = f"failed: {exc}"
        return results
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/agent/tools/mcp_plugins.py backend/tests/test_mcp_plugins.py
git commit -m "feat(mcp): install_from_mcp_servers 标准信封批量装" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: main.py 接入启动钩子

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: 修改 main.py**

在 import 区加入并在 `main()` 内、`run_wechat_ingress` 之前调用：

```python
from app.agent.tools.mcp_plugins import load_enabled_plugins
```

```python
def main() -> None:
    logging.basicConfig(...)
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    load_enabled_plugins()          # ← 新增:启动时重载启用的 MCP 插件
    run_wechat_ingress(handle_message)
```

- [ ] **Step 2: 冒烟——导入不炸**

Run: `cd backend && .venv/bin/python -c "import app.main; print('ok')"`
Expected: 输出 `ok`（`load_enabled_plugins` 在空库时无副作用）

- [ ] **Step 3: 提交**

```bash
git add backend/app/main.py
git commit -m "feat(main): 启动时重载启用的 MCP 插件" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: 集成 round-trip（本地 stdio mock，真 discover）

**Files:**
- Test: `backend/tests/test_mcp_plugins.py`（追加；不 mock discover，走真实子进程）

- [ ] **Step 1: 写测试**

```python
import sys
from pathlib import Path

_STDIO_MOCK = (
    Path(__file__).resolve().parents[1] / "app" / "agent" / "mcp" / "tests" / "stdio_mock_server.py"
)


def test_integration_stdio_install_list_uninstall(manager):
    config = {"transport": "stdio", "command": sys.executable, "args": [str(_STDIO_MOCK)]}
    count = manager.install("mocksrv", config)
    assert count == 3
    assert set(TOOL_REGISTRY) == {
        "mocksrv__echo", "mocksrv__sum", "mocksrv__product_lookup",
    }
    # 占位 handler 可调用且不炸
    out = TOOL_REGISTRY["mocksrv__echo"].handler()
    assert "远程执行尚未接入" in out

    assert manager.uninstall("mocksrv") is True
    assert TOOL_REGISTRY == {}
    assert store.get_plugin("mocksrv") is None
```

- [ ] **Step 2: 运行确认通过（无需先失败——这是集成验证）**

Run: `cd backend && .venv/bin/python -m pytest tests/test_mcp_plugins.py::test_integration_stdio_install_list_uninstall -q`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add backend/tests/test_mcp_plugins.py
git commit -m "test(mcp): 插件装/卸集成 round-trip(本地 stdio mock)" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: 文档 + 全量回归

**Files:**
- Modify: `backend/README.md`（工具章节补 MCP 插件说明）

- [ ] **Step 1: 更新 backend/README.md**

在「工具(仅白名单可用)」一节末尾追加：

```markdown
### MCP 插件(装/卸远端工具)

MCP server 以插件形式接入(`agent/tools/mcp_plugins.py`):`discover()` 取回的工具
清单加 `{插件名}__` 前缀注册进同一注册表,与内置工具统一被 engine 注入与调用;
配置持久化在 sqlite `mcp_plugins` 表,启动自动重载启用的插件。第一步为"定义的装/卸",
远程执行(`tools/call`)为下一步。

```python
from app.agent.tools.mcp_plugins import plugin_manager
plugin_manager.install("12306-mcp", {"type": "streamable_http", "url": "https://.../mcp"})
plugin_manager.list(); plugin_manager.disable("12306-mcp"); plugin_manager.uninstall("12306-mcp")
```
```

- [ ] **Step 2: 全量回归**

Run: `cd backend && .venv/bin/python -m pytest tests app/agent/mcp/tests -q`
Expected: 全部通过（现有 82 + 基座 49 + 新增插件测试）

- [ ] **Step 3: 提交**

```bash
git add backend/README.md
git commit -m "docs(mcp): backend README 补充 MCP 插件说明" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 完成标准

- `install/uninstall/enable/disable/reload/list/load_enabled/install_from_mcp_servers` 全部可用且有测试。
- 真实 12306 server 可 `install` 出 8 个带前缀工具、`uninstall` 干净移除（手工验证或集成测试）。
- 重启后 `load_enabled()` 自动重装启用插件；单个 server 失联不拖累启动。
- 基座 `mcp_discovery`、engine、calls 零改动；现有测试全绿。
