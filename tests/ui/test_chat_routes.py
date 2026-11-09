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


def test_wishes_on_new_lab_and_spec_tab(tmp_home, tmp_path):
    fake = FakeLLM([RAW])
    ctx = AppContext(settings=Settings(mode="local"), llm_override=lambda: fake, prefer_word=False,
                     render_diagrams=False, synchronous=True)
    client = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(client, tmp_path)
    assert 'name="wishes"' in client.get("/labs/new").text
    r = client.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                       "text": "", "wishes": "Вариант дал преподаватель: 7"},
                    files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")}, follow_redirects=False)
    url = r.headers["location"]
    page = client.get(url + "?tab=spec").text
    assert 'name="wishes"' in page and "Вариант дал преподаватель: 7" in page
    client.post(url + "/confirm", data={"topic": RAW["topic"], "goal": RAW["goal"], "date": "24.09.2026",
                                        "tasks": "Создайте 2 простых анимации к выбранной тематике.",
                                        "wishes": "Вариант 7, анимации на CSS"})
    st = ctx.store.load_lab("ivanov_ivan_petrovich", "psip", "7")
    assert st.spec.wishes == "Вариант 7, анимации на CSS"


def _png(w=600, h=400):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (240, 240, 240)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def lab_photo(tmp_home, tmp_path):
    fake = FakeLLM([RAW, SEC, "Фото 1: «Добавить анимацию меню», подпись преподавателя.", PLAN, REVISE])
    ctx = AppContext(settings=Settings(mode="local"), llm_override=lambda: fake, prefer_word=False,
                     render_diagrams=False, synchronous=True)
    client = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(client, tmp_path)
    r = client.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                       "text": ""},
                    files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")}, follow_redirects=False)
    url = r.headers["location"]
    client.post(url + "/execute", follow_redirects=False)
    return client, ctx, url


def test_chat_photo_upload_and_thumbnail(lab_photo):
    client, ctx, url = lab_photo
    r = client.post(url + "/chat", data={"message": ""}, files={"images": ("note.png", _png(), "image/png")},
                    follow_redirects=False)
    assert "err=" not in r.headers["location"]
    page = client.get(url + "?tab=report").text
    st = ctx.store.load_lab("ivanov_ivan_petrovich", "psip", "7")
    src = f"{url}/notes/chat_{st.chat[0].id}_1.jpg"
    assert src in page and "Что прочитано с фото" in page and "подпись преподавателя" in page
    img = client.get(src)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert client.get(url + "/notes/../state.json").status_code == 404
    assert client.get(url + "/notes/evil.jpg").status_code == 404


def test_chat_bad_photo_refused(lab_photo):
    client, ctx, url = lab_photo
    r = client.post(url + "/chat", data={"message": "смотри"}, files={"images": ("x.png", b"nope", "image/png")},
                    follow_redirects=False)
    assert "err=" in r.headers["location"]
    assert ctx.store.load_lab("ivanov_ivan_petrovich", "psip", "7").chat == []


