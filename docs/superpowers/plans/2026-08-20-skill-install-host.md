# Skill 安装/卸载宿主层实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 feibot 的 skill 域补齐宿主层：导入 → 校验 → 落盘 `.feibot/skills/<slug>/` → sqlite 元数据 → 卸载/启停，并接入管理员门控的 `/skill` 渠道指令族。

**Architecture:** 严格仿 MCP 分层。基座层 `app/agent/skills/`（skill_importer 协议基座 + loader 发现原语 + 新增 manager 生命周期）保持零 `app.*` 导入、全依赖注入，可整体抽取；宿主层由 `app/db/store.py`（installed_skills 表）、`app/db/skill_store.py`（SkillStore 适配器）、`slash.py` + `engine.py`（指令面）接线。

**Tech Stack:** Python 3.14, sqlite3（标准库）, pathlib, pytest, Ruff

**Spec:** `docs/superpowers/specs/2026-08-20-skill-install-host-design.md`

**硬约束（每个 Task 都适用）：**

- 不修改 `backend/app/agent/skills/skill_importer/`、`backend/app/agent/skills/pyproject.toml`、`backend/app/agent/mcp/`、`backend/app/channels/wechat/`、`backend/app/agent/tools/mcp_plugins.py`。
- 基座层（`app/agent/skills/` 下的 `loader.py`、`manager.py`、`__init__.py`）禁止出现任何 `app.*` 导入。
- 所有 pytest/ruff 命令在 `backend/` 目录执行，Python 用 `.venv/bin/python`。
- 提交信息逐字使用各 Task 指定的内容。

---

## File map

- Create `backend/tests/test_skill_store.py`：installed_skills 表 CRUD 与适配器测试。
- Create `backend/tests/test_skill_manager.py`：SkillManager 全行为测试（纯注入、零 monkeypatch）。
- Create `backend/app/agent/skills/manager.py`：SkillStore 协议 + SkillManager + SkillManagerError（基座层，零 app.* 导入）。
- Create `backend/app/db/skill_store.py`：SqliteSkillStore 适配器。
- Modify `backend/app/db/store.py`：installed_skills 表 schema + CRUD 函数。
- Modify `backend/app/agent/skills/loader.py`：`list_skills` 改为参数传入目录（去除 `app.config` 耦合）。
- Modify `backend/tests/test_skills.py`：loader 测试改传参式 + 新增架构守卫测试。
- Modify `backend/app/agent/slash.py`：解析 `/skill`。
- Modify `backend/tests/test_slash.py`：`/skill` 解析用例。
- Modify `backend/app/agent/engine.py`：装配 `skill_manager` + `_handle_skill` 指令面 + HELP_TEXT。
- Modify `backend/tests/test_engine.py`：`/skill` 指令用例。
- Modify `backend/app/agent/skills/README.md`：顶部新增基座层总述（赋能步骤 + 宿主清单），原 skill-importer 文档保留。
- Modify `backend/README.md`：目录结构条目 + 新增「Skill 管理」小节。

---

### Task 1: installed_skills 表与 CRUD（store 层）

**Files:**
- Create: `backend/tests/test_skill_store.py`
- Modify: `backend/app/db/store.py`（docstring 第 1 行、`_SCHEMA` 内 messages 表之后、文件末尾追加 CRUD）

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_skill_store.py`：

```python
"""installed_skills 表与 SqliteSkillStore 适配器的回归测试。"""

from app.db import store


def test_upsert_and_get_skill():
    store.upsert_skill("daily-ai-news", "owner/repo", "github", 1)
    row = store.get_skill("daily-ai-news")
    assert row["slug"] == "daily-ai-news"
    assert row["source"] == "owner/repo"
    assert row["source_kind"] == "github"
    assert row["enabled"] == 1


def test_upsert_overwrites_same_slug():
    store.upsert_skill("s1", "src-a", "github", 1)
    store.upsert_skill("s1", "src-b", "url", 0)
    row = store.get_skill("s1")
    assert row["source"] == "src-b"
    assert row["source_kind"] == "url"
    assert row["enabled"] == 0


def test_get_missing_skill_returns_none():
    assert store.get_skill("nope") is None


def test_list_skills_sorted_and_roundtrip():
    store.upsert_skill("b", "src-b", "url", 1)
    store.upsert_skill("a", "src-a", "github", 0)
    slugs = [r["slug"] for r in store.list_skills()]
    assert slugs == ["a", "b"]


def test_delete_skill_returns_whether_deleted():
    store.upsert_skill("s1", "src", "url", 1)
    assert store.delete_skill("s1") is True
    assert store.delete_skill("s1") is False
    assert store.get_skill("s1") is None


def test_set_skill_enabled():
    store.upsert_skill("s1", "src", "url", 1)
    assert store.set_skill_enabled("s1", 0) is True
    assert store.get_skill("s1")["enabled"] == 0
    assert store.set_skill_enabled("nope", 1) is False


