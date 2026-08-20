"""Skill 运行时目录配置与发现行为。"""

from __future__ import annotations

from app import config
from app.agent.skills import loader


def test_skills_dir_is_under_runtime_data_dir() -> None:
    assert config.SKILLS_DIR == config.DATA_DIR / "skills"


def test_list_skills_returns_empty_when_runtime_directory_is_missing(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(loader, "SKILLS_DIR", tmp_path / "missing")

    assert loader.list_skills() == []


def test_list_skills_only_discovers_directories_with_skill_markdown(
    monkeypatch, tmp_path
) -> None:
    skills_dir = tmp_path / "skills"
    (skills_dir / "weather").mkdir(parents=True)
    (skills_dir / "weather" / "SKILL.md").write_text("# weather\n", encoding="utf-8")
    (skills_dir / "missing-manifest").mkdir()
    (skills_dir / "plain-file").write_text("not a skill\n", encoding="utf-8")
    monkeypatch.setattr(loader, "SKILLS_DIR", skills_dir)

    assert loader.list_skills() == ["weather"]


def test_list_skills_returns_names_in_sorted_order(monkeypatch, tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    for name in ("zeta", "alpha"):
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    monkeypatch.setattr(loader, "SKILLS_DIR", skills_dir)

    assert loader.list_skills() == ["alpha", "zeta"]
