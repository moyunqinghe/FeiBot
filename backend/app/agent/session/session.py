"""会话管理:按 conv_key 找或建会话,持久化到 db 的 sessions 表。

conv_key 由渠道层给出(如微信的 from_user_id),agent 层不关心其来源。
"""

from __future__ import annotations

from app.db import store


def get_or_create(conv_key: str) -> str:
    """按 conv_key 找或建会话,返回 session id。"""
    return store.get_or_create_session(conv_key)
