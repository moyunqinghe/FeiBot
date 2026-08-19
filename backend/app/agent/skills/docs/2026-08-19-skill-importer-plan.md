# Skill Importer 纯协议层基座 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 StaffDeck `general_skills.py` 的远程技能源解析萃取为独立纯协议包 `skill-importer`（承载于 `feibot/backend/app/agent/skills/`），并回流 StaffDeck 验证零丢失。

**Architecture:** 纯协议层包（零业务/DB/Web 框架耦合），单一入口 `SkillImporter.import_skill(source) -> SkillPackage`；httpx 可注入 transport 实现离线测试；统一错误 `SkillImporterError(code, cause, message)`。StaffDeck 侧只留一层薄适配把 code 映射回现有中文/英文 detail。

**Tech Stack:** Python ≥3.11 · httpx · pydantic v2 · pytest（包）；FastAPI + SQLModel（StaffDeck 宿主）。

**参考 spec:** `docs/2026-08-19-skill-importer-design.md`（同目录）

**两仓库位置：**
- 包（feibot 仓库）：`/Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills/`
- 宿主（StaffDeck 仓库）：`/Users/moyunqinghe/工作/项目/StaffDeck/backend/`

---

## 文件总览

**feibot 新建：**

| 文件 | 职责 |
|---|---|
| `pyproject.toml` | 包元数据与依赖（httpx、pydantic；test: pytest） |
| `README.md` | 安装 / 快速上手 / 边界 / 错误码表 |
| `skill_importer/__init__.py` | 公开 API 导出 |
| `skill_importer/errors.py` | `SkillImporterError` + 10 个错误码常量 |
| `skill_importer/model.py` | `SkillFile`(pydantic) / `SkillPackage`(frozen dataclass) |
| `skill_importer/metadata.py` | frontmatter 解析 / `metadata_text` / `slugify` / `source_name` |
| `skill_importer/ziputil.py` | 路径清洗 / SKILL.md 定位 / `files_from_zip` / `normalize_skill_files` / `skill_markdown` |
| `skill_importer/resolver.py` | `SkillImporter` 入口 / `_Http` / `_load_remote` / 分发 / HTML 跳转 |
| `skill_importer/github.py` | GitHub repo/tree/blob/raw/archive 处理 |
| `tests/conftest.py` | MockTransport 工厂 |
| `tests/test_errors.py` `test_model.py` `test_metadata.py` `test_ziputil.py` `test_resolver.py` `test_github.py` | 离线测试 |

**StaffDeck 修改：**

| 文件 | 变更 |
|---|---|
| `backend/pyproject.toml` | 依赖增加 `skill-importer @ file:///Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills` |
| `backend/app/api/general_skills.py` | 远程源解析改用包；删除被取代函数；新增适配层 |
| `backend/tests/test_general_skills.py` | 远程导入用例从 monkeypatch 迁移到 MockTransport |

---

## Task 1: 脚手架 + 错误与数据模型

**Files:**
- Create: `pyproject.toml`
- Create: `skill_importer/__init__.py`
- Create: `skill_importer/errors.py`
- Create: `skill_importer/model.py`
- Create: `tests/test_errors.py`
- Create: `tests/test_model.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: 写 pyproject.toml 与包骨架**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "skill-importer"
version = "1.0.0"
description = "Pure protocol layer for importing skill packages from remote sources (open platforms, GitHub repo/tree/blob/raw/archive, raw SKILL.md, zip URLs, owner/repo shorthand)."
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
dependencies = [
    "httpx",
    "pydantic",
]

[project.optional-dependencies]
test = ["pytest"]

[tool.setuptools.packages.find]
include = ["skill_importer*"]
```

创建 `skill_importer/errors.py`：

```python
from __future__ import annotations

ERROR_SOURCE_INVALID = "SOURCE_INVALID"
ERROR_HTTP_ERROR = "HTTP_ERROR"
ERROR_CONNECT_FAILED = "CONNECT_FAILED"
ERROR_TIMEOUT = "TIMEOUT"
ERROR_TOO_LARGE = "TOO_LARGE"
ERROR_PACKAGE_INVALID = "PACKAGE_INVALID"
ERROR_SKILL_MD_MISSING = "SKILL_MD_MISSING"
ERROR_HTML_NOT_SKILL = "HTML_NOT_SKILL"
ERROR_REDIRECT_LOOP = "REDIRECT_LOOP"
ERROR_GITHUB_API_ERROR = "GITHUB_API_ERROR"


class SkillImporterError(Exception):
    """Unified protocol-layer error.

    ``code`` is a machine-readable constant (ERROR_* above); ``cause`` carries
    the underlying exception for logging; ``message`` is human-readable.
    """

    def __init__(
        self,
        code: str,
        message: str,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.cause = cause
        self.message = message
```

创建 `skill_importer/model.py`：

```python
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


class SkillFile(BaseModel):
    """One file of a skill package. Field names mirror the host's GeneralSkillFile."""

    path: str
    content: str
    size: int | None = None
    mime_type: str | None = None


@dataclass(frozen=True)
class SkillPackage:
    """Normalized skill package resolved from a remote source."""

    files: tuple[SkillFile, ...]
    skill_markdown: str
    metadata: dict[str, object] = field(default_factory=dict)
    name_hint: str | None = None
    slug_hint: str | None = None
    homepage_hint: str | None = None
    source_kind: str = "url"
```

创建 `skill_importer/__init__.py`（占位版，后续任务扩充）：

```python
from skill_importer.errors import (
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
from skill_importer.model import SkillFile, SkillPackage

__all__ = [
    "ERROR_CONNECT_FAILED",
    "ERROR_GITHUB_API_ERROR",
    "ERROR_HTML_NOT_SKILL",
    "ERROR_HTTP_ERROR",
    "ERROR_PACKAGE_INVALID",
    "ERROR_REDIRECT_LOOP",
    "ERROR_SKILL_MD_MISSING",
    "ERROR_SOURCE_INVALID",
    "ERROR_TIMEOUT",
    "ERROR_TOO_LARGE",
    "SkillFile",
    "SkillImporterError",
    "SkillPackage",
]
```

创建 `tests/conftest.py`：

```python
from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import httpx
import pytest

from skill_importer import SkillImporter


def make_zip(files: dict[str, str]) -> bytes:
    buf = BytesIO()
    with ZipFile(buf, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buf.getvalue()


def make_transport(routes: dict[str, object]) -> httpx.MockTransport:
    """routes: {url: response}; 响应为 bytes/str/httpx.Response，未命中返回 404。"""

    def handler(request: httpx.Request) -> httpx.Response:
        entry = routes.get(str(request.url))
        if entry is None:
            return httpx.Response(404, text="not found")
        if isinstance(entry, httpx.Response):
            return entry
        return httpx.Response(200, content=entry)

    return httpx.MockTransport(handler)


@pytest.fixture
def make_importer():
    def _make(routes: dict[str, object]) -> SkillImporter:
        return SkillImporter(transport=make_transport(routes))

    return _make


@pytest.fixture
def importer(make_importer):
    return make_importer({})


def files_dict(pkg) -> dict[str, str]:
    return {file.path: file.content for file in pkg.files}
```

