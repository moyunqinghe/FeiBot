import socket

import httpx
import pytest
from _helpers import files_dict, make_zip
from skill_importer import (
    ERROR_CONNECT_FAILED,
    ERROR_HTML_NOT_SKILL,
    ERROR_REDIRECT_LOOP,
    ERROR_SOURCE_INVALID,
    ERROR_TIMEOUT,
    ERROR_TOO_LARGE,
    SkillImporter,
    SkillImporterError,
)


def test_import_platform_bare_slug_uses_download_endpoint(make_importer) -> None:
    importer = make_importer(
        {
            "https://wry-manatee-359.convex.site/api/v1/download?slug=weather-pack": make_zip(
                {"pkg-main/weather/SKILL.md": "---\nname: 天气包\nslug: weather-pack\n---\n"}
            )
        }
    )
    pkg = importer.import_skill("weather-pack")
    assert pkg.source_kind == "platform"
    assert pkg.slug_hint == "weather-pack"
    assert pkg.name_hint == "天气包"
    assert files_dict(pkg) == {"SKILL.md": "---\nname: 天气包\nslug: weather-pack\n---\n"}


def test_import_platform_url_falls_back_to_source(make_importer) -> None:
    importer = make_importer(
        {
            "https://wry-manatee-359.convex.site/api/v1/download?slug=abc": httpx.Response(404),
            "https://skillhub.ai/abc": make_zip({"x/SKILL.md": "# abc\n"}),
        }
    )
    pkg = importer.import_skill("https://skillhub.ai/abc")
    assert pkg.skill_markdown == "# abc\n"


def test_import_owner_repo_shorthand_hits_github(make_importer) -> None:
    importer = make_importer(
        {
            "https://github.com/owner/repo/archive/refs/heads/main.zip": make_zip(
                {"repo-main/SKILL.md": "# repo\n"}
            )
        }
    )
    pkg = importer.import_skill("owner/repo")
    assert pkg.source_kind == "github"
    assert pkg.skill_markdown == "# repo\n"


def test_import_raw_skill_md_url(make_importer) -> None:
    importer = make_importer(
        {"https://example.com/SKILL.md": "---\nname: X\n---\n# X\n"}
    )
    pkg = importer.import_skill("https://example.com/SKILL.md")
    assert files_dict(pkg) == {"SKILL.md": "---\nname: X\n---\n# X\n"}
    assert pkg.name_hint == "X"


def test_import_zip_url(make_importer) -> None:
    importer = make_importer(
        {"https://example.com/weather.zip": make_zip({"w/SKILL.md": "# weather\n"})}
    )
    pkg = importer.import_skill("https://example.com/weather.zip")
    assert pkg.skill_markdown == "# weather\n"


def test_import_html_page_follows_real_package(make_importer) -> None:
    importer = make_importer(
        {
            "https://platform.ai/skill/weather": (
                '<html><a href="https://raw.githubusercontent.com/o/r/main/SKILL.md">download</a></html>'
            ),
            "https://raw.githubusercontent.com/o/r/main/SKILL.md": "# raw\n",
        }
    )
    pkg = importer.import_skill("https://platform.ai/skill/weather")
    assert pkg.skill_markdown == "# raw\n"


def test_import_plain_html_rejected(make_importer) -> None:
    importer = make_importer(
        {"https://platform.ai/skill/weather": "<html><body>no links</body></html>"}
    )
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("https://platform.ai/skill/weather")
    assert exc.value.code == ERROR_HTML_NOT_SKILL


def test_import_html_with_only_unrelated_url_rejected(make_importer) -> None:
    """HTML 中只有非技能亲和性 URL（正文提及/跟踪链接）时必须拒绝，不得跟随。"""
    importer = make_importer(
        {
            "https://platform.ai/skill/weather": (
                '<html>see https://example.com/blog for context</html>'
            )
        }
    )
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("https://platform.ai/skill/weather")
    assert exc.value.code == ERROR_HTML_NOT_SKILL


def test_import_html_first_link_is_tracker_still_finds_skill_link(make_importer) -> None:
    """无亲和性 URL 不得抢占真正的技能包链接。"""
    importer = make_importer(
        {
            "https://platform.ai/skill/weather": (
                '<html><img src="https://tracker.example/pixel.gif">'
                '<a href="https://raw.githubusercontent.com/o/r/main/SKILL.md">dl</a></html>'
            ),
            "https://raw.githubusercontent.com/o/r/main/SKILL.md": "# raw\n",
        }
    )
    pkg = importer.import_skill("https://platform.ai/skill/weather")
    assert pkg.skill_markdown == "# raw\n"


