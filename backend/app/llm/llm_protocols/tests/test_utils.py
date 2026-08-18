from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm_protocols.utils import (
    TURN_STAGE_MESSAGE_MARKER,
    extract_json,
    fit_request_messages,
    loads_llm_json,
    request_tokens,
    thinking_request_kwargs,
    usage_metrics,
)


def test_extract_json_strips_code_fence() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('prefix {"a": 1} suffix') == '{"a": 1}'


def test_loads_llm_json_repairs_trailing_commas_and_literals() -> None:
    assert loads_llm_json('{"a": 1,}') == {"a": 1}
    assert loads_llm_json("{'a': True}") == {"a": True}
    assert loads_llm_json('{"a": "line1\nline2"}') == {"a": "line1\nline2"}
    with pytest.raises(Exception):
        loads_llm_json("not json at all")


def test_fit_request_messages_drops_middle_history_over_budget() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        *[
            {"role": "user", "content": "x" * 400}
            for _ in range(10)
        ],
        {"role": "user", "content": "latest"},
    ]
    fitted = fit_request_messages(messages, token_budget=256)
    assert fitted[0]["content"] == "sys"
    assert fitted[-1]["content"] == "latest"
    assert request_tokens(fitted) <= 256
    assert len(fitted) < len(messages)


def test_fit_request_messages_strips_turn_stage_marker() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "stage", TURN_STAGE_MESSAGE_MARKER: True},
        {"role": "user", "content": "latest"},
    ]
    fitted = fit_request_messages(messages, token_budget=32_000)
    assert all(TURN_STAGE_MESSAGE_MARKER not in m for m in fitted)


def test_thinking_request_kwargs_merges_mode() -> None:
    assert thinking_request_kwargs("enabled", {"thinking": {"clear_thinking": True}}) == {
        "extra_body": {"thinking": {"clear_thinking": True, "type": "enabled"}}
    }
    assert thinking_request_kwargs("", {"vendor": 1}) == {"extra_body": {"vendor": 1}}
    assert thinking_request_kwargs("") == {}


def test_usage_metrics_normalizes_provider_shapes() -> None:
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        prompt_tokens_details=SimpleNamespace(cached_tokens=4),
    )
    assert usage_metrics(usage) == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "cached_input_tokens": 4,
        "uncached_input_tokens": 6,
    }
    assert usage_metrics(None) == {}
    assert usage_metrics({"input_tokens": 3, "output_tokens": 2}) == {
        "input_tokens": 3,
        "output_tokens": 2,
    }
