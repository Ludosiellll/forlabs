"""Страница лабы: блок «Снимки в программах», «где у меня программа», кнопка запуска."""

from forlabs.core.settings import load_settings

APP_PLAN = {"items": [{"section": 2, "block": 2, "app": "idle", "goal": "открыть main.py в IDLE",
                       "shot": "окно IDLE с кодом", "open": ["Проект Сумма/main.py"]}]}


def lab(tmp_home, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_project_ui import PLAN, RAW, SEC, SEC2
    from test_routes import FIXTURE, make_profile

    from forlabs.core.llm import FakeLLM
    from forlabs.core.settings import Settings
    from forlabs.runner.shots import FakeShooter
    from forlabs.ui.app import create_app
    from forlabs.ui.context import AppContext

    fake = FakeLLM([RAW, SEC, SEC2, PLAN, APP_PLAN])
    ctx = AppContext(settings=Settings(mode="local", tour_done=True), llm_override=lambda: fake, prefer_word=False,
                     render_diagrams=False, synchronous=True)
    ctx.pipeline.projects, ctx.pipeline.shooter, ctx.pipeline.agents = True, FakeShooter(), True
    c = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(c, tmp_path)
    url = c.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                    "text": ""},
                 files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")}, follow_redirects=False
                 ).headers["location"].split("?")[0]
    c.post(url + "/execute")
    return c, ctx, url


def test_block_lists_figure_program_and_how_to_get_it(tmp_home, tmp_path, monkeypatch):
    c, ctx, url = lab(tmp_home, tmp_path, monkeypatch)
    page = c.get(url).text
    assert "Снимки в программах: 1" in page and "IDLE (Python)" in page
    assert url + "/apps/run" in page and "Esc — остановить" in page
    assert "python.org/downloads" in page and url + "/apps/path" in page  # нет программы — где взять или указать


def test_program_path_must_be_an_existing_exe(tmp_home, tmp_path, monkeypatch):
    c, ctx, url = lab(tmp_home, tmp_path, monkeypatch)
    r = c.post(url + "/apps/path", data={"key": "idle", "path": str(tmp_path / "нет.exe")}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    r = c.post(url + "/apps/path", data={"key": "photoshop", "path": "x"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    exe = tmp_path / "Мой Python" / "pythonw.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    r = c.post(url + "/apps/path", data={"key": "idle", "path": f'"{exe}"'}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    assert ctx.pipeline.app_paths == {"idle": str(exe)} and load_settings().app_paths == {"idle": str(exe)}


def test_run_outside_windows_is_refused_politely(tmp_home, tmp_path, monkeypatch):
    c, ctx, url = lab(tmp_home, tmp_path, monkeypatch)
    r = c.post(url + "/apps/run", follow_redirects=False)
    assert "err=" in r.headers["location"]
