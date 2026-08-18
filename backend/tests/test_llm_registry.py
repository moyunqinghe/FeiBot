"""模型配置注册表:CRUD 往返、key 加密落盘、当前模型管理。"""

from __future__ import annotations

import pytest

from llm_protocols import decrypt_secret, derive_fernet_key

from app.config import CHANNEL_SECRET
from app.db import store
from app.llm import registry


def _add(name: str = "gpt5", api_key: str = "sk-test-1234567890") -> None:
    registry.add_model(
        name=name,
        protocol="openai_chat_completions",
        model="gpt-5",
        base_url="https://api.openai.com/v1",
        api_key=api_key,
        temperature=0.5,
    )


def test_add_get_list_roundtrip() -> None:
    _add()
    record = registry.get_model("gpt5")
    assert record is not None
    assert record.api_protocol == "openai_chat_completions"
    assert record.model == "gpt-5"
    assert record.temperature == 0.5
    assert [r.name for r in registry.list_models()] == ["gpt5"]
    assert registry.get_model("不存在") is None


def test_api_key_encrypted_on_disk() -> None:
    _add(api_key="sk-plaintext-check-42")
    row = store.get_model_config("gpt5")
    # 落盘的不是明文,也不含明文片段
    assert row["api_key_enc"] != "sk-plaintext-check-42"
    assert "sk-plaintext-check-42" not in row["api_key_enc"]
    # 用同一 secret 派生的 key 能解回原值(与 llm_protocols 位兼容)
    key = derive_fernet_key(CHANNEL_SECRET)
    assert decrypt_secret(row["api_key_enc"], key) == "sk-plaintext-check-42"


def test_masked_key_only_shows_last4() -> None:
    _add(api_key="sk-plaintext-check-42")
    record = registry.get_model("gpt5")
    masked = registry.masked_key(record)
    assert masked.endswith("k-42")
    assert "sk-plaintext" not in masked


def test_first_added_becomes_current_and_switch() -> None:
    _add("a")
    _add("b")
    assert registry.get_current_name() == "a"  # 第一套自动设为当前
    assert registry.set_current("b") is True
    assert registry.get_current().name == "b"
    assert registry.set_current("不存在") is False


def test_remove_clears_current() -> None:
    _add("a")
    assert registry.remove_model("a") is True
    assert registry.get_current() is None
    assert registry.remove_model("a") is False


def test_add_rejects_unknown_protocol() -> None:
    with pytest.raises(ValueError):
        registry.add_model(
            name="bad",
            protocol="not-a-protocol",
            model="m",
            base_url="https://x",
            api_key="k",
        )
