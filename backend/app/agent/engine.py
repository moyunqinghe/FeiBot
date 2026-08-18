"""agent 引擎:一条入站消息 -> 一段回复文本。

决策顺序:先查 slash 指令(本地直接应答),否则走 LLM。
LLM 经 build_active_client() 构造:有当前模型配置走 llm_protocols 真实调用,
没有则回退 EchoLLM 并在回复尾部提示如何配置。

工具:白名单会话(config.is_tool_admin)的上下文会注入工具说明,
模型输出调用 JSON 时 engine 执行工具并把结果回填,最多循环数轮。

engine 只返回文本,分片与发送由渠道层(ingress)负责,保持单向依赖。
"""

from __future__ import annotations

import json
import logging
import re

from llm_protocols import LLMError, ProtocolCallError, loads_llm_json
from mcp_discovery import McpDiscoveryError

from app.agent.context.assemble import assemble_context
from app.agent.memory.distill import distill_memory
from app.agent.memory.store import MemoryStore
from app.agent.profile import profile_summary, reset_profile
from app.agent.session.session import get_or_create
from app.agent.slash import ChannelCommand, parse_command
from app.agent.tools import execute_tool_call, parse_tool_call, render_tools_prompt
from app.agent.tools.mcp_plugins import PluginError, plugin_manager
from app.agent.tools.registry import TOOL_REGISTRY
from app.config import is_tool_admin
from app.db import store
from app.llm import registry
from app.llm.client import EchoLLM, build_active_client

logger = logging.getLogger(__name__)

# 单条消息内的工具调用轮次上限,防模型陷入调用循环
TOOL_MAX_ROUNDS = 3

# 模型偶尔把内部脚手架(tool_call 块、think 标签)泄漏进正文,发给用户前剥掉
_TOOL_BLOCK_RE = re.compile(
    chr(60) + "tool_call" + chr(62) + r"\s*(.*?)\s*" + chr(60) + "/tool_call" + chr(62),
    re.DOTALL,
)
_THINK_TAG_RE = re.compile(chr(60) + r"/?think" + chr(62))


def _clean_final_reply(text: str) -> str:
    """剥掉最终回复里混入的内部脚手架,用户不应看到过程产物。

    只移除内容能解析为工具 JSON 的 tool_call 块(防误删用户要的示例),
    think 残留标签直接移除;清理后为空则回退原文。
    """

    def _drop_tool_block(match: re.Match) -> str:
        try:
            data = loads_llm_json(match.group(1))
        except Exception:  # noqa: BLE001 — 不是 JSON 就原样保留
            return match.group(0)
        if isinstance(data, dict) and (data.get("tool") or data.get("name")):
            return ""
        return match.group(0)

    cleaned = _TOOL_BLOCK_RE.sub(_drop_tool_block, text)
    cleaned = _THINK_TAG_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned or text.strip()

HELP_TEXT = (
    "可用指令:\n"
    "/帮助(/help) - 查看本说明\n"
    "/ping - 回 pong,检查链路是否通\n"
    "/模型(/模型列表) - 列出全部模型配置并标注当前\n"
    "/模型 <名称> - 切换当前使用的模型\n"
    "/记忆(/memory) - 查看我记住的关于你的事\n"
    "/遗忘(/forget) - 清空关于你的全部记忆与画像\n"
    "/mcp(/mcp list) - 列出已装 MCP 插件(仅管理员)\n"
    "/mcp add <名称> <url> - 装入 MCP 插件\n"
    "/mcp remove <名称> - 卸下 MCP 插件\n"
    "/mcp enable|disable <名称> - 启用/停用 MCP 插件\n"
    "其他消息直接发给助理。"
)

ECHO_HINT = "(未配置模型,使用 echo 桩;用 /模型 指令或 python -m app.llm.manage 配置)"


def _handle_command(conv_key: str, cmd: ChannelCommand) -> str:
    """本地应答 slash 指令。"""
    if cmd.kind == "mcp":
        return _handle_mcp(conv_key, cmd.query)
    if cmd.kind == "ping":
        return "pong"
    if cmd.kind == "model":
        return _handle_model(cmd.query)
    if cmd.kind == "memory":
        return _handle_memory()
    if cmd.kind == "forget":
        MemoryStore().clear()
        reset_profile()
        return "已清空关于你的全部记忆与画像。"
    return HELP_TEXT  # help 及未识别指令的兜底


