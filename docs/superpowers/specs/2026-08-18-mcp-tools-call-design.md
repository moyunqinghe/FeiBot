# MCP tools/call（真实远程调用，第二步）— 设计文档

日期：2026-08-18
状态：已与需求方逐项确认，待实现
前置：`2026-08-18-mcp-client-base-design.md`（发现基座）、`2026-08-18-mcp-plugin-lifecycle-design.md`（插件生命周期第一步）

## 1. 目标

让装入的 MCP 插件工具**真正可被执行**：给发现基座补 `tools/call` 能力，并把插件
第一步里的占位 handler 换成真实远程调用。模型调用 `12306-mcp__get-tickets` 之类的
工具时，能拿到真实结果回填；工具报错时模型能看到错误信息。

## 2. 已确认的设计决策

| 决策点 | 结论 |
| --- | --- |
| 调用时的连接生命周期 | **每次新开连接**（open → initialize → tools/call → close），与 `discover()` 一致，无持久会话/泄漏负担 |
| 调用结果形状 | **基座返回数据值**（str / structuredContent），handler 负责 stringify |
| 错误呈现 | **一律抛 `McpDiscoveryError`**（工具 isError 与连接/协议失败都抛），复用 calls.py 现有「工具执行失败」回填路径 |
| handler 接线 | **方案 A**：基座加 `call_tool()`；handler 在注册时捕获归一化配置 |

## 3. 边界（谁动谁不动）

| 模块 | 动作 |
| --- | --- |
| `mcp_discovery` 基座 | **新增** `call_tool()` 公共入口、`_Session.call_tool`、结果归一化 `_extract_tool_result`、错误码 `TOOL_ERROR`；配置校验抽成 `_coerce_config` 供 discover/call_tool 复用 |
| `app/agent/tools/mcp_plugins.py` | **改** `_register_tools` 增 `config` 参数；占位 handler → 真实调用 handler `_make_handler`；新增 `_stringify` 与调用超时常量 |
| `app/agent/tools/registry.py` / `engine.py` / `calls.py` | **零改动**（handler 契约仍是 `(**args) -> str`） |
| `store.py` / `main.py` | **零改动** |

## 4. 基座 API

### 4.1 公共入口 `call_tool()`（镜像 `discover()`）

```python
call_tool(
    config,                        # McpServerConfig 或 mapping（自动校验）
    tool_name: str,                # 工具名（不带插件前缀）
    arguments: Mapping | None = None,
    *,
    timeout_seconds: float = 30.0, # 调用超时（比发现更宽）
    protocol_version: str = PROTOCOL_VERSION,
    client_info: Mapping | None = None,
) -> Any                           # 归一化数据值
```

流程：`_coerce_config(config)` → `_build_session(...)` → `with session:` →
`session.initialize()` → `session.call_tool(tool_name, dict(arguments or {}))` →
`_extract_tool_result(raw)` → 关闭。`McpDiscoveryError` 直抛；其他异常收敛为
`McpDiscoveryError(..., code=PROTOCOL_ERROR, cause=exc)`。

### 4.2 `_Session.call_tool`

```python
def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
    return self._request("tools/call", {"name": name, "arguments": arguments})
```
三种 transport 复用各自现成的 `_request`，无需分别实现。

### 4.3 结果归一化 `_extract_tool_result(raw) -> Any`

- `raw` 非 Mapping → 原样返回
- `isError=true` → 抛 `McpDiscoveryError(工具错误文本, code=TOOL_ERROR)`
- 有 `structuredContent` → 返回它
- 否则抽取 `content` 里的 text 块拼成字符串返回
- 都没有 → 兜底原样返回

辅助 `_content_text(content)`：从 content 列表抽 type=="text" 的 text 拼接。

### 4.4 新错误码

`TOOL_ERROR = "TOOL_ERROR"` — 工具自身返回 isError（协议本身正常）。不可重试。
其余连接/超时/协议失败复用既有 `CONNECT_FAILED` / `TIMEOUT` / `PROTOCOL_ERROR`。

