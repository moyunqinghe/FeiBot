from __future__ import annotations

import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from html import unescape
from urllib.parse import quote, unquote, urljoin, urlparse

import httpx

from skill_importer.errors import (
    ERROR_CONNECT_FAILED,
    ERROR_GITHUB_API_ERROR,
    ERROR_HTML_NOT_SKILL,
    ERROR_HTTP_ERROR,
    ERROR_REDIRECT_LOOP,
    ERROR_SOURCE_INVALID,
    ERROR_TIMEOUT,
    ERROR_TOO_LARGE,
    SkillImporterError,
)
from skill_importer.github import load_github_source
from skill_importer.metadata import metadata_text, parse_skill_metadata, slugify, source_name
from skill_importer.model import SkillFile, SkillPackage
from skill_importer.ziputil import (
    _clean_package_path,
    _decode_text,
    files_from_zip,
    normalize_skill_files,
    skill_markdown,
)

DEFAULT_GITHUB_HOSTS = frozenset({"github.com", "www.github.com", "raw.githubusercontent.com"})
DEFAULT_PLATFORM_HOSTS = frozenset(
    {"clawhub.ai", "www.clawhub.ai", "skillhub.ai", "www.skillhub.ai"}
)
DEFAULT_PLATFORM_DOWNLOAD_ENDPOINT = "https://wry-manatee-359.convex.site/api/v1/download"
RAW_GITHUB_HOST = "raw.githubusercontent.com"


@dataclass(frozen=True)
class _Config:
    timeout_seconds: float
    user_agent: str
    github_hosts: frozenset[str]
    platform_hosts: frozenset[str]
    platform_download_endpoint: str
    max_package_bytes: int
    max_file_bytes: int
    max_files: int
    max_indirections: int


class _Http:
    """Thin httpx wrapper that raises SkillImporterError and enforces limits."""

    def __init__(self, client: httpx.Client, config: _Config) -> None:
        self._client = client
        self._config = config

    MAX_REDIRECTS = 10

    def download(self, url: str) -> tuple[bytes, str]:
        current = url
        for _ in range(self.MAX_REDIRECTS + 1):
            _assert_public_target(current)
            try:
                response = self._client.get(
                    current,
                    headers={"User-Agent": self._config.user_agent},
                    follow_redirects=False,
                )
            except httpx.TimeoutException as exc:
                raise SkillImporterError("download timed out", code=ERROR_TIMEOUT, cause=exc) from exc
            except httpx.TransportError as exc:
                raise SkillImporterError(
                    f"download failed: {exc}", code=ERROR_CONNECT_FAILED, cause=exc
                ) from exc
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise SkillImporterError("redirect without location", code=ERROR_HTTP_ERROR)
                current = urljoin(current, location)
                continue
            if response.status_code >= 400:
                raise SkillImporterError(
                    f"download failed with HTTP {response.status_code}", code=ERROR_HTTP_ERROR
                )
            data = response.content
            if len(data) > self._config.max_package_bytes:
                raise SkillImporterError("skill package is too large", code=ERROR_TOO_LARGE)
            return data, response.headers.get("content-type", "")
        raise SkillImporterError("too many redirects", code=ERROR_REDIRECT_LOOP)

    def download_json(self, url: str) -> object:
        data, _ = self.download(url)
        try:
            return json.loads(_decode_text(data))
        except json.JSONDecodeError as exc:
            raise SkillImporterError(
                "GitHub API returned invalid JSON", code=ERROR_GITHUB_API_ERROR, cause=exc
            ) from exc