def _handle_memory() -> str:
    """/记忆:列出用户画像与长期记忆条目。"""
    sections: list[str] = []
    summary = profile_summary()
    if summary:
        sections.append(
            "你的画像:\n" + "\n".join(f"- {name}:{value}" for name, value in summary.items())
        )
    entries = MemoryStore().load()
    if entries:
        bullets = "\n".join(f"- {entry}" for entry in entries)
        sections.append(f"其他记得的事:\n{bullets}")
    if not sections:
        return "还没有关于你的记忆。聊天中值得记住的画像和事情会自动记下来。"
    return "\n\n".join(sections)


def _handle_model(query: str) -> str:
    """/模型:无参列出全部(标注当前);带名称切换。"""
    if not query:
        configs = registry.list_models()
        if not configs:
            return "还没有模型配置。用 python -m app.llm.manage add 添加。"
        current = registry.get_current_name()
        lines = ["模型配置(* 为当前使用):"]
        for record in configs:
            mark = "*" if record.name == current else " "
            lines.append(f"{mark} {record.name}({record.api_protocol} / {record.model})")
        lines.append("发送 /模型 <名称> 切换。")
        return "\n".join(lines)
    if registry.set_current(query):
        record = registry.get_model(query)
        return f"已切换到模型:{record.name}({record.model})"
    return f"没有名为「{query}」的模型配置,发送 /模型 查看已有配置。"


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


def _generate_reply(client, messages: list[dict], tools_available: bool) -> str:
    """生成回复;tools_available 时支持多轮"调用工具 -> 回填结果 -> 继续"。"""
    if not tools_available or not TOOL_REGISTRY:
        return _clean_final_reply(client.generate(messages))
    msgs = list(messages)
    # 工具说明插在 system 提示之后:模型先知道身份,再知道能力
    insert_at = 1 if msgs and msgs[0]["role"] == "system" else 0
    msgs.insert(insert_at, {"role": "system", "content": render_tools_prompt()})
    reply = ""
    for _ in range(TOOL_MAX_ROUNDS):
        reply = client.generate(msgs)
        call = parse_tool_call(reply)
        if call is None:
            return _clean_final_reply(reply)
        result = execute_tool_call(call)
        logger.info("工具调用:%s args=%s", call.name, call.args)
        # 回填规范化的调用 JSON 而非模型原始输出:模型偶尔把调用和编造的
        # "结果"一起输出,原始文本进上下文会污染后续轮次
        canonical = json.dumps({"tool": call.name, "args": call.args}, ensure_ascii=False)
        msgs.append({"role": "assistant", "content": canonical})
        msgs.append(
            {
                "role": "user",
                "content": f"工具结果:\n{result}\n请基于工具结果回答用户最初的问题。",
            }
        )
    # 轮次用尽:再给一次机会,要求直接作答
    msgs.append(
        {
            "role": "user",
            "content": "(系统)工具调用轮次已用完,不要再调用工具,请基于已有信息直接回答用户的问题。",
        }
    )
    final = client.generate(msgs)
    if parse_tool_call(final) is None:
        return _clean_final_reply(final)
    return "工具调用出了点问题,请换个方式直接描述你的需求。"


def handle_message(conv_key: str, text: str) -> str:
    """处理一条入站消息,返回回复文本。"""
    cmd = parse_command(text)
    if cmd is not None:
        return _handle_command(conv_key, cmd)

    session_id = get_or_create(conv_key)
    # 先装配历史(不含本轮),再把本轮用户消息落库——顺序反了本轮会被重复注入
    messages = assemble_context(session_id, text)
    store.add_message(session_id, "user", text)
    client = build_active_client()
    # 工具只对白名单会话开放;echo 桩没有工具能力
    tools_available = is_tool_admin(conv_key) and not isinstance(client, EchoLLM)
    try:
        reply = _generate_reply(client, messages, tools_available)
    except (LLMError, ProtocolCallError) as exc:
        # 模型调用失败不炸到渠道层,回友好提示(失败的回复不入历史)
        code = getattr(exc, "code", None) or type(exc).__name__
        return f"模型调用失败({code}),请检查模型配置(发 /模型 查看)或稍后再试。"
    store.add_message(session_id, "assistant", reply)
    if not isinstance(client, EchoLLM):
        # 每轮结束后提炼长期记忆(best-effort,同步执行,代价是多一次 LLM 调用)
        distill_memory(client, text, reply)
    if isinstance(client, EchoLLM):
        reply += f"\n{ECHO_HINT}"
    return reply
