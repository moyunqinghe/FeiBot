"""agent 引擎:一条入站消息 -> 一段回复文本。

决策顺序:先查 slash 指令(本地直接应答),否则走 LLM。
LLM 经 build_active_client() 构造:有当前模型配置走 llm_protocols 真实调用,
没有则回退 EchoLLM 并在回复尾部提示如何配置。

engine 只返回文本,分片与发送由渠道层(ingress)负责,保持单向依赖。
"""

from __future__ import annotations

from llm_protocols import LLMError, ProtocolCallError

from app.agent.context.assemble import assemble_context
from app.agent.session.session import get_or_create
from app.agent.slash import ChannelCommand, parse_command
from app.llm import registry
from app.llm.client import EchoLLM, build_active_client

HELP_TEXT = (
    "可用指令:\n"
    "/帮助(/help) - 查看本说明\n"
    "/ping - 回 pong,检查链路是否通\n"
    "/模型(/模型列表) - 列出全部模型配置并标注当前\n"
    "/模型 <名称> - 切换当前使用的模型\n"
    "其他消息直接发给助理。"
)

ECHO_HINT = "(未配置模型,使用 echo 桩;用 /模型 指令或 python -m app.llm.manage 配置)"


def _handle_command(cmd: ChannelCommand) -> str:
    """本地应答 slash 指令。"""
    if cmd.kind == "ping":
        return "pong"
    if cmd.kind == "model":
        return _handle_model(cmd.query)
    return HELP_TEXT  # help 及未识别指令的兜底


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


def handle_message(conv_key: str, text: str) -> str:
    """处理一条入站消息,返回回复文本。"""
    cmd = parse_command(text)
    if cmd is not None:
        return _handle_command(cmd)

    session_id = get_or_create(conv_key)
    messages = assemble_context(session_id, text)
    client = build_active_client()
    try:
        reply = client.generate(messages)
    except (LLMError, ProtocolCallError) as exc:
        # 模型调用失败不炸到渠道层,回友好提示
        code = getattr(exc, "code", None) or type(exc).__name__
        return f"模型调用失败({code}),请检查模型配置(发 /模型 查看)或稍后再试。"
    if isinstance(client, EchoLLM):
        reply += f"\n{ECHO_HINT}"
    return reply
