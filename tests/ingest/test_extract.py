import shutil

import pymupdf
import pytest
from docx import Document
from PIL import Image

from forlabs.ingest.extract import extract_text, normalize


def _cyrillic_font():
    from pathlib import Path
    for c in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
              r"C:\Windows\Fonts\arial.ttf"]:
        if Path(c).exists():
            return c
    return None


TEXT = ("Лабораторная работа №7\nТема работы: Создание визуальных эффектов\nЗадание:\n"
        "1. Создайте 2 простых анимации к выбранной тематике.\n2. Создайте анимированное меню. Эти програм-\nмы проверяются.")


def test_normalize_joins_hyphenation():
    s = normalize("Эти програм-\nмы работают")
    assert "программы работают" in s


@pytest.mark.parametrize("src,expected", [
    ("Создать веб-\nстраницу сайта", "Создать веб-страницу сайта"),
    ("клиент-\nсерверная архитектура", "клиент-серверная архитектура"),
    ("адрес e-\nmail в форме", "адрес e-mail в форме"),
    ("для конфигу-\nрации сети", "для конфигурации сети"),
])
def test_hyphenation_keeps_real_hyphens(src, expected):
    assert normalize(src) == expected


def test_normalize_keeps_numeric_lines():
    # в DOCX и TXT нет страниц: строки-числа — это данные (ячейки таблиц вариантов)
    s = normalize("Вариант\n1\n2\n3\nЗадание")
    assert s == "Вариант\n1\n2\n3\nЗадание"


def _pdf_pages(tmp_path, pages):
    font = _cyrillic_font()
    if font is None:
        pytest.skip("нет шрифта с кириллицей")
    pdf = tmp_path / "pages.pdf"
    doc = pymupdf.open()
    for top, body, bottom in pages:
        page = doc.new_page()
        page.insert_font(fontname="F0", fontfile=font)
        if top:
            page.insert_text((50, 40), top, fontname="F0", fontsize=9)
        page.insert_text((50, 90), body, fontname="F0", fontsize=11)
        if bottom:
            page.insert_text((290, 800), bottom, fontname="F0", fontsize=11)
    doc.save(pdf)
    return pdf


def test_pdf_drops_page_numbers_and_running_header_but_keeps_table_numbers(tmp_path):
    head = "МДК 01.01 Системное программирование"
    pdf = _pdf_pages(tmp_path, [
        (head, "Таблица вариантов\nВариант\n1\n2\n3\nКонец таблицы", "5"),
        (head, "Задание\n4\nПятый пункт", "6"),
        (head, "Контрольные вопросы", "7"),
    ])
    lines = extract_text(pdf).text.split("\n")
    assert head not in lines
    assert "5" not in lines and "6" not in lines and "7" not in lines
    assert lines[lines.index("Вариант") + 1:lines.index("Вариант") + 4] == ["1", "2", "3"]
    assert "4" in lines


def test_pdf_roundtrip(tmp_path):
    pdf = tmp_path / "m.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    font = _cyrillic_font()
    if font is None:
        pytest.skip("нет шрифта с кириллицей")
    page.insert_font(fontname="F0", fontfile=font)
    page.insert_text((50, 72), TEXT, fontname="F0", fontsize=11)
    doc.save(pdf)
    src = extract_text(pdf)
    assert src.pages == 1
    assert "Создайте анимированное меню" in src.text
    assert "программы проверяются" in src.text


def test_docx_with_table(tmp_path):
    path = tmp_path / "m.docx"
    d = Document()
    for line in TEXT.split("\n"):
        d.add_paragraph(line)
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Вариант", "Задание"
    t.cell(1, 0).text, t.cell(1, 1).text = "14", "Найти произведение чисел от a до b"
    d.save(path)
    src = extract_text(path)
    assert "Создайте 2 простых анимации" in src.text
    assert "14 | Найти произведение чисел от a до b" in src.text


def test_txt(tmp_path):
    p = tmp_path / "m.txt"
    p.write_text(TEXT, encoding="utf-8")
    assert "Тема работы" in extract_text(p).text


@pytest.mark.skipif(shutil.which("tesseract") is not None, reason="Tesseract установлен")
def test_image_without_tesseract_warns(tmp_path):
    p = tmp_path / "scan.png"
    Image.new("RGB", (100, 100), "white").save(p)
    src = extract_text(p)
    assert src.text == "" and any("Tesseract" in w for w in src.warnings)


def test_unknown_format(tmp_path):
    p = tmp_path / "m.xyz"
    p.write_text("x")
    with pytest.raises(ValueError):
        extract_text(p)
