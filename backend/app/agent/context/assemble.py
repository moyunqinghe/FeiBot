"""上下文装配:system 提示 -> 用户画像 -> 长期记忆 -> 会话历史 -> 新消息。

画像来自 USER.md(用户手动维护),记忆来自 MEMORY.md(agent 自动提炼);
历史消息来自 db 的 messages 表(由 engine 在每轮结束时写入);
超长会话的 token 裁剪后续用 llm_protocols.fit_request_messages 接入。
"""

from __future__ import annotations

from app.agent.memory.store import MemoryStore
from app.agent.profile import load_user_profile
from app.agent.prompts.loader import load_prompt
from app.db import store

HISTORY_LIMIT = 20  # 每轮携带的最近历史条数(user+assistant 合计)


def assemble_context(session_id: str, new_text: str) -> list[dict]:
    """组装发给 LLM 的消息列表。

    顺序:system 提示(prompts/system.md)-> USER.md -> 长期记忆
    (MEMORY.md,有内容时作为 system 消息)-> 最近历史 -> 新用户消息。
    """
    messages: list[dict] = []
    system_prompt = load_prompt("system")
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    profile = load_user_profile()
    if profile:
        messages.append(
            {"role": "system", "content": f"以下是用户画像,请在回复中参考:\n{profile}"}
        )
    entries = MemoryStore().load()
    if entries:
        bullets = "\n".join(f"- {entry}" for entry in entries)
        messages.append(
            {"role": "system", "content": f"以下是你记住的关于用户的事:\n{bullets}"}
        )
    messages.extend(store.recent_messages(session_id, HISTORY_LIMIT))
    messages.append({"role": "user", "content": new_text})
    return messages
