"""模型配置快照(纯数据层,剥离了 SQLModel / FastAPI 与信任状态机)。

只保留 StaffDeck `app/llm/model_config_resolver.py` 中无耦合的部分:
`ResolvedModelConfig` 值对象和鸭子类型的 `snapshot_model_config`。
原文件中的 `resolve_model_config_for_runtime / resolve_model_config_for_verification`
绑定 ORM 与验证/信任门控,属于宿主职责,不在本包内。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping

from .protocols import ModelApiProtocol, current_protocol_options


@dataclass(frozen=True)
class ResolvedModelConfig:
    id: str
    tenant_id: str
    api_protocol: ModelApiProtocol
    base_url: str | None
    api_key_encrypted: str
    model: str
    temperature: float
    max_output_tokens: int
    protocol_options: Mapping[str, Any]
    legacy_extra_body: Mapping[str, Any]
    config_revision: int
    security_revision: int
    purpose: Literal["runtime", "verification"]
    timeout_seconds: float | None = None


def snapshot_model_config(
    model_config: Any, *, min_output_tokens: int = 0
) -> ResolvedModelConfig:
    if isinstance(model_config, ResolvedModelConfig):
        if model_config.max_output_tokens >= min_output_tokens:
            return model_config
        return ResolvedModelConfig(
            **{
                **model_config.__dict__,
                "max_output_tokens": min_output_tokens,
            }
        )
    protocol = ModelApiProtocol(
        getattr(model_config, "api_protocol", "openai_chat_completions")
    )
    return ResolvedModelConfig(
        id=str(getattr(model_config, "id", "")),
        tenant_id=str(getattr(model_config, "tenant_id", "")),
        api_protocol=protocol,
        purpose=getattr(model_config, "purpose", "runtime"),
        api_key_encrypted=model_config.api_key_encrypted,
        base_url=model_config.base_url,
        model=model_config.model,
        temperature=model_config.temperature,
        max_output_tokens=max(
            int(getattr(model_config, "max_output_tokens", 0) or 0), min_output_tokens
        ),
        protocol_options=_freeze(
            _snapshot_protocol_options(model_config, protocol)
        ),
        legacy_extra_body=_freeze(
            copy.deepcopy(
                getattr(model_config, "legacy_extra_body", {})
                or getattr(model_config, "extra_body_json", {})
            )
        ),
        config_revision=getattr(model_config, "config_revision", 1),
        security_revision=getattr(model_config, "security_revision", 1),
        timeout_seconds=getattr(model_config, "timeout_seconds", None),
    )


def _snapshot_protocol_options(model_config: Any, protocol: ModelApiProtocol) -> dict[str, Any]:
    direct = getattr(model_config, "protocol_options", {})
    if isinstance(direct, dict) and direct:
        return copy.deepcopy(direct)
    return current_protocol_options(
        getattr(model_config, "protocol_options_json", {}), protocol
    )


def _freeze(value: dict[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze_value(item) for key, item in copy.deepcopy(value).items()})


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _freeze(value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value
