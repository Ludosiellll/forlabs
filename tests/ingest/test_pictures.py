"""Картинки внутри методички: место картинки помечается в тексте, сами картинки отдаются отдельно — их читает модель."""

import io

import pymupdf
import pytest
from docx import Document
from docx.shared import Inches
from PIL import Image
from test_extract import _cyrillic_font

from forlabs.ingest.extract import PICTURE_RE, SourceText, extract_many, extract_text, picture_mark


def png(w=400, h=200, color=(240, 240, 240)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def docx_with_pictures(path, sizes=((400, 200),)):
    d = Document()
    d.add_paragraph("Задание 1. Выполните запросы, представленные на рисунках.")
    for i, (w, h) in enumerate(sizes):
        d.add_picture(io.BytesIO(png(w, h, (200 - i, 200, 200))), width=Inches(2))
    d.add_paragraph("Контрольные вопросы")
    d.save(str(path))
    return path


def test_docx_picture_is_marked_in_place(tmp_path):
    src = extract_text(docx_with_pictures(tmp_path / "m.docx"), pictures=True)
    lines = src.text.split("\n")
    assert lines.index(picture_mark(1)) == lines.index("Задание 1. Выполните запросы, представленные на рисунках.") + 1
    assert lines.index("Контрольные вопросы") > lines.index(picture_mark(1))
    assert len(src.pictures) == 1 and not src.pictures[0].page
    with Image.open(io.BytesIO(src.pictures[0].data)) as im:
        assert im.size == (400, 200)


def test_pictures_are_off_unless_asked(tmp_path):
    # связанные отчёты, материалы и поиск номера читают тот же файл: им метки не нужны
    src = extract_text(docx_with_pictures(tmp_path / "m.docx"))
    assert not PICTURE_RE.search(src.text) and src.pictures == []


def test_docx_small_icons_are_skipped(tmp_path):
    src = extract_text(docx_with_pictures(tmp_path / "m.docx", sizes=((24, 24), (400, 200), (90, 12))), pictures=True)
    assert len(src.pictures) == 1
    assert PICTURE_RE.findall(src.text) == ["1"]


def test_docx_picture_inside_table_cell(tmp_path):
    d = Document()
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "Схема базы"
    t.cell(0, 1).paragraphs[0].add_run().add_picture(io.BytesIO(png()), width=Inches(2))
    d.save(str(tmp_path / "t.docx"))
    src = extract_text(tmp_path / "t.docx", pictures=True)
    assert f"Схема базы | {picture_mark(1)}" in src.text
    assert len(src.pictures) == 1


def test_several_files_number_pictures_through(tmp_path):
    a = docx_with_pictures(tmp_path / "a.docx", sizes=((400, 200), (300, 300)))
    b = docx_with_pictures(tmp_path / "b.docx")
    src = extract_many([a, b], pictures=True)
    assert PICTURE_RE.findall(src.text) == ["1", "2", "3"]
    assert len(src.pictures) == 3
    with Image.open(io.BytesIO(src.pictures[2].data)) as im:
        assert im.size == (400, 200)


def _pdf(tmp_path, pages):
    """pages: список страниц; страница — список ("text", строка) и ("img", (w, h)) сверху вниз."""
    font = _cyrillic_font()
    if font is None:
        pytest.skip("нет шрифта с кириллицей")
    doc = pymupdf.open()
    for items in pages:
        page = doc.new_page()
        y = 60
        for kind, val in items:
            if kind == "text":
                page.insert_text((56, y), val, fontname="F0", fontfile=font, fontsize=12)
                y += 30
            else:
                w, h = val
                page.insert_image(pymupdf.Rect(56, y, 56 + w, y + h), stream=png(w * 2, h * 2, (200, 220, 240)))
                y += h + 30
    out = tmp_path / "m.pdf"
    doc.save(str(out))
    return out


def test_pdf_picture_is_marked_between_its_neighbours(tmp_path):
    pdf = _pdf(tmp_path, [[("text", "Задание 1. Повторите форму с рисунка."), ("img", (300, 150)),
                           ("text", "Задание 2. Добавьте кнопку.")]])
    src = extract_text(pdf, pictures=True)
    lines = [ln for ln in src.text.split("\n") if ln]
    assert lines == ["Задание 1. Повторите форму с рисунка.", picture_mark(1), "Задание 2. Добавьте кнопку."]
    assert len(src.pictures) == 1 and not src.pictures[0].page
    with Image.open(io.BytesIO(src.pictures[0].data)) as im:
        assert im.width >= 300  # вырезана со страницы без потери чёткости


def test_pdf_page_without_text_becomes_a_page_picture(tmp_path):
    pdf = _pdf(tmp_path, [[("text", "Лабораторная работа №3. Тема: формы и отчёты в базе данных.")],
                          [("img", (480, 700))]])
    src = extract_text(pdf, pictures=True)
    assert PICTURE_RE.findall(src.text) == ["1"]
    assert src.pictures[0].page
    assert not src.warnings  # страницу прочитает модель: предупреждать «N стр. без текста» рано


def test_pdf_without_pictures_mode_still_warns_about_scans(tmp_path):
    pdf = _pdf(tmp_path, [[("text", "Лабораторная работа №3. Тема: формы и отчёты в базе данных.")],
                          [("img", (480, 700))]])
    src = extract_text(pdf)
    assert src.pictures == [] and any("без текста" in w for w in src.warnings)


def test_pdf_logo_on_every_page_is_not_a_picture(tmp_path):
    font = _cyrillic_font()
    if font is None:
        pytest.skip("нет шрифта с кириллицей")
    doc = pymupdf.open()
    logo = png(300, 120, (10, 60, 120))
    for i in range(4):
        page = doc.new_page()
        page.insert_image(pymupdf.Rect(56, 40, 206, 100), stream=logo)
        page.insert_text((56, 140), f"Страница {i + 1}: текст задания про базу данных и её таблицы.", fontname="F0",
                         fontfile=font, fontsize=12)
    doc.save(str(tmp_path / "logo.pdf"))
    src = extract_text(tmp_path / "logo.pdf", pictures=True)
    assert src.pictures == [] and not PICTURE_RE.search(src.text)


def test_pdf_page_number_under_a_picture_is_still_removed(tmp_path):
    font = _cyrillic_font()
    if font is None:
        pytest.skip("нет шрифта с кириллицей")
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((56, 60), f"Текст страницы {i + 1} про запросы к базе данных.", fontname="F0", fontfile=font,
                         fontsize=12)
        page.insert_image(pymupdf.Rect(56, 100, 356, 300), stream=png(600, 400, (200, 200 + i, 240)))
        page.insert_text((290, 800), str(i + 1), fontname="F0", fontfile=font, fontsize=10)
    doc.save(str(tmp_path / "n.pdf"))
    src = extract_text(tmp_path / "n.pdf", pictures=True)
    lines = [ln for ln in src.text.split("\n") if ln]
    assert "1" not in lines and "2" not in lines and "3" not in lines
    assert len(src.pictures) == 3


def test_photo_without_tesseract_goes_to_the_model(tmp_path, monkeypatch):
    import forlabs.ingest.extract as ex

    monkeypatch.setattr(ex, "find_tesseract", lambda configured=None: None)
    f = tmp_path / "board.png"
    f.write_bytes(png(1200, 900))
    src = extract_text(f, pictures=True)
    assert src.text == picture_mark(1) and src.pictures[0].page and not src.warnings
    assert isinstance(src, SourceText)
    # без режима картинок — как раньше: подсказка про Tesseract
    assert extract_text(f).warnings
