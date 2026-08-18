"""上下文装配:system 提示 -> 用户画像(USER.md) -> 新用户消息。

历史消息暂不做(后续 memory 层再加);token 裁剪后续用
llm_protocols.fit_request_messages 接入。
"""

from __future__ import annotations

from app.agent.profile import load_user_profile
from app.agent.prompts.loader import load_prompt


def assemble_context(session_id: str, new_text: str) -> list[dict]:
    """组装发给 LLM 的消息列表。

    顺序:system 提示(prompts/system.md)-> USER.md(有内容时作为
    system 消息注入)-> 新用户消息。session_id 预留给后续历史装配。
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
    messages.append({"role": "user", "content": new_text})
    return messages
