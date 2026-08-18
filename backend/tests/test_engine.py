"""engine 的回归测试:指令应答、echo 兜底、真实模型路径与异常兜底。"""

from __future__ import annotations

from types import SimpleNamespace

from llm_protocols import ProtocolCallError
from llm_protocols.client import LLMClient as ProtocolLLMClient
from mcp_discovery import McpDiscoveryError

from app.agent import engine
from app.agent import profile
from app.agent.memory.store import MemoryStore
from app.db import store
from app.llm import registry


def _add_model(name: str = "gpt5") -> None:
    registry.add_model(
        name=name,
        protocol="openai_chat_completions",
        model="gpt-5",
        base_url="https://api.openai.com/v1",
        api_key="sk-test-1234567890",
    )


def test_plain_message_echo_fallback() -> None:
    reply = engine.handle_message("conv-1", "你好")
    assert reply.startswith("echo: 你好")
    assert "未配置模型" in reply  # 无配置时尾部追加提示


def test_ping_command_returns_pong() -> None:
    assert engine.handle_message("conv-1", "/ping") == "pong"


def test_help_command_mentions_model() -> None:
    reply = engine.handle_message("conv-1", "/帮助")
    assert "/ping" in reply
    assert "/模型" in reply


def test_model_command_list_empty() -> None:
    assert "还没有模型配置" in engine.handle_message("conv-1", "/模型")


def test_model_command_list_and_switch() -> None:
    _add_model("a")
    _add_model("b")
    reply = engine.handle_message("conv-1", "/模型")
    assert "* a" in reply  # 首个配置自动为当前
    assert " b(" in reply
    assert "已切换到模型:b" in engine.handle_message("conv-1", "/模型 b")
    assert registry.get_current_name() == "b"
    assert "没有名为" in engine.handle_message("conv-1", "/模型 不存在")


def test_engine_with_config_calls_llm_protocols(monkeypatch) -> None:
    """配置了模型时,engine 经 llm_protocols.LLMClient.complete 取回复。"""
    _add_model()
    captured: dict = {}

    def fake_complete(self, request):
        captured.update(request)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="模型回复"))]
        )

    monkeypatch.setattr(ProtocolLLMClient, "complete", fake_complete)
    monkeypatch.setattr(engine, "distill_memory", lambda *a: [])  # 提炼单独测
    reply = engine.handle_message("conv-1", "你好")
    assert reply == "模型回复"
    assert captured["model"] == "gpt-5"
    assert captured["messages"][-1] == {"role": "user", "content": "你好"}


def test_engine_llm_error_returns_friendly_message(monkeypatch) -> None:
    """协议异常不炸到渠道层,回友好提示。"""
    _add_model()

    def boom(self, request):
        raise ProtocolCallError("MODEL_RATE_LIMITED", retryable=True)

    monkeypatch.setattr(ProtocolLLMClient, "complete", boom)
    reply = engine.handle_message("conv-1", "你好")
    assert "模型调用失败" in reply
    assert "MODEL_RATE_LIMITED" in reply


def test_session_is_created_and_reused() -> None:
    engine.handle_message("conv-1", "第一条")
    engine.handle_message("conv-1", "第二条")
    first = store.get_or_create_session("conv-1")
    assert store.get_or_create_session("conv-1") == first
    assert store.get_or_create_session("conv-2") != first


def test_distill_triggered_after_round(monkeypatch) -> None:
    """配置了模型时,每轮结束后触发一次记忆提炼。"""
    _add_model()

    def fake_complete(self, request):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="模型回复"))]
        )

    monkeypatch.setattr(ProtocolLLMClient, "complete", fake_complete)
    calls: list[tuple] = []
    monkeypatch.setattr(
        engine, "distill_memory", lambda client, u, a: calls.append((u, a)) or []
    )
    engine.handle_message("conv-1", "我叫老莫")
    assert calls == [("我叫老莫", "模型回复")]


def test_distill_skipped_for_echo(monkeypatch) -> None:
    """EchoLLM 兜底(未配置模型)时跳过提炼。"""
    calls: list[tuple] = []
    monkeypatch.setattr(
        engine, "distill_memory", lambda client, u, a: calls.append((u, a)) or []
    )
    engine.handle_message("conv-1", "你好")
    assert calls == []


def test_memory_and_forget_commands() -> None:
    assert "还没有关于你的记忆" in engine.handle_message("conv-1", "/记忆")
    MemoryStore().add(["用户在做 SaaS"])
    profile.update_profile({"称呼": "老莫"})
    reply = engine.handle_message("conv-1", "/memory")
    assert "用户在做 SaaS" in reply
    assert "称呼:老莫" in reply  # /记忆 同时展示画像
    assert "已清空关于你的全部记忆与画像" in engine.handle_message("conv-1", "/遗忘")
    assert MemoryStore().load() == []
    assert profile.profile_summary() == {}  # 画像一并清空


def test_mcp_denied_for_non_admin(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: False)
    assert "仅限管理员" in engine.handle_message("conv-1", "/mcp list")


def test_mcp_list_empty_for_admin(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    reply = engine.handle_message("conv-1", "/mcp")
    assert "还没有装入 MCP 插件" in reply


def test_mcp_add_and_remove_flow(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    monkeypatch.setattr(engine.plugin_manager, "install", lambda name, cfg: 3)
    reply = engine.handle_message("conv-1", "/mcp add foo http://x/mcp")
    assert "已装入插件 foo" in reply and "3 个工具" in reply
    monkeypatch.setattr(engine.plugin_manager, "uninstall", lambda name: True)
    assert "已卸下插件 foo" in engine.handle_message("conv-1", "/mcp remove foo")


def test_mcp_add_missing_args_shows_usage(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    assert "用法" in engine.handle_message("conv-1", "/mcp add foo")


def test_mcp_add_install_failure(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)

    def boom(name, cfg):
        raise McpDiscoveryError("连不上", code="CONNECT_FAILED")

    monkeypatch.setattr(engine.plugin_manager, "install", boom)
    assert "装入失败" in engine.handle_message("conv-1", "/mcp add foo http://x")


def test_mcp_enable_and_disable(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    monkeypatch.setattr(engine.plugin_manager, "enable", lambda name: 2)
    monkeypatch.setattr(engine.plugin_manager, "disable", lambda name: True)
    assert "已启用插件 foo" in engine.handle_message("conv-1", "/mcp enable foo")
    assert "已停用插件 foo" in engine.handle_message("conv-1", "/mcp disable foo")


def test_mcp_remove_missing(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    monkeypatch.setattr(engine.plugin_manager, "uninstall", lambda name: False)
    assert "没有名为" in engine.handle_message("conv-1", "/mcp remove ghost")


def test_mcp_unknown_subcommand_shows_usage(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    assert "用法" in engine.handle_message("conv-1", "/mcp wat")