- [ ] **Step 2: 写失败测试（errors + model）**

创建 `tests/test_errors.py`：

```python
from skill_importer import ERROR_SOURCE_INVALID, SkillImporterError


def test_error_carries_code_cause_and_message() -> None:
    cause = ValueError("boom")
    err = SkillImporterError(ERROR_SOURCE_INVALID, "bad source", cause=cause)
    assert err.code == ERROR_SOURCE_INVALID
    assert err.message == "bad source"
    assert err.cause is cause
    assert str(err) == "bad source"
```

创建 `tests/test_model.py`：

```python
import pytest

from skill_importer import SkillFile, SkillPackage


def test_skill_file_defaults() -> None:
    file = SkillFile(path="SKILL.md", content="# hi")
    assert file.size is None
    assert file.mime_type is None


def test_skill_package_is_frozen() -> None:
    pkg = SkillPackage(files=(), skill_markdown="x")
    with pytest.raises(Exception):
        pkg.skill_markdown = "y"  # type: ignore[misc]
```

- [ ] **Step 3: 运行测试确认失败（模块不存在）**

Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills && python -m pytest tests/test_errors.py tests/test_model.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'skill_importer'`

- [ ] **Step 4: 安装为可编辑包**

Run: `pip install -e /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills`
Expected: 成功安装 `skill-importer`

- [ ] **Step 5: 运行测试确认通过**

Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills && python -m pytest tests/test_errors.py tests/test_model.py -q`
Expected: 2 passed

- [ ] **Step 6: 提交**

```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/skills
git commit -m "feat(skills): skill-importer 脚手架(错误类型/数据模型/包配置)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: metadata 工具（frontmatter / slug / 名称提示）

**Files:**
- Create: `skill_importer/metadata.py`
- Create: `tests/test_metadata.py`
- Modify: `skill_importer/__init__.py`（导出 `parse_skill_metadata` / `metadata_text` / `slugify` / `source_name`）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_metadata.py`：

```python
from skill_importer.metadata import (
    metadata_text,
    parse_skill_metadata,
    slugify,
    source_name,
)


def test_parse_full_frontmatter() -> None:
    md = "---\nname: 天气包\nslug: weather-pack\ntags: [a, b]\n---\n\n# body\n"
    meta = parse_skill_metadata(md)
    assert meta["name"] == "天气包"
    assert meta["slug"] == "weather-pack"
    assert meta["tags"] == ["a", "b"]


def test_parse_no_frontmatter_returns_empty() -> None:
    assert parse_skill_metadata("# no frontmatter\n") == {}


def test_parse_ignores_malformed_lines() -> None:
    md = "---\n# comment\nbroken line\nkey: value\n---\n"
    assert parse_skill_metadata(md) == {"key": "value"}


def test_metadata_text_skips_non_string_and_empty() -> None:
    meta = {"name": "", "title": "Hello", "count": 3}
    assert metadata_text(meta, "name", "title") == "Hello"
    assert metadata_text(meta, "count") is None


def test_slugify() -> None:
    assert slugify("Hello World! 你好") == "hello-world"
    assert slugify("   ") == "general-skill"


def test_source_name() -> None:
    assert source_name("https://example.com/weather.zip") == "weather"
    assert source_name("weather-pack") == "weather-pack"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills && python -m pytest tests/test_metadata.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'skill_importer.metadata'`

- [ ] **Step 3: 实现**

创建 `skill_importer/metadata.py`：

```python
from __future__ import annotations

import re
from urllib.parse import urlparse


def parse_skill_metadata(markdown: str) -> dict[str, object]:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    metadata: dict[str, object] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return metadata
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            continue
        metadata[key] = _parse_metadata_value(value.strip())
    return metadata


def _parse_metadata_value(value: str) -> object:
    cleaned = value.strip().strip("'\"")
    if cleaned.startswith("[") and cleaned.endswith("]"):
        return [item.strip().strip("'\"") for item in cleaned[1:-1].split(",") if item.strip()]
    return cleaned


def metadata_text(metadata: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-_")
    return slug or "general-skill"


def source_name(source: str) -> str:
    parsed = urlparse(source)
    path = parsed.path if parsed.scheme else source
    cleaned = path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".zip").removesuffix(".md")
    if cleaned.startswith("upload:"):
        cleaned = cleaned.removeprefix("upload:")
    return cleaned or "open-platform-skill"
```

更新 `skill_importer/__init__.py`，在 import 段追加：

```python
from skill_importer.metadata import metadata_text, parse_skill_metadata, slugify, source_name
```

并在 `__all__` 追加 `"metadata_text"`, `"parse_skill_metadata"`, `"slugify"`, `"source_name"`。

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills && python -m pytest tests/test_metadata.py -q`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/skills
git commit -m "feat(skills): metadata 工具(frontmatter/slug/名称提示)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: ziputil 工具（路径清洗 / zip 解包 / 归一化）

**Files:**
- Create: `skill_importer/ziputil.py`
- Create: `tests/test_ziputil.py`
- Modify: `skill_importer/__init__.py`（导出 `normalize_skill_files` / `skill_markdown`）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_ziputil.py`：

