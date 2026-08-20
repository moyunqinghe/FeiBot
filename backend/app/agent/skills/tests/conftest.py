from __future__ import annotations

import httpx
import pytest
from skill_importer import SkillImporter


def make_transport(routes: dict[str, object]) -> httpx.MockTransport:
    """routes: {url: response}; 响应为 bytes/str/list/dict/httpx.Response，未命中返回 404。"""

    def handler(request: httpx.Request) -> httpx.Response:
        entry = routes.get(str(request.url))
        if entry is None:
            return httpx.Response(404, text="not found")
        if isinstance(entry, httpx.Response):
            return entry
        if isinstance(entry, (list, dict)):
            return httpx.Response(200, json=entry)
        if isinstance(entry, bytes) and entry[:4] == b"PK\x03\x04":
            return httpx.Response(200, content=entry, headers={"content-type": "application/zip"})
        return httpx.Response(200, content=entry)

    return httpx.MockTransport(handler)


@pytest.fixture
def make_importer():
    def _make(routes: dict[str, object]):
        return SkillImporter(transport=make_transport(routes))

    return _make


@pytest.fixture
def importer(make_importer):
    return make_importer({})