def test_new_lab_with_wish_photo(tmp_home, tmp_path):
    fake = FakeLLM(["Фото 1: Вариант 7.", RAW])
    ctx = AppContext(settings=Settings(mode="local"), llm_override=lambda: fake, prefer_word=False,
                     render_diagrams=False, synchronous=True)
    client = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(client, tmp_path)
    assert 'name="wish_images"' in client.get("/labs/new").text
    r = client.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                       "text": "", "wishes": ""},
                    files=[("files", (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")),
                           ("wish_images", ("board.png", _png(), "image/png"))], follow_redirects=False)
    url = r.headers["location"]
    page = client.get(url + "?tab=spec").text
    assert f"{url}/notes/wish_1.jpg" in page and "Вариант 7." in page


def test_incomplete_lab_shows_hint_instead_of_chat(lab):
    client, ctx, url = lab
    client.post(url + "/execute", follow_redirects=False)
    st = ctx.store.load_lab("ivanov_ivan_petrovich", "psip", "7")
    st.spec.tasks.append(st.spec.tasks[0].model_copy(update={"n": 2, "text": "Ещё задание."}))
    st.status = "failed"
    ctx.store.save_lab(st)
    page = client.get(url + "?tab=report").text
    assert 'name="message"' not in page and "не до конца" in page
    r = client.post(url + "/chat", data={"message": "вывод другой"}, follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_teacher_requirements_listed_and_removable(lab):
    client, ctx, url = lab
    client.post(url + "/execute", follow_redirects=False)
    ctx.memory().add_teacher("ivanov_ivan_petrovich", "psip", "Комментарии к каждому методу")
    page = client.get(url + "?tab=report").text
    from forlabs.pipeline.memory import teacher_key

    key = teacher_key(ctx.memory().teacher_list("ivanov_ivan_petrovich", "psip")[0])
    assert "Комментарии к каждому методу" in page and url + f"/teacher/remove/{key}" in page
    client.post(url + f"/teacher/remove/{key}", follow_redirects=False)
    assert ctx.memory().teacher_list("ivanov_ivan_petrovich", "psip") == []


def test_interrupted_executing_lab_offers_continue(lab):
    client, ctx, url = lab
    client.post(url + "/execute", follow_redirects=False)
    st = ctx.store.load_lab("ivanov_ivan_petrovich", "psip", "7")
    st.status = "executing"  # приложение закрыли посреди выполнения, задачи уже нет
    ctx.store.save_lab(st)
    page = client.get(url + "?tab=report").text
    assert "прервалась" in page and url + "/rerender" in page  # всё написано — осталось собрать
    st.spec.tasks.append(st.spec.tasks[0].model_copy(update={"n": 2, "text": "Ещё задание."}))
    ctx.store.save_lab(st)
    page = client.get(url + "?tab=report").text
    assert "прервалась" in page and url + "/execute" in page  # недописано — «Продолжить»


def test_failed_render_offers_rebuild_not_continue(lab):
    client, ctx, url = lab
    client.post(url + "/execute", follow_redirects=False)
    st = ctx.store.load_lab("ivanov_ivan_petrovich", "psip", "7")
    st.status, st.error = "failed", "report.docx открыт в Word — закрой его и нажми «Пересобрать документ»."
    ctx.store.save_lab(st)
    page = client.get(url + "?tab=report").text
    notice = page[page.index("notice err"):page.index("notice err") + 800]
    assert "/rerender" in notice and "Продолжить" not in notice


def test_profile_offer_buttons_apply_and_decline(lab):
    client, ctx, url = lab
    client.post(url + "/execute", follow_redirects=False)
    ctx.llm().responses[:] = [{"kind": "answer", "reply": "Предлагаю поменять руководителя.",
                               "profile": [{"field": "teacher", "value": "Петров П.П."}]}]
    client.post(url + "/chat", data={"message": "руководитель теперь Петров П.П."}, follow_redirects=False)
    page = client.get(url + "?tab=report").text
    assert "Поменять в профиле" in page and "Петров П.П." in page
    st = ctx.store.load_lab("ivanov_ivan_petrovich", "psip", "7")
    msg_id = st.chat[-1].id
    r = client.post(url + f"/chat/profile/{msg_id}", data={"apply": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "err=" not in r.headers["location"]
    assert ctx.store.get_profile("ivanov_ivan_petrovich").enrollment("psip").teacher == "Петров П.П."
    assert "Поменяно в профиле" in client.get(url + "?tab=report").text
    r = client.post(url + f"/chat/profile/{msg_id}", data={"apply": "1"}, follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_subject_memory_visible_and_theme_editable(lab):
    client, ctx, url = lab
    page = client.get(url).text
    assert "Что ForLabs помнит о предмете" in page and 'name="theme"' in page
    r = client.post(url + "/memory/theme", data={"theme": "сайт кофейни «Зерно»"}, follow_redirects=False)
    assert r.status_code == 303
    assert ctx.memory().theme("ivanov_ivan_petrovich", "psip") == "сайт кофейни «Зерно»"
    assert "сайт кофейни «Зерно»" in client.get(url).text
