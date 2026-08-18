"""工具层:解析、执行、内置工具、engine 的工具循环与白名单门控。"""

from __future__ import annotations

from types import SimpleNamespace

from llm_protocols.client import LLMClient as ProtocolLLMClient

from app import config
from app.agent import engine
from app.agent.tools import builtin, parse_tool_call, execute_tool_call, render_tools_prompt
from app.agent.tools.calls import ToolCall
from app.db import store
from app.llm import registry


# ---- parse_tool_call ----


def test_parse_plain_text_is_none() -> None:
    assert parse_tool_call("今天天气不错") is None
    assert parse_tool_call("") is None
    assert parse_tool_call(None) is None


def test_parse_tool_json() -> None:
    call = parse_tool_call('{"tool": "shell", "args": {"command": "ls"}}')
    assert call == ToolCall("shell", {"command": "ls"})


def test_parse_fenced_json() -> None:
    call = parse_tool_call('```json\n{"tool": "pwd", "args": {}}\n```')
    assert call is not None and call.name == "pwd" and call.args == {}


def test_parse_json_without_tool_key_is_none() -> None:
    # 用户让模型写 JSON 示例时不能误判成调用
    assert parse_tool_call('{"name": "飞飞", "age": 3}') is None


def test_parse_unregistered_tool_is_none() -> None:
    # 未注册的工具名不视为调用(防把 JSON 示例误执行)
    assert parse_tool_call('{"tool": "不存在的工具", "args": {}}') is None


def test_parse_call_embedded_in_self_completed_blob() -> None:
    # 模型抢答:一条回复里同时输出调用和编造的"结果",仍要识别出真正的调用
    blob = (
        '{"tool": "list_dir", "args": {}}\n'
        '{"result": {"stdout": "编造的目录内容", "return_code": 0}}\n'
        "以上是我查到的。"
    )
    call = parse_tool_call(blob)
    assert call == ToolCall("list_dir", {})


def test_parse_bad_args_coerced_to_empty() -> None:
    call = parse_tool_call('{"tool": "pwd", "args": "不是字典"}')
    assert call is not None and call.args == {}


# ---- execute_tool_call ----


def test_execute_unknown_tool() -> None:
    out = execute_tool_call(ToolCall("不存在的工具", {}))
    assert "工具不存在" in out and "可用工具" in out


def test_execute_param_mismatch_reported() -> None:
    out = execute_tool_call(ToolCall("current_time", {"bad_arg": "1"}))
    assert "参数不符" in out


def test_execute_shell_echo() -> None:
    assert "ok-feibot" in execute_tool_call(ToolCall("shell", {"command": "echo ok-feibot"}))


# ---- 内置工具 ----


def test_render_tools_prompt_lists_all_tools() -> None:
    text = render_tools_prompt()
    for name in ("current_time", "pwd", "list_dir", "shell"):
        assert name in text
    assert '{"tool"' in text  # 调用格式约定在提示里


def test_current_time_format() -> None:
    out = builtin.current_time()
    assert "星期" in out and ":" in out


def test_pwd_is_backend_dir() -> None:
    assert builtin.pwd() == str(config.BASE_DIR)


