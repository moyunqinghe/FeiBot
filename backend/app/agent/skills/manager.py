"""skill 生命周期管理(基座层):安装/卸载/启停/列表。

零业务耦合:持久化经 SkillStore 协议注入,落盘目录经构造参数注入,
导入器经 SkillImporter 注入(缺省自建)。宿主负责装配与权限门控。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Protocol

from skill_importer import SkillImporter, SkillPackage

# slug == SKILL.md frontmatter 的 name:小写字母/数字/连字符
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillManagerError(Exception):
    """管理层错误(name 不合规、包路径非法、slug 非法等)。"""


class SkillStore(Protocol):
    """持久化接口,由宿主实现(sqlite/内存/其他均可)。"""

    def upsert(self, slug: str, source: str, source_kind: str, enabled: int) -> None: ...

    def get(self, slug: str) -> dict | None: ...

    def list(self) -> list[dict]: ...

    def delete(self, slug: str) -> bool: ...

    def set_enabled(self, slug: str, enabled: int) -> bool: ...


class SkillManager:
    """已安装 skill 的生命周期:install/uninstall/enable/disable/list。"""

    def __init__(
        self,
        store: SkillStore,
        skills_dir: Path,
        importer: SkillImporter | None = None,
    ) -> None:
        self._store = store
        self._skills_dir = skills_dir
        self._importer = importer if importer is not None else SkillImporter()

    def install(self, source: str) -> str:
        """导入并安装;同 slug 幂等覆盖。返回 slug。

        SkillImporterError(来源/下载问题)与 SkillManagerError(校验/落盘问题)上抛。
        """
        package = self._importer.import_skill(source)
        slug = _validated_slug(package)
        _write_package(self._skills_dir / slug, package)
        self._store.upsert(slug, source, package.source_kind, 1)
        return slug

    def uninstall(self, slug: str) -> bool:
        """卸下:删库记录 + 删目录(宽容,任一存在即 True)。不联网。"""
        _validate_slug_arg(slug)
        existed_db = self._store.delete(slug)
        target = self._skills_dir / slug
        removed_dir = target.is_dir()
        if removed_dir:
            shutil.rmtree(target)
        return existed_db or removed_dir

    def enable(self, slug: str) -> bool:
        """启用:仅翻 enabled 标志。不存在返回 False。"""
        _validate_slug_arg(slug)
        if self._store.get(slug) is None:
            return False
        return self._store.set_enabled(slug, 1)

    def disable(self, slug: str) -> bool:
        """停用:仅翻 enabled 标志,文件保留。不存在返回 False。"""
        _validate_slug_arg(slug)
        if self._store.get(slug) is None:
            return False
        return self._store.set_enabled(slug, 0)

    def list(self) -> list[dict]:
        """列出全部行,附文件系统实况(files_ok / file_count)。"""
        rows = []
        for row in self._store.list():
            target = self._skills_dir / row["slug"]
            files_ok = target.is_dir()
            file_count = (
                sum(1 for path in target.rglob("*") if path.is_file()) if files_ok else 0
            )
            rows.append({**row, "files_ok": files_ok, "file_count": file_count})
        return rows


def _validated_slug(package: SkillPackage) -> str:
    """取 frontmatter name 作 slug;缺失或不合规抛 SkillManagerError。"""
    name = package.metadata.get("name")
    if not isinstance(name, str) or not name:
        raise SkillManagerError(
            "SKILL.md 缺少 frontmatter name,无法安装。"
            "要求:name 为小写字母/数字/连字符组成的 slug(如 daily-ai-news)。"
        )
    if not SKILL_NAME_RE.match(name):
        raise SkillManagerError(
            f"SKILL.md 的 name 不合规:{name!r}。"
            "要求小写字母/数字/连字符(如 daily-ai-news),请修正后重新安装。"
        )
    return name


def _write_package(target: Path, package: SkillPackage) -> None:
    """整目录清除后写入包内文件(避免旧版本残留)。"""
    if target.exists():
        shutil.rmtree(target)
    for file in package.files:
        dest = target / file.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(file.content, encoding="utf-8")


def _validate_slug_arg(slug: str) -> None:
    """uninstall/enable/disable 的入参校验:必须是合规 slug 形式。"""
    if not isinstance(slug, str) or not SKILL_NAME_RE.match(slug):
        raise SkillManagerError(f"非法 slug:{slug!r}(要求小写字母/数字/连字符)")
