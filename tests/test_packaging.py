"""Установка на Windows: .bat-файлы и зависимости."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_bat_files_use_windows_null_and_crlf():
    for name in ("Установка.bat", "Запуск.bat"):
        raw = (ROOT / name).read_bytes()
        assert b"/dev/null" not in raw, name
        assert raw.count(b"\r\n") == raw.count(b"\n"), f"{name}: нужны переводы строк CRLF"


def test_runtime_dependencies_match_imports():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["dependencies"]
    names = {re.split(r"[<>=\[ ]", d)[0].lower() for d in deps}
    assert "httpx" in names  # клиент модели и облака
    assert "openai" not in names  # не используется
    code = "\n".join(p.read_text("utf-8") for p in (ROOT / "forlabs").rglob("*.py"))
    assert not re.search(r"^\s*(import|from)\s+openai\b", code, re.M)
