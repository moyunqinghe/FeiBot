# MCP 协议层基座（mcp-client）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 feibot 的 `backend/app/agent/mcp/` 落成一个零业务耦合、可 pip 安装的 MCP 协议层基座包 `mcp_client`，支持 Streamable HTTP / 旧版 SSE 两种传输的 MCP Server 连接与工具发现（tools/list）。

**Architecture:** 单包单模块（`mcp_client/client.py`）：类型化配置 `McpServerConfig` → 传输会话（`_HttpMcpSession` / `_SseMcpSession`，内部为 JSON-RPC 2.0）→ initialize 握手 → tools/list → 规范化为 `McpTool` 列表。`transport="auto"` 时 streamable-http 在 connect/initialize 阶段失败后回退一次 sse。失败统一为带 phase/status_code/server_message 的 `McpDiscoveryError`。

**Tech Stack:** Python >=3.11（feibot venv 为 3.14）、httpx（已在 feibot venv）、pytest。

**设计文档:** `docs/superpowers/specs/2026-08-18-mcp-client-base-design.md`

---

## ⚠️ 硬性约束（每个任务都必须遵守）

1. **交付物中不得出现任何指向其他项目的溯源说明**：代码、注释、docstring、README、pyproject、测试、git 提交信息中一律不提及来源项目；本包是自包含的原创作品。clientInfo 使用 `{"name": "mcp-client", "version": "1.0.0"}`。
2. **不修改 feibot 其他任何文件**。只允许改动：
   - `backend/app/agent/mcp/` 目录内的文件（含移除占位 `__init__.py`）
   - `docs/superpowers/` 下的计划/进度文档
   - venv 级 `pip install -e`（不产生仓库文件变更）
3. 运行时依赖仅 `httpx` + 标准库；测试只用 pytest + 标准库。
4. 每个任务结束必须提交（commit message 用 conventional 风格，中文描述可）。

## 文件结构（最终形态）

```
backend/app/agent/mcp/            # 容器目录（非 Python 包）
├── pyproject.toml                # Task 1
├── .gitignore                    # Task 1
├── README.md                     # Task 6
├── mcp_client/
│   ├── __init__.py               # Task 1（导出），Task 2-5 逐步补齐导出名
│   └── client.py                 # Task 2-5 增量实现
└── tests/
    ├── conftest.py               # Task 3（mock MCP server）
    └── test_mcp_client.py        # Task 2-5 增量追加用例
```

所有命令默认在 `/Users/moyunqinghe/个人/学习/feibot/backend` 下执行，
pytest 用 `.venv/bin/python -m pytest`。

---

### Task 1: 包脚手架

**Files:**
- Remove: `app/agent/mcp/__init__.py`（占位文件，已验证无任何 import）
- Create: `app/agent/mcp/pyproject.toml`
- Create: `app/agent/mcp/.gitignore`
- Create: `app/agent/mcp/mcp_client/__init__.py`
- Create: `app/agent/mcp/mcp_client/client.py`（空骨架）

- [ ] **Step 1: 移除占位文件，创建包骨架**

`app/agent/mcp/pyproject.toml`：

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "mcp-client"
version = "1.0.0"
description = "Pure protocol layer for MCP (Model Context Protocol) servers over Streamable HTTP and legacy SSE: connect, handshake and discover tools (tools/list)."
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
dependencies = [
    "httpx",
]

[project.optional-dependencies]
test = ["pytest"]

[tool.setuptools.packages.find]
include = ["mcp_client*"]
```

`app/agent/mcp/.gitignore`：

```
__pycache__/
*.pyc
*.egg-info/
.pytest_cache/
.DS_Store
```

`app/agent/mcp/mcp_client/client.py`（骨架，后续任务填充）：

```python
"""MCP 协议层：连接 MCP Server 并发现其工具集（tools/list）。

支持 Streamable HTTP 与旧版 SSE 两种传输；同步实现，仅依赖 httpx。
"""

from __future__ import annotations

__all__: list[str] = []
```

`app/agent/mcp/mcp_client/__init__.py`：

```python
"""mcp_client — MCP Server 连接与工具发现的纯协议层。"""

from __future__ import annotations

__all__: list[str] = []
```

README.md 暂不创建（Task 6），但 pyproject 引用了它，pip install 前必须先有——
所以这里先放一个最小占位，Task 6 替换为完整版：

`app/agent/mcp/README.md`：

```markdown
# mcp-client

MCP（Model Context Protocol）Server 连接与工具发现的纯协议层。
完整文档见后续更新。
```

- [ ] **Step 2: 验证包可构建**

```bash
cd /Users/moyunqinghe/个人/学习/feibot/backend
rm app/agent/mcp/__init__.py
# 创建上述文件后：
.venv/bin/python -c "import sys; sys.path.insert(0, 'app/agent/mcp'); import mcp_client; print('ok')"
```

预期输出：`ok`

- [ ] **Step 3: 提交**

```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/mcp
git commit -m "feat(mcp): scaffold mcp-client protocol base package"
```

---

### Task 2: 类型化配置 + 配置校验 + 统一异常

**Files:**
- Modify: `app/agent/mcp/mcp_client/client.py`
- Modify: `app/agent/mcp/mcp_client/__init__.py`
- Create: `app/agent/mcp/tests/test_mcp_client.py`

- [ ] **Step 1: 写失败测试**

`app/agent/mcp/tests/test_mcp_client.py`：

```python
"""mcp_client 单元测试：本地 mock MCP server，不依赖外部网络。"""

