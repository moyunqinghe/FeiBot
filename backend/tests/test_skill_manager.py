"""SkillManager(基座层生命周期)的回归测试:全部离线、纯注入、零 monkeypatch。"""

from __future__ import annotations

import pytest
from skill_importer import SkillFile, SkillImporterError, SkillPackage

from app.agent.skills.manager import SkillManager, SkillManagerError


class FakeStore:
    """SkillStore 协议的内存实现(测试夹具)。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def upsert(self, slug: str, source: str, source_kind: str, enabled: int) -> None:
        existing = self.rows.get(slug)
        self.rows[slug] = {
            "slug": slug,
            "source": source,
            "source_kind": source_kind,
            "enabled": enabled,
            "added_at": existing["added_at"] if existing else 0.0,
            "updated_at": 0.0,
        }

    def get(self, slug: str) -> dict | None:
        return self.rows.get(slug)

    def list(self) -> list[dict]:
        return [self.rows[slug] for slug in sorted(self.rows)]

    def delete(self, slug: str) -> bool:
        return self.rows.pop(slug, None) is not None

    def set_enabled(self, slug: str, enabled: int) -> bool:
        row = self.rows.get(slug)
        if row is None:
            return False
        row["enabled"] = enabled
        return True


class FakeImporter:
    """可注入的导入器替身:返回预置包或抛出预置错误。"""

    def __init__(
        self,
        package: SkillPackage | None = None,
        error: Exception | None = None,
    ) -> None:
        self._package = package
        self._error = error

    def import_skill(self, source: str) -> SkillPackage:
        if self._error is not None:
            raise self._error
        assert self._package is not None
        return self._package


def _make_package(
    name: str | None = "daily-ai-news",
    files: list[tuple[str, str]] | None = None,
    source_kind: str = "github",
) -> SkillPackage:
    if files is None:
        files = [("SKILL.md", f"---\nname: {name}\n---\n\n# {name}\n")]
    markdown = next(content for path, content in files if path == "SKILL.md")
    metadata = {} if name is None else {"name": name}
    return SkillPackage(
        files=tuple(SkillFile(path=path, content=content) for path, content in files),
        skill_markdown=markdown,
        metadata=metadata,
        name_hint=name,
        slug_hint=name,
        homepage_hint=None,
        source_kind=source_kind,
    )


def test_install_writes_files_and_records_metadata(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    store_ = FakeStore()
    manager = SkillManager(store_, skills_dir, importer=FakeImporter(_make_package()))

    slug = manager.install("owner/repo")

    assert slug == "daily-ai-news"
    written = skills_dir / "daily-ai-news" / "SKILL.md"
    assert written.is_file()
    assert "name: daily-ai-news" in written.read_text(encoding="utf-8")
    row = store_.get("daily-ai-news")
    assert row is not None
    assert row["source"] == "owner/repo"
    assert row["source_kind"] == "github"
    assert row["enabled"] == 1


def test_install_rejects_missing_frontmatter_name(tmp_path) -> None:
    manager = SkillManager(
        FakeStore(), tmp_path, importer=FakeImporter(_make_package(name=None))
    )

    with pytest.raises(SkillManagerError, match="name"):
        manager.install("owner/repo")
    assert list(tmp_path.iterdir()) == []  # 拒绝前不落任何文件


def test_install_rejects_non_slug_name(tmp_path) -> None:
    for bad in ("每日AI简报", "Daily-AI-News", "has space", ""):
        package = SkillPackage(
            files=(SkillFile(path="SKILL.md", content="x"),),
            skill_markdown="x",
            metadata={"name": bad},
            name_hint=bad or None,
            slug_hint=None,
            homepage_hint=None,
            source_kind="github",
        )
        manager = SkillManager(FakeStore(), tmp_path, importer=FakeImporter(package))
        with pytest.raises(SkillManagerError):
            manager.install("owner/repo")


def test_install_propagates_importer_error(tmp_path) -> None:
    error = SkillImporterError("boom", code="HTTP_ERROR")
    manager = SkillManager(FakeStore(), tmp_path, importer=FakeImporter(error=error))

    with pytest.raises(SkillImporterError):
        manager.install("owner/repo")


def test_reinstall_same_slug_replaces_stale_files(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    store_ = FakeStore()
    v1 = _make_package(files=[
        ("SKILL.md", "---\nname: daily-ai-news\n---\n\n# v1\n"),
        ("scripts/old.py", "print('old')\n"),
    ])
    v2 = _make_package(files=[("SKILL.md", "---\nname: daily-ai-news\n---\n\n# v2\n")])

    SkillManager(store_, skills_dir, importer=FakeImporter(v1)).install("owner/repo")
    slug = SkillManager(store_, skills_dir, importer=FakeImporter(v2)).install("owner/repo")

    assert slug == "daily-ai-news"
    assert not (skills_dir / "daily-ai-news" / "scripts" / "old.py").exists()
    assert "# v2" in (skills_dir / "daily-ai-news" / "SKILL.md").read_text(encoding="utf-8")
