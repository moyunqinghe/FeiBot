"""distill:提炼输出解析(JSON 契约)与 best-effort 行为。"""

from __future__ import annotations

import json

from app.agent import profile
from app.agent.memory.distill import distill_memory, parse_distill_output
from app.agent.memory.store import MemoryStore


class _FakeClient:
    """返回固定输出的假 LLM 客户端。"""

    def __init__(self, output: str) -> None:
        self._output = output
        self.calls: list[list[dict]] = []

    def generate(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        return self._output


def _json(profile: dict, facts: list) -> str:
    return json.dumps({"profile": profile, "facts": facts}, ensure_ascii=False)


# ---- parse_distill_output ----


def test_parse_empty_and_none() -> None:
    assert parse_distill_output("") == {"profile": {}, "facts": []}
    assert parse_distill_output(None) == {"profile": {}, "facts": []}
    assert parse_distill_output("   ") == {"profile": {}, "facts": []}


def test_parse_json_profile_and_facts() -> None:
    out = _json({"称呼": "飞飞"}, ["用户在做 SaaS 产品"])
    assert parse_distill_output(out) == {
        "profile": {"称呼": "飞飞"},
        "facts": ["用户在做 SaaS 产品"],
    }


def test_parse_json_wrapped_in_fence() -> None:
    # loads_llm_json 会剥掉代码块围栏
    out = "```json\n" + _json({"时区": "Asia/Shanghai"}, []) + "\n```"
    assert parse_distill_output(out) == {
        "profile": {"时区": "Asia/Shanghai"},
        "facts": [],
    }


def test_parse_empty_json() -> None:
    assert parse_distill_output(_json({}, [])) == {"profile": {}, "facts": []}


def test_parse_filters_dirty_facts() -> None:
    # facts 里的空串、NONE、列表前缀都被清洗
    out = _json({}, ["", "NONE", "- 用户叫老莫"])
    assert parse_distill_output(out) == {"profile": {}, "facts": ["用户叫老莫"]}


def test_parse_non_json_falls_back_to_lines() -> None:
    # 模型没按 JSON 输出时,退化为把每行当一条 fact
    assert parse_distill_output("用户叫老莫\n做 SaaS 产品") == {
        "profile": {},
        "facts": ["用户叫老莫", "做 SaaS 产品"],
    }


# ---- distill_memory ----


def test_distill_writes_facts_and_profile(tmp_path) -> None:
    mem = MemoryStore(tmp_path / "MEMORY.md")
    client = _FakeClient(_json({"称呼": "飞飞"}, ["用户喜欢简洁回复"]))
    result = distill_memory(client, "我叫飞飞", "好的飞飞", store=mem)
    assert result["facts"] == ["用户喜欢简洁回复"]
    assert result["profile"] == {"称呼": "飞飞"}
    assert mem.load() == ["用户喜欢简洁回复"]
    assert profile.profile_summary() == {"称呼": "飞飞"}


def test_distill_prompt_includes_existing_state(tmp_path) -> None:
    # 已有画像与记忆原文进 prompt,模型才能去重/覆盖旧值
    mem = MemoryStore(tmp_path / "MEMORY.md")
    mem.add(["用户在做 SaaS"])
    profile.update_profile({"称呼": "老莫"})
    client = _FakeClient(_json({}, []))
    distill_memory(client, "你好", "你好!", store=mem)
    prompt = client.calls[0][0]["content"]
    assert "老莫" in prompt and "用户在做 SaaS" in prompt


def test_distill_none_writes_nothing(tmp_path) -> None:
    mem = MemoryStore(tmp_path / "MEMORY.md")
    result = distill_memory(_FakeClient(_json({}, [])), "你好", "你好!", store=mem)
    assert result == {"profile": {}, "facts": []}
    assert mem.load() == []
    assert profile.profile_summary() == {}


def test_distill_unknown_profile_section_ignored(tmp_path) -> None:
    # 白名单外的小节不写入 USER.md
    mem = MemoryStore(tmp_path / "MEMORY.md")
    client = _FakeClient(_json({"星座": "天蝎", "称呼": "飞飞"}, []))
    result = distill_memory(client, "我叫飞飞", "好的", store=mem)
    assert result["profile"] == {"称呼": "飞飞"}
    assert "星座" not in (tmp_path / "USER.md").read_text(encoding="utf-8")


def test_distill_failure_is_best_effort(tmp_path) -> None:
    class _Boom:
        def generate(self, messages):
            raise RuntimeError("网络炸了")

    mem = MemoryStore(tmp_path / "MEMORY.md")
    assert distill_memory(_Boom(), "hi", "hi", store=mem) == {
        "profile": {},
        "facts": [],
    }
    assert mem.load() == []
