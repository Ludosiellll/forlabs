"""Удаление лабы прямо из списка лаб: кнопка в строке, подтверждение, занятая и открытая в Word лаба."""

from fastapi.testclient import TestClient
from test_project_ui import RAW
from test_routes import FIXTURE, make_profile

from forlabs.core.llm import FakeLLM
from forlabs.core.settings import Settings
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext


def setup(tmp_path, n=2):
    fake = FakeLLM([dict(RAW, number=str(19 + i)) for i in range(n)])
    ctx = AppContext(settings=Settings(mode="local", tour_done=True), llm_override=lambda: fake, prefer_word=False,
                     render_diagrams=False, synchronous=True)
    c = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(c, tmp_path)
    urls = []
    for i in range(n):
        r = c.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip",
                                      "number": str(19 + i), "text": ""},
                   files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")}, follow_redirects=False)
        urls.append(r.headers["location"].split("?")[0])
    return c, ctx, urls


def test_list_has_delete_button_for_each_lab(tmp_home, tmp_path):
    c, ctx, urls = setup(tmp_path)
    page = c.get("/?profile=ivanov_ivan_petrovich&subject=psip").text
    for u in urls:
        assert f'action="{u}/delete"' in page
    assert page.count("event.stopPropagation()") >= 2  # щелчок по кнопке не открывает лабу
    assert "Удалить ЛР 19?" in page and "останутся" in page  # что удалится, а что нет


def test_delete_from_list_returns_to_list(tmp_home, tmp_path):
    c, ctx, urls = setup(tmp_path)
    r = c.post(urls[0] + "/delete", follow_redirects=False)
    loc = r.headers["location"]
    assert loc.startswith("/?profile=") and "msg=" in loc
    page = c.get("/?profile=ivanov_ivan_petrovich&subject=psip").text
    assert f'action="{urls[0]}/delete"' not in page and f'action="{urls[1]}/delete"' in page


def test_lab_with_open_report_is_not_half_deleted(tmp_home, tmp_path, monkeypatch):
    c, ctx, urls = setup(tmp_path, n=1)

    def locked(*a):
        raise PermissionError(32, "Процесс не может получить доступ к файлу", "report.docx")

    monkeypatch.setattr(ctx.store, "delete_lab", locked)
    r = c.post(urls[0] + "/delete", follow_redirects=False)
    assert r.status_code in (302, 303) and "err=" in r.headers["location"]