from __future__ import annotations

import pytest

from mcp_client import McpDiscoveryError, McpServerConfig, McpTool, discover_mcp_tools


def test_config_rejects_non_http_scheme() -> None:
    config = McpServerConfig(url="ftp://example.com/mcp")
    with pytest.raises(McpDiscoveryError) as exc_info:
        discover_mcp_tools(config)
    assert exc_info.value.phase == "config"


def test_config_rejects_invalid_url() -> None:
    config = McpServerConfig(url="not a url")
    with pytest.raises(McpDiscoveryError) as exc_info:
        discover_mcp_tools(config)
    assert exc_info.value.phase == "config"


def test_config_rejects_non_positive_timeout() -> None:
    config = McpServerConfig(url="http://127.0.0.1:9", timeout_seconds=0)
    with pytest.raises(McpDiscoveryError) as exc_info:
        discover_mcp_tools(config)
    assert exc_info.value.phase == "config"


def test_config_rejects_unknown_transport() -> None:
    config = McpServerConfig(url="http://127.0.0.1:9", transport="stdio")  # type: ignore[arg-type]
    with pytest.raises(McpDiscoveryError) as exc_info:
        discover_mcp_tools(config)
    assert exc_info.value.phase == "config"


def test_mcp_tool_is_frozen_dataclass() -> None:
    tool = McpTool(
        name="a", title="", description="", input_schema={}, output_schema={}, annotations={}
    )
    assert tool.name == "a"
    with pytest.raises(Exception):
        tool.name = "b"  # type: ignore[misc]
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /Users/moyunqinghe/个人/学习/feibot/backend
PYTHONPATH=app/agent/mcp .venv/bin/python -m pytest app/agent/mcp/tests/test_mcp_client.py -v
```

预期：FAIL（ImportError: cannot import name 'McpServerConfig'）。

（说明：包尚未 pip install，本任务阶段先用 PYTHONPATH 跑测试；Task 7 正式安装后
不再需要 PYTHONPATH。）

- [ ] **Step 3: 实现配置、异常与校验**

`app/agent/mcp/mcp_client/client.py` 完整替换为：

```python
"""MCP 协议层：连接 MCP Server 并发现其工具集（tools/list）。

支持 Streamable HTTP 与旧版 SSE 两种传输；同步实现，仅依赖 httpx。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit

__all__ = [
    "McpDiscoveryError",
    "McpServerConfig",
    "McpTool",
    "discover_mcp_tools",
]

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "mcp-client", "version": "1.0.0"}
_TRANSPORTS = ("auto", "streamable-http", "sse")


class McpDiscoveryError(RuntimeError):
    """MCP 工具发现失败的统一异常。

    属性：
    - phase: "config" | "connect" | "initialize" | "list_tools"
    - status_code: HTTP 状态码（仅 HTTP 层错误时非 None）
    - server_message: 服务端 JSON-RPC error 或错误正文摘要（如有）
    """

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        status_code: int | None = None,
        server_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.status_code = status_code
        self.server_message = server_message


@dataclass(frozen=True)
class McpServerConfig:
    """MCP Server 连接配置。

    transport 取值：
    - "auto"（默认）：先 streamable-http；仅在 connect/initialize 阶段失败时
      回退一次 sse。
    - "streamable-http" / "sse"：显式指定，不回退。
    """

    url: str
    transport: Literal["auto", "streamable-http", "sse"] = "auto"
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class McpTool:
    """规范化后的 MCP 工具定义。"""

    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    annotations: dict[str, Any]


class _TransportError(Exception):
    """传输层内部异常：由编排层包装为带 phase 的 McpDiscoveryError。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        server_message: str | None = None,
        is_connect: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.server_message = server_message
        self.is_connect = is_connect


def discover_mcp_tools(config: McpServerConfig) -> list[McpTool]:
    """连接 MCP Server，完成握手并返回其工具列表。"""

    _validate_config(config)
    raise McpDiscoveryError("not implemented", phase="config")  # Task 3 起逐步替换


def _validate_config(config: McpServerConfig) -> None:
    if config.transport not in _TRANSPORTS:
        raise McpDiscoveryError(
            f"不支持的 transport：{config.transport!r}", phase="config"
        )
    if config.timeout_seconds <= 0:
        raise McpDiscoveryError("timeout_seconds 必须为正数", phase="config")
    parsed = urlsplit(str(config.url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise McpDiscoveryError(
            f"MCP server URL 非法（需 http/https）：{config.url!r}", phase="config"
        )
```

`app/agent/mcp/mcp_client/__init__.py`：

```python
"""mcp_client — MCP Server 连接与工具发现的纯协议层。"""

from __future__ import annotations

from mcp_client.client import (
    McpDiscoveryError,
    McpServerConfig,
    McpTool,
    discover_mcp_tools,
)

__all__ = ["McpDiscoveryError", "McpServerConfig", "McpTool", "discover_mcp_tools"]
```

- [ ] **Step 4: 运行确认通过**

```bash
cd /Users/moyunqinghe/个人/学习/feibot/backend
PYTHONPATH=app/agent/mcp .venv/bin/python -m pytest app/agent/mcp/tests/test_mcp_client.py -v
```

预期：5 passed（config 阶段用例全部通过；`discover_mcp_tools` 在校验后抛
not implemented，但本任务没有走到那里的用例）。

- [ ] **Step 5: 提交**

```bash
git add backend/app/agent/mcp
git commit -m "feat(mcp): add typed config, validation and discovery error type"
```

---