class SkillImporter:
    """Pure-protocol client: resolve a skill package from a remote source string.

    Supports: open-platform slug / URL, GitHub repo/tree/blob/raw/archive,
    raw SKILL.md, zip URL, and owner/repo shorthand. All parameters are
    optional and explicit; ``transport`` enables offline testing via
    ``httpx.MockTransport``.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 120.0,
        user_agent: str = "skill-importer/1.0",
        github_hosts: frozenset[str] = DEFAULT_GITHUB_HOSTS,
        platform_hosts: frozenset[str] = DEFAULT_PLATFORM_HOSTS,
        platform_download_endpoint: str = DEFAULT_PLATFORM_DOWNLOAD_ENDPOINT,
        max_package_bytes: int = 96 * 1024 * 1024,
        max_file_bytes: int = 2 * 1024 * 1024,
        max_files: int = 240,
        max_indirections: int = 5,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = _Config(
            timeout_seconds=timeout_seconds,
            user_agent=user_agent,
            github_hosts=github_hosts,
            platform_hosts=platform_hosts,
            platform_download_endpoint=platform_download_endpoint,
            max_package_bytes=max_package_bytes,
            max_file_bytes=max_file_bytes,
            max_files=max_files,
            max_indirections=max_indirections,
        )
        self._client = httpx.Client(
            timeout=timeout_seconds, follow_redirects=True, transport=transport
        )
        self._http = _Http(self._client, self._config)

    def close(self) -> None:
        self._client.close()

    def import_skill(self, source: str) -> SkillPackage:
        cleaned = _required_text(source)
        slug = self._clawhub_slug_from_source(cleaned)
        if slug:
            source_url = cleaned if cleaned.startswith(("http://", "https://")) else None
            files = self._load_clawhub_skill_package(slug, source_url=source_url)
            return self._to_package(files, source_kind="platform", source=cleaned)
        if cleaned.startswith(("http://", "https://")):
            files = self._load_remote(cleaned)
            source_kind = self._source_kind_for_url(cleaned)
            return self._to_package(files, source_kind=source_kind, source=cleaned)
        if _looks_like_github_shorthand(cleaned):
            remote = f"https://github.com/{cleaned}"
            files = self._load_remote(remote)
            return self._to_package(files, source_kind="github", source=cleaned)
        raise SkillImporterError(
            "source must be a platform slug, GitHub URL, raw SKILL.md URL, "
            "zip URL, or owner/repo path",
            code=ERROR_SOURCE_INVALID,
        )

    def _to_package(
        self,
        files: list[SkillFile],
        *,
        source_kind: str,
        source: str,
    ) -> SkillPackage:
        files = normalize_skill_files(files)
        markdown = skill_markdown(files)
        metadata = parse_skill_metadata(markdown)
        name_hint = metadata_text(metadata, "name", "title") or source_name(source)
        slug_hint = (
            metadata_text(metadata, "slug", "id")
            or self._clawhub_slug_from_source(source)
            or slugify(name_hint)
        )
        homepage_hint = metadata_text(metadata, "homepage", "url", "source") or (
            self._clawhub_homepage_from_source(source)
        )
        return SkillPackage(
            files=tuple(files),
            skill_markdown=markdown,
            metadata=metadata,
            name_hint=name_hint,
            slug_hint=slug_hint,
            homepage_hint=homepage_hint,
            source_kind=source_kind,
        )

    def _source_kind_for_url(self, source: str) -> str:
        parsed = urlparse(source.strip())
        if parsed.netloc in self._config.github_hosts:
            return "github"
        return "url"

    def _clawhub_slug_from_source(self, source: str) -> str | None:
        cleaned = source.strip()
        if not cleaned:
            return None
        if cleaned.startswith(("http://", "https://")):
            parsed = urlparse(cleaned)
            if parsed.netloc not in self._config.platform_hosts:
                return None
            parts = [part for part in parsed.path.strip("/").split("/") if part]
            if len(parts) >= 2:
                slug = parts[1]
            elif len(parts) == 1:
                slug = parts[0]
            else:
                return None
            return _valid_clawhub_slug(slug)
        if "/" not in cleaned:
            return _valid_clawhub_slug(cleaned)
        return None

    def _clawhub_homepage_from_source(self, source: str) -> str | None:
        cleaned = source.strip()
        parsed = urlparse(cleaned)
        if parsed.scheme and parsed.netloc in self._config.platform_hosts:
            return cleaned
        slug = self._clawhub_slug_from_source(cleaned)
        if slug:
            return f"https://skillhub.ai/{slug}"
        return None

    def _load_clawhub_skill_package(
        self, slug: str, source_url: str | None = None
    ) -> list[SkillFile]:
        download_url = f"{self._config.platform_download_endpoint}?slug={quote(slug, safe='')}"
        try:
            return self._load_remote(download_url)
        except SkillImporterError as download_error:
            if source_url:
                try:
                    return self._load_remote(source_url)
                except SkillImporterError:
                    pass
            raise download_error

    def _load_remote(
        self, url: str, visited: frozenset[str] = frozenset()
    ) -> list[SkillFile]:
        normalized_url = url.strip()
        parsed = urlparse(normalized_url)
        if not parsed.scheme or not parsed.netloc:
            raise SkillImporterError(
                "remote skill source must be a valid URL", code=ERROR_SOURCE_INVALID
            )
        if normalized_url in visited:
            raise SkillImporterError(
                "remote skill source redirects to itself", code=ERROR_REDIRECT_LOOP
            )
        if len(visited) >= self._config.max_indirections:
            raise SkillImporterError(
                "remote skill source contains too many indirections",
                code=ERROR_REDIRECT_LOOP,
            )
        next_visited = visited | {normalized_url}
        if parsed.netloc in self._config.github_hosts:
            return load_github_source(parsed, http=self._http, config=self._config)
        data, content_type = self._http.download(normalized_url)
        lower_content_type = content_type.lower()
        if parsed.path.lower().endswith(".zip") or "zip" in lower_content_type:
            return files_from_zip(
                data,
                max_file_bytes=self._config.max_file_bytes,
                max_files=self._config.max_files,
            )
        text = _decode_text(data)
        if _looks_like_html_response(text, lower_content_type):
            linked_source = _extract_skill_source_from_html(text, normalized_url)
            if linked_source:
                return self._load_remote(linked_source, next_visited)
            raise SkillImporterError(
                "open-platform page exposes no downloadable skill package or GitHub "
                "directory; HTML pages are not imported as SKILL.md",
                code=ERROR_HTML_NOT_SKILL,
            )
        if _looks_like_markdown_source(parsed.path, lower_content_type):
            file_name = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1]) or "SKILL.md"
            if not file_name.lower().endswith(".md"):
                file_name = "SKILL.md"
            return [
                SkillFile(
                    path=_clean_package_path(file_name),
                    content=text,
                    size=len(data),
                    mime_type=content_type or "text/markdown",
                )
            ]
        raise SkillImporterError(
            "remote source must be a zip package, GitHub skill directory, or raw "
            "Markdown skill file",
            code=ERROR_SOURCE_INVALID,
        )


def _required_text(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise SkillImporterError("source cannot be empty", code=ERROR_SOURCE_INVALID)
    return cleaned


def _assert_public_target(url: str) -> None:
    """Reject non-http(s) schemes and targets that resolve only to non-public addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SkillImporterError("only http(s) sources are allowed", code=ERROR_SOURCE_INVALID)
    host = parsed.hostname
    if not host:
        raise SkillImporterError("invalid source host", code=ERROR_SOURCE_INVALID)
    try:
        resolved = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return  # 解析失败由后续 CONNECT_FAILED 兜底，不额外拦截
    ips = [ipaddress.ip_address(info[4][0]) for info in resolved]
    if ips and all(not ip.is_global for ip in ips):
        raise SkillImporterError(
            f"source resolves to a non-public address: {host}", code=ERROR_SOURCE_INVALID
        )


