"""ingress:渠道客户端强制直连 + 轮询循环对网络异常退避重试。"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
from wechat_ilink import encrypt_secret

from app.channels import ingress
from app.db import store


def test_new_client_ignores_env_proxy(monkeypatch) -> None:
    """代理环境变量存在时,渠道客户端也不挂载代理(凭证不走第三方节点)。"""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7897")
    client = ingress._new_client("https://ilinkai.weixin.qq.com")
    try:
        assert client._client._mounts == {}  # 无代理挂载 → 永远直连
    finally:
        client.close()


def test_poll_loop_survives_transport_errors(monkeypatch) -> None:
    """httpx 传输错误(非 OSError)不能杀死轮询循环:退避后继续。"""
    store.set_kv(
        ingress.KV_TOKEN_ENC, encrypt_secret("dummy-token", ingress.FERNET_KEY)
    )
    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_updates(self, cursor, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("TLS 被代理掐断")
            if calls["n"] == 2:
                return {"msgs": [], "get_updates_buf": "c2"}
            raise KeyboardInterrupt  # 第三次收网,结束循环

        def close(self):
            pass

    monkeypatch.setattr(ingress, "WeChatClient", FakeClient)
    monkeypatch.setattr(ingress.time, "sleep", lambda s: None)

    ingress.run_wechat_ingress(lambda conv, text: "ok")
    assert calls["n"] == 3  # 第 1 次失败没让进程崩,循环活到了第 3 次
    assert store.get_kv(ingress.KV_CURSOR) == "c2"


def test_poll_loop_survives_http_status_errors(monkeypatch) -> None:
    """上游 5xx(raise_for_status 抛 HTTPStatusError)同样退避重试。"""
    store.set_kv(
        ingress.KV_TOKEN_ENC, encrypt_secret("dummy-token", ingress.FERNET_KEY)
    )
    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_updates(self, cursor, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                request = httpx.Request("POST", "https://x/ilink/bot/getupdates")
                response = httpx.Response(502, request=request)
                raise httpx.HTTPStatusError("bad gateway", request=request, response=response)
            raise KeyboardInterrupt

        def close(self):
            pass

    monkeypatch.setattr(ingress, "WeChatClient", FakeClient)
    monkeypatch.setattr(ingress.time, "sleep", lambda s: None)

    ingress.run_wechat_ingress(lambda conv, text: "ok")
    assert calls["n"] == 2


def test_model_reply_is_logged(monkeypatch, caplog) -> None:
    """发给用户的模型回复要以 回复(...): ... 出现在日志里(收到/回复成对)。"""
    store.set_kv(
        ingress.KV_TOKEN_ENC, encrypt_secret("dummy-token", ingress.FERNET_KEY)
    )

    fake_msg = SimpleNamespace(
        event_id="e1", from_user_id="user-1", context_token="ctx", text="你好"
    )
    monkeypatch.setattr(
        ingress, "normalize_wechat_message", lambda raw, **kw: fake_msg
    )

    calls = {"n": 0}
    sent: list[str] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_updates(self, cursor, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"msgs": [{"raw": "one"}], "get_updates_buf": "c2"}
            raise KeyboardInterrupt

        def send_message(self, to_user_id, context_token, text, client_id=""):
            sent.append(text)

        def close(self):
            pass

    monkeypatch.setattr(ingress, "WeChatClient", FakeClient)
    monkeypatch.setattr(ingress.time, "sleep", lambda s: None)

    with caplog.at_level(logging.INFO, logger="app.channels.ingress"):
        ingress.run_wechat_ingress(lambda conv, text: "这是模型的回复")

    assert sent == ["这是模型的回复"]  # 确实发送了
    assert "收到(user-1): 你好" in caplog.text
    assert "回复(user-1): 这是模型的回复" in caplog.text