### 4.5 配置校验去重

把 `discover()` 里"mapping → `McpServerConfig.model_validate`（失败转 CONFIG_INVALID）"
那段抽为 `_coerce_config(config) -> McpServerConfig`，`discover` 与 `call_tool` 共用。

## 5. 插件接线（mcp_plugins.py）

- `_register_tools(self, name, tools, config)` 增 `config` 参数；注册时 handler 用
  `_make_handler(config, t.name)`。
- `_make_handler(self, config, tool_name)` 返回闭包：

```python
def handler(**args) -> str:
    result = call_tool(config, tool_name, args, timeout_seconds=MCP_CALL_TIMEOUT_SECONDS)
    return _stringify(result)
```

- `_stringify(value) -> str`：`str` 原样；否则 `json.dumps(value, ensure_ascii=False, default=str)`，失败退 `str(value)`。
- 新增模块常量 `MCP_CALL_TIMEOUT_SECONDS = 30.0`。
- 删除 `_placeholder_handler`。
- 四条注册路径（`install` / `enable` / `reload` / `load_enabled`）都已持有归一化配置，改为传入 `_register_tools`。

## 6. 数据流（一次调用）

```
模型输出 {"tool": "12306-mcp__get-tickets", "args": {...}}
 → calls.parse_tool_call → execute_tool_call → spec.handler(**args)
 → handler → call_tool(config, "get-tickets", args)   [基座]
     open → initialize → tools/call → _extract_tool_result → close → 数据值
 → handler._stringify(value) → str
 → execute_tool_call 返回 str → engine 以「工具结果:…」回填
```
报错：基座抛 `McpDiscoveryError` → `execute_tool_call` 现有 except → 「工具执行失败:…」。

## 7. 错误处理

| 情形 | code | 可重试 |
| --- | --- | --- |
| 工具 isError=true | `TOOL_ERROR` | 否 |
| 连接失败 / HTTP 状态异常 | `CONNECT_FAILED` | 是 |
| 读/写/等待超时 | `TIMEOUT` | 是 |
| JSON-RPC error / 响应不合法 | `PROTOCOL_ERROR` | 否 |
| 配置非法 | `CONFIG_INVALID` | 否 |

## 8. 测试策略

- **基座 `call_tool`**：结果提取（text / structuredContent / isError→TOOL_ERROR / 非 dict 兜底）、`_coerce_config`、经 transport `_request` 发起。HTTP 用 `httpx.MockTransport` 注入（仿第一步 http 测试）。
- **扩展 `tests/stdio_mock_server.py`**：加 `tools/call` 分支（echo 工具返回文本），供真实调用集成测试。
- **插件单测**：monkeypatch `mcp_plugins.call_tool`，验证 handler 用捕获的 config + 工具名调用、正确 stringify、isError 透传。
- **集成 round-trip**：真实 stdio install → handler 真调 → 拿结果 → uninstall。
- **更新第一步遗留**：`test_integration_stdio_install_list_uninstall` 中"占位 handler 提示"断言改为真实调用断言。

## 9. 验收标准

1. install 真实 12306 后，模型调 `12306-mcp__get-tickets` 能拿到真实余票结果（或 stdio 集成测试证明真实调用出结果）。
2. 工具报错时模型收到「工具执行失败:<工具错误>」。
3. 装/卸/启停/重载在接入真实调用后仍正常；engine/calls/store/main 零改动。
4. 全量回归绿。

## 10. 明确不做（YAGNI / 后续）

- 持久会话 / stdio 进程常驻（已选每次新开，若日后性能够不上再做）。
- 每个插件单独配置调用超时（先用全局常量 `MCP_CALL_TIMEOUT_SECONDS`）。
- 调用层重试编排（`code` 已标可重试性，重试策略归宿主）。
- tools/list 分页、resources/prompts 等其他 MCP 能力。
