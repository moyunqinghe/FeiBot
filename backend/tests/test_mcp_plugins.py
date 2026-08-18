import json

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
