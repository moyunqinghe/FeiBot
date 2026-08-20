# Skill Runtime Directory Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the source-level `backend/skills/` directory and make feibot discover installed Skills from the ignored runtime directory `backend/.feibot/skills/`.

**Architecture:** `skill-importer` remains an unchanged pure protocol package that returns `SkillPackage`; this migration changes only feibot's host-side storage configuration and discovery documentation. `app.config.SKILLS_DIR` becomes a child of `DATA_DIR`, while `app.agent.skills.loader` keeps its existing direct-child discovery behavior.

**Tech Stack:** Python 3.14, pathlib, pytest, Ruff, Markdown

---

## File map

- Create `backend/tests/test_skills.py`: protects the runtime-path invariant and characterizes Skill discovery behavior.
- Modify `backend/app/config.py`: point `SKILLS_DIR` at `DATA_DIR / "skills"`.
- Modify `backend/app/agent/skills/loader.py`: update stale source-directory wording only; discovery logic stays unchanged.
- Modify `backend/app/agent/skills/__init__.py`: update stale package description only.
- Modify `backend/README.md`: remove the deleted source-level directory and document `.feibot/skills/`.
- Delete `backend/skills/echo/SKILL.md`: remove the unused example and thereby remove `backend/skills/` from Git.
- Do not modify anything under `backend/app/agent/skills/skill_importer/`.

### Task 1: Move the configured Skill directory under runtime data

**Files:**
- Create: `backend/tests/test_skills.py`
- Modify: `backend/app/config.py:12-16`

- [ ] **Step 1: Write the failing configuration test**

Create `backend/tests/test_skills.py` with:

```python
"""Skill 运行时目录配置与发现行为。"""

from __future__ import annotations

from app import config


def test_skills_dir_is_under_runtime_data_dir() -> None:
    assert config.SKILLS_DIR == config.DATA_DIR / "skills"
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_skills.py::test_skills_dir_is_under_runtime_data_dir -q
```

Expected: FAIL because the current value is `backend/skills`, not `backend/.feibot/skills`.

- [ ] **Step 3: Change only the host-side configuration**

In `backend/app/config.py`, retain the existing `BASE_DIR`, `DATA_DIR`, and `DATA_DIR.mkdir(...)` statements and replace the `SKILLS_DIR` assignment with:

```python
SKILLS_DIR = DATA_DIR / "skills"  # 已安装 skill 运行时目录,不进 git
```

Do not call `SKILLS_DIR.mkdir(...)`: the loader already treats an absent directory as an empty installation set, and a future installation service should create it when needed.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_skills.py::test_skills_dir_is_under_runtime_data_dir -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit the configuration invariant**

```bash
git add backend/app/config.py backend/tests/test_skills.py
git commit -m "refactor(skills): store installed skills under runtime data"
```

### Task 2: Characterize loader behavior at the new storage boundary

**Files:**
- Modify: `backend/tests/test_skills.py`
- Modify: `backend/app/agent/skills/loader.py:1-18`
- Modify: `backend/app/agent/skills/__init__.py:1`

- [ ] **Step 1: Add loader characterization tests**

Extend `backend/tests/test_skills.py` to the following complete content:

```python
"""Skill 运行时目录配置与发现行为。"""

from __future__ import annotations

from app import config
from app.agent.skills import loader


def test_skills_dir_is_under_runtime_data_dir() -> None:
    assert config.SKILLS_DIR == config.DATA_DIR / "skills"


def test_list_skills_returns_empty_when_runtime_directory_is_missing(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(loader, "SKILLS_DIR", tmp_path / "missing")

    assert loader.list_skills() == []


def test_list_skills_only_discovers_directories_with_skill_markdown(
    monkeypatch, tmp_path
) -> None:
    skills_dir = tmp_path / "skills"
    (skills_dir / "weather").mkdir(parents=True)
    (skills_dir / "weather" / "SKILL.md").write_text("# weather\n", encoding="utf-8")
    (skills_dir / "missing-manifest").mkdir()
    (skills_dir / "plain-file").write_text("not a skill\n", encoding="utf-8")
    monkeypatch.setattr(loader, "SKILLS_DIR", skills_dir)

    assert loader.list_skills() == ["weather"]


def test_list_skills_returns_names_in_sorted_order(monkeypatch, tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    for name in ("zeta", "alpha"):
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    monkeypatch.setattr(loader, "SKILLS_DIR", skills_dir)

    assert loader.list_skills() == ["alpha", "zeta"]
```

