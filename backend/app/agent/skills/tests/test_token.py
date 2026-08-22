"""github_token 注入与限流识别的回归测试(全离线,httpx.MockTransport)。"""

from __future__ import annotations

import httpx
import pytest
from skill_importer import ERROR_RATE_LIMITED, SkillImporter, SkillImporterError


def _make_handler(seen: list) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append((url, request.headers.get("authorization")))
        if url.startswith("https://api.github.com/repos/owner/repo/branches"):
            return httpx.Response(200, json=[{"name": "main"}])
        if url.startswith("https://api.github.com/repos/owner/repo/contents"):
            return httpx.Response(200, json=[{
                "type": "file",
                "path": "skills/x/SKILL.md",
                "size": 6,
                "download_url": "https://raw.githubusercontent.com/owner/repo/main/skills/x/SKILL.md",
            }])
        if url.startswith("https://raw.githubusercontent.com"):
            return httpx.Response(200, text="---\nname: x\n---\n\n# x\n")
        return httpx.Response(404, json={"message": "Not Found"})

    return handler


def _import_tree(importer: SkillImporter) -> None:
    importer.import_skill("https://github.com/owner/repo/tree/main/skills/x")


def test_token_sent_to_github_api_only() -> None:
    seen: list = []
    importer = SkillImporter(
        transport=httpx.MockTransport(_make_handler(seen)), github_token="sekrit"
    )
    _import_tree(importer)
    api = [auth for url, auth in seen if url.startswith("https://api.github.com")]
    raw = [auth for url, auth in seen if url.startswith("https://raw.githubusercontent.com")]
    assert api and all(auth == "Bearer sekrit" for auth in api)
    assert raw and all(auth is None for auth in raw)


def test_no_token_no_auth_header() -> None:
    seen: list = []
    importer = SkillImporter(transport=httpx.MockTransport(_make_handler(seen)))
    _import_tree(importer)
    assert seen
    assert all(auth is None for _, auth in seen)


def test_rate_limited_403_raises_rate_limited_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"message": "API rate limit exceeded for 1.2.3.4."}
        )

    importer = SkillImporter(transport=httpx.MockTransport(handler))
    with pytest.raises(SkillImporterError) as excinfo:
        importer.import_skill("owner/repo")
    assert excinfo.value.code == ERROR_RATE_LIMITED


def test_plain_403_still_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Forbidden for another reason"})

    importer = SkillImporter(transport=httpx.MockTransport(handler))
    with pytest.raises(SkillImporterError) as excinfo:
        importer.import_skill("owner/repo")
    assert excinfo.value.code == "HTTP_ERROR"
