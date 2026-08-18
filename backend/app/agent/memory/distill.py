"""每轮对话后的自动提炼:一次 LLM 调用同时产出两类结果——

- profile:用户的稳定属性(称呼/角色/工作习惯/沟通偏好/时区)→ 写入 USER.md;
- facts:其他值得长期记住的事实 → 追加进 MEMORY.md。

由此用户只需跟助理聊天,画像与记忆都自动形成,不需要手写任何文件。

输出约定为一个 JSON 对象:{"profile": {小节: 值}, "facts": [事实, ...]}。
解析用 llm_protocols.loads_llm_json(容忍围栏/尾逗号);解析失败时退化为
把每一行当作一条 fact,保证基本可用。

best-effort:提炼失败(模型异常、输出解析失败)只记日志,不影响主流程。
同步执行——代价是每轮多一次 LLM 调用,个人 bot 场景下正确性优先。
"""

from __future__ import annotations

import logging
import re
from string import Template

from llm_protocols import loads_llm_json

from app.agent import profile
from app.agent.memory.store import MemoryStore
from app.agent.prompts.loader import load_prompt

logger = logging.getLogger(__name__)

# 行首的列表/编号前缀("- "、"* "、"1. "、"1、"),模型输出不规矩时剥掉
_PREFIX_RE = re.compile(r"^(?:[-*•]|\d+[.、])\s*")


def _clean_fact(line: str) -> str:
    """剥掉行首列表/编号前缀与首尾空白;空或 NONE 返回空串。"""
    item = _PREFIX_RE.sub("", line.strip()).strip()
    if not item or item.upper() == "NONE":
        return ""
    return item


def parse_distill_output(text: str | None) -> dict:
    """解析提炼输出为 {"profile": {...}, "facts": [...]}。

    优先按 JSON 解析;失败时退化为把每一行当一条 fact(profile 为空)。
    """
    if not text or not text.strip():
        return {"profile": {}, "facts": []}
    try:
        data = loads_llm_json(text)
    except Exception:  # noqa: BLE001 — 退化为按行 facts
        facts = [f for line in text.splitlines() if (f := _clean_fact(line))]
        return {"profile": {}, "facts": facts}
    if not isinstance(data, dict):
        return {"profile": {}, "facts": []}

    raw_profile = data.get("profile") or {}
    if not isinstance(raw_profile, dict):
        raw_profile = {}
    cleaned_profile = {
        str(k).strip(): str(v).strip()
        for k, v in raw_profile.items()
        if str(k).strip() and str(v).strip()
    }

    raw_facts = data.get("facts") or []
    if isinstance(raw_facts, str):
        raw_facts = [raw_facts]
    if not isinstance(raw_facts, list):
        raw_facts = []
    facts = [f for item in raw_facts if (f := _clean_fact(str(item)))]

    return {"profile": cleaned_profile, "facts": facts}


def distill_memory(
    client, user_text: str, assistant_text: str, store: MemoryStore | None = None
) -> dict:
    """提炼本轮对话:profile 写 USER.md,facts 写 MEMORY.md。

    返回实际写入的 {"profile": {小节: 值}, "facts": [新增条目]}。
    任何失败只记日志,返回空结果,不炸主流程。
    """
    mem = store if store is not None else MemoryStore()
    prompt_template = load_prompt("distill")
    if not prompt_template:
        return {"profile": {}, "facts": []}

    # 把已有画像与记忆原文注入 prompt:模型只有看到已有内容才能避免重复/覆盖旧值
    existing = mem.load()
    prompt = Template(prompt_template).substitute(
        user_text=user_text,
        assistant_text=assistant_text,
        profile_text=profile.load_user_profile() or "(暂无)",
        existing_memory="\n".join(f"- {e}" for e in existing) if existing else "(暂无)",
    )
    try:
        raw = client.generate([{"role": "user", "content": prompt}])
    except Exception as exc:  # noqa: BLE001 — best-effort,任何异常都不炸主流程
        logger.warning("记忆提炼失败:%s", exc)
        return {"profile": {}, "facts": []}

    result = parse_distill_output(raw)
    applied_profile = profile.update_profile(result["profile"]) if result["profile"] else {}
    if result["facts"]:
        mem.add(result["facts"])
    return {"profile": applied_profile, "facts": result["facts"]}
