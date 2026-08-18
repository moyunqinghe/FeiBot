"""LLMError 与轻量 LLMClient。

LLMClient 只做装配与透传:complete/stream 直调协议 driver。
重试编排(空响应重试、reasoning token 预算升档)、可观测性 span/stage、
消息组装(_request_messages 等)全部留给宿主——见 README「本包的边界」。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .crypto import decrypt_secret, derive_fernet_key
from .drivers import CancellationToken, ProtocolDriver
from .factory import DEFAULT_MODEL_API_TIMEOUT_SECONDS, build_protocol_driver
from .protocols import ModelApiProtocol
from .utils import (
    normalize_extra_body,
    thinking_mode_for_model,
    thinking_mode_from_extra_body,
    thinking_request_kwargs,
)


class LLMError(Exception):
    """Raised when an LLM provider request or response normalization fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        provider_code: str | None = None,
        provider_message: str | None = None,
        upstream_body: str | None = None,
        request_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code or (
            message if message.startswith("MODEL_") and " " not in message else None
        )
        self.status_code = status_code
        self.provider_code = provider_code
        self.provider_message = provider_message
        self.upstream_body = upstream_body
        self.request_id = request_id
        self.retryable = retryable

    def public_detail(self) -> dict[str, Any]:
        return {
            "code": self.code or "MODEL_CONNECTION_FAILED",
            "message": str(self),
            "upstream_status": self.status_code,
            "provider_code": self.provider_code,
            "provider_message": self.provider_message,
            "upstream_body": self.upstream_body,
            "request_id": self.request_id,
            "retryable": self.retryable,
        }


class LLMClient:
    """按模型配置装配协议 driver 的轻量客户端。

    model_config 为鸭子类型对象(如 `ResolvedModelConfig` 或宿主的 ORM 行),
    需要的属性:api_protocol、api_key_encrypted、base_url、model、
    temperature、max_output_tokens;可选:name、timeout_seconds、
    legacy_extra_body / extra_body_json、protocol_options。

    与原 StaffDeck 实现的剥离点:
    - 解密所需的 secret 由构造参数显式传入(原为全局 settings 的 APP_SECRET);
    - 超时回退值由 `default_timeout_seconds` 传入(原为全局 settings);
    - thinking 模式的配置来源由 `thinking_mode` / `thinking_models` 传入
      (原为全局 settings 的 model_thinking_mode / model_thinking_models)。
    """

    def __init__(
        self,
        model_config: Any,
        *,
        secret: str,
        default_timeout_seconds: float = DEFAULT_MODEL_API_TIMEOUT_SECONDS,
        thinking_mode: str = "",
        thinking_models: str = "",
    ) -> None:
        try:
            protocol = ModelApiProtocol(
                getattr(model_config, "api_protocol", "openai_chat_completions")
            )
        except ValueError as exc:
            raise LLMError("MODEL_PROTOCOL_UNSUPPORTED") from exc
        api_key = decrypt_secret(model_config.api_key_encrypted, derive_fernet_key(secret))
        if not api_key:
            raise LLMError("Model API key is not configured")
        self.timeout_seconds = (
            getattr(model_config, "timeout_seconds", None)
            or default_timeout_seconds
            or DEFAULT_MODEL_API_TIMEOUT_SECONDS
        )
        self.base_url = str(model_config.base_url or "")
        self.client, self.driver = build_protocol_driver(
            protocol=protocol,
            api_key=api_key,
            base_url=self.base_url,
            model=model_config.model,
            timeout_seconds=self.timeout_seconds,
        )
        self.api_protocol = protocol
        self.api_key = api_key
        self.model = model_config.model
        self.model_config_name = str(getattr(model_config, "name", "") or "").strip()
        self.temperature = model_config.temperature
        self.max_output_tokens = model_config.max_output_tokens
        legacy_extra_body = getattr(model_config, "legacy_extra_body", {})
        protocol_options = getattr(model_config, "protocol_options", {})
        self.extra_body = normalize_extra_body(
            legacy_extra_body
            or getattr(model_config, "extra_body_json", {})
            or protocol_options
        )
        self.thinking_mode = (
            thinking_mode_from_extra_body(self.extra_body)
            or thinking_mode_for_model(thinking_mode, thinking_models, self.model)
        )

    def build_request(
        self,
        messages: list[dict[str, Any]],
        *,
        response_format: dict[str, str] | None = None,
        cancellation: CancellationToken | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """组装规范化请求 dict(含 thinking kwargs),不含任何编排逻辑。"""
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_output_tokens,
        }
        if cancellation is not None:
            request["_cancellation"] = cancellation
        if response_format:
            request["response_format"] = response_format
        request.update(thinking_request_kwargs(self.thinking_mode, self.extra_body))
        return request

    def complete(self, request: dict[str, Any]) -> Any:
        """直调 driver.complete;ProtocolCallError / ValueError 原样向上抛。"""
        return self.driver.complete(request)

    def stream(self, request: dict[str, Any]) -> Iterator[Any]:
        """直调 driver.stream;不做重试、span、stage 处理(宿主职责)。"""
        return self.driver.stream(request)
