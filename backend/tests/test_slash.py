"""斜杠指令解析的回归测试。"""

from __future__ import annotations

from app.agent.slash import parse_command


def test_help_command_cn() -> None:
    cmd = parse_command("/帮助")
    assert cmd is not None
    assert cmd.kind == "help"


def test_help_command_en() -> None:
    cmd = parse_command("/help")
    assert cmd is not None
    assert cmd.kind == "help"


def test_ping_command() -> None:
    cmd = parse_command("/ping")
    assert cmd is not None
    assert cmd.kind == "ping"
    assert cmd.query == ""


def test_command_with_query() -> None:
    cmd = parse_command("/ping 现在")
    assert cmd is not None
    assert cmd.kind == "ping"
    assert cmd.query == "现在"


def test_non_command_returns_none() -> None:
    assert parse_command("你好") is None
    assert parse_command("") is None
    assert parse_command("  ") is None


def test_unknown_command_falls_back_to_help() -> None:
    cmd = parse_command("/不存在 参数")
    assert cmd is not None
    assert cmd.kind == "help"
    assert cmd.query == "不存在 参数"


def test_model_command() -> None:
    cmd = parse_command("/模型")
    assert cmd is not None
    assert cmd.kind == "model"
    assert cmd.query == ""
    assert parse_command("/模型列表").kind == "model"


def test_model_command_with_name() -> None:
    cmd = parse_command("/模型 gpt5")
    assert cmd is not None
    assert cmd.kind == "model"
    assert cmd.query == "gpt5"


def test_mcp_command_no_args() -> None:
    cmd = parse_command("/mcp")
    assert cmd is not None
    assert cmd.kind == "mcp"
    assert cmd.query == ""


def test_mcp_command_list() -> None:
    cmd = parse_command("/mcp list")
    assert cmd is not None
    assert cmd.kind == "mcp"
    assert cmd.query == "list"


def test_mcp_command_add_with_args() -> None:
    cmd = parse_command("/mcp add foo http://x/mcp")
    assert cmd is not None
    assert cmd.kind == "mcp"
    assert cmd.query == "add foo http://x/mcp"


def test_skill_command_no_args() -> None:
    cmd = parse_command("/skill")
    assert cmd is not None
    assert cmd.kind == "skill"
    assert cmd.query == ""


def test_skill_command_add_with_args() -> None:
    cmd = parse_command("/skill add owner/repo/tree/main/skills/x")
    assert cmd is not None
    assert cmd.kind == "skill"
    assert cmd.query == "add owner/repo/tree/main/skills/x"
