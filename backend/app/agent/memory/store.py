"""记忆存储(占位)。

职责规划:
- 短期记忆:会话内的近期消息(供上下文装配)
- 长期记忆:跨会话的用户偏好/事实(检索后注入上下文)
当前为占位实现,接口先行。
"""

from __future__ import annotations


class MemoryStore:
    """短期/长期记忆存取;当前为占位,save/recall 均不做事。"""

    def save(self, session_id: str, role: str, content: str) -> None:
        """保存一条记忆(占位,暂不持久化)。"""
        pass

    def recall(self, session_id: str, limit: int = 20) -> list[dict]:
        """取回近期记忆(占位,返回空列表)。"""
        return []
