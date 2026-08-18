"""模型 API 协议枚举与规范化(纯协议层,不依赖 Web 框架)。

原始实现位于 StaffDeck `app/llm/model_protocols.py`;其中校验失败时抛出的
`HTTPException(status_code=422, detail=...)` 在此剥离为
`ModelProtocolError`,`code` 属性保留原 detail 字符串(如
"MODEL_PROVIDER_UNSUPPORTED"),由宿主自行决定如何映射为 HTTP 响应。
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any
import unicodedata
from urllib.parse import urlsplit, urlunsplit


class ModelProtocolError(ValueError):
    """协议/配置校验失败(对应原实现的 HTTP 422)。code 为原 detail 字符串。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ModelApiProtocol(StrEnum):
    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    GEMINI_GENERATE_CONTENT = "gemini_generate_content"


LEGACY_OPENAI_PROVIDER = "openai_compatible"


def available_model_protocols() -> list[str]:
    return [protocol.value for protocol in ModelApiProtocol]


def resolve_api_protocol(api_protocol: str | None, provider: str | None) -> ModelApiProtocol:
    mapped_provider = None
    if provider is not None:
        if provider != LEGACY_OPENAI_PROVIDER:
            raise ModelProtocolError("MODEL_PROVIDER_UNSUPPORTED")
        mapped_provider = ModelApiProtocol.OPENAI_CHAT_COMPLETIONS
    try:
        explicit = ModelApiProtocol(api_protocol) if api_protocol is not None else None
    except ValueError as exc:
        raise ModelProtocolError("MODEL_PROTOCOL_UNSUPPORTED") from exc
    if explicit is not None and mapped_provider is not None and explicit != mapped_provider:
        raise ModelProtocolError("MODEL_PROTOCOL_CONFLICT")
    return explicit or mapped_provider or ModelApiProtocol.OPENAI_CHAT_COMPLETIONS


def normalize_chat_protocol_options(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {"thinking"}:
        raise ModelProtocolError("MODEL_PROTOCOL_OPTIONS_INVALID")
    thinking = value.get("thinking")
    if thinking is None:
        return {}
    if not isinstance(thinking, dict) or set(thinking) - {"type", "clear_thinking"}:
        raise ModelProtocolError("MODEL_PROTOCOL_OPTIONS_INVALID")
    if thinking.get("type") not in {"enabled", "disabled"}:
        raise ModelProtocolError("MODEL_PROTOCOL_OPTIONS_INVALID")
    if "clear_thinking" in thinking and not isinstance(thinking["clear_thinking"], bool):
        raise ModelProtocolError("MODEL_PROTOCOL_OPTIONS_INVALID")
    return {"thinking": dict(thinking)}


def current_protocol_options(protocol_options: Any, protocol: ModelApiProtocol) -> dict[str, Any]:
    if not isinstance(protocol_options, dict):
        return {}
    value = protocol_options.get(protocol.value, {})
    return dict(value) if isinstance(value, dict) else {}


def model_config_fingerprint(
    *,
    api_protocol: str,
    base_url: str | None,
    model: str,
    key_revision: int,
    protocol_options: dict[str, Any],
    security_revision: int,
) -> str:
    payload = {
        "fingerprint_version": 1,
        "api_protocol": api_protocol,
        "base_url": _normalize_base_url(base_url),
        "model": model,
        "key_revision": key_revision,
        "protocol_options": protocol_options,
        "security_revision": security_revision,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    raw = unicodedata.normalize("NFC", value.strip())
    if not raw:
        return ""
    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password:
        raise ModelProtocolError("MODEL_BASE_URL_INVALID")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ModelProtocolError("MODEL_BASE_URL_INVALID") from exc
    if port == (443 if scheme == "https" else 80):
        port = None
    netloc = f"{hostname}:{port}" if port is not None else hostname
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def validate_model_base_url(value: str | None) -> None:
    _normalize_base_url(value)