```python
import pytest

from skill_importer import (
    ERROR_PACKAGE_INVALID,
    ERROR_SKILL_MD_MISSING,
    SkillFile,
    SkillImporterError,
    normalize_skill_files,
    skill_markdown,
)
from skill_importer.ziputil import files_from_zip

from conftest import make_zip


def test_normalize_single_markdown_becomes_skill_md() -> None:
    files = normalize_skill_files([], markdown="# hello")
    assert files == [SkillFile(path="SKILL.md", content="# hello", size=8, mime_type="text/markdown")]


def test_normalize_requires_content_when_no_files() -> None:
    with pytest.raises(SkillImporterError) as exc:
        normalize_skill_files([], markdown="  ")
    assert exc.value.code == ERROR_PACKAGE_INVALID


def test_normalize_strips_skill_folder_prefix() -> None:
    files = [
        SkillFile(path="skill-pack-main/weather/SKILL.md", content="---\nname: 天气包\n---\n"),
        SkillFile(path="skill-pack-main/weather/scripts/run.py", content="print('ok')\n"),
    ]
    normalized = normalize_skill_files(files)
    assert [file.path for file in normalized] == ["SKILL.md", "scripts/run.py"]


def test_normalize_rejects_missing_skill_md() -> None:
    with pytest.raises(SkillImporterError) as exc:
        normalize_skill_files([SkillFile(path="a.md", content="x")])
    assert exc.value.code == ERROR_SKILL_MD_MISSING


def test_normalize_rejects_path_traversal() -> None:
    with pytest.raises(SkillImporterError) as exc:
        normalize_skill_files([SkillFile(path="../evil", content="x"), SkillFile(path="SKILL.md", content="y")])
    assert exc.value.code == ERROR_PACKAGE_INVALID


def test_skill_markdown_returns_skill_md_content() -> None:
    md = skill_markdown([SkillFile(path="SKILL.md", content="# hi")])
    assert md == "# hi"


def test_files_from_zip_strips_common_root() -> None:
    data = make_zip(
        {
            "skill-pack-main/weather/SKILL.md": "---\nname: 天气包\n---\n",
            "skill-pack-main/weather/scripts/run.py": "print('ok')\n",
            "skill-pack-main/weather/data/cities.json": '{"北京": "101010100"}',
        }
    )
    files = files_from_zip(data, max_file_bytes=1024, max_files=100)
    assert [file.path for file in files] == ["SKILL.md", "scripts/run.py", "data/cities.json"]


def test_files_from_zip_with_subtree() -> None:
    data = make_zip(
        {
            "repo-main/weather/SKILL.md": "# weather\n",
            "repo-main/weather/tools/a.py": "a",
            "repo-main/other/b.py": "b",
        }
    )
    files = files_from_zip(data, subtree="weather", max_file_bytes=1024, max_files=100)
    assert [file.path for file in files] == ["SKILL.md", "tools/a.py"]


def test_files_from_zip_missing_skill_md() -> None:
    data = make_zip({"repo-main/readme.md": "no skill here"})
    with pytest.raises(SkillImporterError) as exc:
        files_from_zip(data, max_file_bytes=1024, max_files=100)
    assert exc.value.code == ERROR_SKILL_MD_MISSING


def test_files_from_zip_skips_bad_dirs_and_respects_limits() -> None:
    data = make_zip(
        {
            "pkg/__MACOSX/SKILL.md": "x",
            "pkg/.git/config": "x",
            "pkg/SKILL.md": "# ok\n",
            "pkg/big.txt": "z" * 2048,
            "pkg/extra.py": "y",
        }
    )
    files = files_from_zip(data, max_file_bytes=1024, max_files=10)
    assert [file.path for file in files] == ["SKILL.md", "extra.py"]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills && python -m pytest tests/test_ziputil.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'skill_importer.ziputil'`

- [ ] **Step 3: 实现**

创建 `skill_importer/ziputil.py`：

```python
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
        raise SkillImporterError(ERROR_PACKAGE_INVALID, f"invalid skill file path: {path}")
    return "/".join(parts)


def _find_skill_file(files: list[SkillFile]) -> SkillFile | None:
    return next(
        (file for file in files if file.path.rsplit("/", 1)[-1].lower() == "skill.md"),
        None,
    )


def skill_markdown(files: list[SkillFile]) -> str:
    skill_file = _find_skill_file(files)
    if not skill_file or not skill_file.content.strip():
        raise SkillImporterError(ERROR_SKILL_MD_MISSING, "SKILL.md cannot be empty")
    return skill_file.content


def normalize_skill_files(
    files: list[SkillFile],
    markdown: str | None = None,
) -> list[SkillFile]:
    if not files:
        if not (markdown or "").strip():
            raise SkillImporterError(ERROR_PACKAGE_INVALID, "skill markdown cannot be empty")
        return [
            SkillFile(
                path="SKILL.md",
                content=markdown,
                size=len(markdown.encode("utf-8")),
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
        raise SkillImporterError(ERROR_SKILL_MD_MISSING, "skill folder must contain SKILL.md")
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
            raise SkillImporterError(ERROR_SKILL_MD_MISSING, "package does not contain SKILL.md")
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
```

更新 `skill_importer/__init__.py`：import 段追加

```python
from skill_importer.ziputil import (
    clean_package_path,
    files_from_zip,
    normalize_skill_files,
    skill_markdown,
)
```

`__all__` 追加 `"clean_package_path"`, `"files_from_zip"`, `"normalize_skill_files"`, `"skill_markdown"`。

> 为导出，需在 ziputil.py 中为 `_clean_package_path` 与 `files_from_zip` 各加一行公开别名：

```python
clean_package_path = _clean_package_path
```

（`files_from_zip` 已是公开名，直接导出即可。）

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills && python -m pytest tests/test_ziputil.py -q`
Expected: 10 passed

- [ ] **Step 5: 提交**

```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/skills
git commit -m "feat(skills): zip 解包与技能包归一化工具

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: HTTP 层 + import_skill 分发（平台 / owner-repo / raw md / zip / HTML 跳转）

**Files:**
- Create: `skill_importer/resolver.py`
- Create: `tests/test_resolver.py`
- Modify: `skill_importer/__init__.py`（导出 `SkillImporter`）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_resolver.py`：

```python
import httpx
import pytest

from skill_importer import (
    ERROR_HTML_NOT_SKILL,
    ERROR_REDIRECT_LOOP,
    ERROR_SOURCE_INVALID,
    ERROR_TIMEOUT,
    ERROR_TOO_LARGE,
    SkillImporter,
    SkillImporterError,
)

from conftest import files_dict, make_zip


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


