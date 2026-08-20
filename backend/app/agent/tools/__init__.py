"""工具层:注册表 + 内置工具 + 调用解析/执行。

import 本包即完成内置工具注册(builtin 与 skill_tools 在导入时 register)。
工具可用性由 engine 按白名单门控(config.TOOL_ADMIN_CONV_KEYS),
本层不判断谁能调用。
"""

from app.agent.tools import (
    builtin,  # noqa: F401  导入即注册内置工具
    skill_tools,  # noqa: F401  导入即注册 skill 工具
)
from app.agent.tools.calls import ToolCall, execute_tool_call, parse_tool_call
from app.agent.tools.registry import (
    TOOL_REGISTRY,
    ToolSpec,
    all_tools,
    get_tool,
    register_tool,
    render_tools_prompt,
    unregister_tool,
)

__all__ = [
    "TOOL_REGISTRY",
    "ToolCall",
    "ToolSpec",
    "all_tools",
    "execute_tool_call",
    "get_tool",
    "parse_tool_call",
    "register_tool",
    "render_tools_prompt",
    "unregister_tool",
]
