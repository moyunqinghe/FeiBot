"""工具调用的解析与执行:模型输出 JSON -> ToolCall -> 执行 -> 结果文本。

解析约定:文本里存在一个配平的 JSON 对象、含 "tool" 键且工具名已注册,
即认定为调用——哪怕模型把调用和编造的"结果"一起输出成多段 JSON,
也能把真正的调用识别出来(编造部分直接丢弃,真实结果由 engine 回填)。
非注册工具名不视为调用(防止把用户要的 JSON 示例误当调用执行)。
执行层的任何错误都转成结果文本回填给模型,不炸主流程。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from llm_protocols import loads_llm_json

from app.agent.tools.registry import TOOL_REGISTRY, get_tool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolCall:
    """一次解析出来的工具调用。"""

    name: str
    args: dict[str, str]


def _iter_json_objects(text: str) -> Iterator[str]:
    """逐个提取文本中顶层配平的 {...} 片段(字符串感知,处理转义)。"""
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth, in_str, esc = 0, False, False
        for j in range(i, n):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield text[i : j + 1]
                    i = j + 1
                    break
        else:
            return  # 剩余部分未配平,没有更多候选


def _from_dict(data: dict) -> ToolCall | None:
    """把一个 JSON 对象识别为工具调用;两种格式都只认已注册的工具名。

    1. 约定格式:{"tool": 工具名, "args": {...}}
    2. qwen 系模型的原生 trace 格式(偶尔泄漏到正文):
       {"name": "tool_call", "arguments": {"name": 工具名, "arguments": {...}}}
       ({"name": "tool_result", ...} 是模型编造的结果,直接忽略)
    """
    name = str(data.get("tool") or "").strip()
    if name and get_tool(name) is not None:
        raw_args = data.get("args") or {}
        if not isinstance(raw_args, dict):
            raw_args = {}
        return ToolCall(name=name, args={str(k): str(v) for k, v in raw_args.items()})
    if data.get("name") == "tool_call":
        inner = data.get("arguments")
        if isinstance(inner, dict):
            inner_name = str(inner.get("name") or "").strip()
            if inner_name and get_tool(inner_name) is not None:
                inner_args = inner.get("arguments") or {}
                if not isinstance(inner_args, dict):
                    inner_args = {}
                return ToolCall(
                    name=inner_name,
                    args={str(k): str(v) for k, v in inner_args.items()},
                )
    return None


def parse_tool_call(text: str | None) -> ToolCall | None:
    """识别模型输出中的工具调用;普通回复返回 None。

    先整体按单个 JSON 解析,失败再逐个扫描顶层 {...} 候选。
    模型把调用和编造的"结果"混在一条回复里时,取第一个真正的调用。
    """
    if not text or not text.strip():
        return None
    for candidate in [text, *_iter_json_objects(text)]:
        try:
            data = loads_llm_json(candidate)
        except Exception:  # noqa: BLE001 — 该候选不是合法 JSON,试下一个
            continue
        if isinstance(data, dict):
            call = _from_dict(data)
            if call is not None:
                return call
    return None


def execute_tool_call(call: ToolCall) -> str:
    """执行工具调用,返回可回填给模型的结果文本(错误同样是文本)。"""
    spec = get_tool(call.name)
    if spec is None:
        known = ", ".join(sorted(TOOL_REGISTRY)) or "(无)"
        return f"工具不存在:{call.name}。可用工具:{known}"
    try:
        result = spec.handler(**call.args)
    except TypeError as exc:  # 参数名/个数不符
        params = dict(spec.parameters) or "无参数"
        return f"工具调用失败(参数不符):{exc}。{call.name} 的参数:{params}"
    except Exception as exc:  # noqa: BLE001 — 工具错误不炸主流程
        logger.warning("工具执行失败:%s(%s):%s", call.name, call.args, exc)
        return f"工具执行失败:{exc}"
    return str(result) if str(result) else "(无返回)"
