# mcp-discovery

MCP (Model Context Protocol) server **工具发现的通用纯协议层**,零业务/数据库/Web 框架耦合。
覆盖三种 transport:stdio、Streamable HTTP(含 `Mcp-Session-Id` 会话管理)、
legacy HTTP+SSE(2024-11-05 规范)。

- 不依赖任何业务代码,不含数据库、不含 fastapi/sqlmodel——所有参数显式传入。
- 依赖:`httpx`、`pydantic`。
- 要求 Python >= 3.11。

## 安装

```bash
# 在新项目中直接以本地路径安装
pip install /path/to/app/agent/mcp

# 或以可编辑模式安装(调试用)
pip install -e /path/to/app/agent/mcp

# 跑测试
pip install "/path/to/app/agent/mcp[test]"
pytest /path/to/app/agent/mcp/tests
```

## 快速上手

```python
from mcp_discovery import McpServerConfig, discover

# Streamable HTTP
result = discover(
    McpServerConfig(
        transport="http",
        url="https://example.com/mcp",
        headers={"Authorization": "Bearer ..."},
    )
)

# stdio(配置也可以是普通 mapping,如数据库里存的一行)
result = discover({"transport": "stdio", "command": "npx", "args": ["-y", "@some/mcp-server"]})

print(result.server_info)  # {'name': ..., 'version': ...}
for tool in result.tools:
    print(tool.name, tool.description, tool.input_schema)
```

## 唯一入口 discover()

```python
discover(
    config,                        # McpServerConfig 或 mapping(自动 Pydantic 校验)
    *,
    timeout_seconds=10.0,          # 连接/读/写统一超时
    protocol_version=PROTOCOL_VERSION,  # initialize 协商的协议版本,默认 2025-06-18
    client_info=None,              # initialize 的 clientInfo,默认 {"name": "mcp-discovery", ...}
) -> DiscoveryResult
```

流程固定为:建连 → `initialize` 握手 → `notifications/initialized` → `tools/list`。
返回 `DiscoveryResult`(frozen):

- `tools: tuple[DiscoveredTool, ...]` — 归一化后的工具定义
  (`name` / `title` / `description` / `input_schema` / `output_schema` /
  `annotations` / `meta`;兼容 camelCase 与 snake_case 键名)
- `protocol_version` / `capabilities` / `server_info` — server 在 initialize 里声明的元信息

## 三种 transport

| transport | 配置 | 说明 |
| --- | --- | --- |
| `stdio` | `command`(+ `args` / `env` / `cwd`) | 拉起子进程,JSON 行成帧 |
| `http` | `url`(+ `headers`) | Streamable HTTP;`streamable_http` 是别名 |
| `sse` | `url`(+ `headers`) | 2024-11-05 HTTP+SSE(GET 流 + POST 消息端点) |

`transport` 可省略:有 `command` 推断 stdio,有 `url` 推断 http。

## 协议行为

协议内核的关键行为(均经实际使用与测试检验):

- **stdio**:线程化管道读取,不依赖 `select()`(Windows 匿名管道上 select 会拒绝管道);
  写入在独立线程里做,超时有界;单条响应 4 MiB 上限;stderr 尾部收集进错误信息。
- **stdio 启动前校验**,错误信息可直接操作:命令不在 PATH(`找不到命令`)、
  工作目录不存在、node/bun/deno 入口文件不存在(提示检查 args 或 cwd)。
- **Streamable HTTP**:请求头 `Accept: application/json, text/event-stream`;
  响应体兼容纯 JSON 与 `text/event-stream`(取最后一个合法 data 行);
  从 initialize 响应头捕获 `Mcp-Session-Id` 并在后续请求回传。
- **SSE**:从首个 `event: endpoint` 协商消息端点(相对路径按 base url 解析,
  绝对 URL 直通);POST 请求后在 SSE 流里按 JSON-RPC id 匹配响应;
  注释行(`:` 开头)跳过。
- **JSON-RPC 2.0 成帧**:按 id 匹配响应,跳过通知与无法解析的行;
  server 返回 `error` 对象时消息原样上抛。

几个值得注意的设计决策:

