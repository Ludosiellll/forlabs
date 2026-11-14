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


def test_preview_from_other_computer_offers_rerender(client, tmp_path):
    make_profile(client, tmp_path)
    r = client.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                       "text": ""},
                    files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")}, follow_redirects=False)
    lab = r.headers["location"]
    ctx = client.app.state.ctx
    st = ctx.store.load_lab("ivanov_ivan_petrovich", "psip", "7")
    st.pages = ["/другой/компьютер/page-1.png"]
    st.docx_path = "/другой/компьютер/report.docx"
    st.warnings = ["Word на том компьютере не найден"]
    st.status = "rendered"
    ctx.store.save_lab(st)
    page = client.get(lab + "?tab=report").text
    assert "Документ нужно пересобрать" in page and "/page/1" not in page
    assert "Скачать .docx" not in page and "Word на том компьютере" not in page


def test_failed_lab_offers_continue(client, tmp_path):
    make_profile(client, tmp_path)
    r = client.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                       "text": ""},
                    files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")}, follow_redirects=False)
    lab = r.headers["location"]
    ctx = client.app.state.ctx
    st = ctx.store.load_lab("ivanov_ivan_petrovich", "psip", "7")
    st.status, st.error = "failed", "Нет связи с моделью"
    ctx.store.save_lab(st)
    page = client.get(lab + "?tab=report").text
    assert "Продолжить" in page and lab + "/execute" in page
    assert "Документа пока нет" not in page


def _new_lab(client, tmp_path):
    make_profile(client, tmp_path)
    r = client.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                       "text": ""},
                    files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")}, follow_redirects=False)
    return r.headers["location"]


def test_actions_refused_while_lab_busy(client, tmp_path):
    lab = _new_lab(client, tmp_path)
    ctx = client.app.state.ctx
    ctx.running.add("ivanov_ivan_petrovich/psip/7")
    r = client.post(lab + "/screenshot/1/2", content=b"png", headers={"Content-Type": "image/png"})
    assert r.status_code == 409 and r.json()["error"]
    for action, data in [("/accept", {"rule": "x"}), ("/confirm", {"topic": "Т", "goal": "Ц"}), ("/final", {}),
                         ("/delete", {})]:
        r = client.post(lab + action, data=data, follow_redirects=False)
        assert r.status_code == 303 and "err=" in r.headers["location"], action
    st = ctx.store.load_lab("ivanov_ivan_petrovich", "psip", "7")
    assert st is not None and st.accepted_rules == [] and st.spec.topic != "Т"


def test_screenshot_does_not_block_the_server(tmp_home, tmp_path):
    import threading

    fake = FakeLLM([RAW, SEC])
    ctx = AppContext(settings=Settings(mode="local"), llm_override=lambda: fake, prefer_word=False,
                     render_diagrams=False, synchronous=True)
    client = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    lab = _new_lab(client, tmp_path)
    client.post(lab + "/execute", follow_redirects=False)
    ctx.synchronous = False
    release = threading.Event()

    def slow_attach(*a, **k):
        release.wait(5)

    ctx.pipeline.attach_screenshot = slow_attach
    t0 = time.monotonic()
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, format="PNG")
    r = client.post(lab + "/screenshot/1/2", content=buf.getvalue(), headers={"Content-Type": "image/png"})
    elapsed = time.monotonic() - t0
    release.set()
    assert r.status_code == 200 and elapsed < 2


def test_new_lab_with_taken_number_shows_message(client, tmp_path):
    _new_lab(client, tmp_path)
    r = client.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "7",
                                       "text": "Лабораторная работа №7"}, follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert "/labs/new" in r.headers["location"]


def test_renamed_lab_gets_idle_event(client, tmp_path):
    _new_lab(client, tmp_path)
    ctx = client.app.state.ctx
    kinds = [e.kind for e in ctx.bus.history("ivanov_ivan_petrovich/psip/7")]
    assert kinds and kinds[-1] == "idle"


def test_semester_build_does_not_block_other_pages(client, tmp_path):
    import threading

    from forlabs.pipeline.run import PipelineError

    make_profile(client, tmp_path)
    ctx = client.app.state.ctx
    release = threading.Event()

    def slow_build(*a, **k):
        release.wait(5)
        raise PipelineError("стоп")

    ctx.pipeline.build_semester = slow_build
    with TestClient(client.app, base_url="http://127.0.0.1:8765") as shared:  # один цикл событий на все запросы
        t = threading.Thread(target=lambda: shared.post("/semester", data={
            "profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "numbers": ["7"]}, follow_redirects=False))
        t.start()
        time.sleep(0.3)
        t0 = time.monotonic()
        r = shared.get("/rules")
        elapsed = time.monotonic() - t0
        release.set()
        t.join(5)
    assert r.status_code == 200 and elapsed < 2


class _SubjectCloud:
    def __init__(self, rows):
        self.rows = rows

    def list_custom_subjects(self):
        return list(self.rows)

    def save_custom_subject(self, subject):
        self.rows.append(subject)


def test_custom_subjects_follow_the_account(tmp_home):
    from forlabs.core.subjects import Subject, all_subjects

    ctx = AppContext(settings=Settings(mode="local"), synchronous=True)
    cloud = _SubjectCloud([Subject(id="oop", name="ООП", short_name="ООП")])
    ctx._store = cloud
    ctx.sync_subjects()
    assert "oop" in all_subjects()  # добавлен на другом компьютере — есть и здесь
    ctx.add_subject(Subject(id="bd", name="Базы данных", short_name="БД"))
    assert "bd" in all_subjects() and [s.id for s in cloud.rows] == ["oop", "bd"]


def _rtf_of(text: str) -> bytes:
    body = "".join(f"\\u{ord(c)}?" if ord(c) > 127 else c for c in text).replace("\n", "\\par\n")
    return ("{\\rtf1\\ansi\\ansicpg1251 " + body + "\\par}").encode("ascii")


def test_rtf_methodology_is_accepted_and_stored_as_docx(client, tmp_path, tmp_home):
    make_profile(client, tmp_path)
    rtf = _rtf_of(FIXTURE.read_text(encoding="utf-8"))
    r = client.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                       "text": ""},
                    files={"files": ("Методичка.rtf", rtf, "application/rtf")}, follow_redirects=False)
    assert r.status_code == 303 and "err=" not in r.headers["location"], r.headers["location"]
    assert "Создайте 2 простых анимации" in client.get(r.headers["location"]).text
    stored = [p.name for p in Path(tmp_home).rglob("source/*")]
    assert stored == ["Методичка.docx"]


def test_zip_methodology_gives_source_and_lab_files(client, tmp_path, tmp_home):
    import zipfile

    make_profile(client, tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ЛР7/методичка.txt", FIXTURE.read_bytes())
        z.writestr("ЛР7/images/фон.png", b"PNG")
    r = client.post("/labs/new", data={"profile_id": "ivanov_ivan_petrovich", "subject_id": "psip", "number": "",
                                       "text": ""},
                    files={"files": ("лаба7.zip", buf.getvalue(), "application/zip")}, follow_redirects=False)
    assert r.status_code == 303 and "err=" not in r.headers["location"], r.headers["location"]
    page = client.get(r.headers["location"]).text
    assert "лаба7.zip" in page  # архив — среди файлов к лабе (картинка из него)
    assert [p.name for p in Path(tmp_home).rglob("source/*")] == ["методичка.txt"]
    assert any(p.name == "фон.png" for p in Path(tmp_home).rglob("assets_in/*/*/*/*"))
