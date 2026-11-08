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


def test_normalize_joins_hyphenation_and_drops_page_numbers():
    s = normalize("Эти програм-\nмы работают\n\n12\n\nДалее текст")
    assert "программы работают" in s
    assert "\n12\n" not in s


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
