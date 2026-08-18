"""斜杠指令解析:结构参考 StaffDeck 的 service_routing.parse_command。

行首 "/" 开头的消息视为指令;非指令返回 None,交给 LLM 处理。
"""

from __future__ import annotations

from dataclasses import dataclass

COMMAND_PREFIX = "/"


@dataclass
class ChannelCommand:
    kind: str  # help/ping/model
    query: str = ""  # 指令后的剩余文本(可为空)


def parse_command(text: str) -> ChannelCommand | None:
    """解析行首斜杠指令(忽略大小写与首尾空白);非指令消息返回 None。"""
    stripped = (text or "").strip()
    if not stripped.startswith(COMMAND_PREFIX):
        return None
    body = stripped[1:].strip()
    if not body:
        return ChannelCommand(kind="help")
    # 取第一个词作为指令名,其余作为参数
    name, _, rest = body.partition(" ")
    name = name.lower()
    query = rest.strip()
    if name in {"帮助", "help", "?", "?"}:
        return ChannelCommand(kind="help", query=query)
    if name == "ping":
        return ChannelCommand(kind="ping", query=query)
    if name in {"模型", "模型列表", "model"}:
        return ChannelCommand(kind="model", query=query)
    # 未识别的指令按帮助处理,引导用户看可用指令
    return ChannelCommand(kind="help", query=body)
