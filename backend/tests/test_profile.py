"""profile:USER.md 的自动维护(白名单写入、值清洗、重置、摘要)。"""

from __future__ import annotations

from app.agent import profile


def test_first_update_creates_file_and_writes_section(tmp_path) -> None:
    path = tmp_path / "USER.md"  # conftest 已把 USER_MD_PATH 指过来
    applied = profile.update_profile({"称呼": "飞飞"})
    assert applied == {"称呼": "飞飞"}
    assert path.exists()
    assert profile.profile_summary() == {"称呼": "飞飞"}
    assert "## 角色" in path.read_text(encoding="utf-8")  # 骨架小节仍在


def test_update_replaces_existing_value(tmp_path) -> None:
    profile.update_profile({"称呼": "老莫"})
    profile.update_profile({"称呼": "飞飞"})
    text = (tmp_path / "USER.md").read_text(encoding="utf-8")
    assert "飞飞" in text and "老莫" not in text
    assert text.count("## 称呼") == 1  # 不重复追加小节


def test_whitelist_filters_unknown_sections(tmp_path) -> None:
    applied = profile.update_profile({"星座": "天蝎", "爱好": "爬山"})
    assert applied == {}
    assert profile.profile_summary() == {}


def test_value_sanitized_single_line(tmp_path) -> None:
    # 多行/标题注入被压成单行,不能破坏文件结构
    profile.update_profile({"工作习惯": "深夜工作\n## 注入的小节\n继续"})
    text = (tmp_path / "USER.md").read_text(encoding="utf-8")
    summary = profile.profile_summary()
    assert "深夜工作" in summary["工作习惯"]
    assert "\n" not in summary["工作习惯"]  # 值被单行化
    # "## 注入的小节" 被压进行内,不再是行首标题:小节总数仍是骨架的 5 个
    headings = [ln for ln in text.splitlines() if ln.startswith("## ")]
    assert len(headings) == 5
    assert not any(ln.startswith("## 注入的小节") for ln in headings)


def test_manual_edit_preserved_until_overwritten(tmp_path) -> None:
    profile.update_profile({"称呼": "飞飞"})
    # 用户手改"角色"小节
    path = tmp_path / "USER.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("## 角色\n", "## 角色\n\n独立开发者\n"),
        encoding="utf-8",
    )
    profile.update_profile({"时区": "Asia/Shanghai"})  # 更新别的小节
    summary = profile.profile_summary()
    assert summary == {"称呼": "飞飞", "角色": "独立开发者", "时区": "Asia/Shanghai"}


def test_reset_profile(tmp_path) -> None:
    profile.update_profile({"称呼": "飞飞", "时区": "UTC+8"})
    profile.reset_profile()
    assert profile.profile_summary() == {}
    assert profile.load_user_profile() == ""  # 骨架不注入


def test_empty_and_comment_only_profile_not_loaded(tmp_path) -> None:
    assert profile.load_user_profile() == ""  # 首次:自动建骨架且不注入
    path = tmp_path / "USER.md"
    path.write_text("# USER.md\n<!-- 只有注释 -->\n## 称呼\n", encoding="utf-8")
    assert profile.load_user_profile() == ""
