"""模型配置注册表:存取、加解密、当前选中模型。

api_key 用 llm_protocols.crypto 加密后落盘(密钥由 config.CHANNEL_SECRET
派生,与渠道 token 同一套);当前选中模型的名字存 kv 表 llm.current_model。

get_current() 返回 ModelRecord:属性名对齐 llm_protocols.LLMClient 的
鸭子类型约定(api_protocol / api_key_encrypted / max_output_tokens),
api_key 保持密文不解开——解密由 LLMClient 内部用同一 secret 完成,
明文只在内存中短暂存在。
"""

from __future__ import annotations

from dataclasses import dataclass

from llm_protocols import (
    ModelApiProtocol,
    decrypt_secret,
    derive_fernet_key,
    encrypt_secret,
    mask_secret,
)

from app.config import CHANNEL_SECRET
from app.db import store

FERNET_KEY = derive_fernet_key(CHANNEL_SECRET)

KV_CURRENT_MODEL = "llm.current_model"
DEFAULT_MAX_OUTPUT_TOKENS = 4096  # 表里没有该列,运行时用固定默认值

PROTOCOL_NAMES = tuple(p.value for p in ModelApiProtocol)


@dataclass(frozen=True)
class ModelRecord:
    """一条模型配置的运行时视图;直接可传给 llm_protocols.LLMClient。"""

    name: str  # 备注名(主键)
    api_protocol: str  # ModelApiProtocol 四值之一
    model: str  # provider 侧的模型名
    base_url: str
    api_key_encrypted: str  # 密文;解密由 LLMClient(secret=CHANNEL_SECRET)完成
    temperature: float = 1.0
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS


def _row_to_record(row: dict) -> ModelRecord:
    return ModelRecord(
        name=row["name"],
        api_protocol=row["protocol"],
        model=row["model"],
        base_url=row["base_url"],
        api_key_encrypted=row["api_key_enc"],
        temperature=float(row["temperature"]),
    )


def add_model(
    *,
    name: str,
    protocol: str,
    model: str,
    base_url: str,
    api_key: str,
    temperature: float = 1.0,
) -> None:
    """添加/覆盖模型配置(api_key 为明文,落盘前加密);第一套配置自动设为当前。"""
    try:
        ModelApiProtocol(protocol)  # 校验协议名,非法值抛 ValueError
    except ValueError:
        raise ValueError(
            f"未知协议:{protocol}(可选:{' / '.join(PROTOCOL_NAMES)})"
        ) from None
    store.upsert_model_config(
        name=name,
        protocol=protocol,
        model=model,
        base_url=base_url,
        api_key_enc=encrypt_secret(api_key, FERNET_KEY),
        temperature=temperature,
    )
    if not get_current_name():
        set_current(name)


def list_models() -> list[ModelRecord]:
    """列出全部模型配置(按名字排序)。"""
    return [_row_to_record(row) for row in store.list_model_configs()]


def get_model(name: str) -> ModelRecord | None:
    """按名字取模型配置;不存在返回 None。"""
    row = store.get_model_config(name)
    return _row_to_record(row) if row else None


def remove_model(name: str) -> bool:
    """删除模型配置;若删的是当前模型,同时清掉 kv 里的选中记录。"""
    deleted = store.delete_model_config(name)
    if deleted and get_current_name() == name:
        store.set_kv(KV_CURRENT_MODEL, "")
    return deleted


def get_current_name() -> str:
    """当前选中模型的名字;未设置返回空串。"""
    return store.get_kv(KV_CURRENT_MODEL)


def set_current(name: str) -> bool:
    """切换当前模型;配置不存在返回 False。"""
    if store.get_model_config(name) is None:
        return False
    store.set_kv(KV_CURRENT_MODEL, name)
    return True


def get_current() -> ModelRecord | None:
    """当前选中模型的完整配置;未设置或配置已被删返回 None。"""
    name = get_current_name()
    return get_model(name) if name else None


def masked_key(record: ModelRecord) -> str:
    """解密后脱敏(只留后 4 位),供 list 展示;不落盘、不外发。"""
    return mask_secret(decrypt_secret(record.api_key_encrypted, FERNET_KEY))
