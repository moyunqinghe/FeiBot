# MCP 协议层基座（mcp-client）设计

日期：2026-08-18
状态：已确认（方案 B）

## 1. 背景与目标

StaffDeck 项目中有一套经过实战检验的 MCP 协议客户端实现
（`backend/app/tools/mcp_client.py`，自研 JSON-RPC 2.0 over MCP，支持
stdio / Streamable HTTP / 旧版 SSE）。feibot 项目需要连接 MCP Server 并
自动发现其工具集，为避免重复造轮子，从 StaffDeck 抽取其中的协议内核，
在 feibot 中落成一个**零业务耦合的纯协议层基座**。

feibot 已有两个同形态先例，本模块完全沿用其惯例：

- `backend/app/llm/llm_protocols/`（包名 `llm_protocols`）
- `backend/app/channels/wechat/`（包名 `wechat_ilink`）

两个项目（StaffDeck 与 feibot）在抽取完成后互不关联：一次性移植，
不建立任何持续的代码同步关系。

### 需求边界（已与用户确认）

- 能力范围：**仅 MCP Server 连接 + 工具发现（`tools/list`）**
- 传输方式：**仅 Streamable HTTP + 旧版 SSE**（不含 stdio）
- 不含：tools/call 工具调用、resources 读取、MCP Apps UI 扩展、前端界面
- 约束：**不修改 feibot 其他任何模块的内容**；模块自包含。
  唯一允许的环境级操作是 `pip install -e` 将本包安装进 feibot 的 venv

## 2. 模块形态

落位路径：`/Users/moyunqinghe/个人/学习/feibot/backend/app/agent/mcp/`

```
backend/app/agent/mcp/            # 基座容器目录（非 Python 包，无 __init__.py）
├── pyproject.toml                # 包名 mcp-client，可本地路径 pip 安装
├── README.md                     # 说明 + 安装 + 快速上手（风格对齐两个先例）
├── .gitignore                    # __pycache__/ *.pyc *.egg-info/ .pytest_cache/ .DS_Store
├── mcp_client/                   # 包本体，import 名 mcp_client
│   ├── __init__.py               # 公开导出 + __all__
│   └── client.py                 # 全部协议实现（约 300 行）
└── tests/
    ├── conftest.py               # 测试公共夹具（本地 mock MCP server）
    └── test_mcp_client.py        # 自包含测试
```

- 现有占位文件 `backend/app/agent/mcp/__init__.py` 移除（当前无任何代码
  import `app.agent.mcp`，已验证），容器布局与两个先例保持一致。
- 安装：`pip install -e backend/app/agent/mcp`（可编辑模式，便于调试）。
- 使用：`from mcp_client import discover_mcp_tools, McpServerConfig`。

### pyproject.toml 要点（对齐先例）

- `[build-system]` setuptools>=68
- `name = "mcp-client"`，`version = "1.0.0"`，MIT license，
  `requires-python = ">=3.11"`
- `dependencies = ["httpx"]`（feibot venv 已有 httpx 0.28.1，无新增负担）
- `[project.optional-dependencies] test = ["pytest"]`
- `[tool.setuptools.packages.find] include = ["mcp_client*"]`

## 3. 公开 API

```python
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

@dataclass(frozen=True)
class McpServerConfig:
    url: str                                            # http/https 端点
    transport: Literal["auto", "streamable-http", "sse"] = "auto"
    headers: Mapping[str, str] = field(default_factory=dict)  # 鉴权等自定义头
    timeout_seconds: float = 10.0                       # 单次请求超时

@dataclass(frozen=True)
class McpTool:
    name: str
    title: str                      # MCP 工具人类可读标题，可为空串
    description: str
    input_schema: dict[str, Any]    # MCP 工具原始 JSON Schema
    output_schema: dict[str, Any]   # 若 server 提供（新版 MCP 规范），否则 {}
    annotations: dict[str, Any]     # MCP 工具 annotations 原样保留

class McpDiscoveryError(RuntimeError):
    """发现失败的统一异常。

    属性：
    - phase: "config" | "connect" | "initialize" | "list_tools"
    - status_code: int | None（HTTP 层错误时）
    - server_message: str | None（服务端 JSON-RPC error / 错误正文摘要）
    """

def discover_mcp_tools(config: McpServerConfig) -> list[McpTool]:
    """连接 MCP Server，完成握手并返回其工具列表。"""
```

行为约定：

- 同步实现（feibot 全项目为同步风格），基于 `httpx.Client`。
- **transport="auto" 的精确语义**（本模块新增逻辑，StaffDeck 无此机制）：
  先按 Streamable HTTP 尝试；仅当失败发生在 connect 或 initialize 阶段
  （任何原因）时，回退尝试一次旧版 SSE；initialize 已成功、失败发生在
  list_tools 阶段时**不回退**（协议已被对端接受）。两种传输都失败时抛
  最后一次尝试的错误，异常 message 注明 auto 模式尝试过的传输顺序。
  显式指定 `streamable-http` / `sse` 时不做回退。
