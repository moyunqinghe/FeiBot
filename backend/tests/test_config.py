"""config 的 .env 加载回归测试。"""

from __future__ import annotations

import os

from app import config

KEY = "FEIBOT_TEST_DOTENV_KEY"


def test_load_dotenv_sets_unset_vars(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("# 注释\n\nFEIBOT_TEST_DOTENV_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.delenv(KEY, raising=False)

    config._load_dotenv(env_file)

    assert os.environ[KEY] == "from-dotenv"


def test_load_dotenv_does_not_override_real_env(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("FEIBOT_TEST_DOTENV_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv(KEY, "from-env")

    config._load_dotenv(env_file)

    assert os.environ[KEY] == "from-env"  # 真实环境变量优先


def test_load_dotenv_strips_quotes_and_export(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('export FEIBOT_TEST_DOTENV_KEY="v1"\n', encoding="utf-8")
    monkeypatch.delenv(KEY, raising=False)

    config._load_dotenv(env_file)

    assert os.environ[KEY] == "v1"


def test_load_dotenv_missing_file_is_noop(tmp_path) -> None:
    config._load_dotenv(tmp_path / "nope.env")  # 不抛异常即通过
