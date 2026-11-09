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


# Windows не может скачать репозиторий с путями вроде «C:/…» или «"/tmp/…» (так сломалась сборка на Windows)
def test_repo_tracks_only_project_files():
    import subprocess

    import pytest

    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=False)
    if out.returncode != 0:
        pytest.skip("нет git")
    names = [p for p in out.stdout.decode("utf-8").split("\0") if p]
    tops = {p.split("/")[0] for p in names}
    allowed = {".github", ".gitignore", "README.md", "docs", "forlabs", "packaging", "pyproject.toml", "run_forlabs.py",
               "run_setup.py", "supabase", "tests", "tools", "Запуск.bat", "Установка.bat"}
    assert tops <= allowed, sorted(tops - allowed)
    bad = [p for p in names if any(c in p for c in '<>:"|?*\\')]
    assert not bad, bad
