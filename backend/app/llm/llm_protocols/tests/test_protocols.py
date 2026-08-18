from __future__ import annotations

from types import MappingProxyType, SimpleNamespace

import pytest

from llm_protocols import (
    LLMClient,
    LLMError,
    ModelApiProtocol,
    ModelProtocolError,
    ResolvedModelConfig,
    available_model_protocols,
    decrypt_secret,
    derive_fernet_key,
    encrypt_secret,
    mask_secret,
    model_config_fingerprint,
    normalize_chat_protocol_options,
    resolve_api_protocol,
    snapshot_model_config,
    validate_model_base_url,
)


def test_protocol_boundary_accepts_only_chat_compatibility() -> None:
    assert resolve_api_protocol(None, "openai_compatible") == (
        ModelApiProtocol.OPENAI_CHAT_COMPLETIONS
    )
    with pytest.raises(ModelProtocolError) as exc_info:
        resolve_api_protocol(None, "anthropic")
    assert exc_info.value.code == "MODEL_PROVIDER_UNSUPPORTED"


def test_resolve_api_protocol_rejects_conflict_and_unknown() -> None:
    with pytest.raises(ModelProtocolError) as exc_info:
        resolve_api_protocol("nonsense", None)
    assert exc_info.value.code == "MODEL_PROTOCOL_UNSUPPORTED"
    with pytest.raises(ModelProtocolError) as exc_info:
        resolve_api_protocol("anthropic_messages", "openai_compatible")
    assert exc_info.value.code == "MODEL_PROTOCOL_CONFLICT"


def test_chat_thinking_options_are_strictly_typed() -> None:
    assert normalize_chat_protocol_options(
        {"thinking": {"type": "disabled", "clear_thinking": True}}
    ) == {"thinking": {"type": "disabled", "clear_thinking": True}}
    with pytest.raises(ModelProtocolError) as exc_info:
        normalize_chat_protocol_options({"thinking": {"type": "disabled", "vendor": 1}})
    assert exc_info.value.code == "MODEL_PROTOCOL_OPTIONS_INVALID"


def test_fingerprint_normalizes_equivalent_base_urls() -> None:
    common = {
        "api_protocol": "openai_chat_completions",
        "model": "model-a",
        "key_revision": 1,
        "protocol_options": {},
        "security_revision": 1,
    }
    assert model_config_fingerprint(base_url="HTTPS://EXAMPLE.COM:443/v1/", **common) == (
        model_config_fingerprint(base_url="https://example.com/v1", **common)
    )


def test_validate_model_base_url_rejects_non_http_and_credentials() -> None:
    for bad in ("ftp://example.com", "https://user:pass@example.com", "not a url"):
        with pytest.raises(ModelProtocolError) as exc_info:
            validate_model_base_url(bad)
        assert exc_info.value.code == "MODEL_BASE_URL_INVALID"
    validate_model_base_url(None)
    validate_model_base_url("https://example.com/v1")


def test_all_implemented_protocols_are_available() -> None:
    assert available_model_protocols() == [
        "openai_chat_completions",
        "openai_responses",
        "anthropic_messages",
        "gemini_generate_content",
    ]


