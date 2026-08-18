import json
import sys
from pathlib import Path

import pytest

from mcp_discovery import DiscoveredTool, DiscoveryResult, McpDiscoveryError

from app.agent.tools import mcp_plugins
from app.agent.tools.mcp_plugins import McpPluginManager, PluginError
from app.db import store

from app.agent.tools.registry import (
    TOOL_REGISTRY,
    ToolSpec,
    register_tool,
    unregister_tool,
)


@pytest.fixture
def clean_registry():
    """清空注册表做隔离,测试后恢复(内置工具在导入时已注册)。"""
    saved = dict(TOOL_REGISTRY)
    TOOL_REGISTRY.clear()
    yield TOOL_REGISTRY
    TOOL_REGISTRY.clear()
    TOOL_REGISTRY.update(saved)


@pytest.fixture
def manager(clean_registry):
    return McpPluginManager()


def _tool(name: str) -> DiscoveredTool:
    return DiscoveredTool(
        name=name,
        title="",
        description=f"{name} 描述",
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "string", "description": "参数 x"}},
        },
        output_schema={},
        annotations={},
        meta={},
    )


def _result(names):
    return DiscoveryResult(
        tools=tuple(_tool(n) for n in names),
        protocol_version="2024-11-05",
        capabilities={},
        server_info={"name": "fake", "version": "1"},
    )


def test_install_registers_prefixed_tools_and_persists(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["alpha", "beta"]))
    count = manager.install("p1", {"transport": "http", "url": "http://x/mcp"})
    assert count == 2
    assert "p1__alpha" in TOOL_REGISTRY
    assert "p1__beta" in TOOL_REGISTRY
    assert TOOL_REGISTRY["p1__alpha"].description == "alpha 描述"
    assert TOOL_REGISTRY["p1__alpha"].parameters == {"x": "参数 x"}
    row = store.get_plugin("p1")
    assert row is not None and row["enabled"] == 1
    assert json.loads(row["config_json"])["url"] == "http://x/mcp"


def test_install_invalid_name_rejected(manager):
    with pytest.raises(PluginError):
        manager.install("bad name!", {"transport": "http", "url": "http://x"})
    with pytest.raises(PluginError):
        manager.install("a__b", {"transport": "http", "url": "http://x"})


def test_install_discover_failure_registers_nothing(manager, monkeypatch):
    def boom(cfg, **kw):
        raise McpDiscoveryError("连不上", code="CONNECT_FAILED")
    monkeypatch.setattr(mcp_plugins, "discover", boom)
    with pytest.raises(McpDiscoveryError):
        manager.install("p1", {"transport": "http", "url": "http://x"})
    assert TOOL_REGISTRY == {}
    assert store.get_plugin("p1") is None


def test_install_maps_type_to_transport(manager, monkeypatch):
    seen = {}
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: (seen.update(cfg), _result(["t"]))[1])
    manager.install("p1", {"type": "streamable_http", "url": "http://x/mcp"})
    assert seen["transport"] == "http"
    assert "type" not in seen


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description="d", parameters={}, handler=lambda: "ok")


def test_unregister_tool_removes_and_reports():
    register_tool(_spec("tmp_tool"))
    assert "tmp_tool" in TOOL_REGISTRY
    assert unregister_tool("tmp_tool") is True
    assert "tmp_tool" not in TOOL_REGISTRY


def test_unregister_missing_tool_returns_false():
    assert unregister_tool("never_registered") is False


