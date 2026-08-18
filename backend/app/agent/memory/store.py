"""长期记忆存储:MEMORY.md 文件,markdown 无序列表格式。

与用户手动维护的 USER.md 不同,MEMORY.md 由 agent 自动维护
(distill.py 每轮对话后提炼写入);用户可以查看和手动编辑/删除。
文件在项目根(与 USER.md 平级),进 git,不放 sqlite——要的就是可读可改。
"""

from __future__ import annotations

from pathlib import Path

from app.config import BASE_DIR

MEMORY_PATH = BASE_DIR / "MEMORY.md"

_HEADER = """# MEMORY.md — 长期记忆

<!-- 本文件由 agent 自动维护,记录它记住的关于你的事;可以手动编辑/删除。 -->
<!-- 每条记忆一行,格式为 "- " 列表项。 -->
"""


class MemoryStore:
    """MEMORY.md 的读写:load 取条目列表,add 追加(去重),clear 清空。"""

    def __init__(self, path: Path | None = None) -> None:
        # path 为 None 时取模块级 MEMORY_PATH(测试可 monkeypatch)
        self._path = path if path is not None else MEMORY_PATH

    def load(self) -> list[str]:
        """返回记忆条目列表(剥掉 "- " 前缀的非空行);文件不存在返回 []。"""
        if not self._path.exists():
            return []
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return []
        entries = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                item = stripped[2:].strip()
                if item:
                    entries.append(item)
        return entries

    def add(self, entries: list[str]) -> None:
        """追加记忆条目(按内容去重:与已有条目及批次内部都不重复)。"""
        existing = self.load()
        seen = set(existing)
        new_items = []
        for entry in entries:
            item = entry.strip()
            if item and item not in seen:
                seen.add(item)
                new_items.append(item)
        if not new_items:
            return
        if not self._path.exists():
            self._path.write_text(_HEADER, encoding="utf-8")
        with self._path.open("a", encoding="utf-8") as f:
            for item in new_items:
                f.write(f"- {item}\n")

    def clear(self) -> None:
        """清空记忆:重置为只剩模板头(保留文件与说明注释)。"""
        self._path.write_text(_HEADER, encoding="utf-8")
