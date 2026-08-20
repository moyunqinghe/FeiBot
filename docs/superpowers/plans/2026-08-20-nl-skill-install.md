# 自然语言 Skill 安装（工具化）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让模型通过三个内置工具（install_skill / uninstall_skill / list_skills）完成 skill 的安装/卸载/列举，管理员在微信里用自然语言即可触发。

**Architecture:** 新增宿主模块 `app/agent/tools/skill_tools.py`（仿 builtin 的导入即注册）：skill_manager 单例装配点从 engine 迁入此处，engine 的友好错误映射一并迁入共用；engine 的 `/skill` 指令面保留，两条路共享同一 manager 与错误映射。基座层（manager/loader/skill_importer）零改动。

**Tech Stack:** Python 3.14, pytest, Ruff

**Spec:** `docs/superpowers/specs/2026-08-20-nl-skill-install-design.md`

**硬约束（每个 Task 都适用）：**

- 不修改 `backend/app/agent/skills/`（manager.py / loader.py / skill_importer/ / pyproject.toml）、`backend/app/agent/mcp/`、`backend/app/channels/wechat/`、`backend/app/agent/tools/mcp_plugins.py`、`backend/app/agent/tools/builtin.py`。
- 所有 pytest/ruff 命令在 `backend/` 目录执行，Python 用 `.venv/bin/python`。
- 提交信息逐字使用各 Task 指定的内容。

---

## File map

- Create `backend/app/agent/tools/skill_tools.py`：skill_manager 单例 + `friendly_import_error` + 三个工具 handler + 注册。
- Create `backend/tests/test_skill_tools.py`：工具 handler 与注册的离线测试（fake manager 注入）。
- Modify `backend/app/agent/tools/__init__.py`：导入 skill_tools（导入即注册）。
- Modify `backend/app/agent/engine.py`：移除重复装配与私有映射，改从 skill_tools 导入；HELP_TEXT 增加自然语言引导行。
- Modify `backend/tests/test_engine.py`：新增 HELP 提及自然语言安装用例。
- Modify `backend/README.md`：Skill 管理小节补充自然语言用法。

---

### Task 1: skill_tools 模块 — 单例装配 + 友好映射 + install_skill

**Files:**
- Create: `backend/tests/test_skill_tools.py`
- Create: `backend/app/agent/tools/skill_tools.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_skill_tools.py`：

```python
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
```

- [ ] **Step 2: 运行验证 RED**

```bash
.venv/bin/python -m pytest tests/test_skill_tools.py -q
```

预期：collect 阶段报错 `ModuleNotFoundError: No module named 'app.agent.tools.skill_tools'`。

- [ ] **Step 3: 实现 skill_tools.py**

创建 `backend/app/agent/tools/skill_tools.py`：

