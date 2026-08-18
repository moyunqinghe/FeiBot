from app.db import store


def test_upsert_and_get_plugin():
    store.upsert_plugin("p1", '{"transport":"http","url":"http://x/mcp"}', 1)
    row = store.get_plugin("p1")
    assert row["name"] == "p1"
    assert row["config_json"] == '{"transport":"http","url":"http://x/mcp"}'
    assert row["enabled"] == 1


def test_upsert_overwrites_same_name():
    store.upsert_plugin("p1", '{"a":1}', 1)
    store.upsert_plugin("p1", '{"a":2}', 0)
    row = store.get_plugin("p1")
    assert row["config_json"] == '{"a":2}'
    assert row["enabled"] == 0


def test_get_missing_plugin_returns_none():
    assert store.get_plugin("nope") is None


def test_list_plugins_sorted_and_roundtrip():
    store.upsert_plugin("b", "{}", 1)
    store.upsert_plugin("a", "{}", 0)
    names = [r["name"] for r in store.list_plugins()]
    assert names == ["a", "b"]


def test_delete_plugin_returns_whether_deleted():
    store.upsert_plugin("p1", "{}", 1)
    assert store.delete_plugin("p1") is True
    assert store.delete_plugin("p1") is False
    assert store.get_plugin("p1") is None


def test_set_plugin_enabled():
    store.upsert_plugin("p1", "{}", 1)
    assert store.set_plugin_enabled("p1", 0) is True
    assert store.get_plugin("p1")["enabled"] == 0
    assert store.set_plugin_enabled("nope", 1) is False
