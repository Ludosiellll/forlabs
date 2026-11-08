"""Обучение при первом запуске: показывается на главной, пока не пройдено; можно пройти заново."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forlabs.core.llm import FakeLLM
from forlabs.core.settings import Settings, load_settings
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

STATIC = Path(__file__).parents[2] / "forlabs" / "ui" / "static"


@pytest.fixture
def client(tmp_home):
    tmp_home.mkdir(parents=True, exist_ok=True)
    ctx = AppContext(settings=Settings(mode="local"), llm_override=lambda: FakeLLM([]), prefer_word=False,
                     render_diagrams=False, synchronous=True)
    return TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")


def test_first_launch_shows_tour_with_anchors(client):
    html = client.get("/").text
    assert 'src="/static/tour.js"' in html
    for anchor in ("labs", "profiles", "semester", "rules", "settings", "start"):
        assert f'data-tour="{anchor}"' in html


def test_done_hides_tour_and_is_saved(client):
    assert client.post("/tour/done").status_code == 204
    assert load_settings().tour_done is True
    assert "/static/tour.js" not in client.get("/").text


def test_tour_only_on_home(client):
    assert "/static/tour.js" not in client.get("/settings").text


def test_restart_from_settings(client):
    client.post("/tour/done")
    page = client.get("/settings").text
    assert 'action="/tour/restart"' in page and "Пройти обучение заново" in page
    r = client.post("/tour/restart", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert load_settings().tour_done is False
    assert "/static/tour.js" in client.get("/").text


def test_settings_save_keeps_tour_state(client):
    client.post("/tour/done")
    client.post("/settings", data={"mode": "local"})
    assert load_settings().tour_done is True


def test_tour_script_is_self_contained():
    js = (STATIC / "tour.js").read_text("utf-8")
    assert "http://" not in js and "https://" not in js
    assert "/tour/done" in js and "Escape" in js
    assert "cubic-bezier(0.1, 0.9, 0.2, 1)" in js or "cubic-bezier(.1,.9,.2,1)" in js
