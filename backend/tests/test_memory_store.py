"""MemoryStore:MEMORY.md 的读写、去重、清空。"""

from __future__ import annotations

from app.agent.memory.store import MemoryStore


def test_load_missing_file_returns_empty(tmp_path) -> None:
    assert MemoryStore(tmp_path / "MEMORY.md").load() == []


def test_add_creates_file_with_header(tmp_path) -> None:
    path = tmp_path / "MEMORY.md"
    MemoryStore(path).add(["用户叫老莫", "偏好简洁中文"])
    text = path.read_text(encoding="utf-8")
    assert "由 agent 自动维护" in text  # 模板头
    assert "- 用户叫老莫" in text
    assert MemoryStore(path).load() == ["用户叫老莫", "偏好简洁中文"]


def test_add_dedup_against_existing_and_batch(tmp_path) -> None:
    mem = MemoryStore(tmp_path / "MEMORY.md")
    mem.add(["用户叫老莫"])
    mem.add(["用户叫老莫", " 用户叫老莫 ", "做 SaaS"])
    assert mem.load() == ["用户叫老莫", "做 SaaS"]


def test_add_empty_or_blank_is_noop(tmp_path) -> None:
    path = tmp_path / "MEMORY.md"
    MemoryStore(path).add(["", "  "])
    assert not path.exists()  # 没有有效条目则不建文件


def test_clear_resets_to_header(tmp_path) -> None:
    path = tmp_path / "MEMORY.md"
    mem = MemoryStore(path)
    mem.add(["用户叫老莫"])
    mem.clear()
    assert mem.load() == []
    assert "由 agent 自动维护" in path.read_text(encoding="utf-8")