```python
"""skill 管理工具:把 skill 宿主管理器暴露为模型可调用的工具。

与 builtin 同款"导入即注册"。工具说明只注入白名单会话(engine 门控),
非白名单会话看不到这些工具,本层不做授权判断。

skill_manager 单例在此装配:engine(/skill 指令面)与本模块的工具共享
同一实例与同一套友好错误映射(friendly_import_error)。
"""

from __future__ import annotations

from skill_importer import (
    ERROR_CONNECT_FAILED,
    ERROR_GITHUB_API_ERROR,
    ERROR_HTML_NOT_SKILL,
    ERROR_HTTP_ERROR,
    ERROR_PACKAGE_INVALID,
    ERROR_REDIRECT_LOOP,
    ERROR_SKILL_MD_MISSING,
    ERROR_SOURCE_INVALID,
    ERROR_TIMEOUT,
    ERROR_TOO_LARGE,
    SkillImporterError,
)

from app.agent.skills.manager import SkillManager, SkillManagerError
from app.agent.tools.registry import ToolSpec, register_tool
from app.config import SKILLS_DIR
from app.db.skill_store import SqliteSkillStore

# skill 宿主管理器:基座 SkillManager + sqlite 持久化 + 运行时目录
skill_manager = SkillManager(SqliteSkillStore(), SKILLS_DIR)

_IMPORT_ERROR_HINTS = {
    ERROR_TIMEOUT: "下载超时,请稍后重试。",
    ERROR_CONNECT_FAILED: "连接来源失败,请检查网络后重试。",
    ERROR_TOO_LARGE: "技能包过大,已拒绝下载。",
    ERROR_SKILL_MD_MISSING: "该目录不是有效的技能包(缺少 SKILL.md)。",
    ERROR_HTML_NOT_SKILL: "链接指向的内容不是技能包。",
    ERROR_REDIRECT_LOOP: "来源重定向次数过多,已拒绝。",
    ERROR_SOURCE_INVALID: "无法识别的来源,请检查链接或 slug 是否正确。",
    ERROR_PACKAGE_INVALID: "技能包内容非法,已拒绝。",
    ERROR_GITHUB_API_ERROR: "GitHub API 返回异常,请稍后重试。",
}


def friendly_import_error(exc: SkillImporterError) -> str:
    """把协议层错误码翻译成对用户有行动指引的文案。"""
    if exc.code == ERROR_HTTP_ERROR:
        return f"来源链接无法访问或不存在,请检查仓库名与路径是否拼写正确({exc})。"
    return _IMPORT_ERROR_HINTS.get(exc.code, f"安装失败:{exc}")


def install_skill(source: str = "") -> str:
    """安装技能:导入来源,落盘 + 落库。"""
    source = source.strip()
    if not source:
        return "缺少技能来源(args 里传 source)。"
    try:
        slug = skill_manager.install(source)
    except SkillImporterError as exc:
        return friendly_import_error(exc)
    except (SkillManagerError, OSError) as exc:
        return f"安装失败:{exc}"
    target = SKILLS_DIR / slug
    file_count = sum(1 for path in target.rglob("*") if path.is_file())
    return f"安装成功:slug={slug},文件数={file_count},位置={target}"


register_tool(ToolSpec(
    name="install_skill",
    description=(
        "安装技能。当用户要求安装/添加/导入技能并给出来源或链接时使用此工具;"
        "不要用 shell 自行下载技能内容"
    ),
    parameters={"source": "技能来源:GitHub URL/tree、raw SKILL.md、zip、平台 slug 或 owner/repo"},
    handler=install_skill,
))
```

- [ ] **Step 4: 运行验证 GREEN**

```bash
.venv/bin/python -m pytest tests/test_skill_tools.py -q
```

预期：`7 passed`。

- [ ] **Step 5: 提交**

```bash
git add backend/tests/test_skill_tools.py backend/app/agent/tools/skill_tools.py
git commit -m "feat(skills): expose skill install as a model tool"
```

### Task 2: uninstall_skill 与 list_skills 工具 + 导入即注册

**Files:**
- Modify: `backend/tests/test_skill_tools.py`
- Modify: `backend/app/agent/tools/skill_tools.py`
- Modify: `backend/app/agent/tools/__init__.py`

- [ ] **Step 1: 追加失败测试**

`backend/tests/test_skill_tools.py` 中，把 Task 1 的 `test_install_skill_registered_with_discipline` 整体替换为：

```python
def test_all_skill_tools_registered() -> None:
    for name in ("install_skill", "uninstall_skill", "list_skills"):
        assert name in TOOL_REGISTRY
    assert "不要用 shell" in TOOL_REGISTRY["install_skill"].description
```

文件末尾追加：

```python
def test_uninstall_skill_success(fake_manager) -> None:
    assert skill_tools.uninstall_skill("docx") == "已卸载技能 docx"


def test_uninstall_skill_missing_slug_arg(fake_manager) -> None:
    assert "缺少技能注册名" in skill_tools.uninstall_skill("  ")


def test_uninstall_skill_not_found(fake_manager) -> None:
    fake_manager.uninstall_result = False
    assert "没有名为" in skill_tools.uninstall_skill("ghost")


def test_uninstall_skill_error(fake_manager) -> None:
    fake_manager.uninstall_error = SkillManagerError("非法 slug")
    assert "卸载失败" in skill_tools.uninstall_skill("../x")


def test_list_skills_empty(fake_manager) -> None:
    assert skill_tools.list_skills() == "当前没有安装任何技能"


def test_list_skills_rows(fake_manager) -> None:
    fake_manager.rows = [
        {"slug": "docx", "source": "owner/repo", "enabled": 1, "files_ok": True},
        {"slug": "ai-news", "source": "owner/repo2", "enabled": 0, "files_ok": False},
    ]
    result = skill_tools.list_skills()
    assert "docx(启用,文件完整) — owner/repo" in result
    assert "ai-news(停用,文件缺失) — owner/repo2" in result
```

