"""Skill 运行时目录配置与发现行为。"""

from __future__ import annotations

from app import config


def test_skills_dir_is_under_runtime_data_dir() -> None:
    assert config.SKILLS_DIR == config.DATA_DIR / "skills"
