"""Связанные лабы (номера + общий отчёт) и файлы к лабе — на странице новой лабы и в самой лабе."""

import io

import docx
import pytest
from fastapi.testclient import TestClient
from test_routes import FIXTURE, RAW, make_profile

from forlabs.core.llm import FakeLLM
from forlabs.core.settings import Settings
from forlabs.ui.app import create_app
from forlabs.ui.context import AppContext

P, S = "ivanov_ivan_petrovich", "psip"
REPORT = """Лабораторная работа №1
Тема: Приложение «Учёт книг»
Главная форма FormMain.
Лабораторная работа №2
Тема: посторонняя
Лабораторная работа №27
Добавлена форма FormReport.
"""


def _docx(text):
    d = docx.Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.fixture
def app(tmp_home, tmp_path):
    fake = FakeLLM([RAW, RAW])
    ctx = AppContext(settings=Settings(mode="local"), llm_override=lambda: fake, prefer_word=False,
                     render_diagrams=False, synchronous=True)
    client = TestClient(create_app(ctx), base_url="http://127.0.0.1:8765")
    make_profile(client, tmp_path)
    return client, ctx


def test_new_lab_takes_linked_labs_and_files(app):
    client, ctx = app
    page = client.get("/labs/new").text
    assert 'name="linked"' in page and 'name="linked_report"' in page and 'name="materials"' in page
    r = client.post("/labs/new", data={"profile_id": P, "subject_id": S, "number": "", "text": "", "linked": "1, 27"},
                    files=[("files", (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")),
                           ("linked_report", ("общий.docx", _docx(REPORT), "application/octet-stream")),
                           ("materials", ("данные.csv", "ФИО;Балл\nИванов;5\n".encode(), "text/csv"))],
                    follow_redirects=False)
    assert r.status_code == 303 and "err=" not in r.headers["location"]
    st = ctx.store.load_lab(P, S, "7")
    assert st.linked == ["1", "27"] and set(st.linked_texts) == {"1", "27"}
    assert [m.name for m in st.materials] == ["данные.csv"]
    assert any(f.rule_id == "spec.linked_lab" and "ЛР 27" in f.message for f in st.findings)
    page = client.get(r.headers["location"]).text
    assert "данные.csv" in page and 'value="1, 27"' in page and "Опираюсь на ЛР 27" in page


def test_lab_page_links_and_files(app):
    client, ctx = app
    r = client.post("/labs/new", data={"profile_id": P, "subject_id": S, "number": "", "text": ""},
                    files={"files": (FIXTURE.name, FIXTURE.read_bytes(), "text/plain")}, follow_redirects=False)
    url = r.headers["location"]
    # связи меняются после разбора: замечания о них обновляются сразу
    r = client.post(url + "/links", data={"linked": "1 и 12"},
                    files={"linked_report": ("общий.docx", _docx(REPORT), "application/octet-stream")},
                    follow_redirects=False)
    assert "12" in r.headers["location"] and "err=" in r.headers["location"]  # ЛР 12 нет нигде — сказано
    st = ctx.store.load_lab(P, S, "7")
    assert st.linked == ["1", "12"] and list(st.linked_texts) == ["1"]
    assert any("ЛР 12" in f.message for f in st.findings if f.rule_id == "spec.linked_lab_missing")
    # файлы: добавить, слишком большой — отказ с причиной, убрать
    r = client.post(url + "/materials", files=[("materials", ("код.py", b"print(1)\n", "text/plain")),
                                               ("materials", ("том.txt", ("слово " * 100_000).encode(), "text/plain"))],
                    follow_redirects=False)
    assert "err=" in r.headers["location"]
    st = ctx.store.load_lab(P, S, "7")
    assert [m.name for m in st.materials] == ["код.py"]
    assert "код.py" in client.get(url).text
    client.post(url + "/materials/remove", data={"name": "код.py"})
    assert ctx.store.load_lab(P, S, "7").materials == []
    # пустые номера — связи убираются
    client.post(url + "/links", data={"linked": ""})
    st = ctx.store.load_lab(P, S, "7")
    assert st.linked == [] and st.linked_texts == {}
    left = " ".join(f.message for f in st.findings if f.rule_id.startswith("spec.linked"))
    assert "ЛР 1 " not in left and "ЛР 12" not in left  # остались только ссылки самой методички
