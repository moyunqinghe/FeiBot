"""工具注册表:agent 可调用的工具统一登记在这里。

每个工具是一个 ToolSpec(名字、描述、参数说明、handler)。
render_tools_prompt() 把全部工具渲染成注入 system 提示的说明文本
(含 JSON 调用格式约定),engine 据此让模型发起调用。
后续 MCP 远端工具也汇入本注册表。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    """一个可被模型调用的工具。handler 接受关键字参数,返回字符串结果。"""

    name: str
    description: str
    parameters: Mapping[str, str]  # 参数名 -> 参数说明
    handler: Callable[..., str]


TOOL_REGISTRY: dict[str, ToolSpec] = {}


def register_tool(spec: ToolSpec) -> ToolSpec:
    """注册工具(同名覆盖)。"""
    TOOL_REGISTRY[spec.name] = spec
    return spec


def unregister_tool(name: str) -> bool:
    """注销工具;返回是否确实移除了已注册的工具。"""
    return TOOL_REGISTRY.pop(name, None) is not None


def get_tool(name: str) -> ToolSpec | None:
    """按名字取工具;不存在返回 None。"""
    return TOOL_REGISTRY.get(name)


def all_tools() -> list[ToolSpec]:
    """全部工具,按名字排序。"""
    return sorted(TOOL_REGISTRY.values(), key=lambda t: t.name)


def render_tools_prompt() -> str:
    """渲染注入 system 提示的工具说明(含调用格式约定);无工具返回空串。"""
    if not TOOL_REGISTRY:
        return ""
    lines = [
        "你可以调用工具获取实时信息或执行操作。需要调用工具时,你的回复只能是且仅是一行 JSON:",
        '{"tool": "工具名", "args": {"参数名": "参数值"}}',
        "不要输出这行 JSON 以外的任何内容。工具执行完后系统会把结果发给你,你再基于结果回答用户;最终回答用自然语言,不要输出 JSON。",
        "重要:你不要自己编造或模拟工具的执行结果,也不要替系统生成下一轮调用;真实结果只会由系统以「工具结果:」开头的消息提供,收到之前请等待。",
        "",
        "可用工具:",
    ]
    for spec in all_tools():
        if spec.parameters:
            args_hint = ", ".join(
                f'"{name}": "{desc}"' for name, desc in spec.parameters.items()
            )
            lines.append(f"- {spec.name} — {spec.description}。args: {{{args_hint}}}")
        else:
            lines.append(f"- {spec.name} — {spec.description}。args: {{}}")
    return "\n".join(lines)
