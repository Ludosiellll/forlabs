"""Панель «Правки» на экране лабы."""

import pytest
from fastapi.testclient import TestClient
from test_routes import FIXTURE, RAW, SEC, make_profile

from forlabs.core.llm import FakeLLM
from forlabs.core.settings import Settings
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

PLAN = {"kind": "edit", "reply": "Добавлю вторую анимацию.", "tasks": [{"n": 1, "instruction": "добавь анимацию меню"}]}
REVISE = {"blocks": [{"keep": "b1"}, {"type": "paragraph", "text": "Для меню добавлена анимация раскрытия пунктов."},
                     {"keep": "b2"}]}


@pytest.fixture
def lab(tmp_home, tmp_path):
    fake = FakeLLM([RAW, SEC, PLAN, REVISE])
    ctx = AppContext(settings=Settings(mode="local"), llm_override=lambda: fake, prefer_word=False,
                     render_diagrams=False, synchronous=True)
    client = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(client, tmp_path)
    r = client.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                       "text": ""},
                    files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")}, follow_redirects=False)
    url = r.headers["location"]
    return client, ctx, url


def test_chat_hidden_until_report_exists(lab):
    client, ctx, url = lab
    page = client.get(url + "?tab=report").text
    assert 'name="message"' not in page and "после выполнения" in page


def test_chat_edit_and_revert(lab):
    client, ctx, url = lab
    client.post(url + "/execute", follow_redirects=False)
    r = client.post(url + "/chat", data={"message": "Преподаватель: добавить анимацию меню"}, follow_redirects=False)
    assert r.status_code == 303 and "err=" not in r.headers["location"]
    page = client.get(url + "?tab=report").text
    assert "Преподаватель: добавить анимацию меню" in page and "Добавлю вторую анимацию." in page
    assert "Вернуть как было" in page and 'name="message"' in page
    st = ctx.store.load_lab("ivanov_ivan_petrovich", "psip", "7")
    assert "анимация раскрытия" in st.report.sections[0].blocks[1].text
    client.post(url + "/chat/revert/1", follow_redirects=False)
    st = ctx.store.load_lab("ivanov_ivan_petrovich", "psip", "7")
    assert st.chat[-1].kind == "revert" and len(st.report.sections[0].blocks) == 2
    assert "Отменить откат" in client.get(url + "?tab=report").text


def test_empty_message_refused(lab):
    client, ctx, url = lab
    client.post(url + "/execute", follow_redirects=False)
    r = client.post(url + "/chat", data={"message": "   "}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    assert ctx.store.load_lab("ivanov_ivan_petrovich", "psip", "7").chat == []


def test_chat_refused_while_busy_and_unknown_version(lab):
    client, ctx, url = lab
    client.post(url + "/execute", follow_redirects=False)
    ctx.running.add("ivanov_ivan_petrovich/psip/7")
    r = client.post(url + "/chat", data={"message": "вывод подробнее"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    ctx.running.clear()
    r = client.post(url + "/chat/revert/42", follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_remember_checkbox_is_passed(lab):
    client, ctx, url = lab
    client.post(url + "/execute", follow_redirects=False)
    client.post(url + "/chat", data={"message": "всегда комментарии в коде", "remember": "1"})
    st = ctx.store.load_lab("ivanov_ivan_petrovich", "psip", "7")
    assert st.chat[0].remember is True
