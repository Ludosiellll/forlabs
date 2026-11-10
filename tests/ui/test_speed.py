"""Переходы между вкладками не ждут облако: повторный заход на страницу не делает запросов, баланс в «Настройках»
подгружается уже после показа страницы, а щелчок по вкладке сразу виден."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_admin import admin, fake, make_client  # noqa: E402, F401

STATIC = Path(__file__).parents[2] / "forlabs" / "ui" / "static"


def paths(fake, since):
    return [r.url.path for r in fake.requests[since:]]


def test_second_round_over_the_tabs_asks_the_cloud_nothing(admin, fake):
    tabs = ["/", "/profiles", "/labs/new", "/rules", "/settings"]
    for url in tabs:
        assert admin.get(url).status_code == 200
    n = len(fake.requests)
    for url in tabs:
        assert admin.get(url).status_code == 200
    assert paths(fake, n) == []


def test_settings_page_does_not_wait_for_the_balance(admin, fake):
    n = len(fake.requests)
    page = admin.get("/settings").text
    assert not any("llm/balance" in p for p in paths(fake, n))  # страница показана без запроса к провайдеру ключа
    assert 'data-my-balance="/settings/balance"' in page
    d = admin.get("/settings/balance").json()
    assert any("llm/balance" in p for p in paths(fake, n)) and "state" in d and d["text"]


def test_balance_is_for_signed_in_cloud_users_only(tmp_home):
    from fastapi.testclient import TestClient

    from forlabs.core.settings import Settings
    from forlabs.ui.app import create_app
    from forlabs.ui.context import AppContext

    c = TestClient(create_app(AppContext(settings=Settings(mode="local"), synchronous=True)),
                   base_url="http://127.0.0.1:8765")
    assert 'data-my-balance' not in c.get("/settings").text
    assert c.get("/settings/balance").status_code == 404


def test_clicked_tab_is_marked_at_once():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert "nav-going" in js and ".nav-going" in css  # щелчок по вкладке подсвечивает её до ответа сервера
