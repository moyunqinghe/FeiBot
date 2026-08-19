from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

from skill_importer.errors import (
    ERROR_CONNECT_FAILED,
    ERROR_HTTP_ERROR,
    ERROR_SKILL_MD_MISSING,
    ERROR_SOURCE_INVALID,
    SkillImporterError,
)
from skill_importer.model import SkillFile
from skill_importer.ziputil import (
    _clean_package_path,
    _decode_text,
    _find_skill_file,
    _guess_mime_type,
    _skip_package_path,
    files_from_zip,
)

if TYPE_CHECKING:
    from skill_importer.resolver import _Config, _Http

RAW_GITHUB_HOST = "raw.githubusercontent.com"


def load_github_source(parsed, *, http: _Http, config: _Config) -> list[SkillFile]:
    """Handle a github.com / raw.githubusercontent.com URL (a urlparse result)."""
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if parsed.netloc == RAW_GITHUB_HOST:
        if len(parts) < 4:
            raise SkillImporterError(
                "raw GitHub source must include owner, repo, branch and path",
                code=ERROR_SOURCE_INVALID,
            )
        owner, repo, branch = parts[0], parts[1], parts[2]
        file_path = "/".join(parts[3:])
        data, content_type = http.download(parsed.geturl())
        return [
            SkillFile(
                path=file_path.rsplit("/", 1)[-1] or "SKILL.md",
                content=_decode_text(data),
                size=len(data),
                mime_type=content_type or "text/markdown",
            )
        ]
    if len(parts) < 2:
        raise SkillImporterError(
            "GitHub source must include owner and repository", code=ERROR_SOURCE_INVALID
        )
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if len(parts) >= 3 and parts[2] == "archive":
        data, _ = http.download(parsed.geturl())
        return files_from_zip(
            data,
            max_file_bytes=config.max_file_bytes,
            max_files=config.max_files,
        )
    # blob/raw/tree 后的分支名可能含 "/"（如 feature/x）；URL 本身有歧义，
    # 先查仓库分支列表，命中即按真实分支解析，API 不可用时退回单段猜测。
    if len(parts) >= 5 and parts[2] in {"blob", "raw", "tree"}:
        branch, remainder = _resolve_github_branch(http, parts)
        if parts[2] == "tree":
            return _download_github_directory(http, config, owner, repo, branch, remainder)
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{remainder}"
        data, content_type = http.download(raw_url)
        return [
            SkillFile(
                path=remainder.rsplit("/", 1)[-1] or "SKILL.md",
                content=_decode_text(data),
                size=len(data),
                mime_type=content_type or "text/markdown",
            )
        ]
    subtree = "/".join(parts[2:]) if len(parts) > 2 else ""
    errors: list[SkillImporterError] = []
    for branch in ["main", "master"]:
        try:
            return _download_github_directory(http, config, owner, repo, branch, subtree)
        except SkillImporterError as exc:
            if exc.code == ERROR_SKILL_MD_MISSING:
                raise
            errors.append(exc)
    return _download_github_archive(http, config, owner, repo, ["main", "master"], subtree)


def _resolve_github_branch(http: _Http, parts: list[str]) -> tuple[str, str]:
    """Resolve branch/path ambiguity after blob/raw/tree.

    GitHub branches may contain "/" (e.g. feature/x). Query the repo's branch
    list once and pick the longest matching branch prefix; when the API is
    unavailable, fall back to the legacy single-segment guess parts[3].
    """
    segments = parts[3:]
    branches = _list_github_branches(http, parts[0], parts[1].removesuffix(".git"))
    if branches:
        for split_at in range(len(segments), 1, -1):
            candidate = "/".join(segments[:split_at])
            remainder = "/".join(segments[split_at:])
            if remainder and candidate in branches:
                return candidate, remainder
        if segments[0] in branches:
            return segments[0], "/".join(segments[1:])
        # 无匹配分支：仍按旧猜测继续，让后续请求给出真实错误码
    return segments[0], "/".join(segments[1:])


