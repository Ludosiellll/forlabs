"""Вид работы в интерфейсе: выбор при создании и на вкладке разбора, подписи «ПЗ 7» вместо «ЛР 7»."""

import pytest
from fastapi.testclient import TestClient
from test_routes import FIXTURE, RAW, make_profile

from forlabs.core.llm import FakeLLM
from forlabs.core.settings import Settings
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

P, S = "ivanov_ivan_petrovich", "psip"


@pytest.fixture
def app(tmp_home, tmp_path):
    fake = FakeLLM([RAW, RAW, RAW])
    ctx = AppContext(settings=Settings(mode="local"), llm_override=lambda: fake, prefer_word=False,
                     render_diagrams=False, synchronous=True)
    client = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(client, tmp_path)
    return client, ctx


def _create(client, **data):
    return client.post("/labs/new", data={"profile_id": P, "subject_id": S, "number": "", "text": "", **data},
                       files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")}, follow_redirects=False)


def test_new_lab_form_offers_the_kind_of_work(app):
    client, _ = app
    page = client.get("/labs/new").text
    fold = page.index('class="pane fold-pane"')
    assert page.index('name="work_kind"') < fold  # на самой форме, не в «Дополнительно»
    for value, title in (("", "как в методичке"), ("lab", "Лабораторная работа"), ("practice", "Практическая работа"),
                         ("session", "Практическое занятие")):
        assert f'<option value="{value}"' in page and title in page


def test_chosen_kind_names_the_work_everywhere(app):
    client, ctx = app
    r = _create(client, work_kind="session")
    assert r.status_code == 303 and "err=" not in r.headers["location"]
    st = ctx.store.load_lab(P, S, "7")
    assert st.spec.work_kind == "session" and st.work_kind == "session"
    page = client.get(r.headers["location"]).text
    assert "<h1>ПЗ 7</h1>" in page and "<title>ПЗ 7 ·" in page and "ЛР 7" not in page
    assert '<option value="session" selected' in page  # на вкладке разбора вид можно поменять
    assert "ПЗ 7</a>" in client.get("/").text


def test_kind_is_changed_on_the_parse_tab(app):
    client, ctx = app
    url = _create(client).headers["location"]
    st = ctx.store.load_lab(P, S, "7")
    assert st.spec.work_kind == "lab" and "<h1>ЛР 7</h1>" in client.get(url).text
    form = {"topic": st.spec.topic, "goal": st.spec.goal, "date": st.spec.date, "assignment": st.spec.assignment,
            "tasks": "\n".join(t.text for t in st.spec.tasks), "questions": "\n".join(st.spec.questions),
            "work_kind": "practice"}
    r = client.post(url + "/confirm", data=form, follow_redirects=False)
    assert r.status_code == 303 and "err=" not in r.headers["location"]
    st = ctx.store.load_lab(P, S, "7")
    assert st.spec.work_kind == "practice" and st.work_kind == "practice"
    assert "<h1>ПР 7</h1>" in client.get(url).text
    # форма без поля (старая страница в другой вкладке) вид не сбрасывает
    form.pop("work_kind")
    client.post(url + "/confirm", data=form, follow_redirects=False)
    assert ctx.store.load_lab(P, S, "7").spec.work_kind == "practice"


def test_practice_next_to_a_lab_with_the_same_number(app):
    client, ctx = app
    _create(client)
    r = _create(client, work_kind="session", number="7")
    assert "err=" not in r.headers["location"]
    labs = {x.number: x for x in ctx.store.list_labs(P, S)}
    assert set(labs) == {"7", "7-пз"} and labs["7-пз"].spec.number == "7"
    home = client.get("/").text
    assert "ЛР 7</a>" in home and "ПЗ 7</a>" in home
