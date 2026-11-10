"""Папку и файл можно выбрать в системном окне, а не вписывать путь руками: кнопка «Выбрать…» у папки для отчётов и
у путей к программам. В окне браузера (без окна ForLabs) такого окна нет — кнопки не показываются."""

from fastapi.testclient import TestClient
from test_appshots_ui import lab

from forlabs.core.llm import FakeLLM
from forlabs.core.settings import Settings
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext


def app_client(pick=None):
    ctx = AppContext(settings=Settings(mode="local", tour_done=True), llm_override=lambda: FakeLLM([]),
                     prefer_word=False, render_diagrams=False, synchronous=True)
    ctx.pick = pick
    return TestClient(create_app(ctx), base_url="http://127.0.0.1:8765"), ctx


def test_folder_for_reports_has_a_choose_button(tmp_home):
    c, ctx = app_client(pick=lambda kind, initial="": "D:\\Лабы\\Иванов")
    page = c.get("/profiles/new").text
    assert 'data-pick="folder"' in page and 'data-pick-for="output_root"' in page and "Выбрать…" in page


def test_no_choose_button_when_there_is_no_window_to_ask(tmp_home):
    c, ctx = app_client(pick=None)
    assert "data-pick=" not in c.get("/profiles/new").text and "data-pick=" not in c.get("/settings").text
    r = c.post("/pick", data={"kind": "folder"})
    assert r.status_code == 409 and "окне ForLabs" in r.json()["error"]


def test_pick_returns_the_chosen_path_and_passes_where_to_start(tmp_home):
    asked = []
    c, ctx = app_client(pick=lambda kind, initial="": asked.append((kind, initial)) or "D:\\Лабы\\Иванов")
    r = c.post("/pick", data={"kind": "folder", "initial": "D:\\Лабы"})
    assert r.status_code == 200 and r.json() == {"path": "D:\\Лабы\\Иванов"}
    c.post("/pick", data={"kind": "file", "initial": ""})
    c.post("/pick", data={"kind": "что-то ещё"})
    assert asked == [("folder", "D:\\Лабы"), ("file", ""), ("folder", "")]


def test_cancelled_dialog_changes_nothing(tmp_home):
    c, ctx = app_client(pick=lambda kind, initial="": None)
    assert c.post("/pick", data={"kind": "folder"}).json() == {"path": ""}


def test_broken_dialog_is_an_error_not_a_crash(tmp_home):
    def boom(kind, initial=""):
        raise RuntimeError("окно не открылось")

    c, ctx = app_client(pick=boom)
    r = c.post("/pick", data={"kind": "folder"})
    assert r.status_code == 500 and r.json()["error"]


def test_program_paths_have_a_choose_button(tmp_home, tmp_path, monkeypatch):
    c, ctx = app_client(pick=lambda kind, initial="": "C:\\Program Files\\LibreOffice\\program\\soffice.exe")
    page = c.get("/settings").text
    assert page.count('data-pick="file"') >= 2 and 'data-pick-for="soffice_path"' in page
    from forlabs.agent import apps

    c2, ctx2, url = lab(tmp_home, tmp_path, monkeypatch)
    ctx2.pick = lambda kind, initial="": None
    monkeypatch.setattr(apps, "installed", lambda *a, **k: {})
    assert 'data-pick="file"' in c2.get(url).text  # «Она у меня есть: путь к .exe»
