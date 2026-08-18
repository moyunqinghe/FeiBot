# MCP 插件管理聊天指令 — 设计文档

日期：2026-08-19
状态：已确认（用户授权全权推进，自主按推荐方案决策）
前置：MCP 发现基座、插件生命周期（第一步）、tools/call 真实调用（第二步）均已合并

## 1. 目标

让**白名单（管理员）会话**在微信里直接用斜杠指令管理 MCP 插件，无需编程：
`/mcp list`、`/mcp add <名称> <url>`、`/mcp remove <名称>`、`/mcp enable <名称>`、`/mcp disable <名称>`。
复用现有 slash 解析机制与 `is_tool_admin` 白名单门控。

## 2. 已确认的设计决策

| 决策点 | 结论 |
| --- | --- |
| 指令集合 | `list` / `add` / `remove` / `enable` / `disable`（与插件管理器方法一一对应；reload 暂不做，YAGNI） |
| add 的配置形式 | 仅 `/mcp add <名称> <url>`，按 `streamable_http`（归一化为 http）安装。聊天里装 stdio 需命令行+参数且涉及本地任意进程，风险高、场景少，不支持（stdio 走编程式 API） |
| 权限门控 | 全部 `/mcp` 指令仅 `is_tool_admin(conv_key)` 可用；非管理员返回拒绝提示。需要把 `conv_key` 传入命令分发 |
| 同步/异步 | 同步执行（管理员操作低频；add 内部 discover 最坏约 10s 超时后返回错误），与现有指令一致，不引入异步 |
| 输出格式 | 纯文本多行（与 `/模型` 一致） |
| 生效时机 | 运行时 bot 进程内直接操作模块级 `plugin_manager` 单例 → 立即在当前进程注册表生效并落库，无需重启 |

## 3. 边界（谁动谁不动）

| 模块 | 动作 |
| --- | --- |
| `app/agent/slash.py` | **改**：`parse_command` 识别 `mcp` → `ChannelCommand(kind="mcp", query=...)` |
| `app/agent/engine.py` | **改**：`_handle_command` 增 `conv_key` 形参并透传；新增 `_handle_mcp(conv_key, query)`；`HELP_TEXT` 加 /mcp 说明；导入 `plugin_manager` |
| `app/agent/tools/mcp_plugins.py` | **零改动**（复用现有 install/uninstall/enable/disable/list） |
| 基座 `mcp_discovery` / `store.py` / `registry.py` / `calls.py` / `main.py` | **零改动** |

## 4. 指令解析（slash.py）

`parse_command` 在现有分支后加：`name == "mcp"` → `ChannelCommand(kind="mcp", query=query)`。
（沿用"第一个词为指令名、其余为参数"的既有约定；query 形如 `"add 12306-mcp https://..."`。）

## 5. 命令分发与门控（engine.py）

- `handle_message` 中 `return _handle_command(cmd)` 改为 `return _handle_command(conv_key, cmd)`。
- `_handle_command(conv_key, cmd)` 签名加 `conv_key`；新增分支：
  ```python
  if cmd.kind == "mcp":
      return _handle_mcp(conv_key, cmd.query)
  ```
  其余 kind（ping/model/memory/forget/help）行为不变、不做门控（保持现状）。

## 6. `_handle_mcp(conv_key, query)` 行为

1. **门控**：`if not is_tool_admin(conv_key): return "MCP 插件管理仅限管理员使用。"`
2. **解析子命令**：`sub, _, arg = query.partition(" ")`；`sub = sub.lower()`；空 query 或 `sub=="list"` → 列表。
3. **list**：`plugin_manager.list()`，逐行输出 `- <name>（启用/停用，N 个工具）status`；无插件时给提示 + 用法。
4. **add**：`arg` 再拆 `<名称> <url>`（缺参 → 用法提示）。`config = {"type":"streamable_http","url":url}`；`n = plugin_manager.install(name, config)` → 成功返回 `已装入插件 <name>，发现 N 个工具。`；捕获 `McpDiscoveryError`/`PluginError` → `装入失败：<原因>`。
5. **remove**：`plugin_manager.uninstall(name)`；True → `已卸下插件 <name>。`；False → `没有名为 <name> 的插件（/mcp 查看）。`
6. **enable / disable**：分别调 `plugin_manager.enable/disable`；enable 捕获 `PluginError`/`McpDiscoveryError` 返回失败原因；disable 返回是否成功。
7. **未知子命令**：返回 /mcp 用法说明。

所有分支返回字符串，不让异常冒到渠道层。

## 7. HELP_TEXT 追加

```
/mcp(/mcp list) - 列出已装 MCP 插件(仅管理员)
/mcp add <名称> <url> - 装入 MCP 插件
/mcp remove <名称> - 卸下 MCP 插件
/mcp enable|disable <名称> - 启用/停用 MCP 插件
```

## 8. 错误处理

| 情形 | 行为 |
| --- | --- |
| 非管理员调用 /mcp | 返回拒绝提示 |
| add 缺参/格式错 | 返回用法提示 |
| install 失败（连不上/配置非法/非法名） | 返回「装入失败：<原因>」 |
| remove/enable/disable 目标不存在 | 返回「没有名为…的插件」或相应提示 |
| 未知子命令 | 返回 /mcp 用法 |

## 9. 测试策略

- **slash**：`/mcp`、`/mcp list`、`/mcp add x http://u` 解析出 kind="mcp" 与正确 query；非 /mcp 不受影响。
- **门控**：非管理员 conv 调 `/mcp list` 返回拒绝；管理员放行（monkeypatch `is_tool_admin`）。
- **_handle_mcp**：monkeypatch `plugin_manager` 的 install/uninstall/enable/disable/list，验证各子命令的调用与输出文案；add 缺参返回用法；install 抛 McpDiscoveryError 时返回「装入失败」。
- 复用现有临时 db 夹具；不联网（monkeypatch 管理器方法）。

## 10. 验收标准

1. 管理员会话发 `/mcp add 12306-mcp <url>` 能装入并在后续对话真实调用；`/mcp list` 可见；`/mcp remove` 可卸。
2. 非管理员会话发任何 /mcp 指令都被拒绝。
3. enable/disable 正确切换工具可用性。
4. 引擎/基座/存储零改动外的既有行为不回归；全量测试绿。

## 11. 明确不做（YAGNI）

- 聊天里装 stdio 插件（命令行+参数、本地任意进程，风险高）。
- `/mcp reload`、SSE/stdio 的聊天安装、批量信封安装（编程式 API 已覆盖）。
- 异步/后台执行、进度反馈。
