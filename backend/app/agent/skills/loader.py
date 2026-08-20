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