def _list_github_branches(http: _Http, owner: str, repo: str) -> frozenset[str]:
    api_url = (
        f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}"
        "/branches?per_page=100"
    )
    try:
        payload = http.download_json(api_url)
    except SkillImporterError:
        return frozenset()
    entries = payload if isinstance(payload, list) else [payload]
    names = [
        str(entry.get("name") or "")
        for entry in entries
        if isinstance(entry, dict)
    ]
    return frozenset(name for name in names if name)


def _download_github_directory(
    http: _Http,
    config: _Config,
    owner: str,
    repo: str,
    branch: str,
    subtree: str = "",
) -> list[SkillFile]:
    try:
        return _download_github_directory_contents(http, config, owner, repo, branch, subtree)
    except SkillImporterError as api_error:
        if api_error.code == ERROR_SKILL_MD_MISSING:
            raise
        try:
            return _download_github_archive(http, config, owner, repo, [branch], subtree)
        except SkillImporterError:
            raise api_error


def _download_github_directory_contents(
    http: _Http,
    config: _Config,
    owner: str,
    repo: str,
    branch: str,
    subtree: str = "",
) -> list[SkillFile]:
    normalized_subtree = subtree.strip("/")
    files: list[SkillFile] = []
    visited_dirs: set[str] = set()

    def walk(path: str) -> None:
        if len(files) >= config.max_files:
            return
        if path in visited_dirs:
            return
        visited_dirs.add(path)
        api_path = quote(path, safe="/")
        api_url = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/contents"
        if api_path:
            api_url = f"{api_url}/{api_path}"
        api_url = f"{api_url}?ref={quote(branch, safe='')}"
        payload = http.download_json(api_url)
        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            if len(files) >= config.max_files:
                break
            if not isinstance(entry, dict):
                continue
            item_type = str(entry.get("type") or "")
            item_path = str(entry.get("path") or "").strip("/")
            if not item_path or _skip_package_path(item_path):
                continue
            if item_type == "dir":
                walk(item_path)
                continue
            if item_type != "file":
                continue
            size = int(entry.get("size") or 0)
            if size > config.max_file_bytes:
                continue
            download_url = str(entry.get("download_url") or "")
            if not download_url:
                continue
            relative = item_path
            if normalized_subtree and item_path.startswith(f"{normalized_subtree}/"):
                relative = item_path[len(normalized_subtree) + 1:]
            data, content_type = http.download(download_url)
            if len(data) > config.max_file_bytes:
                continue
            files.append(
                SkillFile(
                    path=_clean_package_path(relative),
                    content=_decode_text(data),
                    size=len(data),
                    mime_type=content_type or _guess_mime_type(relative),
                )
            )

    walk(normalized_subtree)
    if not _find_skill_file(files):
        raise SkillImporterError(
            "GitHub directory does not contain SKILL.md", code=ERROR_SKILL_MD_MISSING
        )
    return files


def _download_github_archive(
    http: _Http,
    config: _Config,
    owner: str,
    repo: str,
    branches: list[str],
    subtree: str = "",
) -> list[SkillFile]:
    errors: list[SkillImporterError] = []
    for branch in branches:
        archive_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
        try:
            data, _ = http.download(archive_url)
            return files_from_zip(
                data,
                subtree=subtree,
                max_file_bytes=config.max_file_bytes,
                max_files=config.max_files,
            )
        except SkillImporterError as exc:
            errors.append(exc)
    detail = "; ".join(str(exc) for exc in errors)
    codes = [exc.code for exc in errors]
    aggregate_code = (
        ERROR_HTTP_ERROR
        if ERROR_HTTP_ERROR in codes
        else ERROR_CONNECT_FAILED
        if ERROR_CONNECT_FAILED in codes
        else (codes[0] if codes else ERROR_CONNECT_FAILED)
    )
    raise SkillImporterError(
        f"unable to download GitHub skill package: {detail}", code=aggregate_code
    )
