# MCP 插件生命周期管理 — 设计文档

日期：2026-08-18
状态：已与需求方逐项确认，待实现
关联基座：`backend/app/agent/mcp/`（`mcp-discovery`，见同日 `2026-08-18-mcp-client-base-design.md`）

## 1. 目标

把 MCP Server 当作**插件**接入 feibot：工具可以动态**装入** agent、随时**卸载移除**、
重启后仍然生效，且多插件并存互不冲突。核心诉求是**灵活**——装、卸、启、停、刷新都由
干净的编程式 API 控制。

本设计只做**第一步**：工具"定义"的装入/卸下（发现 → 注册 → 模型可见）。
真正的远程执行（`tools/call`）是**第二步**，本 spec 为其留好插槽但不实现。

## 2. 已确认的设计决策

| 决策点 | 结论 |
| --- | --- |
| 范围 | 分两步走；第一步只做"定义的装/卸"，**零改基座** |
| 插件粒度 | 一个插件 = 一个 MCP Server（装=装入其全部工具，卸=整批移除） |
| 持久化 | sqlite 存 server 配置 + 启用状态；启动时重载 |
| 第一入口 | 干净的编程式 API（聊天指令/UI 以后再加） |
| 命名 | 工具加插件名前缀 `{插件名}__{工具名}` |
| 架构 | 单一插件管理器模块（方案 A），归属映射收在一处 |

## 3. 在完整链路中的位置

feibot 调用 MCP 工具是一条 5 环链路，本设计是第 ② 环（接入/管理）：

```
① 连接/发现   mcp_discovery 基座：连上 server，取回工具清单        ✅ 已建好
② 接入/管理   ★本设计★：清单 → 注册表正式工具，管装/卸/持久化
③ 暴露给模型  engine.render_tools_prompt() 注入 system 提示         ✅ 已存在
④ 解析/路由   calls.parse_tool_call() 模型输出 JSON → 查注册表       ✅ 已存在
⑤ 执行        handler：内置=本地 Python；MCP=需 tools/call           ⏳ 第二步
```

**核心价值**：一旦工具被第 ② 环放进 `TOOL_REGISTRY`，第 ③④ 环（engine 注入提示、
calls 解析路由）**自动生效、零改动**——因为 engine 每轮实时读注册表。MCP 工具由此和
内置工具（`current_time`/`shell`）一样成为 agent 的一等公民。

**明确不负责**：本部分不让工具真正被执行；执行是第 ⑤ 环的 `tools/call`（第二步）。
第一步做到的是"可见、可路由、可装卸、可持久"，并留好执行插槽。

## 4. 架构边界（谁动谁不动）

| 模块 | 动作 |
| --- | --- |
| `mcp_discovery` 基座 | **完全不动**，只被管理器调 `discover()` |
| `app/agent/tools/mcp_plugins.py` | **新增**：`McpPluginManager`（核心） |
| `app/agent/tools/registry.py` | **加一个** `unregister_tool(name) -> bool` |
| `app/db/store.py` | **加一张** `mcp_plugins` 表 + 几个 CRUD 函数 |
| `app/main.py` | **加一行** `manager.load_enabled()`（启动钩子） |
| `app/agent/engine.py` / `calls.py` | **零改动** |

> registry/store/main 的改动是**宿主代码的正常扩展**；"基座不和其他模块打架"指的是
> 基座本身一个字不改。

## 5. 数据模型

### 5.1 持久化表 `mcp_plugins`（仿 `model_configs` 风格）

```sql
CREATE TABLE IF NOT EXISTS mcp_plugins (
    name        TEXT PRIMARY KEY,   -- 插件名，也用作工具前缀
    config_json TEXT NOT NULL,      -- MCP server 连接配置
    enabled     INTEGER NOT NULL DEFAULT 1,
    added_at    TEXT,
    updated_at  TEXT
);
```

**只持久化配置 + 启用状态，不缓存发现到的工具清单**——工具的真相以 server 实时状态为准，
装载/启用时重新 `discover()`，避免陈旧 schema。

### 5.2 内存归属映射（卸载的关键）

管理器持有 `插件名 → 已注册进 TOOL_REGISTRY 的实际 key 集合`。卸载按此集合逐个移出，
**不靠反解工具名**，因此名字里含任何合法字符都不会歧义。

### 5.3 工具前缀与命名

- 注册 key 形如 `{插件名}__{工具名}`（双下划线分隔）。
- 插件名允许 `[A-Za-z0-9_-]`，**不得包含 `__`**；非法名在 install 时拒绝。
- 双下划线分隔 + 内置工具无前缀，保证插件之间、插件与内置之间不会撞名。

