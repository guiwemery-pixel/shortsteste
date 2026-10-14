import os

from app.config import load_dotenv


def test_load_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("CC_TEST_KEY", raising=False)
    monkeypatch.delenv("CC_TEST_QUOTED", raising=False)
    monkeypatch.delenv("CC_TEST_EMPTY", raising=False)
    monkeypatch.setenv("CC_TEST_EXISTING", "do-sistema")
    env = tmp_path / ".env"
    env.write_text(
        "﻿# comentário\nCC_TEST_KEY=abc=123\nexport CC_TEST_QUOTED=\"com espaço\"\n"
        "CC_TEST_EMPTY=\nCC_TEST_EXISTING=do-arquivo\nlinha inválida\n",
        encoding="utf-8",
    )
    load_dotenv(env)
    assert os.environ["CC_TEST_KEY"] == "abc=123"
    assert os.environ["CC_TEST_QUOTED"] == "com espaço"
    assert "CC_TEST_EMPTY" not in os.environ
    assert os.environ["CC_TEST_EXISTING"] == "do-sistema"
    monkeypatch.delenv("CC_TEST_KEY")
    monkeypatch.delenv("CC_TEST_QUOTED")


def test_load_dotenv_missing_file(tmp_path):
    load_dotenv(tmp_path / "nao-existe.env")
