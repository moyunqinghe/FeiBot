from app.agent.tools.registry import (
    TOOL_REGISTRY,
    ToolSpec,
    register_tool,
    unregister_tool,
)


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description="d", parameters={}, handler=lambda: "ok")


def test_unregister_tool_removes_and_reports():
    register_tool(_spec("tmp_tool"))
    assert "tmp_tool" in TOOL_REGISTRY
    assert unregister_tool("tmp_tool") is True
    assert "tmp_tool" not in TOOL_REGISTRY


def test_unregister_missing_tool_returns_false():
    assert unregister_tool("never_registered") is False