- SSE 等待不用 `read=None` 无限阻塞——读超时取 `timeout_seconds`,
  静默流收敛为 TIMEOUT 错误(否则坏 server 会把调用方挂死)。
- 子进程守护为内置 `_ProcessGuard`(terminate→kill→wait),
  不引入 Job Object 之类的重型机制;发现流程的进程都是短命的,
  退出路径必然回收。

## 错误:单一类型 McpDiscoveryError

所有可预期失败都抛 `McpDiscoveryError`,带机器可读 `.code` 与 `.cause`
(触发它的底层异常,如 pydantic `ValidationError` / httpx 异常,便于记日志):

| code | 含义 |
| --- | --- |
| `CONFIG_INVALID` | 配置不合法(缺 command/url、未知 transport 等) |
| `LAUNCH_FAILED` | stdio 子进程启动失败(命令不存在、无权限、cwd/入口文件不存在) |
| `CONNECT_FAILED` | HTTP/SSE 连接失败、HTTP 状态码异常、stdio 进程提前退出 |
| `TIMEOUT` | 读/写/等待响应超时 |
| `PROTOCOL_ERROR` | JSON-RPC error、响应体不合法、SSE 未返回 endpoint 等 |

## 本包的边界:使用方需要自己决定的事

本包只负责"怎么跟 MCP server 说话、把它的工具清单拿回来"。以下问题
没有标准答案,接入项目按自己的业务实现:

1. **配置存哪** — server 连接配置落不落库、表结构、加密,是宿主的存储设计。
   `discover()` 同时接受 `McpServerConfig` 与普通 mapping,怎么存怎么取都行。
2. **何时发现、结果怎么缓存** — 定时同步、按需探测、TTL,都是宿主的编排。
   tools/list 分页(cursor)目前未实现——绝大多数 server 一页返回全部。
3. **tools/call** — 本包只做发现。会话结构(`_Session` 的
   initialize/request/notify)已经为扩展 tools/call、resources/read 留好位置,
   但那是下一个模块的事。
4. **重试策略与可观测性** — `code` 标好了错误分类
   (TIMEOUT/CONNECT_FAILED 通常值得重试),重试与打点由宿主决定。
5. **工具汇入 agent 注册表** — 把 `DiscoveredTool` 翻译成宿主工具系统的
   定义、命名空间冲突处理,是宿主 agent 层的事。

### 本包有意不包含的东西

- MCP-UI Apps 扩展(`io.modelcontextprotocol/ui`)、`tools/call`、
  `resources/read`、内置演示工具——只做发现;会话结构留有扩展位。
- 子进程管理不引入 Job Object 之类的重型机制,内置 `_ProcessGuard`
  (terminate→kill→wait)足够。
- 不做自由 dict 猜测式配置——配置是类型化的 `McpServerConfig`
  (保留 `streamable_http` 别名归一化与 command/url 推断)。
- 统一异常为带 `.code` 分类与 `.cause` 的 `McpDiscoveryError`,
  不分散成多个异常类型。
- `clientInfo` 默认 `mcp-discovery`,可传参覆盖。

## API 摘要

### `mcp_discovery`(即 `mcp_discovery.client`)

- `discover(config, *, timeout_seconds=10.0, protocol_version=PROTOCOL_VERSION,
  client_info=None)` — 唯一入口,返回 `DiscoveryResult`。
- `McpServerConfig` — Pydantic 配置(frozen):`transport` / `command` / `args` /
  `env` / `cwd` / `url` / `headers`。
- `DiscoveredTool` — frozen dataclass:`name` / `title` / `description` /
  `input_schema` / `output_schema` / `annotations` / `meta`。
- `DiscoveryResult` — frozen dataclass:`tools` / `protocol_version` /
  `capabilities` / `server_info`。
- `McpDiscoveryError` — 单一异常:`.code`(上方常量)/ `.cause`。
- 常量:`PROTOCOL_VERSION`、`CLIENT_INFO`、五个错误码
  (`CONFIG_INVALID` / `LAUNCH_FAILED` / `CONNECT_FAILED` / `TIMEOUT` / `PROTOCOL_ERROR`)。