def test_import_redirect_loop_detected(make_importer) -> None:
    importer = make_importer(
        {
            "https://a.example/skill": "<html><a href='https://b.example/skill'>b</a></html>",
            "https://b.example/skill": "<html><a href='https://a.example/skill'>a</a></html>",
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
    def handler(request):  # noqa: ANN001
        raise httpx.ConnectTimeout("boom")

    importer = SkillImporter(transport=httpx.MockTransport(handler))
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("https://example.com/SKILL.md")
    assert exc.value.code == ERROR_TIMEOUT


def test_import_too_large_maps_to_too_large_error(make_importer) -> None:
    importer = make_importer({"https://example.com/SKILL.md": b"x" * (96 * 1024 * 1024 + 1)})
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("https://example.com/SKILL.md")
    assert exc.value.code == ERROR_TOO_LARGE
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills && python -m pytest tests/test_resolver.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'skill_importer.resolver'`

- [ ] **Step 3: 实现 resolver.py**

创建 `skill_importer/resolver.py`：

```python
from __future__ import annotations

import json
import re
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

    def download(self, url: str) -> tuple[bytes, str]:
        try:
            response = self._client.get(url, headers={"User-Agent": self._config.user_agent})
        except httpx.TimeoutException as exc:
            raise SkillImporterError(ERROR_TIMEOUT, "download timed out", cause=exc) from exc
        except httpx.TransportError as exc:
            raise SkillImporterError(
                ERROR_CONNECT_FAILED, f"download failed: {exc}", cause=exc
            ) from exc
        if response.status_code >= 400:
            raise SkillImporterError(
                ERROR_HTTP_ERROR, f"download failed with HTTP {response.status_code}"
            )
        data = response.content
        if len(data) > self._config.max_package_bytes:
            raise SkillImporterError(ERROR_TOO_LARGE, "skill package is too large")
        return data, response.headers.get("content-type", "")

    def download_json(self, url: str) -> object:
        data, _ = self.download(url)
        try:
            return json.loads(_decode_text(data))
        except json.JSONDecodeError as exc:
            raise SkillImporterError(
                ERROR_GITHUB_API_ERROR, "GitHub API returned invalid JSON", cause=exc
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
            return self._load_remote(cleaned)
        if _looks_like_github_shorthand(cleaned):
            return self._load_remote(f"https://github.com/{cleaned}")
        raise SkillImporterError(
            ERROR_SOURCE_INVALID,
            "source must be a platform slug, GitHub URL, raw SKILL.md URL, "
            "zip URL, or owner/repo path",
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
                ERROR_SOURCE_INVALID, "remote skill source must be a valid URL"
            )
        if normalized_url in visited:
            raise SkillImporterError(
                ERROR_REDIRECT_LOOP, "remote skill source redirects to itself"
            )
        if len(visited) >= self._config.max_indirections:
            raise SkillImporterError(
                ERROR_REDIRECT_LOOP, "remote skill source contains too many indirections"
            )
        next_visited = visited | {normalized_url}
        if parsed.netloc in self._config.github_hosts:
            return load_github_source(parsed, http=self._http, config=self._config)
        data, content_type = self._http.download(normalized_url)
        lower_content_type = content_type.lower()
        if parsed.path.lower().endswith(".zip") or "zip" in lower_content_type:
            from skill_importer.ziputil import files_from_zip

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
                ERROR_HTML_NOT_SKILL,
                "open-platform page exposes no downloadable skill package or GitHub "
                "directory; HTML pages are not imported as SKILL.md",
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
            ERROR_SOURCE_INVALID,
            "remote source must be a zip package, GitHub skill directory, or raw "
            "Markdown skill file",
        )


def _required_text(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise SkillImporterError(ERROR_SOURCE_INVALID, "source cannot be empty")
    return cleaned


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
    return None


def _is_clawhub_download_url(parsed) -> bool:
    path = parsed.path.lower().rstrip("/")
    return path.endswith("/api/v1/download") and "slug=" in parsed.query.lower()
```

> 注：`_load_remote` 里 `files_from_zip` 用函数内导入以避免模块级循环导入（github.py 也要用它）。

更新 `skill_importer/__init__.py`：import 段追加 `from skill_importer.resolver import SkillImporter`，`__all__` 追加 `"SkillImporter"`。

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills && python -m pytest tests/test_resolver.py -q`
Expected: 12 passed（`test_import_timeout_maps_to_timeout_error` 若因 MockTransport 行为差异跳过则记录并后续统一处理，其余必须全绿）

- [ ] **Step 5: 提交**

```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/skills
git commit -m "feat(skills): import_skill 入口与远程源分发(平台/owner-repo/raw md/zip/html)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: GitHub 加载（repo / tree / blob / raw / archive）

**Files:**
- Create: `skill_importer/github.py`
- Create: `tests/test_github.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_github.py`：

```python
import httpx
import pytest

from skill_importer import ERROR_SKILL_MD_MISSING, SkillImporterError

from conftest import files_dict, make_zip


def test_github_blob_url_downloads_raw(make_importer) -> None:
    importer = make_importer(
        {
            "https://raw.githubusercontent.com/owner/repo/main/skills/x/SKILL.md": "# blob\n"
        }
    )
    pkg = importer.import_skill("https://github.com/owner/repo/blob/main/skills/x/SKILL.md")
    assert files_dict(pkg) == {"SKILL.md": "# blob\n"}


def test_github_raw_host_single_file(make_importer) -> None:
    importer = make_importer(
        {"https://raw.githubusercontent.com/owner/repo/main/SKILL.md": "# raw\n"}
    )
    pkg = importer.import_skill(
        "https://raw.githubusercontent.com/owner/repo/main/SKILL.md"
    )
    assert files_dict(pkg) == {"SKILL.md": "# raw\n"}


def test_github_tree_directory_via_api(make_importer) -> None:
    importer = make_importer(
        {
            "https://api.github.com/repos/owner/repo/contents/skills/weather?ref=main": [
                {
                    "type": "file",
                    "path": "skills/weather/SKILL.md",
                    "size": 10,
                    "download_url": "https://raw.githubusercontent.com/owner/repo/main/skills/weather/SKILL.md",
                },
                {
                    "type": "dir",
                    "path": "skills/weather/tools",
                },
            ],
            "https://api.github.com/repos/owner/repo/contents/skills/weather/tools?ref=main": [
                {
                    "type": "file",
                    "path": "skills/weather/tools/run.py",
                    "size": 5,
                    "download_url": "https://raw.githubusercontent.com/owner/repo/main/skills/weather/tools/run.py",
                }
            ],
            "https://raw.githubusercontent.com/owner/repo/main/skills/weather/SKILL.md": "# tree\n",
            "https://raw.githubusercontent.com/owner/repo/main/skills/weather/tools/run.py": "print(1)\n",
        }
    )
    pkg = importer.import_skill("https://github.com/owner/repo/tree/main/skills/weather")
    assert files_dict(pkg) == {"SKILL.md": "# tree\n", "tools/run.py": "print(1)\n"}


def test_github_repo_root_tries_main_then_master_then_archive(make_importer) -> None:
    importer = make_importer(
        {
            "https://api.github.com/repos/owner/repo/contents?ref=main": httpx.Response(404),
            "https://api.github.com/repos/owner/repo/contents?ref=master": httpx.Response(404),
            "https://github.com/owner/repo/archive/refs/heads/main.zip": make_zip(
                {"repo-main/SKILL.md": "# archive\n"}
            ),
        }
    )
    pkg = importer.import_skill("owner/repo")
    assert pkg.skill_markdown == "# archive\n"


def test_github_directory_without_skill_md_raises(make_importer) -> None:
    importer = make_importer(
        {
            "https://api.github.com/repos/owner/repo/contents?ref=main": [
                {"type": "file", "path": "readme.md", "size": 5,
                 "download_url": "https://raw.githubusercontent.com/owner/repo/main/readme.md"}
            ],
            "https://raw.githubusercontent.com/owner/repo/main/readme.md": "hello",
        }
    )
    with pytest.raises(SkillImporterError) as exc:
        importer.import_skill("owner/repo")
    assert exc.value.code == ERROR_SKILL_MD_MISSING
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills && python -m pytest tests/test_github.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'skill_importer.github'`

- [ ] **Step 3: 实现 github.py**

创建 `skill_importer/github.py`：

```python
from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

from skill_importer.errors import (
    ERROR_CONNECT_FAILED,
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
                ERROR_SOURCE_INVALID,
                "raw GitHub source must include owner, repo, branch and path",
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
            ERROR_SOURCE_INVALID, "GitHub source must include owner and repository"
        )
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if len(parts) >= 3 and parts[2] == "archive":
        data, _ = http.download(parsed.geturl())
        return files_from_zip(
            data,
            max_file_bytes=config.max_file_bytes,
            max_files=config.max_files,
        )
    if len(parts) >= 5 and parts[2] in {"blob", "raw"}:
        branch = parts[3]
        file_path = "/".join(parts[4:])
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{file_path}"
        data, content_type = http.download(raw_url)
        return [
            SkillFile(
                path=file_path.rsplit("/", 1)[-1] or "SKILL.md",
                content=_decode_text(data),
                size=len(data),
                mime_type=content_type or "text/markdown",
            )
        ]
    if len(parts) >= 5 and parts[2] == "tree":
        branch = parts[3]
        subtree = "/".join(parts[4:])
        return _download_github_directory(http, config, owner, repo, branch, subtree)
    subtree = "/".join(parts[2:]) if len(parts) > 2 else ""
    errors: list[SkillImporterError] = []
    for branch in ["main", "master"]:
        try:
            return _download_github_directory(http, config, owner, repo, branch, subtree)
        except SkillImporterError as exc:
            errors.append(exc)
    return _download_github_archive(http, config, owner, repo, ["main", "master"], subtree)


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
            ERROR_SKILL_MD_MISSING, "GitHub directory does not contain SKILL.md"
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
    raise SkillImporterError(
        ERROR_CONNECT_FAILED, f"unable to download GitHub skill package: {detail}"
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills && python -m pytest tests/ -q`
Expected: 全量通过（errors/model/metadata/ziputil/resolver/github 合计 35 个用例全绿）

- [ ] **Step 5: 提交**

```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/skills
git commit -m "feat(skills): GitHub 技能源加载(repo/tree/blob/raw/archive, main/master 回退)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: README 与包收尾

**Files:**
- Create: `README.md`

- [ ] **Step 1: 写 README**

创建 `README.md`（内容见 spec §11 示例 + 安装方式 + 边界 + 错误码表，核心小节）：

```markdown
# skill-importer

技能包导入的通用纯协议层，零业务/数据库/Web 框架耦合。给定一个来源字符串，
解析并归一化出标准技能包（文件集 + SKILL.md + frontmatter 元数据）。

支持：开源平台 slug/URL、GitHub repo/tree/blob/raw/archive、raw SKILL.md、
zip URL、owner/repo 简写。

- 不依赖任何业务代码，不含数据库、不含 fastapi/sqlmodel——所有参数显式传入。
- 依赖：`httpx`、`pydantic`。要求 Python >= 3.11。

## 安装

```bash
pip install /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills
pip install -e /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills   # 调试
```

## 快速上手

```python
from skill_importer import SkillImporter, SkillImporterError

importer = SkillImporter()          # 全默认
pkg = importer.import_skill("weather-pack")                      # 开源平台 slug
pkg = importer.import_skill("owner/repo/tree/main/skills/weather")  # GitHub tree
pkg = importer.import_skill("https://raw.githubusercontent.com/owner/repo/main/SKILL.md")
pkg = importer.import_skill("owner/repo")                        # 自动探测 main/master
for file in pkg.files:
    print(file.path, len(file.content))
print(pkg.skill_markdown, pkg.slug_hint, pkg.name_hint)
```

## 本包的边界（使用方自己决定的事）

- 技能包存哪、表结构、权限模型 —— 宿主的存储设计。
- slug 冲突策略（-2 后缀等）、最终 name/slug/homepage —— 由宿主从 hints 决定。
- 下载结果如何落库 / 如何接入 agent 工具系统。

## 错误

统一 `SkillImporterError(code, cause, message)`，code 取值：
`SOURCE_INVALID / HTTP_ERROR / CONNECT_FAILED / TIMEOUT / TOO_LARGE /
PACKAGE_INVALID / SKILL_MD_MISSING / HTML_NOT_SKILL / REDIRECT_LOOP / GITHUB_API_ERROR`

## 运行测试

```bash
pip install "/path/to/skills[test]"
pytest /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills/tests
```
全部离线运行：纯函数 + httpx.MockTransport，不发真实网络请求。
```

- [ ] **Step 2: 最终校验（全新进程导入 + 全量测试）**

Run: `cd /tmp && python -c "import skill_importer; print(skill_importer.SkillImporter, skill_importer.__file__)"`
Expected: 打印 SkillImporter 类与其安装路径
Run: `cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills && python -m pytest tests/ -q`
Expected: 全绿

- [ ] **Step 3: 提交**

```bash
cd /Users/moyunqinghe/个人/学习/feibot
git add backend/app/agent/skills
git commit -m "docs(skills): skill-importer README 与收尾

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: StaffDeck 回流（依赖 + 适配层 + 替换 + 删除死代码）

> 本任务在 **StaffDeck 仓库**（`/Users/moyunqinghe/工作/项目/StaffDeck`）执行。前提：Task 1 Step 4 已把 `skill-importer` 装进环境。

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/api/general_skills.py`
- Modify: `backend/app/general_skills/schema.py`（如需，见 Step 4 说明）

- [ ] **Step 1: 加依赖**

在 `backend/pyproject.toml` 的 `dependencies` 列表末尾追加：

```toml
  "skill-importer @ file:///Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills",
```

- [ ] **Step 2: 增加 import 与适配层**

在 `backend/app/api/general_skills.py` 顶部 import 段追加：

```python
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
    SkillImporter,
    SkillImporterError,
    SkillPackage,
    files_from_zip,
    metadata_text,
    normalize_skill_files,
    parse_skill_metadata,
    skill_markdown,
    slugify,
)
```

在原 `MAX_CLAWHUB_PACKAGE_BYTES` 等常量附近新增适配层（保留原常量值）：

```python
_IMPORT_ERROR_MESSAGES: dict[str, str] = {
    ERROR_SOURCE_INVALID: "开源平台来源必须是开源平台 slug、GitHub URL、raw SKILL.md URL、zip URL 或 owner/repo 路径",
    ERROR_HTTP_ERROR: "下载技能包失败，请检查地址后重试",
    ERROR_CONNECT_FAILED: "下载技能包失败，请检查地址后重试",
    ERROR_TIMEOUT: "下载技能包超时，请稍后重试",
    ERROR_TOO_LARGE: "技能包过大，请使用更小的包",
    ERROR_PACKAGE_INVALID: "技能包内容无效",
    ERROR_SKILL_MD_MISSING: "技能包中未找到 SKILL.md",
    ERROR_HTML_NOT_SKILL: "开源平台页面没有暴露可下载的技能包或 GitHub 目录。HTML 页面不会被当作 SKILL.md 导入。",
    ERROR_REDIRECT_LOOP: "远程技能来源包含过多跳转",
    ERROR_GITHUB_API_ERROR: "GitHub API 返回无效数据",
}


def _skill_import_error_detail(exc: SkillImporterError) -> str:
    return _IMPORT_ERROR_MESSAGES.get(exc.code, exc.message or "导入技能失败")


_skill_importer: SkillImporter | None = None


def _get_skill_importer() -> SkillImporter:
    global _skill_importer
    if _skill_importer is None:
        _skill_importer = SkillImporter()
    return _skill_importer


def _load_skill_package(source: str) -> SkillPackage:
    try:
        return _get_skill_importer().import_skill(source)
    except SkillImporterError as exc:
        raise HTTPException(status_code=400, detail=_skill_import_error_detail(exc)) from exc


def _normalize_skill_files(
    requested_files: list[GeneralSkillFile],
    markdown: str | None,
) -> list[GeneralSkillFile]:
    try:
        files = normalize_skill_files(requested_files, markdown)
        return [GeneralSkillFile.model_validate(file) for file in files]
    except SkillImporterError as exc:
        raise HTTPException(status_code=400, detail=_skill_import_error_detail(exc)) from exc


def _skill_markdown_from_files(files: list[GeneralSkillFile]) -> str:
    try:
        return skill_markdown(files)
    except SkillImporterError as exc:
        raise HTTPException(status_code=400, detail=_skill_import_error_detail(exc)) from exc
```

- [ ] **Step 3: 改写两个导入端点 + 上传端点**

`import_skillhub_skill`（`import_clawhub_skill` 委托它，无需改）改为：

```python
@router.post("/import-skillhub", response_model=GeneralSkillRead)
def import_skillhub_skill(
    request: GeneralSkillClawHubImportRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> GeneralSkillRead:
    ensure_tenant(db, request.tenant_id)
    pkg = _load_skill_package(request.source)
    return _create_imported_general_skill(
        db,
        tenant_id=request.tenant_id,
        pkg=pkg,
        import_source=request.source,
        agent_id=request.agent_id,
        status=request.status,
        name=request.name,
        slug=request.slug,
        description=request.description,
        homepage=request.homepage,
        capability_scope=request.capability_scope,
        current_user=current_user,
    )
```

`import_general_skill_package` 的 zip 分支改为用包的 `files_from_zip`（md 分支不变）：

```python
    data = _decode_base64_payload(request.content_base64)
    if filename.lower().endswith(".zip"):
        try:
            raw_files = files_from_zip(
                data,
                max_file_bytes=MAX_CLAWHUB_FILE_BYTES,
                max_files=MAX_CLAWHUB_FILES,
            )
        except SkillImporterError as exc:
            raise HTTPException(status_code=400, detail=_skill_import_error_detail(exc)) from exc
    elif filename.lower().endswith((".md", ".markdown", ".txt")):
        text = data.decode("utf-8", errors="replace")
        raw_files = [
            GeneralSkillFile(
                path="SKILL.md",
                content=text,
                size=len(data),
                mime_type=(
                    "text/markdown"
                    if filename.lower().endswith((".md", ".markdown"))
                    else "text/plain"
                ),
            )
        ]
    else:
        raise HTTPException(
            status_code=400, detail="Uploaded skill package must be a .zip or Markdown file"
        )
```

并把其后续调用改为传 `pkg`（由 `raw_files` 构造）：

```python
    files = _normalize_skill_files(raw_files, None)
    return _create_imported_general_skill(
        db,
        tenant_id=request.tenant_id,
        pkg=_package_from_files(files, request.filename),
        import_source=f"upload:{request.filename}",
        agent_id=request.agent_id,
        status=request.status,
        name=request.name,
        slug=request.slug,
        description=request.description,
        homepage=request.homepage,
        capability_scope=request.capability_scope,
        current_user=current_user,
    )
```

新增辅助 `_package_from_files`：

```python
def _package_from_files(files: list[GeneralSkillFile], source: str) -> SkillPackage:
    markdown = _skill_markdown_from_files(files)
    metadata = dict(parse_skill_metadata(markdown))
    return SkillPackage(
        files=tuple(files),
        skill_markdown=markdown,
        metadata=metadata,
        name_hint=metadata_text(metadata, "name", "title"),
        slug_hint=metadata_text(metadata, "slug", "id"),
        homepage_hint=metadata_text(metadata, "homepage", "url", "source"),
        source_kind="upload",
    )
```

> 注：上传端点因没有 `SkillPackage` 全量字段（无 name_hint 兜底、无 homepage 派生），统一走 `_package_from_files`；行为与现状一致（现状上传路径同样只从 frontmatter 取 name/slug）。

- [ ] **Step 4: 改写 `_create_imported_general_skill` 签名**

把参数 `files: list[GeneralSkillFile]` 换成 `pkg: SkillPackage`，函数体相应替换（其余权限/绑定逻辑不动）：

```python
def _create_imported_general_skill(
    db: Session,
    *,
    tenant_id: str,
    pkg: SkillPackage,
    import_source: str,
    agent_id: str | None,
    status: str,
    name: str | None = None,
    slug: str | None = None,
    description: str | None = None,
    homepage: str | None = None,
    capability_scope: str = "general",
    current_user: object | None = None,
) -> GeneralSkillRead:
    markdown = pkg.skill_markdown
    metadata = dict(pkg.metadata)
    resolved_name = _optional_text(name) or pkg.name_hint or "未命名通用技能"
    slug_base = (
        _optional_text(slug)
        or pkg.slug_hint
        or _slugify(resolved_name)
    )
    resolved_slug = _unique_slug(db, tenant_id, slug_base)
    resolved_description = _optional_text(description) or metadata_text(
        metadata, "description", "summary"
    )
    resolved_homepage = _optional_text(homepage) or pkg.homepage_hint
    _validate_slug(resolved_slug)
    now = utc_now()
    resolved_agent_id = _agent_id_or_none(agent_id)
    agent = ensure_agent_scope_manager(db, tenant_id, resolved_agent_id, current_user)
    row = GeneralSkill(
        tenant_id=tenant_id,
        slug=resolved_slug,
        name=resolved_name,
        description=resolved_description,
        homepage=resolved_homepage,
        skill_markdown=markdown,
        skill_files_json=[file.model_dump(mode="json") for file in pkg.files],
        metadata_json=user_creator_metadata(
            current_user, {**metadata, "import_source": import_source}
        ),
        status=status,
        capability_scope=normalize_capability_scope(capability_scope),
        permissions_json={"network": True, "python": True},
        runtime_config_json={"runtime": "python", "timeout_seconds": 12},
        created_at=now,
        updated_at=now,
    )
    # —— 以下与现状完全一致，原样保留 ——
    if not (agent and not agent.is_overall):
        ensure_open_gallery_admin(tenant_id, current_user)
    if agent and not agent.is_overall:
        mark_resource_private_for_agent(row, agent.id, row.metadata_json or {})
    else:
        mark_resource_open_gallery(row, row.metadata_json or {})
    db.add(row)
    db.flush()
    if agent and not agent.is_overall:
        ensure_private_resource_binding(
            db,
            tenant_id,
            agent.id,
            "general_skill",
            row.id,
            "active" if status == "published" else "inactive",
            metadata_json=row.metadata_json or {},
        )
    else:
        ensure_open_gallery_binding(
            db,
            tenant_id,
            "general_skill",
            row.id,
            "active" if status == "published" else "inactive",
            metadata_json=row.metadata_json or {},
        )
    db.commit()
    db.refresh(row)
    return general_skill_read(row)
```

同时把 `import_general_skill`（POST /import）中对的 `_parse_skill_metadata` → `parse_skill_metadata`、`_metadata_text` → `metadata_text`、`_slugify` → `slugify`、`_skill_markdown_from_files(files)`（已改为包装版）原样保留。

- [ ] **Step 5: 删除被取代的函数**

删除以下函数（由包取代）：`_load_clawhub_source`、`_load_clawhub_skill_package`、`_load_remote_skill_source`、`_load_github_skill_source`、`_download_github_directory`、`_download_github_directory_contents`、`_download_github_archive`、`_download_json`、`_download_url`、`_files_from_zip`、`_zip_relative_path`、`_skip_package_path`、`_decode_text`、`_guess_mime_type`、`_extract_skill_source_from_html`、`_is_clawhub_download_url`、`_looks_like_html_response`、`_looks_like_markdown_source`、`_looks_like_github_shorthand`、`_clawhub_slug_from_source`、`_valid_clawhub_slug`、`_clawhub_homepage_from_source`、`_clawhub_download_url`、`_parse_skill_metadata`、`_parse_metadata_value`、`_metadata_text`、`_slugify`、`_source_name`、`_required_text`、`_find_skill_file`、`_clean_package_path`、`_normalize_skill_files`（替换为包装版）、`_skill_markdown_from_files`（替换为包装版）。

> 注意保留：`_unique_slug`、`_validate_slug`、`_optional_text`、`_skill_directories_from_values`、`_skill_directories`、`_skill_files_or_markdown`、`_get_general_skill` 等宿主逻辑。
> `_skill_directories_from_values` 内部使用的 `_clean_package_path` 改为 `skill_importer.clean_package_path`（ziputil 公开别名），并捕获 `SkillImporterError` 映射为原中文/英文 detail（或直接 `raise HTTPException(400, detail=str(exc))`，与现状等价的"General skill directory conflicts..."行为）。

同步清理不再使用的 import：`zipfile`、`BytesIO`、`urlopen`、`Request`、`urljoin`、`quote`/`unquote`（若仅被删函数使用）、`html.unescape`、`re`（若仅被删函数使用）、`json`（若仅被删函数使用）、`HTTPError`/`URLError`（若仅被删函数使用）。用 `ruff check backend/app/api/general_skills.py` 确认无未使用 import。

- [ ] **Step 6: 安装依赖并跑现有测试**

Run: `cd /Users/moyunqinghe/工作/项目/StaffDeck/backend && pip install -e /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills && python -m pytest tests/test_general_skills.py -q -x`
Expected: 部分远程导入用例因 monkeypatch 目标函数已删除而 FAIL（这正是 Task 8 迁移对象），其余（CRUD/权限/runner 等）必须全部 PASS。

- [ ] **Step 7: 提交（StaffDeck）**

```bash
cd /Users/moyunqinghe/工作/项目/StaffDeck
git add backend/pyproject.toml backend/app/api/general_skills.py
git commit -m "refactor(skills): 远程技能源解析改为复用 skill-importer 纯协议包

删除被取代的下载/解包/解析函数，保留适配层(错误映射)与宿主 CRUD/权限逻辑。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: StaffDeck 测试迁移（monkeypatch → MockTransport）与全量回归

> 本任务在 **StaffDeck 仓库**执行。迁移目标：`backend/tests/test_general_skills.py` 中 6 个远程导入用例，把 `monkeypatch _download_url / _download_json` 换成注入 `SkillImporter(transport=MockTransport(...))`。

**Files:**
- Modify: `backend/tests/test_general_skills.py`

- [ ] **Step 1: 加共享辅助函数**

在 `test_general_skills.py` 顶部 import 段追加：

```python
from httpx import MockTransport, Response

from skill_importer import SkillImporter


def _importer_with_routes(routes: dict[str, object]) -> SkillImporter:
    """构造一个命中 routes 表（url -> bytes/str/Response）的离线 importer。"""

    def handler(request):
        entry = routes.get(str(request.url))
        if entry is None:
            return Response(404, text="not found")
        if isinstance(entry, Response):
            return entry
        return Response(200, content=entry)

    return SkillImporter(transport=MockTransport(handler))


def _patch_importer(monkeypatch, routes: dict[str, object]) -> None:
    monkeypatch.setattr(
        "app.api.general_skills._get_skill_importer",
        lambda: _importer_with_routes(routes),
    )
```

- [ ] **Step 2: 迁移 6 个用例（整段替换原函数体）**

替换 `test_import_clawhub_skill_reads_zip_package_without_overwriting`：

```python
def test_import_clawhub_skill_reads_zip_package_without_overwriting(monkeypatch) -> None:
    package = BytesIO()
    with ZipFile(package, "w") as archive:
        archive.writestr(
            "skill-pack-main/weather/SKILL.md",
            "---\nname: 天气包\nslug: weather-pack\n---\n\n# 天气包\n",
        )
        archive.writestr("skill-pack-main/weather/scripts/run.py", "print('ok')\n")
        archive.writestr("skill-pack-main/weather/data/cities.json", '{"北京": "101010100"}')

    _patch_importer(
        monkeypatch,
        {"https://example.com/weather.zip": package.getvalue()},
    )

    with _test_session() as db:
        _seed_minimal_tenant(db)
        first = import_clawhub_skill(
            GeneralSkillClawHubImportRequest(
                tenant_id="tenant_demo", source="https://example.com/weather.zip"
            ),
            db,
            _admin_user(),
        )
        second = import_clawhub_skill(
            GeneralSkillClawHubImportRequest(
                tenant_id="tenant_demo", source="https://example.com/weather.zip"
            ),
            db,
            _admin_user(),
        )

        assert first.slug == "weather-pack"
        assert second.slug == "weather-pack-2"
        assert [file.path for file in first.skill_files] == [
            "SKILL.md",
            "scripts/run.py",
            "data/cities.json",
        ]
        assert first.skill_markdown.startswith("---\nname: 天气包")
```

替换 `test_import_clawhub_skill_reads_github_directory_package`：

```python
def test_import_clawhub_skill_reads_github_directory_package(monkeypatch) -> None:
    _patch_importer(
        monkeypatch,
        {
            "https://api.github.com/repos/example/skill-pack/contents/weather?ref=main": [
                {
                    "type": "file",
                    "path": "weather/SKILL.md",
                    "download_url": "https://raw.githubusercontent.com/example/skill-pack/main/weather/SKILL.md",
                    "size": 46,
                },
                {"type": "dir", "path": "weather/scripts"},
                {
                    "type": "file",
                    "path": "weather/data/cities.json",
                    "download_url": "https://raw.githubusercontent.com/example/skill-pack/main/weather/data/cities.json",
                    "size": 24,
                },
            ],
            "https://api.github.com/repos/example/skill-pack/contents/weather/scripts?ref=main": [
                {
                    "type": "file",
                    "path": "weather/scripts/run.py",
                    "download_url": "https://raw.githubusercontent.com/example/skill-pack/main/weather/scripts/run.py",
                    "size": 12,
                }
            ],
            "https://raw.githubusercontent.com/example/skill-pack/main/weather/SKILL.md": "---\nname: 目录天气\nslug: weather-dir\n---\n\n# 天气\n",
            "https://raw.githubusercontent.com/example/skill-pack/main/weather/scripts/run.py": "print('ok')\n",
            "https://raw.githubusercontent.com/example/skill-pack/main/weather/data/cities.json": '{"北京":"101010100"}',
        },
    )

    with _test_session() as db:
        _seed_minimal_tenant(db)
        row = import_clawhub_skill(
            GeneralSkillClawHubImportRequest(
                tenant_id="tenant_demo",
                source="https://github.com/example/skill-pack/tree/main/weather",
            ),
            db,
            _admin_user(),
        )

        assert row.slug == "weather-dir"
        assert [file.path for file in row.skill_files] == [
            "SKILL.md",
            "scripts/run.py",
            "data/cities.json",
        ]
        assert row.skill_files[1].content == "print('ok')\n"
```

替换 `test_import_clawhub_skill_follows_page_to_real_skill_package`：

```python
def test_import_clawhub_skill_follows_page_to_real_skill_package(monkeypatch) -> None:
    _patch_importer(
        monkeypatch,
        {
            "https://clawhub.example/skills/weather": (
                b'<html><a href="https://github.com/example/skill-pack/tree/main/weather">download</a></html>'
            ),
            "https://api.github.com/repos/example/skill-pack/contents/weather?ref=main": [
                {
                    "type": "file",
                    "path": "weather/SKILL.md",
                    "download_url": "https://raw.githubusercontent.com/example/skill-pack/main/weather/SKILL.md",
                    "size": 46,
                }
            ],
            "https://raw.githubusercontent.com/example/skill-pack/main/weather/SKILL.md": "---\nname: 页面天气\nslug: weather-page\n---\n\n# 天气\n",
        },
    )

    with _test_session() as db:
        _seed_minimal_tenant(db)
        row = import_clawhub_skill(
            GeneralSkillClawHubImportRequest(
                tenant_id="tenant_demo", source="https://clawhub.example/skills/weather"
            ),
            db,
            _admin_user(),
        )

        assert row.slug == "weather-page"
        assert row.skill_files[0].path == "SKILL.md"
```

替换 `test_import_clawhub_skill_uses_clawhub_download_api_for_page_url`：

```python
def test_import_clawhub_skill_uses_clawhub_download_api_for_page_url(monkeypatch) -> None:
    package = BytesIO()
    with ZipFile(package, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: weather\n---\n\n# 天气\n")
        archive.writestr("scripts/weather.py", "print('weather')\n")
        archive.writestr("references/weather_details.md", "# details\n")

    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        return Response(200, content=package.getvalue())

    monkeypatch.setattr(
        "app.api.general_skills._get_skill_importer",
        lambda: SkillImporter(transport=MockTransport(handler)),
    )

    with _test_session() as db:
        _seed_minimal_tenant(db)
        row = import_clawhub_skill(
            GeneralSkillClawHubImportRequest(
                tenant_id="tenant_demo",
                source="https://clawhub.ai/maomaoshuo/maomao-weather",
            ),
            db,
            _admin_user(),
        )

        assert calls == ["https://wry-manatee-359.convex.site/api/v1/download?slug=maomao-weather"]
        assert row.name == "weather"
        assert row.slug == "maomao-weather"
        assert row.homepage == "https://clawhub.ai/maomaoshuo/maomao-weather"
        assert [file.path for file in row.skill_files] == [
            "SKILL.md",
            "scripts/weather.py",
            "references/weather_details.md",
        ]
```

替换 `test_import_clawhub_skill_accepts_cli_slug`：

```python
def test_import_clawhub_skill_accepts_cli_slug(monkeypatch) -> None:
    package = BytesIO()
    with ZipFile(package, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: weather\n---\n\n# 天气\n")

    _patch_importer(
        monkeypatch,
        {
            "https://wry-manatee-359.convex.site/api/v1/download?slug=maomao-weather": package.getvalue()
        },
    )

    with _test_session() as db:
        _seed_minimal_tenant(db)
        row = import_clawhub_skill(
            GeneralSkillClawHubImportRequest(tenant_id="tenant_demo", source="maomao-weather"),
            db,
            _admin_user(),
        )

        assert row.slug == "maomao-weather"
        assert row.skill_files[0].content.startswith("---\nname: weather")
```

替换 `test_import_clawhub_skill_rejects_plain_html_page`：

```python
def test_import_clawhub_skill_rejects_plain_html_page(monkeypatch) -> None:
    _patch_importer(
        monkeypatch,
        {
            "https://clawhub.example/skills/weather": (
                b"<html><body>skill landing page without package</body></html>"
            )
        },
    )

    with _test_session() as db:
        _seed_minimal_tenant(db)
        try:
            import_clawhub_skill(
                GeneralSkillClawHubImportRequest(
                    tenant_id="tenant_demo", source="https://clawhub.example/skills/weather"
                ),
                db,
                _admin_user(),
            )
        except HTTPException as error:
            assert error.status_code == 400
            assert "HTML 页面不会被当作 SKILL.md 导入" in str(error.detail)
        else:
            raise AssertionError("plain HTML page must not be imported as SKILL.md")
```

> 断言含 "HTML 页面不会被当作 SKILL.md 导入"——与 Task 7 的 `_IMPORT_ERROR_MESSAGES[ERROR_HTML_NOT_SKILL]` 文案一致，无需改动断言。

- [ ] **Step 3: 跑全量后端测试**

Run: `cd /Users/moyunqinghe/工作/项目/StaffDeck/backend && python -m pytest tests/ -q`
Expected: 全部通过（含迁移后的 6 个用例；如有 runner/其它用例因依赖被删函数而失败，逐一定位——应为 0）。

- [ ] **Step 4: 提交（StaffDeck）**

```bash
cd /Users/moyunqinghe/工作/项目/StaffDeck
git add backend/tests/test_general_skills.py
git commit -m "test(skills): 远程导入用例迁移到 MockTransport, 回归全绿

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 收尾验证

- [ ] 全量测试双端通过：`feibot/backend/app/agent/skills`（pytest）与 `StaffDeck/backend`（pytest tests/）
- [ ] 新项目接入三行示例可运行（README 内 `import_skill` 各形态）
- [ ] StaffDeck 现有 6 个远程导入用例与包内测试共同锁定行为等价

---

## 完成标准（DoD）

1. `skill-importer` 独立包在 feibot `app/agent/skills/` 就位，离线测试全绿，可被任意项目 `pip install`。
2. StaffDeck `general_skills.py` 不再包含任何远程源下载/解包/解析实现，仅保留适配层与宿主 CRUD/权限。
3. StaffDeck 全量测试通过，行为与文案等价（HTML 拒绝/平台下载端点/owner-repo/slug 去重等原断言逐条保留）。
4. 两份文档（spec + plan）均已提交 feibot。





