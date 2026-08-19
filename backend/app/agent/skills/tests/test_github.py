import httpx
import pytest

from skill_importer import (
    ERROR_GITHUB_API_ERROR,
    ERROR_HTTP_ERROR,
    ERROR_SKILL_MD_MISSING,
    SkillImporterError,
)

from conftest import files_dict, make_zip


def test_github_blob_url_downloads_raw(make_importer) -> None:
    importer = make_importer(
        {
            "https://raw.githubusercontent.com/owner/repo/main/skills/x/SKILL.md": "# blob\n"
        }
    )
    pkg = importer.import_skill("https://github.com/owner/repo/blob/main/skills/x/SKILL.md")
    assert files_dict(pkg) == {"SKILL.md": "# blob\n"}


def test_github_raw_host_single_file(make_importer) -> None:
    importer = make_importer(
        {"https://raw.githubusercontent.com/owner/repo/main/SKILL.md": "# raw\n"}
    )
    pkg = importer.import_skill(
        "https://raw.githubusercontent.com/owner/repo/main/SKILL.md"
    )
    assert files_dict(pkg) == {"SKILL.md": "# raw\n"}


def test_github_tree_directory_via_api(make_importer) -> None:
    importer = make_importer(
        {
            "https://api.github.com/repos/owner/repo/contents/skills/weather?ref=main": [
                {
                    "type": "file",
                    "path": "skills/weather/SKILL.md",
                    "size": 10,
                    "download_url": "https://raw.githubusercontent.com/owner/repo/main/skills/weather/SKILL.md",
                },
                {
                    "type": "dir",
                    "path": "skills/weather/tools",
                },
            ],
            "https://api.github.com/repos/owner/repo/contents/skills/weather/tools?ref=main": [
                {
                    "type": "file",
                    "path": "skills/weather/tools/run.py",
                    "size": 5,
                    "download_url": "https://raw.githubusercontent.com/owner/repo/main/skills/weather/tools/run.py",
                }
            ],
            "https://raw.githubusercontent.com/owner/repo/main/skills/weather/SKILL.md": "# tree\n",
            "https://raw.githubusercontent.com/owner/repo/main/skills/weather/tools/run.py": "print(1)\n",
        }
    )
    pkg = importer.import_skill("https://github.com/owner/repo/tree/main/skills/weather")
    assert files_dict(pkg) == {"SKILL.md": "# tree\n", "tools/run.py": "print(1)\n"}


def test_github_repo_root_falls_back_to_archive(make_importer) -> None:
    importer = make_importer(
        {
            "https://api.github.com/repos/owner/repo/contents?ref=main": httpx.Response(404),
            "https://github.com/owner/repo/archive/refs/heads/main.zip": make_zip(
                {"repo-main/SKILL.md": "# archive\n"}
            ),
        }
    )
    pkg = importer.import_skill("owner/repo")
    assert pkg.skill_markdown == "# archive\n"


def test_github_directory_without_skill_md_raises(make_importer) -> None:
    importer = make_importer(
        {
            "https://api.github.com/repos/owner/repo/contents?ref=main": [
                {"type": "file", "path": "readme.md", "size": 5,
                 "download_url": "https://raw.githubusercontent.com/owner/repo/main/readme.md"}
            ],
            "https://raw.githubusercontent.com/owner/repo/main/readme.md": "hello",
        }
    )
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("owner/repo")
    assert exc.value.code == ERROR_SKILL_MD_MISSING


def test_github_blob_404_maps_http_error(make_importer) -> None:
    importer = make_importer(
        {
            "https://raw.githubusercontent.com/owner/repo/main/skills/x/SKILL.md": httpx.Response(404)
        }
    )
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("https://github.com/owner/repo/blob/main/skills/x/SKILL.md")
    assert exc.value.code == ERROR_HTTP_ERROR


def test_github_api_invalid_json_maps_to_api_error(make_importer) -> None:
    importer = make_importer(
        {
            "https://api.github.com/repos/owner/repo/contents/skills/weather?ref=main": b"not json at all"
        }
    )
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("https://github.com/owner/repo/tree/main/skills/weather")
    assert exc.value.code == ERROR_GITHUB_API_ERROR