示例（真实 12306 server）：插件名 `12306-mcp`，工具注册为
`12306-mcp__get-tickets`、`12306-mcp__get-stations-code-in-city` 等。

## 6. 管理器 API（`McpPluginManager`，编程式第一入口）

```python
manager.install(name, config)          # 校验→discover→加前缀注册→落库；同名=幂等重装
manager.uninstall(name) -> bool        # 纯本地：移出工具→删库→清归属；不存在返回 False
manager.enable(name) / disable(name)   # disable=移出工具但留配置；enable=重新 discover+注册
manager.reload(name)                   # 重新 discover，diff 更新已注册工具
manager.list()                         # 各插件 + 状态（工具数/最近加载结果）
manager.load_enabled()                 # 启动钩子：读库→逐个 install 启用中的插件

# 便捷适配：直接吃标准 mcpServers 信封（Claude Desktop / Cursor 同款），批量装
manager.install_from_mcp_servers({"mcpServers": {name: {"type": ..., "url": ...}}})
```

- `config` 接受 mapping 或 `McpServerConfig`；`type` 字段映射为基座的 `transport`
  （`streamable_http` 归一化为 `http`）。
- 装卸是低频管理操作，第一步假设单线程调用，不加锁。

## 7. handler 占位与第二步插槽

第一步里每个注册的 MCP 工具，其 `ToolSpec.handler` 是一个闭包，返回
*"该工具来自 MCP 插件 {name}，远程执行尚未接入"*——模型看得到工具、误调用时得到友好
文本而非崩溃（贴合 `calls.py`"工具错误转文本、不炸主流程"的约定）。

第二步把该闭包替换为真正的 `tools/call`（需要基座补 call 能力 + 连接管理），
注册/装卸逻辑不变。占位 handler 就是这个插槽。

## 8. 生命周期数据流

- **install**：校验名 → 构造配置 → `discover()`（失败即止，不注册不落库）→ 同名则先移旧
  → 加前缀注册（占位 handler）→ 更新归属 → upsert 落库（enabled=1）。
- **uninstall**：查归属集合 → 逐个 `unregister_tool` → 删库行 → 清归属。**不联网**。
- **disable**：移出工具、保留库记录；**enable**：重新 discover + 注册。
- **reload**：discover → diff（新增注册 / server 已删的移除 / 已有更新）→ 刷新 updated_at。
- **load_enabled**：读库 enabled=1 → 逐个 install；单个失败不拖累整体。

## 9. 错误处理

| 场景 | 行为 |
| --- | --- |
| install 时 discover 失败 | 不注册不落库，抛 `McpDiscoveryError`（带 `.code`） |
| 启动重载某插件失败 | 跳过 + 记录原因，继续装其他，bot 照常启动 |
| uninstall 不存在的插件 | 返回 `False`，不抛异常 |
| 占位 handler 被调用 | 返回友好文本，不抛异常 |
| 非法插件名 / 非法配置 | install 时拒绝，返回/抛出明确错误 |

## 10. 测试策略

- **单测（不联网）**：monkeypatch `discover`，验证 install 注册正确前缀工具、uninstall
  卸干净、幂等重装、enable/disable、非法名被拒、归属映射正确。用内存 registry + 临时
  sqlite（复用现有 tmp-db 夹具）。
- **store 层**：`mcp_plugins` 表 CRUD（仿 `model_configs` 测试风格）。
- **集成 round-trip（稳定可重复）**：用基座自带 `tests/stdio_mock_server.py` 当本地
  server，走 install→list→uninstall；不依赖外网。真连 12306 用例标 `skipif` 作为可选。
- **回归**：现有 tools/engine 测试保持全绿。

## 11. 第二步预告（不在本 spec 范围）

- 基座补 `tools/call`（复用现有 session 结构）+ 连接生命周期（stdio 子进程 / HTTP 会话）。
- 把第 7 节的占位 handler 换成真实远程调用。
- 届时 `execute_tool_call` 走到 MCP 工具即真正出结果，第 ③④⑤ 环全线打通。

## 12. 验收标准（第一步）

1. 用编程式 API 装上真实 12306 server，能在 `TOOL_REGISTRY`/提示中看到 8 个带前缀工具。
2. `uninstall("12306-mcp")` 后这 8 个工具从注册表与提示中消失，库记录删除。
3. 重启进程后 `load_enabled()` 自动重装启用中的插件。
4. disable/enable/reload 行为符合第 8 节；两个插件同名工具不冲突。
5. 基座零改动；现有 tools/engine 测试全绿。
