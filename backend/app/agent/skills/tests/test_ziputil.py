import pytest

from skill_importer import (
    ERROR_PACKAGE_INVALID,
    ERROR_SKILL_MD_MISSING,
    SkillFile,
    SkillImporterError,
    normalize_skill_files,
    skill_markdown,
)
from skill_importer.ziputil import files_from_zip

from conftest import make_zip


def test_normalize_single_markdown_becomes_skill_md() -> None:
    files = normalize_skill_files([], markdown="# hello")
    assert files == [SkillFile(path="SKILL.md", content="# hello", size=7, mime_type="text/markdown")]


def test_normalize_requires_content_when_no_files() -> None:
    with pytest.raises(SkillImporterError) as exc:
        normalize_skill_files([], markdown="  ")
    assert exc.value.code == ERROR_PACKAGE_INVALID


def test_normalize_strips_skill_folder_prefix() -> None:
    files = [
        SkillFile(path="skill-pack-main/weather/SKILL.md", content="---\nname: 天气包\n---\n"),
        SkillFile(path="skill-pack-main/weather/scripts/run.py", content="print('ok')\n"),
    ]
    normalized = normalize_skill_files(files)
    assert [file.path for file in normalized] == ["SKILL.md", "scripts/run.py"]


def test_normalize_rejects_missing_skill_md() -> None:
    with pytest.raises(SkillImporterError) as exc:
        normalize_skill_files([SkillFile(path="a.md", content="x")])
    assert exc.value.code == ERROR_SKILL_MD_MISSING


def test_normalize_rejects_path_traversal() -> None:
    with pytest.raises(SkillImporterError) as exc:
        normalize_skill_files([SkillFile(path="../evil", content="x"), SkillFile(path="SKILL.md", content="y")])
    assert exc.value.code == ERROR_PACKAGE_INVALID


def test_skill_markdown_returns_skill_md_content() -> None:
    md = skill_markdown([SkillFile(path="SKILL.md", content="# hi")])
    assert md == "# hi"


def test_files_from_zip_strips_common_root() -> None:
    data = make_zip(
        {
            "skill-pack-main/weather/SKILL.md": "---\nname: 天气包\n---\n",
            "skill-pack-main/weather/scripts/run.py": "print('ok')\n",
            "skill-pack-main/weather/data/cities.json": '{"北京": "101010100"}',
        }
    )
    files = files_from_zip(data, max_file_bytes=1024, max_files=100)
    assert [file.path for file in files] == ["SKILL.md", "scripts/run.py", "data/cities.json"]


def test_files_from_zip_with_subtree() -> None:
    data = make_zip(
        {
            "repo-main/weather/SKILL.md": "# weather\n",
            "repo-main/weather/tools/a.py": "a",
            "repo-main/other/b.py": "b",
        }
    )
    files = files_from_zip(data, subtree="weather", max_file_bytes=1024, max_files=100)
    assert [file.path for file in files] == ["SKILL.md", "tools/a.py"]


def test_files_from_zip_missing_skill_md() -> None:
    data = make_zip({"repo-main/readme.md": "no skill here"})
    with pytest.raises(SkillImporterError) as exc:
        files_from_zip(data, max_file_bytes=1024, max_files=100)
    assert exc.value.code == ERROR_SKILL_MD_MISSING


def test_files_from_zip_skips_bad_dirs_and_respects_limits() -> None:
    data = make_zip(
        {
            "pkg/__MACOSX/SKILL.md": "x",
            "pkg/.git/config": "x",
            "pkg/SKILL.md": "# ok\n",
            "pkg/big.txt": "z" * 2048,
            "pkg/extra.py": "y",
        }
    )
    files = files_from_zip(data, max_file_bytes=1024, max_files=10)
    assert [file.path for file in files] == ["SKILL.md", "extra.py"]


def test_files_from_zip_rejects_zip_slip() -> None:
    data = make_zip({"SKILL.md": "# ok\n", "../escape.md": "x"})
    with pytest.raises(SkillImporterError) as exc:
        files_from_zip(data, max_file_bytes=1024, max_files=100)
    assert exc.value.code == ERROR_PACKAGE_INVALID


def test_normalize_drops_sibling_of_skill_root() -> None:
    files = [
        SkillFile(path="a/b/SKILL.md", content="# ok\n"),
        SkillFile(path="a/b-c/secret.md", content="x"),
    ]
    normalized = normalize_skill_files(files)
    assert [file.path for file in normalized] == ["SKILL.md"]
