import dataclasses

import pytest

from skill_importer import SkillFile, SkillPackage


def test_skill_file_defaults() -> None:
    file = SkillFile(path="SKILL.md", content="# hi")
    assert file.size is None
    assert file.mime_type is None


def test_skill_package_is_frozen() -> None:
    pkg = SkillPackage(files=(), skill_markdown="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        pkg.skill_markdown = "y"  # type: ignore[misc]