These are characterization tests for intentionally preserved loader behavior, so they are expected to pass without changing its implementation.

- [ ] **Step 2: Run the loader tests**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_skills.py -q
```

Expected: `4 passed`.

- [ ] **Step 3: Update stale loader descriptions without changing logic**

Replace `backend/app/agent/skills/loader.py` with:

```python
"""skill 发现:扫描运行时 skills 数据目录,列出含 SKILL.md 的子目录。

只列名字,不执行 skill 内容;后续接入 agent 时再解析 frontmatter。
"""

from __future__ import annotations

from app.config import SKILLS_DIR


def list_skills() -> list[str]:
    """返回已安装且含 SKILL.md 的 skill 目录名,按字母序排列。"""
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(
        child.name
        for child in SKILLS_DIR.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    )
```

Replace `backend/app/agent/skills/__init__.py` with:

```python
"""feibot 的 skill 导入适配与运行时发现层。"""
```

- [ ] **Step 4: Re-run the focused tests after the wording cleanup**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_skills.py -q
```

Expected: `4 passed`.

- [ ] **Step 5: Commit the loader coverage and descriptions**

```bash
git add backend/tests/test_skills.py backend/app/agent/skills/loader.py backend/app/agent/skills/__init__.py
git commit -m "test(skills): cover runtime skill discovery"
```

### Task 3: Remove the obsolete source directory and repair documentation

**Files:**
- Delete: `backend/skills/echo/SKILL.md`
- Modify: `backend/README.md:15-17`

- [ ] **Step 1: Delete the tracked example Skill**

Delete `backend/skills/echo/SKILL.md`. Do not create an empty replacement directory. Git will then stop carrying `backend/skills/` entirely.

- [ ] **Step 2: Replace the stale README directory entries**

In `backend/README.md`, remove this line:

```markdown
- `skills/` — 项目根级 skill 内容目录(每个子目录一个 SKILL.md)
```

Replace the existing `.feibot/` line with:

```markdown
- `.feibot/` — 运行时数据目录，不进 git；sqlite 数据库位于 `.feibot/feibot.db`，已安装 Skill 位于 `.feibot/skills/<skill-id>/`
```

Do not document a Skill installer, management API, or database schema because none is implemented in this change.

- [ ] **Step 3: Confirm no active source/config reference still treats `backend/skills/` as storage**

Run:

```bash
rg -n 'BASE_DIR / "skills"|项目根级 skill 内容目录|backend/skills/|`skills/`' backend/app backend/tests backend/README.md
```

Expected: no matches. References inside historical design documents under `backend/app/agent/skills/docs/` are out of scope because they describe prior extraction work and should not be rewritten as current documentation.

- [ ] **Step 4: Confirm the independent importer base is untouched**

Run:

```bash
git diff --exit-code HEAD -- backend/app/agent/skills/skill_importer backend/app/agent/skills/pyproject.toml
```

Expected: exit code `0` and no output.

- [ ] **Step 5: Commit deletion and documentation**

```bash
git add backend/README.md backend/skills/echo/SKILL.md
git commit -m "docs(skills): remove source-level skill directory"
```

### Task 4: Run final verification

**Files:**
- Verify only; no planned modifications.

- [ ] **Step 1: Run the complete backend test suite**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests -q
```

Expected: exit code `0`, with all tests passing.

- [ ] **Step 2: Run Ruff across application and backend tests**

Run:

```bash
cd backend
.venv/bin/python -m ruff check app tests
```

Expected: exit code `0` and `All checks passed!`.

- [ ] **Step 3: Verify the repository shape and final diff**

Run from the repository root:

```bash
test ! -e backend/skills
test -f backend/app/agent/skills/skill_importer/__init__.py
git status --short
git log -3 --oneline
```

Expected:

- `test ! -e backend/skills` exits `0`.
- The importer package still exists.
- `git status --short` contains no migration-related uncommitted files. Pre-existing unrelated files may remain and must not be added or modified.
- The three migration commits appear in the log.

## Acceptance checklist

- `backend/skills/` no longer exists.
- `config.SKILLS_DIR == config.DATA_DIR / "skills"`.
- Missing `.feibot/skills/` is treated as zero installed Skills and is not eagerly created.
- Loader discovery behavior remains direct-child, manifest-gated, and sorted.
- `backend/README.md` documents `.feibot/skills/<skill-id>/` and no longer advertises root-level `skills/`.
- No file in `backend/app/agent/skills/skill_importer/` changes.
- Full backend tests and Ruff checks pass.
