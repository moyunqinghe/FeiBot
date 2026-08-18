"""用户画像:USER.md 由 agent 根据对话自动维护,用户只聊天、不写文件。

画像更新由 distill.py 每轮提炼后经 update_profile() 写入对应小节;
用户手工编辑也允许,会被保留到该小节下次被对话更新为止。
USER.md 是项目级内容文件(进 git,仿 AGENTS.md 惯例),首次运行自动创建骨架。
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.config import BASE_DIR

USER_MD_PATH = BASE_DIR / "USER.md"

# 画像只认这些小节:提示词里约束模型只输出这些 key,写入时同样白名单过滤
PROFILE_SECTIONS = ("称呼", "角色", "工作习惯", "沟通偏好", "时区")

_TEMPLATE = """# USER.md — 用户画像

<!-- 本文件由助理根据对话自动维护,无需手写;手工修改也可以, -->
<!-- 会被保留到对应小节下次被对话更新为止。 -->

## 称呼

## 角色

## 工作习惯

## 沟通偏好

## 时区
"""

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _ensure_template() -> None:
    """首次运行时创建骨架文件。"""
    if not USER_MD_PATH.exists():
        USER_MD_PATH.write_text(_TEMPLATE, encoding="utf-8")


def reset_profile() -> None:
    """重置为初始骨架(清空所有画像内容,/遗忘 用)。"""
    USER_MD_PATH.write_text(_TEMPLATE, encoding="utf-8")


def load_user_profile() -> str:
    """读取用户画像原文;骨架未填实质内容时返回 ""(不注入上下文)。"""
    _ensure_template()
    try:
        raw = USER_MD_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""
    # 去掉 HTML 注释后,若除标题外没有任何实质文本,视为未填写
    text = _COMMENT_RE.sub("", raw)
    has_content = any(
        line.strip() and not line.strip().startswith("#")
        for line in text.splitlines()
    )
    return text.strip() if has_content else ""


def profile_summary() -> dict[str, str]:
    """已填写的小节 {名称: 内容};注释与空小节不计,按文件顺序返回。"""
    summary = {}
    for name, body in _split_sections(_read_or_empty())[1]:
        text = _COMMENT_RE.sub("", "\n".join(body)).strip()
        if text:
            summary[name] = text
    return summary


def update_profile(updates: Mapping[str, object]) -> dict[str, str]:
    """把 {小节: 值} 写入 USER.md 对应小节;只认 PROFILE_SECTIONS。

    值经清洗(单行化、去 markdown 行首标记、限长),防止破坏文件结构;
    文件里缺失的小节追加到末尾。返回实际写入的 {小节: 值}。
    """
    cleaned: dict[str, str] = {}
    for name in PROFILE_SECTIONS:
        if name in updates:
            value = _clean_value(updates[name])
            if value:
                cleaned[name] = value
    if not cleaned:
        return {}

    _ensure_template()
    preamble, sections = _split_sections(USER_MD_PATH.read_text(encoding="utf-8"))
    applied: dict[str, str] = {}
    rebuilt: list[tuple[str, list[str]]] = []
    for name, body in sections:
        if name in cleaned:
            rebuilt.append((name, [cleaned[name]]))
            applied[name] = cleaned[name]
        else:
            rebuilt.append((name, body))
    seen = {name for name, _ in rebuilt}
    for name in PROFILE_SECTIONS:  # 文件里缺的小节补到末尾
        if name in cleaned and name not in seen:
            rebuilt.append((name, [cleaned[name]]))
            applied[name] = cleaned[name]
    USER_MD_PATH.write_text(_render(preamble, rebuilt), encoding="utf-8")
    return applied


def _read_or_empty() -> str:
    _ensure_template()
    try:
        return USER_MD_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


def _split_sections(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """拆成(首个 '## ' 之前的前言行, [(小节名, 正文行列表)...]),保持原顺序。"""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections.append((current[0], _drop_trailing_blanks(current[1])))
            current = (line[3:].strip(), [])
        elif current is None:
            preamble.append(line)
        else:
            current[1].append(line)
    if current is not None:
        sections.append((current[0], _drop_trailing_blanks(current[1])))
    return preamble, sections


def _drop_trailing_blanks(lines: list[str]) -> list[str]:
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _render(preamble: list[str], sections: list[tuple[str, list[str]]]) -> str:
    """按固定格式重建文件:每个小节标题后一空行,正文后一空行。"""
    lines = list(preamble)
    if lines and lines[-1].strip():
        lines.append("")
    for name, body in sections:
        lines.append(f"## {name}")
        lines.append("")
        lines.extend(body)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _clean_value(value: object) -> str:
    """画像值清洗:单行化防结构注入,去掉 markdown 行首标记,限长。"""
    text = " ".join(str(value).split())
    text = re.sub(r"^[#>*•\-]+", "", text).strip()
    return text[:100]
