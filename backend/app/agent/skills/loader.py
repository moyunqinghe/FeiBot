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
