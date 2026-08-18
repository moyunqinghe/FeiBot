"""LLM 客户端:对上层(engine)只暴露 generate(messages) -> str。

真实调用走 llm_protocols 包(四协议 driver 都在包里);本模块只做桥接:
按当前模型配置装配 llm_protocols.LLMClient,把 build_request + complete
两步封装成一次 generate,从归一化响应取 choices[0].message.content。
无当前配置时回退 EchoLLM 桩。
"""

from __future__ import annotations

from typing import Protocol

from llm_protocols import LLMClient as ProtocolLLMClient
from llm_protocols import LLMError

from app.config import CHANNEL_SECRET
from app.llm import registry

DEFAULT_TIMEOUT_SECONDS = 60.0  # 聊天场景的单次请求超时


class LLMClient(Protocol):
    """LLM 客户端协议:输入消息列表,输出一段回复文本。"""

    def generate(self, messages: list[dict]) -> str:
        """messages 形如 [{"role": "user", "content": "..."}, ...]。"""
        ...


class EchoLLM:
    """stub 实现:原样回显最后一条 user 消息,用于未配置模型时打通链路。"""

    def generate(self, messages: list[dict]) -> str:
        last_user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user = msg.get("content", "")
                break
        return f"echo: {last_user}"


class _ActiveLLM:
    """桥接 llm_protocols.LLMClient:build_request + complete -> 回复文本。

    协议错误(ProtocolCallError)由 LLMClient.complete 原样向上抛;
    响应形状异常包装成 LLMError,统一由 engine 兜底成友好提示。
    """

    def __init__(self, record: registry.ModelRecord) -> None:
        self.record = record
        self._inner = ProtocolLLMClient(
            record,  # ModelRecord 鸭子类型满足 LLMClient 的配置约定
            secret=CHANNEL_SECRET,
            default_timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        )

    def generate(self, messages: list[dict]) -> str:
        request = self._inner.build_request(messages)
        completion = self._inner.complete(request)
        try:
            return completion.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError) as exc:
            raise LLMError("MODEL_BAD_RESPONSE") from exc


def build_active_client() -> LLMClient:
    """按当前模型配置构造客户端;无当前配置时回退 EchoLLM。"""
    record = registry.get_current()
    if record is None:
        return EchoLLM()
    return _ActiveLLM(record)


def get_default_client() -> LLMClient:
    """无配置时的兜底客户端(echo 桩)。"""
    return EchoLLM()
