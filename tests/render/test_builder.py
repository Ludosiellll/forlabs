import shutil
import zipfile
from pathlib import Path

import pytest
from docx import Document

from forlabs.render.conformance import DocxModel, compare, iter_paragraphs, q
from forlabs.render.docx_builder import build_lab, build_semester

ASSETS = Path(__file__).resolve().parents[2] / "forlabs" / "render" / "assets"
REAL_NAMES = ["Лабынцев", "Корчагина", "Подгорная", "Довнар", "Шилова", "Манжура"]


def test_assets_have_no_personal_data():
    for f in ASSETS.iterdir():
        if f.suffix == ".docx":
            with zipfile.ZipFile(f) as z:
                blob = b"".join(z.read(n) for n in z.namelist())
            text = blob.decode("utf-8", errors="ignore")
        else:
            text = f.read_text(encoding="utf-8", errors="ignore")
        for name in REAL_NAMES:
            assert name not in text, f"{name} в {f.name}"


def roles_of(path):
    return [(role, "".join(t.text or "" for t in p.iter(q("t")))) for role, _, p in iter_paragraphs(DocxModel(path))]


def test_lab_docx_conforms(sample_doc, tmp_path):
    out = build_lab(sample_doc, tmp_path / "lab.docx", author="Иванова Анна")
    assert compare(out) == []
    roles = [r for r, _ in roles_of(out)]
    for needed in ["lab_heading", "header_field", "results_title", "task_heading", "body", "dash_item", "listing_ref",
                   "listing_caption", "code", "code_blank", "figure_ref", "image", "figure_caption", "numbered_item",
                   "table_ref", "table_caption", "table_cell", "cq_title", "cq_question", "cq_answer", "conclusion"]:
        assert needed in roles, needed


def test_texts_are_derived(sample_doc, tmp_path):
    out = build_lab(sample_doc, tmp_path / "lab.docx", author="Иванова Анна")
    texts = [t for _, t in roles_of(out)]
    assert "Лабораторная работа №8" in texts
    assert "Задание 1. По данному натуральному n ≤ 30 выведите лесенку из n ступенек." in texts
    assert "Исходный код программы вывода лесенки представлен в листинге 1" in texts
    assert "Листинг 1 – Исходный код программы вывода лесенки" in texts
    assert "Рисунок 1 – Результат выполнения программы первого задания в консоли" in texts
    assert "В таблице 1 представлены параметры функций программы" in texts
    assert "Таблица 1 – Параметры функций программы" in texts
    assert "- вывод лесенки через двумерный срез stairs;" in texts
    assert "Вывод: Сформировал умения создавать простейшие программы работы со ссылочными типами данных." in texts
    assert "1. Что называется функцией?" in texts
    assert "Ответ: Именованный блок кода." in texts
    assert any(t.startswith("[Вставить скриншот:") for t in texts)


def test_listing_frame_continuous(sample_doc, tmp_path):
    out = build_lab(sample_doc, tmp_path / "lab.docx", author="x")
    model = DocxModel(out)
    borders = [key[-1] for role, key, _ in iter_paragraphs(model) if role in ("code", "code_blank")]
    assert len(borders) == 9
    assert len(set(borders)) == 1 and borders[0].startswith("top:single/4/1")


def test_tabs_become_spaces(sample_doc, tmp_path):
    out = build_lab(sample_doc, tmp_path / "lab.docx", author="x")
    texts = [t for r, t in roles_of(out) if r == "code"]
    assert "    var n int" in texts


def test_image_width_limited(sample_doc, tmp_path):
    out = build_lab(sample_doc, tmp_path / "lab.docx", author="x")
    d = Document(out)
    widths = [s.width.cm for s in d.inline_shapes]
    assert widths and max(widths) <= 16.5 + 1e-6


def test_core_properties(sample_doc, tmp_path):
    out = build_lab(sample_doc, tmp_path / "lab.docx", author="Иванова Анна")
    cp = Document(out).core_properties
    assert cp.author == "Иванова Анна" and cp.last_modified_by == "Иванова Анна"
    with zipfile.ZipFile(out) as z:
        app = z.read("docProps/app.xml").decode()
    assert "python-docx" not in app and "Microsoft Office Word" in app


def test_semester_docx_conforms_and_title(sample_doc, profile_f, tmp_path):
    doc2 = sample_doc.model_copy(update={"lab_number": "9"})
    out = build_semester([sample_doc, doc2], profile_f, profile_f.enrollment("ssp"), tmp_path / "sem.docx")
    assert compare(out) == []
    texts = [t for _, t in roles_of(out)]
    joined = "\n".join(texts)
    assert "Выполнила уч-ся гр.РС02-23" in joined and "А.П. Иванова" in joined
    assert "Б.Б. Сидорова" in joined and "2026/2027" in joined
    teacher_line = next(t for t in texts if "Руководитель" in t)
    assert teacher_line.rstrip().endswith("   Б.Б. Сидорова") and "А.С" not in teacher_line and "А.C" not in teacher_line
    name_line = next(t for t in texts if "уч-ся" in t)
    assert name_line.rstrip().endswith("   А.П. Иванова")
    assert "Учебный предмет: Современные системы программирования" in joined
    assert "{{" not in joined
    assert "Содержание" in texts
    assert texts.count("Лабораторная работа №8") >= 1 and "Лабораторная работа №9" in texts
    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml").decode()
    assert 'TOC \\o "1-3" \\h \\z \\u' in xml
    assert xml.count("PAGEREF _Toc") == 2


@pytest.mark.skipif(shutil.which("soffice") is None, reason="нет LibreOffice")
def test_libreoffice_renders_pdf(sample_doc, tmp_path):
    from forlabs.render.libreoffice import to_pdf
    out = build_lab(sample_doc, tmp_path / "lab.docx", author="x")
    pdf = to_pdf(out, tmp_path)
    assert pdf.exists() and pdf.stat().st_size > 1000