def test_uninstall_removes_tools_and_record(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a", "b"]))
    manager.install("p1", {"transport": "http", "url": "http://x"})
    assert manager.uninstall("p1") is True
    assert "p1__a" not in TOOL_REGISTRY and "p1__b" not in TOOL_REGISTRY
    assert store.get_plugin("p1") is None


def test_uninstall_missing_returns_false(manager):
    assert manager.uninstall("ghost") is False


def test_install_idempotent_reinstall(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a"]))
    manager.install("p1", {"transport": "http", "url": "http://x"})
    manager.install("p1", {"transport": "http", "url": "http://x"})
    assert list(TOOL_REGISTRY) == ["p1__a"]
    assert len(store.list_plugins()) == 1


def test_disable_keeps_record_removes_tools(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a"]))
    manager.install("p1", {"transport": "http", "url": "http://x"})
    assert manager.disable("p1") is True
    assert "p1__a" not in TOOL_REGISTRY
    row = store.get_plugin("p1")
    assert row is not None and row["enabled"] == 0


def test_disable_missing_returns_false(manager):
    assert manager.disable("ghost") is False


def test_enable_rediscovers_and_registers(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a"]))
    manager.install("p1", {"transport": "http", "url": "http://x"})
    manager.disable("p1")
    assert manager.enable("p1") == 1
    assert "p1__a" in TOOL_REGISTRY
    assert store.get_plugin("p1")["enabled"] == 1


def test_enable_missing_raises(manager):
    with pytest.raises(PluginError):
        manager.enable("ghost")


def test_reload_updates_tool_set(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a", "b"]))
    manager.install("p1", {"transport": "http", "url": "http://x"})
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a", "c", "d"]))
    assert manager.reload("p1") == 3
    assert set(TOOL_REGISTRY) == {"p1__a", "p1__c", "p1__d"}


def test_reload_missing_raises(manager):
    with pytest.raises(PluginError):
        manager.reload("ghost")


def test_list_reports_plugins(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a"]))
    manager.install("p1", {"transport": "http", "url": "http://x"})
    manager.install("p2", {"transport": "stdio", "command": "node"})
    manager.disable("p2")
    info = {row["name"]: row for row in manager.list()}
    assert info["p1"]["enabled"] is True
    assert info["p1"]["registered"] == ["p1__a"]
    assert info["p2"]["enabled"] is False
    assert info["p2"]["registered"] == []


def test_load_enabled_registers_enabled_only_and_skips_failures(clean_registry, monkeypatch):
    mgr = McpPluginManager()
    # 预置三条库记录:p1 启用可连,p2 停用,p3 启用但 discover 失败
    store.upsert_plugin("p1", json.dumps({"transport": "http", "url": "http://ok"}), 1)
    store.upsert_plugin("p2", json.dumps({"transport": "http", "url": "http://off"}), 0)
    store.upsert_plugin("p3", json.dumps({"transport": "http", "url": "http://bad"}), 1)

    def fake_discover(cfg, **kw):
        if cfg["url"] == "http://bad":
            raise McpDiscoveryError("连不上", code="CONNECT_FAILED")
        return _result(["a"])

    monkeypatch.setattr(mcp_plugins, "discover", fake_discover)
    mgr.load_enabled()  # 不应抛异常

    assert "p1__a" in TOOL_REGISTRY
    assert "p2__a" not in TOOL_REGISTRY
    assert "p3__a" not in TOOL_REGISTRY
    status = {row["name"]: row["status"] for row in mgr.list()}
    assert status["p1"].startswith("ok")
    assert "load failed" in status["p3"]


def test_install_from_mcp_servers_batch(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["t"]))
    payload = {
        "mcpServers": {
            "train": {"type": "streamable_http", "url": "http://x/mcp"},
            "bad name!": {"type": "stdio", "command": "node"},
        }
    }
    results = manager.install_from_mcp_servers(payload)
    assert results["train"] == 1
    assert str(results["bad name!"]).startswith("failed")
    assert "train__t" in TOOL_REGISTRY


def test_install_from_mcp_servers_empty(manager):
    assert manager.install_from_mcp_servers({}) == {}


_STDIO_MOCK = (
    Path(__file__).resolve().parents[1] / "app" / "agent" / "mcp" / "tests" / "stdio_mock_server.py"
)


def test_integration_stdio_install_call_uninstall(manager):
    config = {"transport": "stdio", "command": sys.executable, "args": [str(_STDIO_MOCK)]}
    count = manager.install("mocksrv", config)
    assert count == 3
    assert set(TOOL_REGISTRY) == {
        "mocksrv__echo", "mocksrv__sum", "mocksrv__product_lookup",
    }
    # 真实远程调用 echo
    out = TOOL_REGISTRY["mocksrv__echo"].handler(text="hi")
    assert out == "echo: hi"

    assert manager.uninstall("mocksrv") is True
    assert TOOL_REGISTRY == {}
    assert store.get_plugin("mocksrv") is None


def test_reload_disabled_plugin_raises(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a"]))
    manager.install("p1", {"transport": "http", "url": "http://x"})
    manager.disable("p1")
    with pytest.raises(PluginError):
        manager.reload("p1")
    assert TOOL_REGISTRY == {}


def test_enable_discover_failure_records_status(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["a"]))
    manager.install("p1", {"transport": "http", "url": "http://x"})
    manager.disable("p1")

    def boom(cfg, **kw):
        raise McpDiscoveryError("连不上", code="CONNECT_FAILED")

    monkeypatch.setattr(mcp_plugins, "discover", boom)
    with pytest.raises(McpDiscoveryError):
        manager.enable("p1")
    status = {row["name"]: row["status"] for row in manager.list()}
    assert "enable failed" in status["p1"]
    assert store.get_plugin("p1")["enabled"] == 0
    assert TOOL_REGISTRY == {}


def test_stringify_str_passthrough_and_dict():
    from app.agent.tools.mcp_plugins import _stringify
    assert _stringify("hello") == "hello"
    assert json.loads(_stringify({"a": 1})) == {"a": 1}


def test_install_handler_calls_call_tool_with_config(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["alpha"]))
    captured = {}

    def fake_call(config, tool_name, arguments=None, **kw):
        captured.update({"config": config, "tool": tool_name, "args": arguments})
        return {"ok": True, "echo": arguments}

    monkeypatch.setattr(mcp_plugins, "call_tool", fake_call)
    manager.install("p1", {"transport": "http", "url": "http://x/mcp"})
    out = TOOL_REGISTRY["p1__alpha"].handler(a="1")
    assert captured["tool"] == "alpha"
    assert captured["args"] == {"a": "1"}
    assert captured["config"]["url"] == "http://x/mcp"
    assert json.loads(out) == {"ok": True, "echo": {"a": "1"}}


def test_handler_raises_on_call_error(manager, monkeypatch):
    monkeypatch.setattr(mcp_plugins, "discover", lambda cfg, **kw: _result(["alpha"]))

    def boom(config, tool_name, arguments=None, **kw):
        raise McpDiscoveryError("工具报错", code="TOOL_ERROR")

    monkeypatch.setattr(mcp_plugins, "call_tool", boom)
    manager.install("p1", {"transport": "http", "url": "http://x"})
    with pytest.raises(McpDiscoveryError):
        TOOL_REGISTRY["p1__alpha"].handler()
