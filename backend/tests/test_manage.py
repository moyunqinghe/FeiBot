"""manage CLI 冒烟:空表 list、add 后 list 脱敏、use/remove。"""

from __future__ import annotations

from app.llm import manage, registry


def test_list_empty_friendly(capsys) -> None:
    manage.main(["list"])
    out = capsys.readouterr().out
    assert "无模型配置" in out


def test_add_then_list_masked(capsys) -> None:
    manage.main([
        "add", "--name", "c1", "--protocol", "anthropic_messages",
        "--model", "claude-sonnet-4-5", "--base-url", "https://api.anthropic.com",
        "--api-key", "sk-ant-secretkey-99",
    ])
    manage.main(["list"])
    out = capsys.readouterr().out
    assert "* c1" in out  # 首个配置自动为当前
    assert "claude-sonnet-4-5" in out
    assert "sk-ant-secretkey-99" not in out  # 不明文展示
    assert "y-99" in out  # 只留后 4 位


def test_use_and_remove(capsys) -> None:
    manage.main([
        "add", "--name", "c1", "--protocol", "openai_responses",
        "--model", "gpt-5", "--base-url", "https://api.openai.com/v1",
        "--api-key", "sk-x",
    ])
    capsys.readouterr()
    manage.main(["use", "不存在"])
    assert "没有名为" in capsys.readouterr().out
    manage.main(["remove", "c1"])
    assert registry.list_models() == []