def _row(**overrides):  # noqa: ANN003
    values = {
        "id": "model_a",
        "tenant_id": "tenant_a",
        "name": "Chat",
        "api_key_encrypted": "encrypted",
        "base_url": "https://example.test/v1",
        "model": "model-a",
        "temperature": 0.2,
        "max_output_tokens": 8192,
        "api_protocol": "openai_chat_completions",
        "protocol_options_json": {
            "openai_chat_completions": {"thinking": {"type": "disabled"}}
        },
        "extra_body_json": {"legacy_vendor_flag": True},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_snapshot_model_config_preserves_anthropic_protocol_and_options() -> None:
    row = _row(
        api_protocol="anthropic_messages",
        protocol_options_json={"anthropic_messages": {}},
        extra_body_json={},
    )

    snapshot = snapshot_model_config(row, min_output_tokens=16_384)

    assert snapshot.api_protocol is ModelApiProtocol.ANTHROPIC_MESSAGES
    assert snapshot.max_output_tokens == 16_384


def test_snapshot_freezes_protocol_options_and_extra_body() -> None:
    snapshot = snapshot_model_config(_row())

    assert isinstance(snapshot.protocol_options, MappingProxyType)
    assert dict(snapshot.protocol_options) == {"thinking": {"type": "disabled"}}
    assert snapshot.legacy_extra_body["legacy_vendor_flag"] is True
    with pytest.raises(TypeError):
        snapshot.protocol_options["new"] = True  # type: ignore[index]


def test_snapshot_resolved_config_respects_min_output_tokens() -> None:
    resolved = snapshot_model_config(_row(max_output_tokens=4096))
    assert snapshot_model_config(resolved, min_output_tokens=1024) is resolved
    bumped = snapshot_model_config(resolved, min_output_tokens=8192)
    assert bumped.max_output_tokens == 8192
    assert isinstance(bumped, ResolvedModelConfig)


def test_crypto_roundtrip_and_mask() -> None:
    key = derive_fernet_key("app-secret")
    enc = encrypt_secret("sk-live-key", key)
    assert enc != "sk-live-key"
    assert decrypt_secret(enc, key) == "sk-live-key"
    assert decrypt_secret("", key) == ""
    # 同一个 secret 串派生同一把 key(跨项目位兼容)
    assert derive_fernet_key("app-secret") == key
    with pytest.raises(ValueError):
        decrypt_secret(enc, derive_fernet_key("other-secret"))
    assert mask_secret("sk-1234567890abcd") == "sk--****abcd"
    assert mask_secret("short") == "****"
    assert mask_secret("") == ""


def test_llm_error_code_derivation_and_public_detail() -> None:
    err = LLMError("MODEL_RATE_LIMITED", retryable=True)
    assert err.code == "MODEL_RATE_LIMITED"
    assert err.public_detail()["retryable"] is True
    assert LLMError("some message").public_detail()["code"] == "MODEL_CONNECTION_FAILED"


def test_llm_client_assembles_driver_and_delegates(monkeypatch) -> None:
    key = derive_fernet_key("test-secret")
    captured = {}

    def fake_build(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        driver = SimpleNamespace(
            complete=lambda request: ("completed", request),
            request_kind="chat.completions",
        )
        return SimpleNamespace(), driver

    monkeypatch.setattr("llm_protocols.client.build_protocol_driver", fake_build)
    config = SimpleNamespace(
        api_protocol="openai_chat_completions",
        api_key_encrypted=encrypt_secret("sk-plain", key),
        base_url=None,
        model="gpt-test",
        temperature=0.2,
        max_output_tokens=128,
        protocol_options={},
        legacy_extra_body={"thinking": {"type": "enabled"}},
    )

    client = LLMClient(config, secret="test-secret")

    assert captured["api_key"] == "sk-plain"
    assert client.thinking_mode == "enabled"
    request = client.build_request([{"role": "user", "content": "ping"}])
    assert request["max_tokens"] == 128
    assert request["extra_body"]["thinking"]["type"] == "enabled"
    assert client.complete(request) == ("completed", request)


def test_llm_client_rejects_missing_or_undecryptable_key() -> None:
    config = SimpleNamespace(
        api_protocol="openai_chat_completions",
        api_key_encrypted="",
        base_url=None,
        model="gpt-test",
        temperature=0.2,
        max_output_tokens=128,
    )
    with pytest.raises(LLMError) as exc_info:
        LLMClient(config, secret="test-secret")
    assert exc_info.value.code is None  # "Model API key is not configured" 无 MODEL_ 前缀
    with pytest.raises(LLMError) as exc_info:
        LLMClient(
            SimpleNamespace(**{**vars(config), "api_protocol": "nonsense"}),
            secret="test-secret",
        )
    assert exc_info.value.code == "MODEL_PROTOCOL_UNSUPPORTED"
