"""Картинки внутри методички при разборе: модель читает их, текст встаёт на место картинки."""

import io
import json

from docx import Document
from docx.shared import Inches
from PIL import Image
from test_run import RAW, setup

from forlabs.core.llm import LLMError
from forlabs.pipeline.images import UNREAD_PICTURE


def png(w=400, h=200, color=(240, 240, 240)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def methodology(n=1) -> bytes:
    d = Document()
    d.add_paragraph("Лабораторная работа №7")
    d.add_paragraph("Тема работы: Создание визуальных эффектов и анимации на HTML странице")
    d.add_paragraph("Создайте 2 простых анимации к выбранной тематике.")
    d.add_paragraph("Создайте анимацию с эффектом параллакса.")
    d.add_paragraph("Выполните запросы, представленные на рисунках:")
    for i in range(n):
        d.add_picture(io.BytesIO(png(color=(200, 200, 200 - i))), width=Inches(2))
    d.add_paragraph("Hover-эффекты и их применение.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_parse_puts_picture_text_into_the_methodology(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [{"pictures": ["SELECT * FROM Products\nLIMIT 3;"]}, RAW])
    st = pipe.create_lab("ivanov", "psip", [("m.docx", methodology())])
    st = pipe.parse(st, date="24.09.2026")
    assert "[Картинка в методичке:\nSELECT * FROM Products\nLIMIT 3;\n]" in st.spec.source_text
    assert "[[картинка" not in st.spec.source_text
    # разбор получил методичку уже с текстом картинки
    assert "SELECT * FROM Products" in fake.calls[1][-1]["content"]
    assert not [f for f in st.findings if f.rule_id == "spec.pictures_unread"]
    assert any("картинк" in e.text.lower() for e in pipe.journal(st))


def test_second_parse_does_not_pay_for_the_same_pictures(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [{"pictures": ["SELECT 1;"]}, RAW, RAW])
    st = pipe.create_lab("ivanov", "psip", [("m.docx", methodology())])
    st = pipe.parse(st, date="24.09.2026")
    st = pipe.parse(st, date="24.09.2026")
    assert len(fake.calls) == 3 and "SELECT 1;" in st.spec.source_text
    saved = json.loads((pipe.lab_dir(st) / "source_pictures.json").read_text(encoding="utf-8"))
    assert list(saved.values()) == ["SELECT 1;"]


def test_unread_pictures_become_a_note_not_a_failure(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [LLMError("network", "timeout"), RAW])
    st = pipe.create_lab("ivanov", "psip", [("m.docx", methodology(2))])
    st = pipe.parse(st, date="24.09.2026")
    assert st.status == "parsed"
    assert st.spec.source_text.count(UNREAD_PICTURE) == 2
    note = [f for f in st.findings if f.rule_id == "spec.pictures_unread"]
    assert len(note) == 1 and "2" in note[0].message and note[0].severity == "warn"


def test_photo_methodology_without_tesseract_is_read_by_the_model(tmp_home, tmp_path, monkeypatch):
    import forlabs.ingest.extract as ex

    monkeypatch.setattr(ex, "find_tesseract", lambda configured=None: None)
    page = ("Лабораторная работа №7\nТема работы: Создание визуальных эффектов и анимации на HTML странице\n"
            "Создайте 2 простых анимации к выбранной тематике.\nСоздайте анимацию с эффектом параллакса.\n"
            "Hover-эффекты и их применение.")
    pipe, fake = setup(tmp_home, tmp_path, [{"pictures": [page]}, RAW])
    st = pipe.create_lab("ivanov", "psip", [("board.png", png(1200, 900))])
    st = pipe.parse(st, date="24.09.2026")
    assert st.status == "parsed" and st.spec.source_text.startswith("Лабораторная работа №7")
    assert "Картинка в методичке" not in st.spec.source_text  # страница целиком — обычный текст методички


def test_photo_methodology_that_could_not_be_read_says_why(tmp_home, tmp_path, monkeypatch):
    import forlabs.ingest.extract as ex

    monkeypatch.setattr(ex, "find_tesseract", lambda configured=None: None)
    pipe, fake = setup(tmp_home, tmp_path, [LLMError("network", "timeout")])
    st = pipe.create_lab("ivanov", "psip", [("board.png", png(1200, 900))])
    st = pipe.parse(st, date="24.09.2026")
    assert st.status == "failed" and "картинк" in st.error.lower() and ".." not in st.error
    assert len(fake.calls) == 1  # разбор без текста не запускается


def test_theory_illustrations_cost_nothing_at_parse(tmp_home, tmp_path):
    d = Document()
    d.add_paragraph("Лабораторная работа №7")
    d.add_paragraph("Тема работы: Создание визуальных эффектов и анимации на HTML странице")
    d.add_paragraph("Основные теоретические сведения")
    d.add_paragraph("Псевдокласс hover меняет вид элемента при наведении курсора. " * 30)
    d.add_picture(io.BytesIO(png(color=(10, 20, 30))), width=Inches(2))
    d.add_paragraph("Порядок выполнения работы")
    d.add_paragraph("Создайте 2 простых анимации к выбранной тематике.")
    d.add_paragraph("Создайте анимацию с эффектом параллакса.")
    d.add_paragraph("Hover-эффекты и их применение.")
    buf = io.BytesIO()
    d.save(buf)
    pipe, fake = setup(tmp_home, tmp_path, [RAW])  # запроса на картинки нет: разбор — единственный
    st = pipe.create_lab("ivanov", "psip", [("m.docx", buf.getvalue())])
    st = pipe.parse(st, date="24.09.2026")
    assert st.status == "parsed" and len(fake.calls) == 1
    assert "[[картинка" not in st.spec.source_text and "Картинка в методичке" not in st.spec.source_text
    assert not [f for f in st.findings if f.rule_id == "spec.pictures_unread"]
