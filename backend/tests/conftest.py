"""测试公共夹具:把 db 指向临时文件,避免污染运行时数据目录。"""

from __future__ import annotations

import pytest

from app.db import store


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "feibot.db")
