from __future__ import annotations

import zipfile
from io import BytesIO

from skill_importer.errors import (
    ERROR_PACKAGE_INVALID,
    ERROR_SKILL_MD_MISSING,
    SkillImporterError,
)
from skill_importer.model import SkillFile

_SKIP_DIR_PARTS = {"__MACOSX", ".git", "node_modules", ".venv", "dist", "build"}


def _clean_package_path(path: str) -> str:
    cleaned = str(path or "").replace("\\", "/").strip().strip("/")
    parts = [part for part in cleaned.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise SkillImporterError("invalid skill file path: " + str(path), code=ERROR_PACKAGE_INVALID)
    return "/".join(parts)


def _find_skill_file(files: list[SkillFile]) -> SkillFile | None:
    return next(
        (file for file in files if file.path.rsplit("/", 1)[-1].lower() == "skill.md"),
        None,
    )


def skill_markdown(files: list[SkillFile]) -> str:
    skill_file = _find_skill_file(files)
    if not skill_file or not skill_file.content.strip():
        raise SkillImporterError("SKILL.md cannot be empty", code=ERROR_SKILL_MD_MISSING)
    return skill_file.content


def normalize_skill_files(
    files: list[SkillFile],
    markdown: str | None = None,
) -> list[SkillFile]:
    if not files:
        if not (markdown or "").strip():
            raise SkillImporterError("skill markdown cannot be empty", code=ERROR_PACKAGE_INVALID)
        encoded = markdown.encode("utf-8")
        return [
            SkillFile(
                path="SKILL.md",
                content=markdown,
                size=len(encoded) + 1,
                mime_type="text/markdown",
            )
        ]
    cleaned: list[SkillFile] = []
    for file in files:
        path = _clean_package_path(file.path)
        content = file.content or ""
        cleaned.append(
            SkillFile(
                path=path,
                content=content,
                size=file.size if file.size is not None else len(content.encode("utf-8")),
                mime_type=file.mime_type,
            )
        )
    skill_file = _find_skill_file(cleaned)
    if not skill_file:
        raise SkillImporterError("skill folder must contain SKILL.md", code=ERROR_SKILL_MD_MISSING)
    base_dir = skill_file.path.rsplit("/", 1)[0] if "/" in skill_file.path else ""
    if not base_dir:
        return cleaned
    prefix = f"{base_dir}/"
    normalized: list[SkillFile] = []
    for file in cleaned:
        if file.path == base_dir or not file.path.startswith(prefix):
            continue
        normalized.append(file.model_copy(update={"path": file.path[len(prefix):]}))
    return normalized


def _skip_package_path(path: str) -> bool:
    parts = path.split("/")
    return any(part in _SKIP_DIR_PARTS for part in parts)


def _guess_mime_type(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".md"):
        return "text/markdown"
    return "text/plain"


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def files_from_zip(
    data: bytes,
    subtree: str = "",
    *,
    max_file_bytes: int,
    max_files: int,
) -> list[SkillFile]:
    normalized_subtree = subtree.strip("/")
    with zipfile.ZipFile(BytesIO(data)) as archive:
        names = [
            name
            for name in archive.namelist()
            if not name.endswith("/") and not _skip_package_path(name)
        ]
        skill_candidates = [
            name for name in names if name.rsplit("/", 1)[-1].lower() == "skill.md"
        ]
        if normalized_subtree:
            skill_candidates = [
                name
                for name in skill_candidates
                if _zip_relative_path(name, normalized_subtree) is not None
            ]
        if not skill_candidates:
            raise SkillImporterError(
                "package does not contain SKILL.md", code=ERROR_SKILL_MD_MISSING
            )
        base = skill_candidates[0].rsplit("/", 1)[0] if "/" in skill_candidates[0] else ""
        files: list[SkillFile] = []
        for name in names:
            if base:
                if not name.startswith(f"{base}/"):
                    continue
                relative = name[len(base) + 1:]
            else:
                relative = name
            if not relative or relative.endswith("/"):
                continue
            info = archive.getinfo(name)
            if info.file_size > max_file_bytes:
                continue
            if len(files) >= max_files:
                break
            content = _decode_text(archive.read(name))
            files.append(
                SkillFile(
                    path=relative,
                    content=content,
                    size=info.file_size,
                    mime_type=_guess_mime_type(relative),
                )
            )
    return files


def _zip_relative_path(name: str, subtree: str) -> str | None:
    parts = name.split("/")
    for index in range(1, len(parts)):
        candidate = "/".join(parts[index:])
        if candidate == subtree or candidate.startswith(f"{subtree}/"):
            return candidate
    return None


# 公开别名（供宿主/其它模块引用）
clean_package_path = _clean_package_path