- [ ] **Step 2: 运行验证 RED**

```bash
.venv/bin/python -m pytest tests/test_skill_tools.py -q
```

预期：`test_all_skill_tools_registered` FAIL（缺 uninstall_skill/list_skills），6 个新 handler 测试 FAIL（`AttributeError: module ... has no attribute 'uninstall_skill'`），Task 1 的 7 个测试仍通过。

- [ ] **Step 3: 实现两个工具**

在 `backend/app/agent/tools/skill_tools.py` 的 `install_skill` 函数之后追加：

```python
def uninstall_skill(slug: str = "") -> str:
    """卸载技能:删库记录 + 删目录。"""
    slug = slug.strip()
    if not slug:
        return "缺少技能注册名(args 里传 slug)。"
    try:
        removed = skill_manager.uninstall(slug)
    except (SkillManagerError, OSError) as exc:
        return f"卸载失败:{exc}"
    if removed:
        return f"已卸载技能 {slug}"
    return f"没有名为「{slug}」的技能"


def list_skills() -> str:
    """列出已安装技能。"""
    try:
        rows = skill_manager.list()
    except Exception as exc:  # noqa: BLE001 — 与其他工具一致,不让异常漏到模型
        return f"查看技能失败:{exc}"
    if not rows:
        return "当前没有安装任何技能"
    lines = []
    for row in rows:
        state = "启用" if row["enabled"] else "停用"
        files = "文件完整" if row["files_ok"] else "文件缺失"
        lines.append(f"{row['slug']}({state},{files}) — {row['source']}")
    return "\n".join(lines)
```

文件末尾 `install_skill` 的 `register_tool` 之后追加：

```python
register_tool(ToolSpec(
    name="uninstall_skill",
    description="卸载技能。当用户要求卸载/删除/移除某个已安装的技能时使用此工具",
    parameters={"slug": "技能注册名(安装时返回的 slug)"},
    handler=uninstall_skill,
))
register_tool(ToolSpec(
    name="list_skills",
    description="列出已安装的技能。当用户询问装了哪些技能/技能列表时使用此工具",
    parameters={},
    handler=list_skills,
))
```

- [ ] **Step 4: 接入导入即注册**

在 `backend/app/agent/tools/__init__.py` 的 builtin 导入行之后追加：

```python
from app.agent.tools import skill_tools  # noqa: F401  导入即注册 skill 工具
```

并把模块 docstring 第一行 `"""工具层:注册表 + 内置工具 + 调用解析/执行。` 之后的说明行 `import 本包即完成内置工具注册(builtin 在导入时 register)。` 改为：

```
import 本包即完成内置工具注册(builtin 与 skill_tools 在导入时 register)。
```

- [ ] **Step 5: 运行验证 GREEN**

```bash
.venv/bin/python -m pytest tests/test_skill_tools.py tests/test_tools.py -q
```

预期：全部通过（test_skill_tools 14 例 + test_tools 原有用例不回归）。

- [ ] **Step 6: 提交**

```bash
git add backend/tests/test_skill_tools.py backend/app/agent/tools/skill_tools.py backend/app/agent/tools/__init__.py
git commit -m "feat(skills): add uninstall and list tools with import-time registration"
```

### Task 3: engine 去重装配 + 共享友好映射 + HELP 引导

**Files:**
- Modify: `backend/tests/test_engine.py`
- Modify: `backend/app/agent/engine.py`

- [ ] **Step 1: 追加失败测试**

`backend/tests/test_engine.py` 末尾追加：

```python
def test_help_mentions_nl_skill_install() -> None:
    assert "安装这个skill" in engine.handle_message("conv-1", "/帮助")
```

运行验证 RED：

```bash
.venv/bin/python -m pytest tests/test_engine.py::test_help_mentions_nl_skill_install -q
```

预期：FAIL（HELP_TEXT 还没有该行）。

- [ ] **Step 2: engine 导入区改造**

在 `backend/app/agent/engine.py` 中：

（a）`from app.agent.tools.registry import TOOL_REGISTRY` 行之后追加：

```python
from app.agent.tools.skill_tools import friendly_import_error, skill_manager
```

（b）删除这一行（装配点已迁入 skill_tools）：

```python
from app.agent.skills.manager import SkillManager, SkillManagerError
```

替换为（`_skill_*` 各函数仍捕获 `SkillManagerError`，保留该名字）：