- 配置非法（非 http/https、URL 无法解析、timeout <= 0）抛
  `McpDiscoveryError(phase="config")`，不发起网络请求。
- 工具定义规范化移植自 StaffDeck `_normalize_tool_definition`，行为保持
  一致：字段缺失置空值（不跳过条目）；`name`/`title`/`description` 转
  字符串并 strip；`inputSchema`/`outputSchema`/`annotations` 非 dict 时
  置 `{}`。是否过滤无名工具由调用方决定。

## 4. 内部实现（移植清单）

源文件：StaffDeck `backend/app/tools/mcp_client.py`（913 行）。
目标：`mcp_client/client.py`，只保留发现链路所需部分。

移植（保留经过验证的协议逻辑）：

1. JSON-RPC 2.0 请求构造与响应解析；JSON-RPC error 对象映射为异常。
2. initialize 握手：协议版本 `2024-11-05`，clientInfo，capability 交换；
   成功后发送 `notifications/initialized` 通知。
3. Streamable HTTP 会话（StaffDeck `_HttpSession`）：POST JSON-RPC 到
   端点；解析普通 JSON 与 SSE 两种响应形态；携带并跟踪 `Mcp-Session-Id`。
4. 旧版 SSE 会话（StaffDeck `_SseSession`）：GET 建立 SSE 流，读取
   `endpoint` 事件取得消息端点，再 POST JSON-RPC 消息，响应从 SSE 流读取。
5. `tools/list` 调用与结果规范化。

改造（与 StaffDeck 的差异）：

- dict 传参 → 类型化 `McpServerConfig`；返回类型化 `McpTool` 列表。
- 删除：stdio 会话及子进程管理、`execute_mcp_tool*`（调用）、
  `read_mcp_resource`（资源）、MCP Apps 协商、内置演示 MCP。
- 错误收敛：StaffDeck 内部错误码统一映射为单一 `McpDiscoveryError`
  （保留阶段/状态码/服务端信息三个诊断属性）。
- 新增：`transport="auto"` 的回退编排逻辑（StaffDeck 的 transport 均为
  显式指定，此为唯一的新增行为，语义见第 3 节）。
- 不引入 StaffDeck 的任何模块；运行时依赖仅 httpx + 标准库。

## 5. 测试策略

全部位于 `backend/app/agent/mcp/tests/`，自包含、不依赖 feibot 业务代码
与外部网络：

- `conftest.py`：基于 `http.server` 的本地 mock MCP server 夹具，
  可分别以 Streamable HTTP 与旧版 SSE 形态应答，可注入错误行为。
- 用例覆盖：
  1. streamable-http 正常发现（含 `Mcp-Session-Id` 协商）
  2. sse 显式指定正常发现
  3. auto 模式：streamable-http 端点直接成功
  4. auto 模式：streamable-http 连接/握手失败 → 回退 sse 成功
  5. auto 模式：initialize 成功但 tools/list 失败 → 不回退，直接抛错
  6. initialize 阶段服务端返回 JSON-RPC error → 异常带 initialize 阶段
  7. tools/list 阶段 HTTP 5xx → 异常带状态码与 list_tools 阶段
  8. 畸形工具定义被规范化（缺字段置空值、非 dict 的 schema 置 {}、
     不跳过条目）
  9. 连接超时 → connect 阶段异常
  10. 非法配置（坏 URL / 非 http 协议 / timeout<=0）→ config 阶段异常
- 适配参考：StaffDeck `backend/tests/test_mcp_servers_api.py` 中的
  mock server 与断言模式。

## 6. 验收标准

1. `pytest backend/app/agent/mcp/tests` 全部通过（feibot venv，Python 3.14）。
2. feibot 原有测试 `backend/tests` 全量运行，结果与改动前一致（不受影响）。
3. `pip show mcp-client` 显示已安装；`python -c "from mcp_client import
   discover_mcp_tools"` 成功。
4. feibot 仓库中除 `backend/app/agent/mcp/` 目录内容与占位
   `__init__.py` 的移除外，无任何文件变更（git status 验证）。
5. README 含：模块定位（纯协议层、零业务耦合）、依赖声明、安装方式、
   快速上手示例（对齐 llm_protocols / wechat_ilink 的 README 结构）。

## 7. 非目标（明确不做）

- 前端界面（用户明确表示目前完全不做前端）
- tools/call 工具调用、resources/prompts 读取、MCP Apps
- stdio 传输与子进程管理
- feibot 侧的工具注册表接入（`agent/tools/registry`）——由 feibot
  后续业务自行消费本基座，本次不改
- 与 StaffDeck 的持续同步机制（一次性移植）