def test_import_redirect_loop_detected(make_importer) -> None:
    endpoint = "https://wry-manatee-359.convex.site/api/v1/download"
    importer = make_importer(
        {
            "https://a.example/skill": f'<html><a href="{endpoint}?slug=one">x</a></html>',
            f"{endpoint}?slug=one": f'<html><a href="{endpoint}?slug=two">y</a></html>',
            f"{endpoint}?slug=two": f'<html><a href="{endpoint}?slug=one">z</a></html>',
        }
    )
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("https://a.example/skill")
    assert exc.value.code == ERROR_REDIRECT_LOOP


def test_import_empty_source_rejected(importer) -> None:
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("   ")
    assert exc.value.code == ERROR_SOURCE_INVALID


def test_import_unrecognized_source_rejected(importer) -> None:
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("not a source")
    assert exc.value.code == ERROR_SOURCE_INVALID


def test_import_timeout_maps_to_timeout_error() -> None:
    def handler(request):
        raise httpx.ConnectTimeout("boom")

    importer = SkillImporter(transport=httpx.MockTransport(handler))
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("https://example.com/SKILL.md")
    assert exc.value.code == ERROR_TIMEOUT


def test_import_too_large_maps_too_large_error(make_importer) -> None:
    importer = make_importer({"https://example.com/SKILL.md": b"x" * (96 * 1024 * 1024 + 1)})
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("https://example.com/SKILL.md")
    assert exc.value.code == ERROR_TOO_LARGE


def test_import_too_large_via_content_length_header_rejected_early(make_importer) -> None:
    """Content-Length 预检：声明超限即拒绝，body 不会被读取。"""
    seen = {"body_read": False}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body_read"] = True
        return httpx.Response(
            200,
            headers={"content-length": str(96 * 1024 * 1024 + 1)},
            content=b"x",
        )

    importer = SkillImporter(transport=httpx.MockTransport(handler))
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("https://example.com/SKILL.md")
    assert exc.value.code == ERROR_TOO_LARGE
    assert seen["body_read"]  # handler 已被调用，但响应体远小于声明，仍按声明拒绝


def test_download_rejects_private_ip_literal(importer) -> None:
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("http://127.0.0.1/SKILL.md")
    assert exc.value.code == ERROR_SOURCE_INVALID


def test_download_rejects_mixed_public_private_dns_answers(
    importer, monkeypatch
) -> None:
    """混合公网+私网 DNS 应答必须拒绝（rebinding 常见形态）。"""

    def fake_getaddrinfo(host, *args, **kwargs):
        class Info:
            def __init__(self, addr):
                self._addr = addr

        # 模拟同时返回公网与私网地址
        return [
            (socket.AF_INET, None, None, "", ("93.184.216.34", 0)),
            (socket.AF_INET, None, None, "", ("192.168.1.1", 0)),
        ]

    monkeypatch.setattr("skill_importer.resolver.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("https://rebind.example/SKILL.md")
    assert exc.value.code == ERROR_SOURCE_INVALID


def test_download_follows_redirect_with_guard(make_importer) -> None:
    importer = make_importer(
        {
            "https://a.example/x": httpx.Response(
                302, headers={"location": "https://b.example/SKILL.md"}
            ),
            "https://b.example/SKILL.md": httpx.Response(
                200, content=b"# hi\n", headers={"content-type": "text/plain"}
            ),
        }
    )
    pkg = importer.import_skill("https://a.example/x")
    assert pkg.skill_markdown == "# hi\n"


def test_download_rejects_redirect_to_private(make_importer) -> None:
    importer = make_importer(
        {
            "https://a.example/x": httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data"}
            )
        }
    )
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("https://a.example/x")
    assert exc.value.code == ERROR_SOURCE_INVALID


def test_download_connect_failed_maps_to_connect_failed() -> None:
    def handler(request):
        raise httpx.ConnectError("connection refused")

    importer = SkillImporter(transport=httpx.MockTransport(handler))
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("https://example.com/SKILL.md")
    assert exc.value.code == ERROR_CONNECT_FAILED
