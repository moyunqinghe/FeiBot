"""用户画像:读取项目根 USER.md,注入对话上下文。

USER.md 是项目级内容文件(进 git,仿 AGENTS.md 惯例)。
首次运行(文件不存在)时自动创建中文模板;模板里的 HTML 注释会被忽略,
用户没有实际填写任何内容时返回空串(不注入)。
"""

from __future__ import annotations

import re

from app.config import BASE_DIR

USER_MD_PATH = BASE_DIR / "USER.md"

_TEMPLATE = """# USER.md — 用户画像

<!-- 填写后 Agent 会记住你:本文件内容会作为系统提示注入每轮对话。 -->
<!-- 按需填写各小节,留空不填即可;HTML 注释(本行这种)不会被注入。 -->

## 称呼

<!-- 希望 Agent 怎么称呼你 -->

## 角色

<!-- 你的职业/角色 -->

## 工作习惯

## 沟通偏好

## 时区

<!-- 例如:Asia/Shanghai (UTC+8) -->
"""

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _ensure_template() -> None:
    """首次运行时创建模板文件。"""
    if not USER_MD_PATH.exists():
        USER_MD_PATH.write_text(_TEMPLATE, encoding="utf-8")


def load_user_profile() -> str:
    """读取用户画像;文件不存在(会自动建模板)或用户没填实质内容则返回 ""。"""
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