```python
from app.agent.skills.manager import SkillManagerError
```

（c）删除这两行：

```python
from app.config import SKILLS_DIR, is_tool_admin
```

替换为：

```python
from app.config import is_tool_admin
```

（d）删除这一行：

```python
from app.db.skill_store import SqliteSkillStore
```

（e）删除装配行与注释：

```python
# skill 宿主管理器:基座 SkillManager + sqlite 持久化 + 运行时目录
skill_manager = SkillManager(SqliteSkillStore(), SKILLS_DIR)
```

- [ ] **Step 3: 删除私有映射、切换 _skill_add、补 HELP 行**

（a）删除 engine 中的 `_IMPORT_ERROR_HINTS` 字典与 `_friendly_import_error` 函数（整个定义）。

（b）`_skill_add` 中的 `return _friendly_import_error(exc)` 改为：

```python
        return friendly_import_error(exc)
```

（c）`HELP_TEXT` 中 `"/skill enable|disable <slug> - 启用/停用技能\n"` 行之后追加：

```python
    "安装技能也可以直接发:安装这个skill:<链接>(仅管理员)\n"
```

- [ ] **Step 4: 运行验证 GREEN（含全部存量用例）**

```bash
.venv/bin/python -m pytest tests/test_engine.py -q
```

预期：全部通过（含 3 个友好文案用例——行为未变，仅实现位置迁移；`engine.skill_manager` 现在是导入绑定，monkeypatch 用例照常工作）。

- [ ] **Step 5: 提交**

```bash
git add backend/tests/test_engine.py backend/app/agent/engine.py
git commit -m "refactor(skills): share skill manager and error hints with tools"
```

### Task 4: README 文档

**Files:**
- Modify: `backend/README.md`

- [ ] **Step 1: Skill 管理小节补充自然语言用法**

在 `backend/README.md` 的「Skill 管理（装/卸技能包）」小节末尾（「非管理员不可用。」之后）追加一段：

```markdown
管理员也可以直接说自然语言：「安装这个skill：<链接>」「把 <slug> 卸载了」「装了哪些技能」——模型会调用内置工具 `install_skill` / `uninstall_skill` / `list_skills` 完成（工具说明含纪律约束，禁止模型用 shell 自行下载技能内容）。未配置模型时请改用 `/skill` 指令。
```

- [ ] **Step 2: 验证基座与受保护文件零差异**

```bash
git diff --exit-code HEAD -- backend/app/agent/skills backend/app/agent/mcp backend/app/channels/wechat backend/app/agent/tools/mcp_plugins.py backend/app/agent/tools/builtin.py
```

预期：exit code `0`，无输出。

- [ ] **Step 3: 提交**

```bash
git add backend/README.md
git commit -m "docs(skills): document natural-language skill management"
```

### Task 5: 最终验证

**Files:** 仅验证，无计划内修改。

- [ ] **Step 1: 全量测试**

```bash
.venv/bin/python -m pytest tests -q
```

预期：exit code `0`，全部通过（167 存量 + 本次新增约 15 例）。

- [ ] **Step 2: Ruff 检查改动文件**

```bash
.venv/bin/python -m ruff check \
  app/agent/tools/skill_tools.py app/agent/tools/__init__.py \
  app/agent/engine.py \
  tests/test_skill_tools.py tests/test_engine.py
```

预期：exit code `0`，`All checks passed!`。若出现 `I001`（导入排序）用 `--fix` 归位后重跑本步与 Step 1。

- [ ] **Step 3: 仓库形态与提交记录**

```bash
git status --short
git log -5 --oneline
```

预期：无本任务未提交文件（原有两个无关未跟踪文档保持原样）；日志含本计划 4 个提交（Task 1–4）。

## 验收清单（对照 spec §7）

- [ ] `skill_tools.py` 就位：单例装配 + 友好映射 + 三工具注册；engine 无重复装配
- [ ] 三工具描述含触发时机与纪律句（测试断言「不要用 shell」）
- [ ] handler 全路径覆盖：成功/来源缺失/协议错误友好文案/管理错误/OSError/不存在
- [ ] 友好错误映射单一来源，`/skill add` 与 `install_skill` 共用（engine 无私有副本）
- [ ] HELP_TEXT 含自然语言安装引导
- [ ] 全量测试通过；改动文件 Ruff 零发现；基座与受保护文件零差异
