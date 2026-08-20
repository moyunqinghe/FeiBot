"""skill 管理工具(skill_tools)的回归测试:全部离线,fake manager 注入。"""

from __future__ import annotations

import pytest
from skill_importer import ERROR_HTTP_ERROR, ERROR_TIMEOUT, SkillImporterError

from app.agent.skills.manager import SkillManagerError
from app.agent.tools import skill_tools
from app.agent.tools.registry import TOOL_REGISTRY


class FakeManager:
    """SkillManager 替身:结果与异常均可预置。"""

    def __init__(self) -> None:
        self.install_result = "docx"
        self.install_error: Exception | None = None
        self.uninstall_result = True
        self.uninstall_error: Exception | None = None
        self.rows: list[dict] = []

    def install(self, source: str) -> str:
        if self.install_error is not None:
            raise self.install_error
        return self.install_result

    def uninstall(self, slug: str) -> bool:
        if self.uninstall_error is not None:
            raise self.uninstall_error
        return self.uninstall_result

    def list(self) -> list[dict]:
        return self.rows


@pytest.fixture
def fake_manager(monkeypatch):
    manager = FakeManager()
    monkeypatch.setattr(skill_tools, "skill_manager", manager)
    return manager


def test_install_skill_registered_with_discipline() -> None:
    assert "install_skill" in TOOL_REGISTRY
    assert "不要用 shell" in TOOL_REGISTRY["install_skill"].description


def test_install_skill_success(fake_manager) -> None:
    result = skill_tools.install_skill("owner/repo")
    assert result.startswith("安装成功:slug=docx")
    assert "位置=" in result


def test_install_skill_missing_source(fake_manager) -> None:
    assert "缺少技能来源" in skill_tools.install_skill("  ")


def test_install_skill_importer_error_friendly(fake_manager) -> None:
    fake_manager.install_error = SkillImporterError(
        "download failed with HTTP 404", code=ERROR_HTTP_ERROR
    )
    result = skill_tools.install_skill("owner/repo")
    assert "无法访问或不存在" in result
    assert "404" in result


def test_install_skill_timeout_friendly(fake_manager) -> None:
    fake_manager.install_error = SkillImporterError("timed out", code=ERROR_TIMEOUT)
    assert "超时" in skill_tools.install_skill("owner/repo")


def test_install_skill_manager_error(fake_manager) -> None:
    fake_manager.install_error = SkillManagerError("SKILL.md 缺少 frontmatter name")
    assert "安装失败" in skill_tools.install_skill("owner/repo")


def test_install_skill_os_error(fake_manager) -> None:
    fake_manager.install_error = OSError("disk full")
    assert "安装失败" in skill_tools.install_skill("owner/repo")
