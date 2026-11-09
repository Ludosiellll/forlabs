"""Форма «Новая лаба» с методичками разных лаб и замена методички на странице лабы."""

import io
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import docx
import pytest
from fastapi.testclient import TestClient
from test_routes import FIXTURE, RAW, SEC, make_profile

from forlabs.core.llm import FakeLLM, LLMError
from forlabs.core.settings import Settings
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

P, S = "ivanov_ivan_petrovich", "psip"


def lab_text(n, topic):
    return (f"Лабораторная работа №{n}\nТема: {topic}\nЦель: научиться решать задачи по теме «{topic}» на Python.\n"
            "Оснащение: ПК, Python 3, IDLE.\nЗадания:\n"
            f"1. Напишите программу к теме «{topic}», которая читает данные и выводит результат.\n"
            "2. Дополните программу из задания 1 проверкой ввода.\n")


def as_docx(text):
    d = docx.Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


TOPICS = {"19": "Библиотеки", "20": "Файлы", "21": "Работа с JSON"}
NAMES = {"19": "ЛР19 библиотеки.docx", "20": "ЛР20 файлы.docx", "21": "ЛР21 работа с JSON.docx"}


def upload(*numbers):
    return [("files", (NAMES[n], as_docx(lab_text(n, TOPICS[n])), "application/octet-stream")) for n in numbers]


def raw(n):
    return dict(RAW, number=n, topic=TOPICS[n])


def make(responses, tmp_path):
    fake = FakeLLM(responses)
    ctx = AppContext(settings=Settings(mode="local"), llm_override=lambda: fake, prefer_word=False,
                     render_diagrams=False, synchronous=True)
    client = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(client, tmp_path)
    return client, ctx, fake


def new_lab(client, files, **data):
    return client.post("/labs/new", data={"profile_id": P, "subject_id": S, "number": "", "text": "", **data},
                       files=files, follow_redirects=False)


def labs(ctx):
    return {lab.number: lab for lab in ctx.store.list_labs(P, S)}


def msg(r, key="msg"):
    return parse_qs(urlparse(r.headers["location"]).query).get(key, [""])[0]


def test_methodologies_of_different_labs_become_separate_labs(tmp_home, tmp_path):
    client, ctx, fake = make([raw("19"), raw("20"), raw("21")], tmp_path)
    r = new_lab(client, upload("19", "20", "21"), wishes="код на Python 3", linked="1")
    assert r.status_code == 303 and r.headers["location"].startswith(f"/?profile={P}&subject={S}")
    assert "ЛР 19, ЛР 20, ЛР 21" in msg(r) and not msg(r, "err")
    found = labs(ctx)
    assert sorted(found) == ["19", "20", "21"]
    for n, st in found.items():
        assert st.status == "parsed", st.error
        assert st.spec.topic == TOPICS[n] and st.spec.wishes == "код на Python 3" and st.linked == ["1"]
        assert ctx.pipeline.source_names(st) == [NAMES[n].replace(" ", "_")]
    sent = [c[1]["content"] for c in fake.calls]
    assert len(sent) == 3
    for n, text in zip(("19", "20", "21"), sent):  # модель видит только свою методичку
        assert f"Номер работы: {n}" in text and f"Тема: {TOPICS[n]}" in text
        assert sum(f"Тема: {t}" in text for t in TOPICS.values()) == 1
    home = client.get(r.headers["location"]).text
    assert "ЛР 19, ЛР 20, ЛР 21" in home and "Библиотеки" in home and "Работа с JSON" in home


def test_typed_number_keeps_files_in_one_lab(tmp_home, tmp_path):
    client, ctx, fake = make([raw("19")], tmp_path)
    r = new_lab(client, upload("19", "20", "21"), number="19")
    assert r.headers["location"] == "/labs/ivanov_ivan_petrovich/psip/19"
    assert sorted(labs(ctx)) == ["19"] and len(ctx.pipeline.source_names(labs(ctx)["19"])) == 3


