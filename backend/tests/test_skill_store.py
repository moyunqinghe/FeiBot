"""installed_skills 表与 SqliteSkillStore 适配器的回归测试。"""

from app.db import store
from app.db.skill_store import SqliteSkillStore


def test_upsert_and_get_skill():
    store.upsert_skill("daily-ai-news", "owner/repo", "github", 1)
    row = store.get_skill("daily-ai-news")
    assert row["slug"] == "daily-ai-news"
    assert row["source"] == "owner/repo"
    assert row["source_kind"] == "github"
    assert row["enabled"] == 1


def test_upsert_overwrites_same_slug():
    store.upsert_skill("s1", "src-a", "github", 1)
    store.upsert_skill("s1", "src-b", "url", 0)
    row = store.get_skill("s1")
    assert row["source"] == "src-b"
    assert row["source_kind"] == "url"
    assert row["enabled"] == 0


def test_get_missing_skill_returns_none():
    assert store.get_skill("nope") is None


def test_list_skills_sorted_and_roundtrip():
    store.upsert_skill("b", "src-b", "url", 1)
    store.upsert_skill("a", "src-a", "github", 0)
    slugs = [r["slug"] for r in store.list_skills()]
    assert slugs == ["a", "b"]


def test_delete_skill_returns_whether_deleted():
    store.upsert_skill("s1", "src", "url", 1)
    assert store.delete_skill("s1") is True
    assert store.delete_skill("s1") is False
    assert store.get_skill("s1") is None


def test_set_skill_enabled():
    store.upsert_skill("s1", "src", "url", 1)
    assert store.set_skill_enabled("s1", 0) is True
    assert store.get_skill("s1")["enabled"] == 0
    assert store.set_skill_enabled("nope", 1) is False


def test_skill_upsert_preserves_added_and_bumps_updated(monkeypatch):
    clock = [1000.0]

    def fake_time():
        clock[0] += 1.0
        return clock[0]

    monkeypatch.setattr(store.time, "time", fake_time)
    store.upsert_skill("s1", "src", "url", 1)
    first = store.get_skill("s1")
    store.upsert_skill("s1", "src", "url", 1)
    second = store.get_skill("s1")
    assert second["added_at"] == first["added_at"]
    assert second["updated_at"] > first["updated_at"]


def test_sqlite_skill_store_roundtrip():
    s = SqliteSkillStore()
    s.upsert("daily-ai-news", "owner/repo", "github", 1)
    row = s.get("daily-ai-news")
    assert row["source"] == "owner/repo"
    assert [r["slug"] for r in s.list()] == ["daily-ai-news"]
    assert s.set_enabled("daily-ai-news", 0) is True
    assert s.get("daily-ai-news")["enabled"] == 0
    assert s.delete("daily-ai-news") is True
    assert s.get("daily-ai-news") is None


def test_sqlite_skill_store_missing_rows():
    s = SqliteSkillStore()
    assert s.get("nope") is None
    assert s.delete("nope") is False
    assert s.set_enabled("nope", 1) is False
