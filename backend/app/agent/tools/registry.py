"""工具注册表:agent 可调用的工具统一登记在这里。

用 @register_tool 装饰器注册;后续 MCP 工具也汇入本注册表。
"""

from __future__ import annotations

from collections.abc import Callable

TOOL_REGISTRY: dict[str, Callable[..., str]] = {}


def register_tool(func: Callable[..., str]) -> Callable[..., str]:
    """注册工具:以函数名为 key 存入 TOOL_REGISTRY。"""
    TOOL_REGISTRY[func.__name__] = func
    return func


def get_tool(name: str) -> Callable[..., str] | None:
    """按名字取工具;不存在返回 None。"""
    return TOOL_REGISTRY.get(name)


@register_tool
def echo_tool(text: str) -> str:
    """示例工具:原样返回输入。"""
    return text