def test_appendix_without_number_keeps_one_lab_and_warns(tmp_home, tmp_path):
    client, ctx, fake = make([raw("19")], tmp_path)
    files = upload("19", "20") + [("files", ("варианты.docx", as_docx("Приложение А. Варианты"), "application/x"))]
    r = new_lab(client, files)
    assert r.headers["location"] == "/labs/ivanov_ivan_petrovich/psip/19"
    st = labs(ctx)["19"]
    assert len(ctx.pipeline.source_names(st)) == 3
    warn = [f.message for f in st.findings if f.rule_id == "spec.several_labs"]
    assert warn and "19, 20" in warn[0] and "ЛР 19" in warn[0]
    assert "Разобрана ЛР 19" in client.get(r.headers["location"] + "?tab=spec").text


def test_failure_on_one_lab_keeps_the_others(tmp_home, tmp_path):
    client, ctx, fake = make([raw("19"), LLMError("network", "нет сети"), raw("21")], tmp_path)
    r = new_lab(client, upload("19", "20", "21"))
    assert "ЛР 19, ЛР 20, ЛР 21" in msg(r)
    found = labs(ctx)
    assert found["19"].status == "parsed" and found["21"].status == "parsed"
    assert found["20"].status == "failed" and ctx.pipeline.source_names(found["20"]) == ["ЛР20_файлы.docx"]
    assert "Разобрать ещё раз" in client.get("/labs/ivanov_ivan_petrovich/psip/20").text


def test_lab_that_could_not_be_created_is_named_others_stay(tmp_home, tmp_path, monkeypatch):
    from forlabs.pipeline.run import PipelineError

    client, ctx, fake = make([raw("19"), raw("21")], tmp_path)
    real = ctx.pipeline.create_lab

    def flaky(profile_id, subject_id, files, **kw):
        if files[0][0].startswith("ЛР20"):
            raise PipelineError("Диск переполнен")
        return real(profile_id, subject_id, files, **kw)

    monkeypatch.setattr(ctx.pipeline, "create_lab", flaky)
    r = new_lab(client, upload("19", "20", "21"))
    assert "ЛР 19, ЛР 21" in msg(r) and "ЛР 20" in msg(r, "err") and "Диск переполнен" in msg(r, "err")
    assert sorted(labs(ctx)) == ["19", "21"]


def test_taken_number_becomes_n_2(tmp_home, tmp_path):
    client, ctx, fake = make([raw("19"), raw("19"), raw("20")], tmp_path)
    new_lab(client, upload("19"))
    r = new_lab(client, upload("19", "20"))
    assert "ЛР 19-2, ЛР 20" in msg(r)
    assert sorted(labs(ctx)) == ["19", "19-2", "20"]
    assert labs(ctx)["19-2"].spec.number == "19"


def test_split_form_submitted_twice_creates_labs_once(tmp_home, tmp_path):
    client, ctx, fake = make([raw("19"), raw("20")], tmp_path)
    first = new_lab(client, upload("19", "20"), form_token="tok-split")
    again = new_lab(client, upload("19", "20"), form_token="tok-split")
    assert again.headers["location"] == first.headers["location"]
    assert sorted(labs(ctx)) == ["19", "20"] and len(fake.calls) == 2


def test_split_respects_upload_limit(tmp_home, tmp_path, monkeypatch):
    from forlabs.ui import app as app_mod

    client, ctx, fake = make([], tmp_path)
    monkeypatch.setattr(app_mod, "MAX_UPLOAD", 1000)
    r = new_lab(client, upload("19", "20"))
    assert "err=" in r.headers["location"] and labs(ctx) == {}


def test_nothing_created_returns_to_form(tmp_home, tmp_path, monkeypatch):
    from forlabs.pipeline.run import PipelineError

    client, ctx, fake = make([], tmp_path)

    def broken(*a, **k):
        raise PipelineError("Не больше 4 фото за раз")

    monkeypatch.setattr(ctx.pipeline, "create_lab", broken)
    r = new_lab(client, upload("19", "20"), form_token="tok-err")
    assert r.headers["location"].startswith("/labs/new?") and msg(r, "err") == "Не больше 4 фото за раз"
    assert ctx.form_tokens.get("tok-err") is None  # форму можно отправить снова


