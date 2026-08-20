"""sqlite 持久化:kv / sessions / model_configs / messages / mcp_plugins / installed_skills。

只用标准库 sqlite3;每次调用新开连接,简单且对当前单线程轮询足够。
"""

from __future__ import annotations

import sqlite3
import time
import uuid

from app.config import DATA_DIR

DB_PATH = DATA_DIR / "feibot.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    conv_key   TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS model_configs (
    name        TEXT PRIMARY KEY,
    protocol    TEXT NOT NULL,
    model       TEXT NOT NULL,
    base_url    TEXT NOT NULL,
    api_key_enc TEXT NOT NULL,
    temperature REAL NOT NULL DEFAULT 1.0
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mcp_plugins (
    name        TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    added_at    REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS installed_skills (
    slug        TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    added_at    REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
"""


def _connect() -> sqlite3.Connection:
    """打开连接并确保表结构存在(幂等)。"""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


def get_kv(key: str, default: str = "") -> str:
    """读取 kv;不存在返回 default。"""
    with _connect() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_kv(key: str, value: str) -> None:
    """写入 kv(存在则覆盖)。"""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_or_create_session(conv_key: str) -> str:
    """按 conv_key 找会话,没有则新建;返回 session id。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE conv_key = ?", (conv_key,)
        ).fetchone()
        if row:
            return row[0]
        session_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO sessions (id, conv_key, created_at) VALUES (?, ?, ?)",
            (session_id, conv_key, time.time()),
        )
    return session_id


# ---- 模型配置(model_configs 表;api_key_enc 为密文,加解密在 llm/registry.py)----


def upsert_model_config(
    name: str,
    protocol: str,
    model: str,
    base_url: str,
    api_key_enc: str,
    temperature: float,
) -> None:
    """写入模型配置(同名覆盖)。"""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO model_configs"
            " (name, protocol, model, base_url, api_key_enc, temperature)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET"
            " protocol = excluded.protocol, model = excluded.model,"
            " base_url = excluded.base_url, api_key_enc = excluded.api_key_enc,"
            " temperature = excluded.temperature",
            (name, protocol, model, base_url, api_key_enc, temperature),
        )


def get_model_config(name: str) -> dict | None:
    """按名字取模型配置行(dict);不存在返回 None。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT name, protocol, model, base_url, api_key_enc, temperature"
            " FROM model_configs WHERE name = ?",
            (name,),
        ).fetchone()
    return _model_row_to_dict(row) if row else None


def list_model_configs() -> list[dict]:
    """列出全部模型配置行(按名字排序)。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT name, protocol, model, base_url, api_key_enc, temperature"
            " FROM model_configs ORDER BY name"
        ).fetchall()
    return [_model_row_to_dict(row) for row in rows]


def delete_model_config(name: str) -> bool:
    """删除模型配置;返回是否确实删掉了行。"""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM model_configs WHERE name = ?", (name,))
        return cur.rowcount > 0


def _model_row_to_dict(row: tuple) -> dict:
    return {
        "name": row[0],
        "protocol": row[1],
        "model": row[2],
        "base_url": row[3],
        "api_key_enc": row[4],
        "temperature": row[5],
    }


# ---- 消息历史(messages 表,按会话存储对话轮次)----


def add_message(session_id: str, role: str, content: str) -> None:
    """往会话追加一条消息(role 为 user/assistant)。"""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at)"
            " VALUES (?, ?, ?, ?)",
            (session_id, role, content, time.time()),
        )


def recent_messages(session_id: str, limit: int = 20) -> list[dict]:
    """取会话最近 limit 条消息,按时间正序返回 [{"role","content"}, ...]。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM ("
            "   SELECT id, role, content FROM messages"
            "   WHERE session_id = ? ORDER BY id DESC LIMIT ?"
            ") ORDER BY id",
            (session_id, limit),
        ).fetchall()
    return [{"role": role, "content": content} for role, content in rows]


# ---- MCP 插件(mcp_plugins 表;config_json 为连接配置的 JSON 文本)----


def upsert_plugin(name: str, config_json: str, enabled: int) -> None:
    """写入 MCP 插件(同名覆盖);首次写入记 added_at,每次刷新 updated_at。"""
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO mcp_plugins (name, config_json, enabled, added_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET"
            " config_json = excluded.config_json, enabled = excluded.enabled,"
            " updated_at = excluded.updated_at",
            (name, config_json, enabled, now, now),
        )


def get_plugin(name: str) -> dict | None:
    """按名字取插件行(dict);不存在返回 None。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT name, config_json, enabled, added_at, updated_at"
            " FROM mcp_plugins WHERE name = ?",
            (name,),
        ).fetchone()
    return _plugin_row_to_dict(row) if row else None


def list_plugins() -> list[dict]:
    """列出全部插件行(按名字排序)。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT name, config_json, enabled, added_at, updated_at"
            " FROM mcp_plugins ORDER BY name"
        ).fetchall()
    return [_plugin_row_to_dict(row) for row in rows]


def delete_plugin(name: str) -> bool:
    """删除插件;返回是否确实删掉了行。"""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM mcp_plugins WHERE name = ?", (name,))
        return cur.rowcount > 0


def set_plugin_enabled(name: str, enabled: int) -> bool:
    """切换插件启用状态;返回是否命中已有行。"""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE mcp_plugins SET enabled = ?, updated_at = ? WHERE name = ?",
            (enabled, time.time(), name),
        )
        return cur.rowcount > 0


def _plugin_row_to_dict(row: tuple) -> dict:
    return {
        "name": row[0],
        "config_json": row[1],
        "enabled": row[2],
        "added_at": row[3],
        "updated_at": row[4],
    }


# ---- 已安装 skill(installed_skills 表;slug == SKILL.md frontmatter name)----


def upsert_skill(slug: str, source: str, source_kind: str, enabled: int) -> None:
    """写入已安装 skill(同 slug 覆盖);首次写入记 added_at,每次刷新 updated_at。"""
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO installed_skills (slug, source, source_kind, enabled, added_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(slug) DO UPDATE SET"
            " source = excluded.source, source_kind = excluded.source_kind,"
            " enabled = excluded.enabled, updated_at = excluded.updated_at",
            (slug, source, source_kind, enabled, now, now),
        )


def get_skill(slug: str) -> dict | None:
    """按 slug 取 skill 行(dict);不存在返回 None。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT slug, source, source_kind, enabled, added_at, updated_at"
            " FROM installed_skills WHERE slug = ?",
            (slug,),
        ).fetchone()
    return _skill_row_to_dict(row) if row else None


def list_skills() -> list[dict]:
    """列出全部已安装 skill 行(按 slug 排序)。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT slug, source, source_kind, enabled, added_at, updated_at"
            " FROM installed_skills ORDER BY slug"
        ).fetchall()
    return [_skill_row_to_dict(row) for row in rows]


def delete_skill(slug: str) -> bool:
    """删除 skill 行;返回是否确实删掉了行。"""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM installed_skills WHERE slug = ?", (slug,))
        return cur.rowcount > 0


def set_skill_enabled(slug: str, enabled: int) -> bool:
    """切换 skill 启用状态;返回是否命中已有行。"""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE installed_skills SET enabled = ?, updated_at = ? WHERE slug = ?",
            (enabled, time.time(), slug),
        )
        return cur.rowcount > 0


def _skill_row_to_dict(row: tuple) -> dict:
    return {
        "slug": row[0],
        "source": row[1],
        "source_kind": row[2],
        "enabled": row[3],
        "added_at": row[4],
        "updated_at": row[5],
    }
