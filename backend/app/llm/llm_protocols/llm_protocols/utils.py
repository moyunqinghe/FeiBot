"""纯函数工具集(抽自 StaffDeck `app/llm/client.py`,逻辑原样保留):

- JSON 抽取/修复:`extract_json` / `loads_llm_json`
- 消息裁剪与 token 预算:`fit_request_messages` / `request_tokens`
- thinking kwargs:`thinking_request_kwargs` 等
- usage 归一化:`usage_metrics`
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
import copy
import json
import math
import re
from typing import Any

DEFAULT_INPUT_TOKEN_BUDGET = 32_000
TURN_STAGE_MESSAGE_MARKER = "_agent_turn_message"


# ---------------------------------------------------------------------------
# JSON 抽取与修复
# ---------------------------------------------------------------------------


def extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end >= start:
        return stripped[start : end + 1]
    return stripped


def loads_llm_json(text: str) -> Any:
    candidate = extract_json(text)
    last_error: json.JSONDecodeError | None = None
    seen: set[str] = set()
    for variant in _json_candidate_variants(candidate):
        if variant in seen:
            continue
        seen.add(variant)
        try:
            return json.loads(variant)
        except json.JSONDecodeError as exc:
            last_error = exc
    try:
        literal = ast.literal_eval(candidate)
    except (SyntaxError, ValueError):
        literal = None
    if isinstance(literal, (dict, list)):
        return literal
    if last_error is not None:
        raise last_error
    raise json.JSONDecodeError("Could not decode JSON", candidate, 0)


def _json_candidate_variants(text: str) -> tuple[str, ...]:
    stripped = text.strip()
    no_trailing_commas = _remove_trailing_commas(stripped)
    repaired_strings = _repair_json_string_content(stripped)
    repaired_strings_no_trailing = _remove_trailing_commas(repaired_strings)
    return (
        stripped,
        no_trailing_commas,
        repaired_strings,
        repaired_strings_no_trailing,
    )


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def _repair_json_string_content(text: str) -> str:
    output: list[str] = []
    in_string = False
    index = 0
    while index < len(text):
        char = text[index]
        if not in_string:
            output.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue
        if char == "\\":
            output.append(char)
            index += 1
            if index < len(text):
                output.append(text[index])
                index += 1
            continue
        if char == '"':
            if _quote_likely_closes_string(text, index):
                output.append(char)
                in_string = False
            else:
                output.append('\\"')
            index += 1
            continue
        if char == "\n":
            output.append("\\n")
        elif char == "\r":
            output.append("\\r")
        elif char == "\t":
            output.append("\\t")
        else:
            output.append(char)
        index += 1
    return "".join(output)


def _quote_likely_closes_string(text: str, quote_index: int) -> bool:
    index = quote_index + 1
    while index < len(text) and text[index].isspace():
        index += 1
    return index >= len(text) or text[index] in {":", ",", "}", "]"}


# ---------------------------------------------------------------------------
# 消息裁剪与 token 预算
# ---------------------------------------------------------------------------


def fit_request_messages(
    messages: list[dict[str, Any]], token_budget: int = DEFAULT_INPUT_TOKEN_BUDGET
) -> list[dict[str, Any]]:
    projected = copy.deepcopy(messages)
    while len(projected) > 2 and request_tokens(projected) > token_budget:
        removable_index = next(
            (
                index
                for index in range(1, len(projected) - 1)
                if not _is_history_summary_message(projected[index])
                and not _is_turn_stage_message(projected[index])
            ),
            None,
        )
        if removable_index is None:
            break
        projected.pop(removable_index)

    while len(projected) > 2 and request_tokens(projected) > token_budget:
        removable_index = next(
            (
                index
                for index in range(1, len(projected) - 1)
                if not _is_turn_stage_message(projected[index])
            ),
            None,
        )
        if removable_index is None:
            break
        projected.pop(removable_index)

    _trim_turn_stage_messages(projected, token_budget)
    _drop_oldest_turn_stage_exchanges(projected, token_budget)

    if projected and request_tokens(projected) > token_budget:
        fixed_tokens = request_tokens(projected[:-1])
        projected[-1] = _trim_request_message(
            projected[-1], max(1, token_budget - fixed_tokens)
        )
    return [
        {key: value for key, value in message.items() if key != TURN_STAGE_MESSAGE_MARKER}
        for message in projected
    ]


def request_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(
        max(1, math.ceil(len(content_text(message.get("content")).encode("utf-8")) / 4))
        + 6
        for message in messages
    )


def _is_history_summary_message(message: dict[str, Any]) -> bool:
    content = content_text(message.get("content")).lstrip()
    return content.startswith(
        ("历史的信息可以被总结为：", "近期的历史信息总结为：")
    )


def _is_turn_stage_message(message: dict[str, Any]) -> bool:
    return message.get(TURN_STAGE_MESSAGE_MARKER) is True


def _trim_turn_stage_messages(
    messages: list[dict[str, Any]], token_budget: int
) -> None:
    while request_tokens(messages) > token_budget:
        candidates = [
            (len(content_text(message.get("content"))), index)
            for index, message in enumerate(messages[1:-1], start=1)
            if _is_turn_stage_message(message)
            and len(content_text(message.get("content"))) > 512
        ]
        if not candidates:
            break
        current_length, index = max(candidates)
        excess_tokens = request_tokens(messages) - token_budget
        target_tokens = max(128, math.ceil(current_length / 4) - excess_tokens)
        trimmed = _trim_request_message(messages[index], target_tokens)
        trimmed[TURN_STAGE_MESSAGE_MARKER] = True
        if len(content_text(trimmed.get("content"))) >= current_length:
            break
        messages[index] = trimmed


def _drop_oldest_turn_stage_exchanges(
    messages: list[dict[str, Any]], token_budget: int
) -> None:
    while request_tokens(messages) > token_budget:
        stage_indices = [
            index
            for index, message in enumerate(messages[1:-1], start=1)
            if _is_turn_stage_message(message)
        ]
        if len(stage_indices) <= 2:
            break
        first_index = stage_indices[0]
        remove_count = 1
        if (
            len(stage_indices) > 1
            and stage_indices[1] == first_index + 1
            and messages[first_index].get("role") == "user"
            and messages[first_index + 1].get("role") == "assistant"
        ):
            remove_count = 2
        del messages[first_index : first_index + remove_count]


def _trim_request_message(
    message: dict[str, Any], token_budget: int
) -> dict[str, Any]:
    content = message.get("content")
    byte_budget = max(4, token_budget * 4)
    if isinstance(content, list):
        parts = copy.deepcopy(content)
        text_part = next(
            (
                part
                for part in parts
                if isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            ),
            None,
        )
        if text_part is not None:
            text_part["text"] = _trim_request_text(text_part["text"], byte_budget)
        return {**message, "content": parts}
    return {**message, "content": _trim_request_text(str(content or ""), byte_budget)}


def _trim_request_text(text: str, byte_budget: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= byte_budget:
        return text
    marker = "\n...<输入超过 32k，已省略中间部分>...\n"
    marker_bytes = len(marker.encode("utf-8"))
    available = max(8, byte_budget - marker_bytes)
    head_size = int(available * 0.7)
    tail_size = available - head_size
    head = encoded[:head_size].decode("utf-8", errors="ignore")
    tail = encoded[-tail_size:].decode("utf-8", errors="ignore")
    return f"{head}{marker}{tail}"


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_content_part_text(item) for item in content)
    return _content_part_text(content)


def _content_part_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    text: Any = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(text, dict) and isinstance(text.get("value"), str):
        return text["value"]
    value = getattr(text, "value", None)
    return value if isinstance(value, str) else ""


# ---------------------------------------------------------------------------
# thinking 模式与 extra_body
# ---------------------------------------------------------------------------


def normalize_thinking_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"enabled", "disabled"} else ""


def thinking_mode_for_model(mode: Any, configured_models: Any, model: Any) -> str:
    normalized_mode = normalize_thinking_mode(mode)
    if not normalized_mode:
        return ""
    allowed_models = {
        item.strip().lower()
        for item in str(configured_models or "").split(",")
        if item.strip()
    }
    if allowed_models and str(model or "").strip().lower() not in allowed_models:
        return ""
    return normalized_mode


def normalize_extra_body(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _mutable_copy(item) for key, item in value.items()}


def _mutable_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _mutable_copy(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable_copy(item) for item in value]
    return copy.deepcopy(value)


def thinking_mode_from_extra_body(extra_body: Any) -> str:
    normalized = normalize_extra_body(extra_body)
    thinking = normalized.get("thinking")
    if not isinstance(thinking, dict):
        return ""
    return normalize_thinking_mode(thinking.get("type"))


def thinking_request_kwargs(mode: Any, extra_body: Any = None) -> dict[str, Any]:
    body = normalize_extra_body(extra_body)
    normalized = normalize_thinking_mode(mode)
    if normalized:
        thinking = body.get("thinking")
        body["thinking"] = {
            **(thinking if isinstance(thinking, dict) else {}),
            "type": normalized,
        }
    return {"extra_body": body} if body else {}


# ---------------------------------------------------------------------------
# usage 归一化
# ---------------------------------------------------------------------------


def usage_metrics(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    input_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
    output_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
    total_tokens = _usage_value(usage, "total_tokens")
    prompt_details = _usage_object(usage, "prompt_tokens_details", "input_tokens_details")
    cached_input_tokens = _usage_value(
        prompt_details,
        "cached_tokens",
        "cache_read_tokens",
        "cache_read_input_tokens",
    )
    if cached_input_tokens is None:
        cached_input_tokens = _usage_value(
            usage,
            "cached_tokens",
            "prompt_cache_hit_tokens",
            "cache_read_input_tokens",
        )
    metrics: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": cached_input_tokens,
    }
    if input_tokens is not None and cached_input_tokens is not None:
        metrics["uncached_input_tokens"] = max(0, input_tokens - cached_input_tokens)
    return {key: value for key, value in metrics.items() if value is not None}


def _usage_object(source: Any, *names: str) -> Any:
    for name in names:
        value = source.get(name) if isinstance(source, dict) else getattr(source, name, None)
        if value is not None:
            return value
    return None


def _usage_value(source: Any, *names: str) -> int | None:
    value = _usage_object(source, *names)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None
