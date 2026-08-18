"""按协议装配 SDK 客户端并返回对应 driver(抽自 StaffDeck `app/llm/client.py` 的 __init__ 装配段)。

行为与原实现一致,包括 Anthropic base_url 的 /v1(/messages) 后缀剥离 hack。
解密 API key 不是本模块职责:调用方先用 `llm_protocols.crypto` 解密后再传入明文。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx
from anthropic import Anthropic
from openai import OpenAI

from .drivers import (
    AnthropicMessagesDriver,
    ChatCompletionsDriver,
    GeminiGenerateContentDriver,
    OpenAIResponsesDriver,
    ProtocolDriver,
)
from .protocols import ModelApiProtocol

DEFAULT_MODEL_API_TIMEOUT_SECONDS = 600.0


def build_protocol_driver(
    *,
    protocol: ModelApiProtocol | str,
    api_key: str,
    base_url: str,
    model: str,
    timeout_seconds: float = DEFAULT_MODEL_API_TIMEOUT_SECONDS,
) -> tuple[Any, ProtocolDriver]:
    """按协议构造 SDK 客户端与 driver,返回 (sdk_client, driver)。

    api_key 必须是解密后的明文;解密与密钥来源由宿主决定(见 crypto 模块)。
    """
    from .client import LLMError  # 延迟导入,避免与 client.py 循环依赖

    try:
        resolved = ModelApiProtocol(protocol)
    except ValueError as exc:
        raise LLMError("MODEL_PROTOCOL_UNSUPPORTED") from exc
    if resolved is ModelApiProtocol.OPENAI_CHAT_COMPLETIONS:
        client: Any = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )
        return client, ChatCompletionsDriver(client)
    if resolved is ModelApiProtocol.OPENAI_RESPONSES:
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )
        return client, OpenAIResponsesDriver(client)
    if resolved is ModelApiProtocol.ANTHROPIC_MESSAGES:
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": timeout_seconds,
            "max_retries": 0,
        }
        if base_url:
            # Anthropic's SDK always appends /v1/messages. If an operator
            # already configured a /v1 API root, remove only that suffix
            # for the SDK transport so the effective URL remains exactly
            # the configured API root plus /messages.
            sdk_base_url = base_url.rstrip("/")
            sdk_path = urlsplit(sdk_base_url).path.rstrip("/")
            if sdk_path.endswith("/v1/messages"):
                sdk_base_url = sdk_base_url[: -len("/v1/messages")].rstrip("/")
            elif sdk_path.endswith("/v1"):
                sdk_base_url = sdk_base_url[:-3].rstrip("/")
            kwargs["base_url"] = sdk_base_url
        client = Anthropic(**kwargs)
        return client, AnthropicMessagesDriver(client)
    if resolved is ModelApiProtocol.GEMINI_GENERATE_CONTENT:
        client = httpx.Client(timeout=timeout_seconds)
        return client, GeminiGenerateContentDriver(client, base_url, api_key, model)
    raise LLMError("MODEL_PROTOCOL_UNSUPPORTED")
