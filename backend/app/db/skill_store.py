"""SkillStore 的 sqlite 实现:把 skill 基座的持久化协议透传到 app.db.store。"""

from __future__ import annotations

from app.db import store


class SqliteSkillStore:
    """实现 skill 基座 manager.SkillStore 协议(结构子类型,无需显式继承)。"""

    def upsert(self, slug: str, source: str, source_kind: str, enabled: int) -> None:
        store.upsert_skill(slug, source, source_kind, enabled)

    def get(self, slug: str) -> dict | None:
        return store.get_skill(slug)

    def list(self) -> list[dict]:
        return store.list_skills()

    def delete(self, slug: str) -> bool:
        return store.delete_skill(slug)

    def set_enabled(self, slug: str, enabled: int) -> bool:
        return store.set_skill_enabled(slug, enabled)