# замена методички

def lab_from_wrong_file(tmp_path, extra=()):
    client, ctx, fake = make([raw("20"), *extra], tmp_path)
    r = new_lab(client, upload("20"))
    return client, ctx, fake, r.headers["location"]


def test_lab_page_shows_methodology_files_and_replace_entry(tmp_home, tmp_path):
    client, ctx, fake, url = lab_from_wrong_file(tmp_path)
    page = client.get(url).text
    assert "Методичка: ЛР20_файлы.docx" in page and "?tab=spec&replace=1#replace" in page
    spec = client.get(url + "?tab=spec").text
    assert "Заменить методичку" in spec and f'action="{url}/source"' in spec
    assert "<details" in spec and 'id="replace" open' not in spec
    assert 'id="replace" open' in client.get(url + "?tab=spec&replace=1").text


def test_replace_methodology_reparses_under_new_number(tmp_home, tmp_path):
    client, ctx, fake, url = lab_from_wrong_file(tmp_path, [raw("19")])
    r = client.post(url + "/source", files=upload("19"), follow_redirects=True)
    assert r.url.path == "/labs/ivanov_ivan_petrovich/psip/19"
    assert sorted(labs(ctx)) == ["19"] and labs(ctx)["19"].spec.topic == "Библиотеки"
    assert "ЛР19_библиотеки.docx" in r.text and "ЛР20_файлы.docx" not in r.text


def test_replace_methodology_with_pasted_text(tmp_home, tmp_path):
    client, ctx, fake, url = lab_from_wrong_file(tmp_path, [raw("20")])
    client.post(url + "/source", data={"text": lab_text(20, "Файлы и папки")}, follow_redirects=False)
    st = labs(ctx)["20"]
    assert ctx.pipeline.source_names(st) == ["текст.txt"] and "Файлы и папки" in st.spec.source_text


def test_replace_refused_while_busy_or_empty(tmp_home, tmp_path, monkeypatch):
    from forlabs.ui import app as app_mod

    client, ctx, fake, url = lab_from_wrong_file(tmp_path)
    ctx.running.add("ivanov_ivan_petrovich/psip/20")
    r = client.post(url + "/source", files=upload("19"), follow_redirects=False)
    assert "err=" in r.headers["location"]
    ctx.running.clear()
    r = client.post(url + "/source", data={"text": "  "}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    monkeypatch.setattr(app_mod, "MAX_UPLOAD", 1000)
    r = client.post(url + "/source", files=upload("19"), follow_redirects=False)
    assert "err=" in r.headers["location"]
    assert ctx.pipeline.source_names(labs(ctx)["20"]) == ["ЛР20_файлы.docx"] and len(fake.calls) == 1


def test_replace_on_lab_with_report_warns_and_old_report_comes_back(tmp_home, tmp_path):
    client, ctx, fake = make([RAW, SEC, dict(RAW, topic="Новая тема")], tmp_path)
    r = new_lab(client, [("files", (FIXTURE.name, FIXTURE.read_bytes(), "text/plain"))])
    url = r.headers["location"]
    client.post(url + "/execute", follow_redirects=False)
    assert labs(ctx)["7"].report is not None
    spec = client.get(url + "?tab=spec").text
    assert "Задания разберутся заново" in spec
    client.post(url + "/source", data={"text": lab_text(7, "Новая тема")}, follow_redirects=False)
    st = labs(ctx)["7"]
    assert st.report is None and st.spec.topic == "Новая тема" and st.status == "parsed"
    vid = st.versions[-1].id
    page = client.get(url + "?tab=spec").text
    assert "Вернуть прежний отчёт" in page and f"/chat/revert/{vid}" in page
    client.post(url + f"/chat/revert/{vid}", follow_redirects=False)
    st = labs(ctx)["7"]
    assert st.report is not None and st.spec.topic == RAW["topic"]
    assert ctx.pipeline.source_names(st) == [FIXTURE.name]
