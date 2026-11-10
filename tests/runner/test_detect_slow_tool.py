"""Инструмент установлен, но на проверку версии не ответил вовремя (холодный диск, антивирус, все проверки стартуют
разом). ForLabs не должен объявлять его отсутствующим с первого раза: спрашивает ещё раз, уже по одному."""

import shutil
import sys
import time
from pathlib import Path

import pytest

from forlabs.runner import languages as L

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="поддельные инструменты — sh-скрипты")
SLEEP = shutil.which("sleep") or "/bin/sleep"


def script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_tool_that_answered_too_slowly_gets_a_second_try(tmp_path, monkeypatch):
    mark = tmp_path / "уже-спрашивали"
    php = script(tmp_path / "bin" / "php",
                 f"if [ ! -e '{mark}' ]; then : > '{mark}'; exec {SLEEP} 20; fi\necho 'PHP 8.3.0 (cli)'\n")
    monkeypatch.setattr(L, "PROBE_TIMEOUT", 0.5)
    tools = L.detect(env={"PATH": str(php.parent)}, tools_dir=tmp_path / "нет")
    assert tools["php"].path == str(php) and tools["php"].version == "8.3.0"


def test_tool_that_never_answers_is_given_up_after_the_second_try(tmp_path, monkeypatch):
    calls = tmp_path / "вызовы"
    php = script(tmp_path / "bin" / "php", f"echo x >> '{calls}'\nexec {SLEEP} 20\n")
    monkeypatch.setattr(L, "PROBE_TIMEOUT", 0.3)
    t0 = time.perf_counter()
    assert "php" not in L.detect(env={"PATH": str(php.parent)}, tools_dir=tmp_path / "нет")
    assert time.perf_counter() - t0 < 5
    assert calls.read_text().count("x") == 2  # один раз вместе со всеми, один раз отдельно — и хватит


def test_broken_tool_is_not_asked_twice(tmp_path):
    calls = tmp_path / "вызовы"
    go = script(tmp_path / "bin" / "go", f"echo x >> '{calls}'\nexit 3\n")
    assert "go" not in L.detect(env={"PATH": str(go.parent)}, tools_dir=tmp_path / "нет")
    assert calls.read_text().count("x") == 1  # ответил ошибкой — это ответ, повторять незачем