def test_skill_upsert_preserves_added_and_bumps_updated(monkeypatch):
    clock = [1000.0]

    def fake_time():
        clock[0] += 1.0
        return clock[0]

    monkeypatch.setattr(store.time, "time", fake_time)
    store.upsert_skill("s1", "src", "url", 1)
    first = store.get_skill("s1")
    store.upsert_skill("s1", "src", "url", 1)
    second = store.get_skill("s1")
    assert second["added_at"] == first["added_at"]
    assert second["updated_at"] > first["updated_at"]
```

（DB 指向临时文件由 `tests/conftest.py` 的 autouse 夹具完成，无需额外处理。）

- [ ] **Step 2: 运行验证 RED**

```bash
.venv/bin/python -m pytest tests/test_skill_store.py -q
```

预期：FAIL，首个测试报 `AttributeError: module 'app.db.store' has no attribute 'upsert_skill'`。

- [ ] **Step 3: 实现 store 层**

修改 `backend/app/db/store.py`：

（a）第 1 行 docstring 改为：

```python
"""sqlite 持久化:kv / sessions / model_configs / messages / mcp_plugins / installed_skills。
```

（b）`_SCHEMA` 中 `mcp_plugins` 建表语句之后追加：

```sql
CREATE TABLE IF NOT EXISTS installed_skills (
    slug        TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    added_at    REAL NOT NULL,
    updated_at  REAL NOT NULL
);
```

（c）文件末尾（`_plugin_row_to_dict` 之后）追加：

```python
# ---- 已安装 skill(installed_skills 表;slug == SKILL.md frontmatter name)----


def upsert_skill(slug: str, source: str, source_kind: str, enabled: int) -> None:
    """写入已安装 skill(同 slug 覆盖);首次写入记 added_at,每次刷新 updated_at。"""
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO installed_skills (slug, source, source_kind, enabled, added_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(slug) DO UPDATE SET"
            " source = excluded.source, source_kind = excluded.source_kind,"
            " enabled = excluded.enabled, updated_at = excluded.updated_at",
            (slug, source, source_kind, enabled, now, now),
        )


def get_skill(slug: str) -> dict | None:
    """按 slug 取 skill 行(dict);不存在返回 None。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT slug, source, source_kind, enabled, added_at, updated_at"
            " FROM installed_skills WHERE slug = ?",
            (slug,),
        ).fetchone()
    return _skill_row_to_dict(row) if row else None


