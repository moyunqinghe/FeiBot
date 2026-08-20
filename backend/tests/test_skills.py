"""Skill 运行时目录配置与发现行为。"""

from __future__ import annotations

from pathlib import Path

from app import config
from app.agent.skills import loader


def test_skills_dir_is_under_runtime_data_dir() -> None:
    assert config.SKILLS_DIR == config.DATA_DIR / "skills"


def test_list_skills_returns_empty_when_directory_is_missing(tmp_path) -> None:
    assert loader.list_skills(tmp_path / "missing") == []


def test_list_skills_only_discovers_directories_with_skill_markdown(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    (skills_dir / "weather").mkdir(parents=True)
    (skills_dir / "weather" / "SKILL.md").write_text("# weather\n", encoding="utf-8")
    (skills_dir / "missing-manifest").mkdir()
    (skills_dir / "plain-file").write_text("not a skill\n", encoding="utf-8")

    assert loader.list_skills(skills_dir) == ["weather"]


def test_list_skills_returns_names_in_sorted_order(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    for name in ("zeta", "alpha"):
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")

    assert loader.list_skills(skills_dir) == ["alpha", "zeta"]


def test_skills_base_has_no_host_imports() -> None:
    """架构守卫:基座目录(app/agent/skills/)禁止出现 app.* 导入。"""
    base_dir = Path(loader.__file__).resolve().parent
    offenders: list[str] = []
    for path in sorted(base_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("from app.", "import app.")):
                offenders.append(f"{path.relative_to(base_dir)}: {stripped}")
    assert offenders == []
