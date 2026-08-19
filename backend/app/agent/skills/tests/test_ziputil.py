import httpx
import pytest
from _helpers import make_zip

from skill_importer import (
    ERROR_PACKAGE_INVALID,
    ERROR_SKILL_MD_MISSING,
    ERROR_TOO_LARGE,
    SkillFile,
    SkillImporterError,
    normalize_skill_files,
    skill_markdown,
)
from skill_importer.ziputil import files_from_zip


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


def test_files_from_zip_non_zip_bytes_maps_to_package_invalid() -> None:
    """非 zip 字节必须映射为 SkillImporterError，不得泄漏裸 BadZipFile。"""
    with pytest.raises(SkillImporterError) as exc:
        files_from_zip(b"\x1f\x8b\x08\x00not a zip", max_file_bytes=1024, max_files=100)
    assert exc.value.code == ERROR_PACKAGE_INVALID


def test_import_zip_content_type_non_zip_bytes_maps_to_package_invalid(
    make_importer,
) -> None:
    """content-type 含 zip 但 body 非 zip 时，协议层仍应抛 SkillImporterError。"""
    importer = make_importer(
        {
            "https://example.com/skill.bin": httpx.Response(
                200,
                content=b"\x1f\x8b\x08\x00not a zip",
                headers={"content-type": "application/x-zip-compressed-extra"},
            )
        }
    )
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("https://example.com/skill.bin")
    assert exc.value.code == ERROR_PACKAGE_INVALID


def test_normalize_drops_sibling_of_skill_root() -> None:
    files = [
        SkillFile(path="a/b/SKILL.md", content="# ok\n"),
        SkillFile(path="a/b-c/secret.md", content="x"),
    ]
    normalized = normalize_skill_files(files)
    assert [file.path for file in normalized] == ["SKILL.md"]


def test_files_from_zip_total_limit_raises_too_large_not_skill_md_missing() -> None:
    """超 total 上限必须抛 TOO_LARGE，不得静默丢弃 SKILL.md 后误报缺失。"""
    data = make_zip(
        {
            "pkg/assets/big.bin": "z" * 1200,
            "pkg/SKILL.md": "# ok\n",
        }
    )
    with pytest.raises(SkillImporterError) as exc:
        files_from_zip(data, max_file_bytes=2048, max_files=10, max_total_bytes=1000)
    assert exc.value.code == ERROR_TOO_LARGE


def test_files_from_zip_max_files_raises_too_large() -> None:
    data = make_zip(
        {
            "pkg/SKILL.md": "# ok\n",
            "pkg/a.py": "a",
            "pkg/b.py": "b",
        }
    )
    with pytest.raises(SkillImporterError) as exc:
        files_from_zip(data, max_file_bytes=1024, max_files=1)
    assert exc.value.code == ERROR_TOO_LARGE


def test_files_from_zip_skill_md_survives_when_later_files_skip() -> None:
    """SKILL.md 之后的大文件按 continue 跳过，SKILL.md 不受影响。"""
    data = make_zip(
        {
            "pkg/SKILL.md": "# ok\n",
            "pkg/big.bin": "z" * 2048,
            "pkg/small.py": "s",
        }
    )
    files = files_from_zip(data, max_file_bytes=1024, max_files=10)
    assert [file.path for file in files] == ["SKILL.md", "small.py"]