def list_skills() -> list[dict]:
    """列出全部已安装 skill 行(按 slug 排序)。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT slug, source, source_kind, enabled, added_at, updated_at"
            " FROM installed_skills ORDER BY slug"
        ).fetchall()
    return [_skill_row_to_dict(row) for row in rows]


def delete_skill(slug: str) -> bool:
    """删除 skill 行;返回是否确实删掉了行。"""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM installed_skills WHERE slug = ?", (slug,))
        return cur.rowcount > 0


def set_skill_enabled(slug: str, enabled: int) -> bool:
    """切换 skill 启用状态;返回是否命中已有行。"""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE installed_skills SET enabled = ?, updated_at = ? WHERE slug = ?",
            (enabled, time.time(), slug),
        )
        return cur.rowcount > 0


def _skill_row_to_dict(row: tuple) -> dict:
    return {
        "slug": row[0],
        "source": row[1],
        "source_kind": row[2],
        "enabled": row[3],
        "added_at": row[4],
        "updated_at": row[5],
    }
```

- [ ] **Step 4: 运行验证 GREEN**

```bash
.venv/bin/python -m pytest tests/test_skill_store.py -q
```

预期：`7 passed`。

- [ ] **Step 5: 提交**

```bash
git add backend/tests/test_skill_store.py backend/app/db/store.py
git commit -m "feat(skills): persist installed skill metadata in sqlite"
```

### Task 2: SqliteSkillStore 适配器

**Files:**
- Create: `backend/app/db/skill_store.py`
- Modify: `backend/tests/test_skill_store.py`

- [ ] **Step 1: 追加适配器失败测试**

在 `backend/tests/test_skill_store.py` 顶部导入区追加：

```python
from app.db.skill_store import SqliteSkillStore
```

文件末尾追加：

```python
def test_sqlite_skill_store_roundtrip():
    s = SqliteSkillStore()
    s.upsert("daily-ai-news", "owner/repo", "github", 1)
    row = s.get("daily-ai-news")
    assert row["source"] == "owner/repo"
    assert [r["slug"] for r in s.list()] == ["daily-ai-news"]
    assert s.set_enabled("daily-ai-news", 0) is True
    assert s.get("daily-ai-news")["enabled"] == 0
    assert s.delete("daily-ai-news") is True
    assert s.get("daily-ai-news") is None


def test_sqlite_skill_store_missing_rows():
    s = SqliteSkillStore()
    assert s.get("nope") is None
    assert s.delete("nope") is False
    assert s.set_enabled("nope", 1) is False
```

- [ ] **Step 2: 运行验证 RED**

```bash
.venv/bin/python -m pytest tests/test_skill_store.py -q
```

预期：collect 阶段报错 `ModuleNotFoundError: No module named 'app.db.skill_store'`。

- [ ] **Step 3: 实现适配器**

创建 `backend/app/db/skill_store.py`：

```python
"""SkillStore 的 sqlite 实现:把 skill 基座的持久化协议透传到 app.db.store。"""

from __future__ import annotations

from app.db import store


class SqliteSkillStore:
    """实现 skill 基座 manager.SkillStore 协议(结构子类型,无需显式继承)。"""

    def upsert(self, slug: str, source: str, source_kind: str, enabled: int) -> None:
        store.upsert_skill(slug, source, source_kind, enabled)

    def get(self, slug: str) -> dict | None:
        return store.get_skill(slug)

    def list(self) -> list[dict]:
        return store.list_skills()

    def delete(self, slug: str) -> bool:
        return store.delete_skill(slug)

    def set_enabled(self, slug: str, enabled: int) -> bool:
        return store.set_skill_enabled(slug, enabled)
```

- [ ] **Step 4: 运行验证 GREEN**

```bash
.venv/bin/python -m pytest tests/test_skill_store.py -q
```

预期：`9 passed`。

- [ ] **Step 5: 提交**

```bash
git add backend/tests/test_skill_store.py backend/app/db/skill_store.py
git commit -m "feat(skills): add sqlite SkillStore adapter"
```

### Task 3: SkillManager — install（严格校验 + 幂等覆盖）

**Files:**
- Create: `backend/app/agent/skills/manager.py`
- Create: `backend/tests/test_skill_manager.py`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_skill_manager.py`：

```python
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
```

- [ ] **Step 2: 运行验证 RED**

```bash
.venv/bin/python -m pytest tests/test_skill_manager.py -q
```

预期：collect 阶段报错 `ModuleNotFoundError: No module named 'app.agent.skills.manager'`。

- [ ] **Step 3: 实现 manager.py**

创建 `backend/app/agent/skills/manager.py`（**基座层文件：禁止任何 `app.*` 导入**）：

```python
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
```

- [ ] **Step 4: 运行验证 GREEN**

```bash
.venv/bin/python -m pytest tests/test_skill_manager.py -q
```

预期：`5 passed`。

- [ ] **Step 5: 提交**

```bash
git add backend/tests/test_skill_manager.py backend/app/agent/skills/manager.py
git commit -m "feat(skills): add SkillManager install with strict slug validation"
```

### Task 4: SkillManager — uninstall / enable / disable / list

**Files:**
- Modify: `backend/tests/test_skill_manager.py`
- Modify: `backend/app/agent/skills/manager.py`

- [ ] **Step 1: 追加失败测试**

`backend/tests/test_skill_manager.py` 顶部导入区追加：

```python
import shutil
```

文件末尾追加：

```python
def test_uninstall_removes_files_and_record(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    store_ = FakeStore()
    manager = SkillManager(store_, skills_dir, importer=FakeImporter(_make_package()))
    manager.install("owner/repo")

    assert manager.uninstall("daily-ai-news") is True
    assert not (skills_dir / "daily-ai-news").exists()
    assert store_.get("daily-ai-news") is None


def test_uninstall_missing_slug_returns_false(tmp_path) -> None:
    manager = SkillManager(FakeStore(), tmp_path, importer=FakeImporter())
    assert manager.uninstall("nope") is False


def test_uninstall_rejects_invalid_slug(tmp_path) -> None:
    manager = SkillManager(FakeStore(), tmp_path, importer=FakeImporter())
    with pytest.raises(SkillManagerError):
        manager.uninstall("../escape")


def test_enable_disable_toggle_flag_only(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    store_ = FakeStore()
    manager = SkillManager(store_, skills_dir, importer=FakeImporter(_make_package()))
    manager.install("owner/repo")

    assert manager.disable("daily-ai-news") is True
    assert store_.get("daily-ai-news")["enabled"] == 0
    assert (skills_dir / "daily-ai-news" / "SKILL.md").is_file()  # 文件保留
    assert manager.enable("daily-ai-news") is True
    assert store_.get("daily-ai-news")["enabled"] == 1


def test_enable_disable_missing_slug_returns_false(tmp_path) -> None:
    manager = SkillManager(FakeStore(), tmp_path, importer=FakeImporter())
    assert manager.disable("nope") is False
    assert manager.enable("nope") is False


def test_list_reports_rows_with_filesystem_state(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    store_ = FakeStore()
    manager = SkillManager(store_, skills_dir, importer=FakeImporter(_make_package()))
    manager.install("owner/repo")

    (row,) = manager.list()
    assert row["slug"] == "daily-ai-news"
    assert row["enabled"] == 1
    assert row["files_ok"] is True
    assert row["file_count"] == 1

    shutil.rmtree(skills_dir / "daily-ai-news")
    (row,) = manager.list()
    assert row["files_ok"] is False
    assert row["file_count"] == 0
```

- [ ] **Step 2: 运行验证 RED**

```bash
.venv/bin/python -m pytest tests/test_skill_manager.py -q
```

预期：新增 6 个测试 FAIL（`AttributeError: 'SkillManager' object has no attribute 'uninstall'`），原 5 个仍通过。

- [ ] **Step 3: 实现 uninstall/enable/disable/list**

在 `backend/app/agent/skills/manager.py` 的 `SkillManager` 类中 `install` 方法之后追加：

```python
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
```

在模块末尾（`_write_package` 之后）追加：

```python
def _validate_slug_arg(slug: str) -> None:
    """uninstall/enable/disable 的入参校验:必须是合规 slug 形式。"""
    if not isinstance(slug, str) or not SKILL_NAME_RE.match(slug):
        raise SkillManagerError(f"非法 slug:{slug!r}(要求小写字母/数字/连字符)")
```

- [ ] **Step 4: 运行验证 GREEN**

```bash
.venv/bin/python -m pytest tests/test_skill_manager.py -q
```

预期：`11 passed`。

- [ ] **Step 5: 提交**

```bash
git add backend/tests/test_skill_manager.py backend/app/agent/skills/manager.py
git commit -m "feat(skills): add SkillManager uninstall and enable/disable"
```

### Task 5: SkillManager — 路径越界防线

**Files:**
- Modify: `backend/tests/test_skill_manager.py`
- Modify: `backend/app/agent/skills/manager.py`（`_write_package` 重写）

- [ ] **Step 1: 追加失败测试**

`backend/tests/test_skill_manager.py` 末尾追加：

```python
def test_install_rejects_path_traversal(tmp_path) -> None:
    package = _make_package(files=[
        ("SKILL.md", "---\nname: daily-ai-news\n---\n\n# t\n"),
        ("../evil.py", "print('escape')\n"),
    ])
    store_ = FakeStore()
    manager = SkillManager(store_, tmp_path / "skills", importer=FakeImporter(package))

    with pytest.raises(SkillManagerError, match="非法路径"):
        manager.install("owner/repo")
    assert store_.get("daily-ai-news") is None
    assert not (tmp_path / "evil.py").exists()
```

- [ ] **Step 2: 运行验证 RED**

```bash
.venv/bin/python -m pytest tests/test_skill_manager.py::test_install_rejects_path_traversal -q
```

预期：FAIL（当前 `_write_package` 会把 `../evil.py` 写到 skills_dir 之外）。

- [ ] **Step 3: 重写 `_write_package`（先全量校验，后清除写入）**

把 `backend/app/agent/skills/manager.py` 中的 `_write_package` 整个替换为：

```python
def _write_package(target: Path, package: SkillPackage) -> None:
    """先校验全部路径,再整目录清除并写入(避免旧版本残留);拒绝越界路径。"""
    target_abs = target.resolve()
    for file in package.files:
        dest = (target / file.path).resolve()
        if not dest.is_relative_to(target_abs):
            raise SkillManagerError(f"安装包含非法路径:{file.path!r},整包拒绝。")
    if target.exists():
        shutil.rmtree(target)
    for file in package.files:
        dest = target / file.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(file.content, encoding="utf-8")
```

（校验先于 rmtree：恶意包不会破坏已安装的旧版本。）

- [ ] **Step 4: 运行验证 GREEN**

```bash
.venv/bin/python -m pytest tests/test_skill_manager.py -q
```

预期：`12 passed`。

- [ ] **Step 5: 提交**

```bash
git add backend/tests/test_skill_manager.py backend/app/agent/skills/manager.py
git commit -m "feat(skills): reject skill packages escaping the install directory"
```

### Task 6: loader 参数化 + 基座纯净守卫测试

**Files:**
- Modify: `backend/tests/test_skills.py`（整体替换）
- Modify: `backend/app/agent/skills/loader.py`（整体替换）

- [ ] **Step 1: 用新内容整体替换测试文件（RED）**

把 `backend/tests/test_skills.py` 整体替换为：

```python
"""Skill 运行时目录配置与发现行为。"""

from __future__ import annotations

from pathlib import Path

from app import config
from app.agent.skills import loader


def test_skills_dir_is_under_runtime_data_dir() -> None:
    assert config.SKILLS_DIR == config.DATA_DIR / "skills"


def test_list_skills_returns_empty_when_directory_is_missing(tmp_path) -> None:
    assert loader.list_skills(tmp_path / "missing") == []


def test_list_skills_only_discovers_directories_with_skill_markdown(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    (skills_dir / "weather").mkdir(parents=True)
    (skills_dir / "weather" / "SKILL.md").write_text("# weather\n", encoding="utf-8")
    (skills_dir / "missing-manifest").mkdir()
    (skills_dir / "plain-file").write_text("not a skill\n", encoding="utf-8")

    assert loader.list_skills(skills_dir) == ["weather"]


def test_list_skills_returns_names_in_sorted_order(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    for name in ("zeta", "alpha"):
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")

    assert loader.list_skills(skills_dir) == ["alpha", "zeta"]


def test_skills_base_has_no_host_imports() -> None:
    """架构守卫:基座目录(app/agent/skills/)禁止出现 app.* 导入。"""
    base_dir = Path(loader.__file__).resolve().parent
    offenders: list[str] = []
    for path in sorted(base_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("from app.", "import app.")):
                offenders.append(f"{path.relative_to(base_dir)}: {stripped}")
    assert offenders == []
```

- [ ] **Step 2: 运行验证 RED**

```bash
.venv/bin/python -m pytest tests/test_skills.py -q
```

预期：3 个 loader 测试 FAIL（`TypeError: list_skills() takes 0 positional arguments but 1 was given`）；config 不变量与守卫测试的状态随当前 loader 而定（此时 loader 仍含 `from app.config import SKILLS_DIR`，守卫测试也应 FAIL）。

- [ ] **Step 3: 参数化 loader.py**

把 `backend/app/agent/skills/loader.py` 整体替换为：

```python
"""skill 发现:扫描运行时 skills 数据目录,列出含 SKILL.md 的子目录。

