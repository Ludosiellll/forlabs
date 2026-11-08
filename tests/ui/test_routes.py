import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from forlabs.core.llm import FakeLLM
from forlabs.core.settings import Settings
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

FIXTURE = Path(__file__).parents[1] / "fixtures" / "methodology_psip_lr7.txt"
RAW = {"number": "7", "topic": "Создание визуальных эффектов и анимации на HTML странице",
       "goal": "Сформировать умение создавать визуальные эффекты и анимации на HTML – странице",
       "equipment": "Методические указания к выполнению, ОС Windows, Sublime Text, MS Word",
       "assignment": "Задания выполняются к своей теме из ЛР №2",
       "main_tasks": ["Создайте 2 простых анимации к выбранной тематике."], "extra_tasks": [],
       "variant_text": None, "questions": []}
SEC = {"blocks": [{"type": "paragraph", "text": "Для страницы о Бобруйске описаны две анимации в style.css."},
                  {"type": "figure", "kind": "screenshot", "topic": "Анимация заголовка в браузере",
                   "instruction": "Открой index.html"}]}


@pytest.fixture
def client(tmp_home, tmp_path):
    fake = FakeLLM([RAW, SEC])
    ctx = AppContext(settings=Settings(mode="local"), llm_override=lambda: fake, prefer_word=False,
                     render_diagrams=False, synchronous=True)
    return TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")


def make_profile(client, tmp_path):
    r = client.post("/profiles/save", data={
        "id": "", "full_name": "Иванов Иван Петрович", "title_name": "И.П. Иванов", "gender": "m",
        "group": "РС02-23", "output_root": str(tmp_path / "out"), "doc_author": "Иванов Иван",
        "enr_psip_on": "on", "enr_psip_title": "Программные средства создания интернет-приложений",
        "enr_psip_teacher": "Т.Т. Тестова", "enr_psip_year": "2026/2027", "enr_psip_variant": "14",
        "enr_psip_style": "short"}, follow_redirects=False)
    assert r.status_code == 303
    return r


def test_pages_render(client, tmp_path):
    for url in ["/", "/profiles", "/profiles/new", "/rules", "/settings", "/semester"]:
        r = client.get(url)
        assert r.status_code == 200, url
        assert "ForLabs" in r.text


def test_profile_and_lab_flow(client, tmp_path):
    make_profile(client, tmp_path)
    home = client.get("/").text
    assert "Иванов Иван Петрович" in home
    r = client.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                       "text": ""},
                    files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")}, follow_redirects=False)
    assert r.status_code == 303
    lab_url = r.headers["location"]
    page = client.get(lab_url).text
    assert "Создайте 2 простых анимации" in page  # экран подтверждения разбора
    r = client.post(lab_url + "/confirm", data={
        "topic": RAW["topic"], "goal": RAW["goal"], "equipment": RAW["equipment"], "assignment": RAW["assignment"],
        "date": "24.09.2026", "tasks": "Создайте 2 простых анимации к выбранной тематике.", "extra_from": "",
        "questions": ""}, follow_redirects=False)
    assert r.status_code == 303
    r = client.post(lab_url + "/execute", follow_redirects=False)
    assert r.status_code == 303
    page = client.get(lab_url).text
    assert "Нужно от тебя" in page and "Открой index.html" in page
    buf = io.BytesIO()
    Image.new("RGB", (800, 400), "white").save(buf, format="PNG")
    r = client.post(lab_url + "/screenshot/1/2", content=buf.getvalue(), headers={"Content-Type": "image/png"})
    assert r.status_code == 200 and r.json()["status"] == "rendered"
    assert client.get(lab_url + "/page/1").headers["content-type"] == "image/png"
    d = client.get(lab_url + "/docx")
    assert d.status_code == 200 and d.content[:2] == b"PK"
    r = client.post(lab_url + "/final", follow_redirects=False)
    assert r.status_code == 303
    assert (tmp_path / "out" / "ПССИП" / "ЛР7" / "ЛР7_ПССИП.docx").exists()


def test_rules_edit(client):
    r = client.post("/rules/save", data={"path": "common/voice.md", "text": "Коротко."}, follow_redirects=False)
    assert r.status_code == 303
    assert "Коротко." in client.get("/rules?file=common/voice.md").text
    client.post("/rules/reset", data={"path": "common/voice.md"})
    assert "Коротко." not in client.get("/rules?file=common/voice.md").text


def test_journal_survives_restart(client, tmp_path, tmp_home):
    make_profile(client, tmp_path)
    r = client.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                       "text": ""},
                    files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")}, follow_redirects=False)
    lab_url = r.headers["location"]
    fresh = TestClient(create_app(AppContext(settings=Settings(mode="local"), synchronous=True)),
                       base_url="http://127.0.0.1:8765")
    page = fresh.get(lab_url + "?tab=journal").text
    assert "Разбор готов" in page


def test_rules_have_human_titles(client):
    page = client.get("/rules").text
    assert "Стандарт отчёта" in page and "Анти-ИИ: текст" in page
    assert page.index("Стандарт отчёта") < page.index("Анти-ИИ: текст")


def test_non_infinitive_goal_warns_and_accepts_own_conclusion(client, tmp_path):
    make_profile(client, tmp_path)
    r = client.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                       "text": ""},
                    files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")}, follow_redirects=False)
    lab_url = r.headers["location"]
    client.post(lab_url + "/confirm", data={"topic": RAW["topic"], "goal": "Формирование навыков анимации",
                                            "equipment": "", "assignment": "", "date": "24.09.2026",
                                            "tasks": "Создайте 2 простых анимации к выбранной тематике.",
                                            "extra_from": "", "questions": "", "conclusion": ""})
    page = client.get(lab_url + "?tab=spec").text
    assert "не начинается с инфинитива" in page
    client.post(lab_url + "/confirm", data={"topic": RAW["topic"], "goal": "Формирование навыков анимации",
                                            "equipment": "", "assignment": "", "date": "24.09.2026",
                                            "tasks": "Создайте 2 простых анимации к выбранной тематике.",
                                            "extra_from": "", "questions": "",
                                            "conclusion": "Сформировал навыки анимации."})
    page = client.get(lab_url + "?tab=spec").text
    assert "Сформировал навыки анимации." in page


def test_foreign_host_rejected(client):
    r = client.get("/rules", headers={"host": "evil.example"})
    assert r.status_code == 400


def test_cross_site_post_rejected(client):
    r = client.post("/rules/reset", data={"path": "common/voice.md"},
                    headers={"origin": "https://evil.example"}, follow_redirects=False)
    assert r.status_code == 403
    r = client.post("/rules/reset", data={"path": "common/voice.md"},
                    headers={"referer": "https://evil.example/page"}, follow_redirects=False)
    assert r.status_code == 403


def test_same_origin_post_allowed(client):
    r = client.post("/rules/reset", data={"path": "common/voice.md"},
                    headers={"origin": "http://127.0.0.1:8765"}, follow_redirects=False)
    assert r.status_code == 303


def test_unknown_rule_path_is_a_message_not_a_crash(client):
    r = client.post("/rules/save", data={"path": "../pyproject.toml", "text": "x"}, follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
