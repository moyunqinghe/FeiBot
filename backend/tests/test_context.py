"""assemble_context:system 提示 -> USER.md 画像 -> 新用户消息。"""

from __future__ import annotations

from app.agent import profile
from app.agent.context.assemble import assemble_context


def _point_user_md(monkeypatch, tmp_path, text: str | None = None):
    """把 USER.md 指向临时路径;text 为 None 时文件不存在(触发自动建模板)。"""
    path = tmp_path / "USER.md"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(profile, "USER_MD_PATH", path)
    return path


def test_missing_file_creates_template_and_not_injected(monkeypatch, tmp_path) -> None:
    path = _point_user_md(monkeypatch, tmp_path)
    messages = assemble_context("s1", "你好")
    assert path.exists()  # 首次运行自动创建模板
    # 模板未填写实质内容 → 不注入画像,只有 system 提示 + 用户消息
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "你好"}
    assert all("用户画像" not in m["content"] for m in messages)


def test_filled_profile_injected_as_system(monkeypatch, tmp_path) -> None:
    _point_user_md(monkeypatch, tmp_path, "# USER.md\n\n## 称呼\n\n老莫\n")
    messages = assemble_context("s1", "你好")
    assert len(messages) == 3
    assert messages[0]["role"] == "system"  # prompts/system.md
    assert messages[1]["role"] == "system"
    assert messages[1]["content"].startswith("以下是用户画像,请在回复中参考:")
    assert "老莫" in messages[1]["content"]
    assert messages[2] == {"role": "user", "content": "你好"}


def test_comment_only_file_not_injected(monkeypatch, tmp_path) -> None:
    _point_user_md(monkeypatch, tmp_path, "# USER.md\n<!-- 只有注释 -->\n## 称呼\n")
    messages = assemble_context("s1", "你好")
    assert all("用户画像" not in m["content"] for m in messages)