只列名字,不执行 skill 内容;后续接入 agent 时再解析 frontmatter。
目录由调用方传入(基座层不依赖宿主配置)。
"""

from __future__ import annotations

from pathlib import Path


def list_skills(skills_dir: Path) -> list[str]:
    """返回指定目录下含 SKILL.md 的子目录名,按字母序排列。"""
    if not skills_dir.is_dir():
        return []
    return sorted(
        child.name
        for child in skills_dir.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    )
```

- [ ] **Step 4: 运行验证 GREEN**

```bash
.venv/bin/python -m pytest tests/test_skills.py -q
```

预期：`5 passed`（含守卫测试——manager.py 与 skill_importer 均无 app.* 导入）。

- [ ] **Step 5: 提交**

```bash
git add backend/tests/test_skills.py backend/app/agent/skills/loader.py
git commit -m "refactor(skills): parameterize loader and guard base purity"
```

### Task 7: slash 解析 /skill

**Files:**
- Modify: `backend/tests/test_slash.py`
- Modify: `backend/app/agent/slash.py`

- [ ] **Step 1: 追加失败测试**

`backend/tests/test_slash.py` 末尾追加：

```python
def test_skill_command_no_args() -> None:
    cmd = parse_command("/skill")
    assert cmd is not None
    assert cmd.kind == "skill"
    assert cmd.query == ""


def test_skill_command_add_with_args() -> None:
    cmd = parse_command("/skill add owner/repo/tree/main/skills/x")
    assert cmd is not None
    assert cmd.kind == "skill"
    assert cmd.query == "add owner/repo/tree/main/skills/x"
```

- [ ] **Step 2: 运行验证 RED**

```bash
.venv/bin/python -m pytest tests/test_slash.py -q
```

预期：2 个新测试 FAIL（未识别指令回退成 `kind == "help"`）。

- [ ] **Step 3: 实现解析**

在 `backend/app/agent/slash.py` 中 `mcp` 分支之后追加：

```python
    if name == "skill":
        return ChannelCommand(kind="skill", query=query)
```

并把 `ChannelCommand.kind` 的行内注释改为：

```python
    kind: str  # help/ping/model/memory/forget/mcp/skill
```

- [ ] **Step 4: 运行验证 GREEN**

```bash
.venv/bin/python -m pytest tests/test_slash.py -q
```

预期：全部通过（原 11 个 + 新 2 个 = `13 passed`）。

- [ ] **Step 5: 提交**

```bash
git add backend/tests/test_slash.py backend/app/agent/slash.py
git commit -m "feat(skills): parse /skill channel command"
```

### Task 8: engine 装配与 /skill 指令面

**Files:**
- Modify: `backend/app/agent/engine.py`
- Modify: `backend/tests/test_engine.py`

- [ ] **Step 1: 追加失败测试**

`backend/tests/test_engine.py` 顶部导入区追加：

```python
from app.agent.skills.manager import SkillManagerError
```

文件末尾追加：

```python
def test_help_mentions_skill_command() -> None:
    assert "/skill" in engine.handle_message("conv-1", "/帮助")


def test_skill_denied_for_non_admin(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: False)
    assert "仅限管理员" in engine.handle_message("conv-1", "/skill list")


def test_skill_list_empty_for_admin(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    assert "还没有安装技能" in engine.handle_message("conv-1", "/skill")


def test_skill_add_and_remove_flow(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    monkeypatch.setattr(engine.skill_manager, "install", lambda source: "daily-ai-news")
    reply = engine.handle_message("conv-1", "/skill add owner/repo")
    assert "已装入技能 daily-ai-news" in reply
    monkeypatch.setattr(engine.skill_manager, "uninstall", lambda slug: True)
    reply = engine.handle_message("conv-1", "/skill remove daily-ai-news")
    assert "已卸下技能 daily-ai-news" in reply


def test_skill_add_missing_args_shows_usage(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    assert "用法" in engine.handle_message("conv-1", "/skill add")


def test_skill_add_invalid_package_shows_message(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)

    def boom(source: str) -> str:
        raise SkillManagerError("SKILL.md 缺少 frontmatter name")

    monkeypatch.setattr(engine.skill_manager, "install", boom)
    assert "安装失败" in engine.handle_message("conv-1", "/skill add owner/repo")


def test_skill_enable_disable_flow(monkeypatch) -> None:
    monkeypatch.setattr(engine, "is_tool_admin", lambda conv_key: True)
    monkeypatch.setattr(engine.skill_manager, "enable", lambda slug: True)
    reply = engine.handle_message("conv-1", "/skill enable daily-ai-news")
    assert "已启用技能 daily-ai-news" in reply
    monkeypatch.setattr(engine.skill_manager, "disable", lambda slug: True)
    reply = engine.handle_message("conv-1", "/skill disable daily-ai-news")
    assert "已停用技能 daily-ai-news" in reply
```

- [ ] **Step 2: 运行验证 RED**

```bash
.venv/bin/python -m pytest tests/test_engine.py -q -k skill
```

预期：新测试 FAIL（`AttributeError: module 'app.agent.engine' has no attribute 'skill_manager'` 或 help 文案缺失）。

- [ ] **Step 3: 修改 engine.py 导入与装配**

（a）导入区改动：

- `from mcp_discovery import McpDiscoveryError` 行之后追加：

```python
from skill_importer import SkillImporterError
```

- `from app.agent.tools.registry import TOOL_REGISTRY` 行之后追加：

```python
from app.agent.skills.manager import SkillManager, SkillManagerError
```

- `from app.config import is_tool_admin` 改为：

```python
from app.config import SKILLS_DIR, is_tool_admin
```

- `from app.db import store` 行之后追加：

```python
from app.db.skill_store import SqliteSkillStore
```

（b）在 `logger = logging.getLogger(__name__)` 行之后追加装配：

```python
# skill 宿主管理器:基座 SkillManager + sqlite 持久化 + 运行时目录
skill_manager = SkillManager(SqliteSkillStore(), SKILLS_DIR)
```

（c）`HELP_TEXT` 中 `"/mcp enable|disable <名称> - 启用/停用 MCP 插件\n"` 行之后追加：

```python
    "/skill(/skill list) - 列出已装技能(仅管理员)\n"
    "/skill add <来源> - 装入技能\n"
    "/skill remove <slug> - 卸下技能\n"
    "/skill enable|disable <slug> - 启用/停用技能\n"
```

（d）`_handle_command` 中 mcp 分支之后追加：

```python
    if cmd.kind == "skill":
        return _handle_skill(conv_key, cmd.query)
```

- [ ] **Step 4: 实现指令处理函数**

在 `backend/app/agent/engine.py` 的 `_mcp_disable` 函数之后追加：

```python
SKILL_HELP = (
    "用法:\n"
    "/skill 或 /skill list - 列出已装技能\n"
    "/skill add <来源> - 装入技能(GitHub URL/tree、raw SKILL.md、zip、平台 slug)\n"
    "/skill remove <slug> - 卸下技能\n"
    "/skill enable <slug> 或 /skill disable <slug> - 启用/停用技能"
)


def _handle_skill(conv_key: str, query: str) -> str:
    """/skill:管理已安装技能(仅管理员)。list/add/remove/enable/disable。"""
    if not is_tool_admin(conv_key):
        return "Skill 管理仅限管理员使用。"
    sub, _, arg = query.partition(" ")
    sub = sub.lower().strip()
    arg = arg.strip()
    if sub in {"", "list"}:
        return _skill_list()
    if sub == "add":
        return _skill_add(arg)
    if sub == "remove":
        return _skill_remove(arg)
    if sub == "enable":
        return _skill_enable(arg)
    if sub == "disable":
        return _skill_disable(arg)
    return SKILL_HELP


def _skill_list() -> str:
    try:
        rows = skill_manager.list()
    except Exception as exc:  # noqa: BLE001 — 与其他子命令一致,不让异常漏到渠道
        return f"查看技能失败:{exc}"
    if not rows:
        return f"还没有安装技能。\n{SKILL_HELP}"
    lines = ["已装技能(* 为启用):"]
    for row in rows:
        mark = "*" if row["enabled"] else " "
        state = "" if row["files_ok"] else "(文件缺失)"
        lines.append(f"{mark} {row['slug']}{state} — {row['source']}")
    return "\n".join(lines)


def _skill_add(arg: str) -> str:
    source = arg.strip()
    if not source:
        return "用法:/skill add <来源>"
    try:
        slug = skill_manager.install(source)
    except (SkillImporterError, SkillManagerError, OSError) as exc:
        return f"安装失败:{exc}"
    return f"已装入技能 {slug}。"


def _skill_remove(arg: str) -> str:
    slug = arg.strip()
    if not slug:
        return "用法:/skill remove <slug>"
    try:
        removed = skill_manager.uninstall(slug)
    except SkillManagerError as exc:
        return f"卸载失败:{exc}"
    if removed:
        return f"已卸下技能 {slug}。"
    return f"没有名为「{slug}」的技能,/skill 查看已装技能。"


def _skill_enable(arg: str) -> str:
    slug = arg.strip()
    if not slug:
        return "用法:/skill enable <slug>"
    try:
        enabled = skill_manager.enable(slug)
    except SkillManagerError as exc:
        return f"启用失败:{exc}"
    if enabled:
        return f"已启用技能 {slug}。"
    return f"没有名为「{slug}」的技能,/skill 查看已装技能。"


def _skill_disable(arg: str) -> str:
    slug = arg.strip()
    if not slug:
        return "用法:/skill disable <slug>"
    try:
        disabled = skill_manager.disable(slug)
    except SkillManagerError as exc:
        return f"停用失败:{exc}"
    if disabled:
        return f"已停用技能 {slug}(文件保留,/skill enable 可恢复)。"
    return f"没有名为「{slug}」的技能,/skill 查看已装技能。"
```

- [ ] **Step 5: 运行验证 GREEN**

```bash
.venv/bin/python -m pytest tests/test_engine.py -q
```

预期：全部通过（原有用量 + 新 7 个）。

- [ ] **Step 6: 归位 test_engine.py 的存量 import 排序问题**

`tests/test_engine.py` 在迁移前就带有一处存量 Ruff 发现（`I001` import 块未排序）。本 Task 动了该文件导入区，顺手归位：

```bash
.venv/bin/python -m ruff check tests/test_engine.py
```

若输出含 `I001`，执行安全自动修复（仅重排导入块）：

```bash
.venv/bin/python -m ruff check tests/test_engine.py --fix
```

然后重跑该文件测试确认无回归：

```bash
.venv/bin/python -m pytest tests/test_engine.py -q
.venv/bin/python -m ruff check tests/test_engine.py
```

预期：测试全过；ruff `All checks passed!`。

- [ ] **Step 7: 提交**

```bash
git add backend/app/agent/engine.py backend/tests/test_engine.py
git commit -m "feat(skills): manage installed skills via /skill commands"
```

### Task 9: 端到端串联测试（安装 → 发现）

**Files:**
- Modify: `backend/tests/test_skill_manager.py`

- [ ] **Step 1: 追加测试**

`backend/tests/test_skill_manager.py` 顶部导入区追加：

```python
from app.agent.skills.loader import list_skills
```

文件末尾追加：

```python
def test_install_then_loader_discovers(tmp_path) -> None:
    """端到端串联:安装落盘后,发现原语能列出该 skill。"""
    skills_dir = tmp_path / "skills"
    manager = SkillManager(FakeStore(), skills_dir, importer=FakeImporter(_make_package()))

    slug = manager.install("owner/repo")

    assert list_skills(skills_dir) == [slug]
```

- [ ] **Step 2: 运行验证（表征测试，应直接通过）**

```bash
.venv/bin/python -m pytest tests/test_skill_manager.py -q
```

预期：`13 passed`。

- [ ] **Step 3: 提交**

```bash
git add backend/tests/test_skill_manager.py
git commit -m "test(skills): wire install through filesystem discovery"
```

### Task 10: README 文档

**Files:**
- Modify: `backend/app/agent/skills/README.md`
- Modify: `backend/README.md`

- [ ] **Step 1: 在基座 README 顶部插入基座层总述**

在 `backend/app/agent/skills/README.md` 的 `# skill-importer` 标题**之前**插入以下内容（原有 skill-importer 文档**一字不动**保留在其后，`pyproject.toml` 的 readme 指向该文件，包文档不能丢）：

````markdown
# skills 基座层(容器目录)

feibot 的 skill 导入适配与运行时发现层。整个目录零业务/数据库/Web 框架耦合
（有架构守卫测试保证：任何 `app.*` 导入都会让 `tests/test_skills.py` 失败），
可整体抽取为独立基座包供新项目复用。

## 构成

| 模块 | 职责 |
|---|---|
| `skill_importer/` | 导入协议：`import_skill(source)` 把来源解析为 `SkillPackage`（包文档见下文） |
| `loader.py` | 发现原语：`list_skills(skills_dir)` 列出含 `SKILL.md` 的子目录（不执行内容） |
| `manager.py` | 生命周期：`SkillManager(store, skills_dir)` 安装/卸载/启停/列表；持久化经 `SkillStore` 协议注入 |

## SKILL.md 合规要求

frontmatter 必须含 `name`，且 slug 与 name 完全一致：仅允许小写字母/数字/连字符
（`^[a-z0-9]+(?:-[a-z0-9]+)*$`，如 `daily-ai-news`）。缺失或不合规的包会被
`SkillManager.install` 拒绝；同名重装为幂等覆盖（升级路径）。

## 赋能新项目（步骤）

1. 安装（依赖 `httpx`、`pydantic`，Python >= 3.11；抽取打包后改为基座包名）：

   ```bash
   pip install <repo>/backend/app/agent/skills
   ```

2. 实现 `SkillStore` 协议（`upsert/get/list/delete/set_enabled` 五个方法）。
   内存版最小示例：

   ```python
   class MemorySkillStore:
       def __init__(self):
           self.rows = {}

       def upsert(self, slug, source, source_kind, enabled):
           self.rows[slug] = {"slug": slug, "source": source,
                              "source_kind": source_kind, "enabled": enabled}

       def get(self, slug):
           return self.rows.get(slug)

       def list(self):
           return [self.rows[s] for s in sorted(self.rows)]

       def delete(self, slug):
           return self.rows.pop(slug, None) is not None

       def set_enabled(self, slug, enabled):
           row = self.rows.get(slug)
           if row is None:
               return False
           row["enabled"] = enabled
           return True
   ```

   sqlite 宿主可参照 feibot 的 `app/db/skill_store.py`（透传到 store 函数）。

3. 装配管理器并调用：

   ```python
   from pathlib import Path
   from skill_importer import SkillImporter          # 导入协议(可选自定义)
   from <基座包>.manager import SkillManager          # 抽取打包后以实际包名导入

   manager = SkillManager(store=MemorySkillStore(), skills_dir=Path("/data/skills"))
   slug = manager.install("owner/repo/tree/main/skills/daily-ai-news")
   manager.list(); manager.disable(slug); manager.enable(slug); manager.uninstall(slug)
   ```

4. 把 `install/uninstall/enable/disable/list` 接上自己的 API / 渠道指令面；
   权限门控由宿主自行决定。

## 宿主清单（基座不提供，新项目需自备）

- `SkillStore` 持久化实现（表结构、slug 冲突策略归宿主）；
- skills 目录的路径配置（安装会按需创建子目录）；
- 用户交互面（API / 渠道指令）与安全门控；
- 基座**不含**：启动钩子（内容被动，落盘即生效）、agent 提示词注入、
  升级/批量指令、卸载二次确认。

---

````

- [ ] **Step 2: 更新 backend/README.md**

（a）目录结构小节中 `app/agent/` 一行，把 `提示词、skills、tools` 改为 `提示词、skills(导入基座 + 装/卸管理)、tools`。

（b）目录结构小节中 `.feibot/` 一行的 `<skill-id>` 改为 `<slug>`。

（c）「MCP 插件（装/卸远端工具）」小节之后、「模型配置」小节之前插入：

```markdown
## Skill 管理（装/卸技能包）

已安装技能位于运行时目录 `.feibot/skills/<slug>/`（不进 git），元数据持久化在 sqlite `installed_skills` 表；`slug` 即 SKILL.md frontmatter 的 `name`（要求小写字母/数字/连字符，不合规的包会被拒绝）。导入协议走基座 `skill_importer`（勿改），生命周期管理在 `agent/skills/manager.py`（基座层，依赖注入，零 app.* 耦合）。

管理员可在微信里用 `/skill` 系列指令管理技能：`/skill` 或 `/skill list` 查看已装技能，`/skill add <来源>` 装入（来源支持 GitHub URL/tree、raw SKILL.md、zip、平台 slug），`/skill remove <slug>` 卸下，`/skill enable|disable <slug>` 启停。非管理员不可用。
```

- [ ] **Step 3: 验证基座与受保护文件仍无差异**

```bash
git diff --exit-code HEAD -- backend/app/agent/skills/skill_importer backend/app/agent/skills/pyproject.toml backend/app/agent/mcp backend/app/channels/wechat backend/app/agent/tools/mcp_plugins.py
```

预期：exit code `0`，无输出。

- [ ] **Step 4: 提交**

```bash
git add backend/app/agent/skills/README.md backend/README.md
git commit -m "docs(skills): document skills base and host wiring"
```

### Task 11: 最终验证

**Files:** 仅验证，无计划内修改。

- [ ] **Step 1: 全量测试**

```bash
.venv/bin/python -m pytest tests -q
```

预期：exit code `0`，全部通过（132 个存量 + 本次新增约 30 个）。

- [ ] **Step 2: Ruff 检查改动文件**

```bash
.venv/bin/python -m ruff check \
  app/db/store.py app/db/skill_store.py \
  app/agent/skills/manager.py app/agent/skills/loader.py app/agent/skills/__init__.py \
  app/agent/slash.py app/agent/engine.py \
  tests/test_skill_store.py tests/test_skill_manager.py tests/test_skills.py \
  tests/test_slash.py tests/test_engine.py
```

预期：exit code `0`，`All checks passed!`。（全仓其余存量发现不属本任务，口径与 2026-08-20 迁移收尾一致。）

- [ ] **Step 3: 仓库形态与基座零差异**

```bash
git diff --exit-code HEAD -- backend/app/agent/skills/skill_importer backend/app/agent/skills/pyproject.toml backend/app/agent/mcp backend/app/channels/wechat backend/app/agent/tools/mcp_plugins.py
git status --short
git log -10 --oneline
```

预期：

- diff 命令 exit `0`（skill_importer、pyproject、mcp、wechat、mcp_plugins 零变化）；
- `git status --short` 无本任务未提交文件（原有两个无关未跟踪文档保持原样，不处理）；
- 日志含本计划 10 个提交（Task 1–10 各一个）。

## 验收清单（对照 spec §11）

- [ ] `app/agent/skills/` 下无任何 `app.*` 导入（守卫测试绿）
- [ ] `loader.list_skills(skills_dir)` 传参式，不再引用 `app.config`
- [ ] `SkillManager` 全依赖注入，无模块级单例（基座内）
- [ ] 安装：校验 → 路径防线 → 清目录 → 落盘 → 落库；不合规 name 被拒；同名幂等覆盖
- [ ] 卸载：删库 + 删目录，宽容幂等；启停只翻标志
- [ ] `/skill` 指令族管理员门控，异常全转文案
- [ ] `installed_skills` 表 + CRUD + SqliteSkillStore 适配器就位
- [ ] 两处 README 更新（含赋能步骤与宿主清单）
- [ ] 全量测试通过；改动文件 Ruff 零发现；skill_importer 与 pyproject 零差异
