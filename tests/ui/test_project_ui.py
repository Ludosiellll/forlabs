"""Программа лабы на странице: блок «Программа», причины в «Нужно от тебя», «Повторить», папка и zip проекта."""

import io
import zipfile

from fastapi.testclient import TestClient
from test_routes import FIXTURE, make_profile

from forlabs.core.llm import FakeLLM
from forlabs.core.settings import Settings
from forlabs.runner import languages
from forlabs.runner.shots import FakeShooter
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

RAW = {"number": "7", "topic": "Сумма чисел", "goal": "Научиться вводу", "equipment": "ПК",
       "main_tasks": ["Сумма двух чисел", "Окно среды"], "extra_tasks": [], "variant_text": None, "questions": []}
SEC = {"blocks": [
    {"type": "listing", "topic": "Код", "language": "python",
     "code": "a = float(input(\"a: \"))\nb = float(input(\"b: \"))\nprint(\"Сумма:\", a + b)"},
    {"type": "figure", "kind": "screenshot", "topic": "Результат", "instruction": "Запусти и введи 12 и 5"}]}
SEC2 = {"blocks": [{"type": "paragraph", "text": "Код открыт в среде."},
                   {"type": "figure", "kind": "screenshot", "topic": "Окно IDLE", "instruction": "Открой main.py"}]}
CODE = "a = float(input(\"a: \"))\nb = float(input(\"b: \"))\nprint(\"Сумма:\", a + b)\n"
PLAN = {"language": "python", "kind": "console", "name": "Сумма", "entry": "main.py",
        "files": [{"path": "main.py", "content": CODE}],
        "shots": [{"section": 1, "block": 2, "mode": "console", "input": "12\n5\n", "expect": "17"},
                  {"section": 2, "block": 2, "mode": "manual", "reason": "окно среды разработки"}]}


def lab(tmp_home, tmp_path, monkeypatch, tools=None):
    fake = FakeLLM([RAW, SEC, SEC2, PLAN])
    ctx = AppContext(settings=Settings(mode="local", tour_done=True), llm_override=lambda: fake, prefer_word=False,
                     render_diagrams=False, synchronous=True)
    ctx.pipeline.projects, ctx.pipeline.shooter = True, FakeShooter()
    if tools is not None:
        monkeypatch.setattr(languages, "detect", lambda *a, **k: tools)
    c = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(c, tmp_path)
    url = c.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                    "text": ""},
                 files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")}, follow_redirects=False
                 ).headers["location"].split("?")[0]
    c.post(url + "/execute")
    return c, ctx, url


def test_program_block_and_manual_reason(tmp_home, tmp_path, monkeypatch):
    c, ctx, url = lab(tmp_home, tmp_path, monkeypatch)
    page = c.get(url).text
    assert "Программа" in page and "Сумма" in page and "собрана" in page
    assert url + "/project/open" in page and url + "/project.zip" in page
    assert "Нужно от тебя: 1" in page and "окно среды разработки" in page  # первый рисунок снял ForLabs
    z = zipfile.ZipFile(io.BytesIO(c.get(url + "/project.zip").content))
    assert z.read("Сумма/main.py").decode("utf-8") == CODE


def test_missing_tool_offers_retry(tmp_home, tmp_path, monkeypatch):
    c, ctx, url = lab(tmp_home, tmp_path, monkeypatch, tools={})
    page = c.get(url).text
    assert "Нужно от тебя: 2" in page and "Python" in page and url + "/project/retry" in page
    assert 'data-tool-missing="python"' in page and "Скачать и установить" in page  # поставить прямо отсюда
    monkeypatch.undo()  # Python установили
    r = c.post(url + "/project/retry", follow_redirects=False)
    assert r.status_code == 303
    assert "Нужно от тебя: 1" in c.get(url).text
