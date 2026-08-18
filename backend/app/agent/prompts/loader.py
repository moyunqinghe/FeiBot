"""系统提示加载:读取本目录下的 md 文件。"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(name: str = "system") -> str:
    """读取 prompts/<name>.md 的内容;文件不存在返回空串。"""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()
