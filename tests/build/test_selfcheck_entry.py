"""Самопроверка собранной программы (ForLabs.exe --selfcheck) и точка входа run_forlabs.py."""

import importlib.util
import sys
from pathlib import Path

import pytest

from forlabs import selfcheck, version
from forlabs.core import rules

ROOT = Path(__file__).parents[2]


def _entry():
    spec = importlib.util.spec_from_file_location("run_forlabs", ROOT / "run_forlabs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _fresh_rules(monkeypatch):
    monkeypatch.setattr(rules, "_BUILTIN", None)


def test_build_checks_pass_from_sources(tmp_home):
    checks = selfcheck.build_checks()
    names = [c.name for c in checks if c.name != "Хранилище паролей"]  # только на Windows
    assert names == ["Версия", "Правила", "Морфология", "Библиотеки", "Шаблоны и оформление", "Документ", "PDF", "Сервер", "Программы лаб"]
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    assert version.__version__ in checks[0].detail
    assert "отвечает" in next(c for c in checks if c.name == "Сервер").detail


def test_selfcheck_fails_and_writes_report_when_rules_missing(tmp_home, tmp_path, monkeypatch):
    monkeypatch.setattr(rules, "builtin_rules", dict)
    out = tmp_path / "selfcheck.txt"
    assert selfcheck.main(["--selfcheck", "--out", str(out), "--offline"]) == 1
    text = out.read_text("utf-8")
    assert "[НЕТ  ] Правила" in text


def test_frozen_build_requires_embedded_rules(tmp_home, monkeypatch):
    monkeypatch.setattr(version, "frozen", lambda: True)
    monkeypatch.delitem(sys.modules, "forlabs._embedded_rules", raising=False)
    check = next(c for c in selfcheck.build_checks() if c.name == "Правила")
    assert not check.ok and "не встроены" in check.detail


def test_selfcheck_passes_and_exits_zero(tmp_home, tmp_path):
    out = tmp_path / "ok.txt"
    assert selfcheck.main(["--selfcheck", "--out", str(out), "--offline"]) == 0
    assert "Сервер" in out.read_text("utf-8")


def test_health_reports_version(tmp_home):
    from fastapi.testclient import TestClient

    from forlabs.core.settings import Settings
    from forlabs.ui.app import create_app
    from forlabs.ui.context import AppContext

    tmp_home.mkdir(parents=True, exist_ok=True)
    client = TestClient(create_app(AppContext(settings=Settings(mode="local"))), base_url="http://127.0.0.1:8765")
    assert client.get("/health").json() == {"app": "forlabs", "version": version.__version__}


def test_entry_dispatches_selfcheck(monkeypatch):
    seen = {}
    monkeypatch.setattr(selfcheck, "main", lambda argv: seen.setdefault("argv", argv) and 7)
    assert _entry().main(["--selfcheck", "--out", "x.txt"]) == 7
    assert seen["argv"] == ["--selfcheck", "--out", "x.txt"]


def test_entry_runs_app_by_default(monkeypatch):
    import forlabs.__main__ as app_main

    calls = []
    monkeypatch.setattr(app_main, "main", lambda: calls.append("app"))
    assert _entry().main([]) == 0
    assert calls == ["app"]