def _valid_clawhub_slug(value: str) -> str | None:
    slug = value.strip().removesuffix(".zip").removesuffix(".md")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,127}", slug):
        return slug
    return None


def _looks_like_github_shorthand(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/.+)?", value.strip()))


def _looks_like_markdown_source(path: str, content_type: str) -> bool:
    lower_path = path.lower()
    lower_content_type = content_type.lower()
    return (
        lower_path.endswith(".md")
        or lower_path.endswith("/skill")
        or "text/markdown" in lower_content_type
        or "text/plain" in lower_content_type
    )


def _looks_like_html_response(text: str, content_type: str) -> bool:
    stripped = text.lstrip().lower()
    return (
        "text/html" in content_type
        or stripped.startswith("<!doctype html")
        or stripped.startswith("<html")
    )


def _extract_skill_source_from_html(text: str, base_url: str) -> str | None:
    normalized = unescape(text).replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
    candidates: list[str] = []
    candidates.extend(re.findall(r"https?://[^\s\"'<>]+", normalized))
    for match in re.finditer(
        r"""(?:href|src)\s*=\s*["']([^"']+)["']""", normalized, flags=re.IGNORECASE
    ):
        candidates.append(urljoin(base_url, match.group(1)))
    seen: set[str] = set()
    for candidate in candidates:
        cleaned = candidate.strip().rstrip("),.;]")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        parsed = urlparse(cleaned)
        if not parsed.scheme or not parsed.netloc:
            continue
        lower_path = parsed.path.lower()
        if parsed.netloc == RAW_GITHUB_HOST:
            return cleaned
        if _is_clawhub_download_url(parsed):
            return cleaned
        if parsed.netloc in DEFAULT_GITHUB_HOSTS and (
            "/tree/" in lower_path
            or "/blob/" in lower_path
            or lower_path.endswith(".zip")
            or "/archive/" in lower_path
        ):
            return cleaned
        if lower_path.endswith(".zip"):
            return cleaned
    for candidate in candidates:
        cleaned = candidate.strip().rstrip("),.;]")
        if cleaned:
            return cleaned
    return None


def _is_clawhub_download_url(parsed) -> bool:
    path = parsed.path.lower().rstrip("/")
    return path.endswith("/api/v1/download") and "slug=" in parsed.query.lower()
