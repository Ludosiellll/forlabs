"""Режим «задания отдельными файлами» в интерфейсе: выбор при создании и на вкладке разбора, настройка у предмета,
картинка-метка в профиле."""

import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
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


def _png(size=(300, 200), color=(200, 80, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_new_lab_form_offers_the_mode(app):
    client, ctx = app
    page = client.get("/labs/new").text
    fold = page.index('class="pane fold-pane"')
    assert page.index('name="task_files"') < fold
    assert "отдельными файлами" in page and "как у предмета" in page
    url = _create(client, task_files="1").headers["location"]
    st = ctx.store.load_lab(P, S, "7")
    assert st.spec.task_files is True and st.task_files is True
    page = client.get(url).text
    assert 'name="task_files" value="1" checked' in page  # на вкладке разбора режим виден и меняется


def test_mode_is_changed_on_the_parse_tab(app):
    client, ctx = app
    url = _create(client).headers["location"]
    st = ctx.store.load_lab(P, S, "7")
    assert st.spec.task_files is False
    form = {"topic": st.spec.topic, "goal": st.spec.goal, "date": st.spec.date, "assignment": st.spec.assignment,
            "tasks": "\n".join(t.text for t in st.spec.tasks), "questions": "\n".join(st.spec.questions)}
    client.post(url + "/confirm", data={**form, "files_shown": "1", "task_files": "1"}, follow_redirects=False)
    assert ctx.store.load_lab(P, S, "7").spec.task_files is True
    client.post(url + "/confirm", data=form, follow_redirects=False)  # страница без галочки её не сбрасывает
    assert ctx.store.load_lab(P, S, "7").spec.task_files is True
    client.post(url + "/confirm", data={**form, "files_shown": "1"}, follow_redirects=False)
    assert ctx.store.load_lab(P, S, "7").spec.task_files is False


def test_subject_can_default_to_the_mode(app, tmp_path):
    client, ctx = app
    page = client.get(f"/profiles/{P}").text
    assert 'name="enr_psip_files"' in page and 'name="enr_psip_files" checked' not in page
    client.post("/profiles/save", data={
        "id": P, "full_name": "Иванов Иван Петрович", "gender": "m", "group": "РС02-23",
        "output_root": str(tmp_path / "out"), "enr_psip_on": "on", "enr_psip_title": "ПССИП",
        "enr_psip_teacher": "Т.Т. Тестова", "enr_psip_year": "2026/2027", "enr_psip_variant": "14",
        "enr_psip_style": "short", "enr_psip_files": "on"}, follow_redirects=False)
    assert ctx.store.get_profile(P).enrollment("psip").task_files is True
    assert 'name="enr_psip_files" checked' in client.get(f"/profiles/{P}").text
    _create(client)  # «как у предмета»
    assert ctx.store.load_lab(P, S, "7").spec.task_files is True


def test_stamp_is_uploaded_shown_and_removed(app):
    client, ctx = app
    page = client.get(f"/profiles/{P}").text
    assert f'action="/profiles/{P}/stamp"' in page and "Картинка-метка" in page
    r = client.post(f"/profiles/{P}/stamp", files={"stamp": ("me.png", _png(), "image/png")}, follow_redirects=False)
    assert r.status_code == 303 and "err=" not in r.headers["location"]
    stamp = ctx.store.get_profile(P).stamp
    with Image.open(io.BytesIO(base64.b64decode(stamp))) as im:
        assert im.format == "PNG" and im.width == im.height <= 400  # квадрат: в документе не исказится
    assert "data:image/png;base64," in client.get(f"/profiles/{P}").text
    # сохранение остальной анкеты метку не стирает
    make_profile_again = {"id": P, "full_name": "Иванов Иван Петрович", "gender": "m", "group": "РС02-23",
                          "enr_psip_on": "on", "enr_psip_variant": "14"}
    client.post("/profiles/save", data=make_profile_again, follow_redirects=False)
    assert ctx.store.get_profile(P).stamp == stamp
    client.post(f"/profiles/{P}/stamp/remove", follow_redirects=False)
    assert ctx.store.get_profile(P).stamp == ""


def test_bad_stamp_is_refused_with_a_reason(app):
    client, ctx = app
    r = client.post(f"/profiles/{P}/stamp", files={"stamp": ("me.png", b"not an image", "image/png")},
                    follow_redirects=False)
    assert "err=" in r.headers["location"] and ctx.store.get_profile(P).stamp == ""
    r = client.post(f"/profiles/{P}/stamp", files={"stamp": ("", b"", "application/octet-stream")},
                    follow_redirects=False)
    assert "err=" in r.headers["location"]
