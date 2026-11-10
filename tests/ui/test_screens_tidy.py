"""Экраны без лишнего: «Программы на этом ПК» и редкие действия — в свёрнутом «Дополнительно», в шапке одна
кнопка выхода, поля предмета в профиле — только у отмеченных, дата лабы — по-человечески."""

from datetime import datetime

from fastapi.testclient import TestClient
from test_routes import make_profile

from forlabs.core.settings import Settings
from forlabs.ui.app import create_app, when
from forlabs.ui.context import AppContext


def client(tmp_home):
    ctx = AppContext(settings=Settings(mode="local", tour_done=True), synchronous=True)
    return TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")


def test_program_paths_are_tucked_away_in_settings(tmp_home):
    page = client(tmp_home).get("/settings").text
    assert "Программы на этом ПК" not in page  # студенту этот блок ни к чему
    more = page[page.index("<span>Дополнительно</span>"):]
    assert 'name="soffice_path"' in more and 'name="tesseract_path"' in more  # но путь указать можно
    assert "Пройти обучение заново" in more and "Проверить связь с моделью" in more
    assert "<details" in page and "найдено" in page  # список программ для лаб свёрнут в одну строку


def test_lab_date_reads_like_a_person_wrote_it():
    now = datetime(2026, 11, 10, 15, 0)
    assert when("2026-11-10T13:27:05", now) == "сегодня 13:27"
    assert when("2026-11-09T09:10:00", now) == "вчера 09:10"
    assert when("2026-10-03T09:10:00", now) == "3 окт"
    assert when("2025-10-03T09:10:00", now) == "03.10.2025"
    assert when("", now) == ""


def test_profile_form_is_short_and_keeps_every_field(tmp_home, tmp_path):
    c = client(tmp_home)
    page = c.get("/profiles/new").text
    for name in ("full_name", "group", "gender", "output_root", "title_name", "doc_author", "college_lines_0",
                 "specialty_code", "city", "enr_psip_on", "enr_psip_teacher", "enr_psip_variant", "enr_psip_title",
                 "enr_psip_year", "enr_psip_style", "enr_psip_workplace"):
        assert f'name="{name}"' in page, name
    assert page.index("<span>Титульный лист</span>") > page.index('name="enr_psip_on"')  # титул свёрнут и ниже предметов
    make_profile(c, tmp_path)  # и форма по-прежнему сохраняется
    assert "Иванов Иван Петрович" in c.get("/profiles").text


def test_new_lab_form_shows_only_what_is_needed(tmp_home, tmp_path):
    c = client(tmp_home)
    make_profile(c, tmp_path)
    page = c.get("/labs/new").text
    extra = page[page.index("<span>Дополнительно</span>"):]
    assert 'name="number"' in extra and 'name="linked"' in extra and 'name="materials"' in extra
    assert 'name="files"' in page and 'name="wishes"' in page and 'name="text"' in page


def test_one_profile_needs_no_profile_switcher(tmp_home, tmp_path):
    c = client(tmp_home)
    make_profile(c, tmp_path)
    assert "<h3>Профиль</h3>" not in c.get("/").text


def test_new_lab_form_starts_on_the_students_own_subject(tmp_home, tmp_path):
    c = client(tmp_home)
    make_profile(c, tmp_path)  # в профиле отмечен один предмет — psip
    page = c.get("/labs/new").text
    assert '<option value="psip" selected>' in page  # а не первый предмет из общего списка
    assert page.index('value="psip"') < page.index('value="ssp"')  # свои предметы — первыми
    assert 'type="hidden" name="profile_id"' in page  # профиль один — выбирать не из чего


def test_waiting_page_checks_approval_by_itself(tmp_home):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from test_admin import make_client
    from test_cloud import FakeSupabase

    fake = FakeSupabase()
    c = make_client(fake, tmp_home, me="member")
    fake.tables["members"][0]["approved"] = False
    page = c.get("/").text
    assert "ждёт одобрения" in page and 'http-equiv="refresh"' in page  # студенту не нужно жать «Обновить»
