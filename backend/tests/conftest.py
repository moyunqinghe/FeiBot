"""测试公共夹具:db、MEMORY.md、USER.md 都指向临时文件,避免污染运行时数据。"""

from __future__ import annotations

import pytest

from app.agent import profile
from app.agent.memory import store as memory_store
from app.db import store


@pytest.fixture(autouse=True)
def _tmp_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "feibot.db")
    monkeypatch.setattr(memory_store, "MEMORY_PATH", tmp_path / "MEMORY.md")
    monkeypatch.setattr(profile, "USER_MD_PATH", tmp_path / "USER.md")