def test_list_dir_lists_entries(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    out = builtin.list_dir(str(tmp_path))
    assert "sub/" in out and "a.txt" in out  # 目录带 / 后缀


def test_list_dir_missing_path() -> None:
    assert "不存在" in builtin.list_dir("/no/such/path-feibot-test")


def test_shell_reports_nonzero_exit() -> None:
    assert "exit code: 3" in builtin.shell("exit 3")


def test_shell_empty_command_rejected() -> None:
    assert "缺少" in builtin.shell("")


# ---- engine 集成:白名单门控 + 工具循环 ----


def _add_model() -> None:
    registry.add_model(
        name="gpt5",
        protocol="openai_chat_completions",
        model="gpt-5",
        base_url="https://api.openai.com/v1",
        api_key="sk-test-1234567890",
    )


def _fake_complete_factory(calls: list):
    """按上下文决定输出:无工具说明→普通回复;有说明→先调用后作答。"""

    def fake_complete(self, request):
        calls.append(request)
        msgs = request["messages"]
        # 精确判断:工具说明是含"可用工具:"的 system 消息;
        # 真实结果消息以"工具结果:"开头(提示词里提到该词不算)
        has_tools_prompt = any(
            "可用工具:" in str(m.get("content", "")) for m in msgs
        )
        has_tool_result = any(
            str(m.get("content", "")).startswith("工具结果:") for m in msgs
        )
        if not has_tools_prompt:
            content = "我没法获取实时信息。"
        elif has_tool_result:
            content = "现在是晚上八点。"
        else:
            content = '{"tool": "current_time", "args": {}}'
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    return fake_complete


def test_admin_triggers_tool_loop(monkeypatch) -> None:
    """白名单会话:注入工具说明,模型先调用工具,拿结果后作答。"""
    _add_model()
    monkeypatch.setattr(config, "TOOL_ADMIN_CONV_KEYS", frozenset({"conv-admin"}))
    calls: list = []
    monkeypatch.setattr(ProtocolLLMClient, "complete", _fake_complete_factory(calls))
    monkeypatch.setattr(engine, "distill_memory", lambda *a, **kw: {})

    reply = engine.handle_message("conv-admin", "现在几点了")
    assert reply == "现在是晚上八点。"
    assert len(calls) == 2  # 第一次触发调用,第二次基于结果作答
    # 第一次请求里带了工具说明
    first_msgs = calls[0]["messages"]
    assert any("可用工具" in str(m.get("content", "")) for m in first_msgs)
    # 历史只落用户消息与最终回复,工具往返过程不入历史
    session_id = store.get_or_create_session("conv-admin")
    history = store.recent_messages(session_id)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[-1]["content"] == "现在是晚上八点。"


def test_non_admin_gets_no_tools(monkeypatch) -> None:
    """非白名单会话:不注入工具说明,模型无从调用,单次生成。"""
    _add_model()
    monkeypatch.setattr(config, "TOOL_ADMIN_CONV_KEYS", frozenset({"conv-admin"}))
    calls: list = []
    monkeypatch.setattr(ProtocolLLMClient, "complete", _fake_complete_factory(calls))
    monkeypatch.setattr(engine, "distill_memory", lambda *a, **kw: {})

    reply = engine.handle_message("conv-stranger", "现在几点了")
    assert reply == "我没法获取实时信息。"
    assert len(calls) == 1  # 没有工具循环
    first_msgs = calls[0]["messages"]
    assert not any("可用工具" in str(m.get("content", "")) for m in first_msgs)

def test_parse_qwen_native_tool_call_format() -> None:
    # qwen 系模型偶尔输出原生 trace 格式,也要能识别成真正的调用
    blob = (
        "<tool_call>\n"
        '{"name": "tool_call", "arguments": {"name": "shell", "arguments": {"command": "echo hi"}}}\n'
        "</tool_call>\n"
    )
    call = parse_tool_call(blob)
    assert call == ToolCall("shell", {"command": "echo hi"})


def test_parse_qwen_tool_result_is_ignored() -> None:
    # 编造的 tool_result 不是调用
    blob = (
        "<tool_call>\n"
        '{"name": "tool_result", "arguments": {"output": "391"}}\n'
        "</tool_call>\n"
    )
    assert parse_tool_call(blob) is None


def test_clean_final_reply_strips_scaffolding() -> None:
    text = (
        "<tool_call>\n"
        '{"name": "tool_call", "arguments": {"name": "shell", "arguments": {"command": "x"}}}\n'
        "</tool_call>\n"
        "<tool_call>\n"
        '{"name": "tool_result", "arguments": {"output": "1"}}\n'
        "</tool_call>\n"
        "答案:391"
    )
    cleaned = engine._clean_final_reply(text)
    assert cleaned == "答案:391"


def test_clean_final_reply_keeps_user_requested_example() -> None:
    # 用户要的 JSON 示例不在 tool_call 标签里,不应被剥
    text = '示例:{"tool": "shell", "args": {}}'
    assert engine._clean_final_reply(text) == text

