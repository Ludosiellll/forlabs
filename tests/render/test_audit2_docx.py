"""Аудит-2, документ: титул семестра, без Word и LibreOffice, свойства, таблицы, переносы, схемы, мелочи."""

import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from forlabs.core.models import Listing, Paragraph, ReportDoc, Table, TaskSection
from forlabs.render import finalize as fin
from forlabs.render import word_com
from forlabs.render.docx_builder import (
    build_lab,
    build_semester,
    column_widths,
    image_size_cm,
)

ASSETS = Path(__file__).parents[2] / "forlabs" / "render" / "assets"


def xml(docx: Path, part: str = "word/document.xml") -> str:
    return zipfile.ZipFile(docx).read(part).decode("utf-8")


# G1: длинное название предмета не выталкивает год на страницу с содержанием
def test_semester_title_and_toc_page_break(sample_doc, profile_f, tmp_path):
    assert "     Учебный предмет" not in (ASSETS / "title.xml").read_text(encoding="utf-8")
    out = build_semester([sample_doc], profile_f, profile_f.enrollments[0], tmp_path / "s.docx")
    body = xml(out)
    toc = body[:body.index(">Содержание<")]
    assert "pageBreakBefore" in toc[toc.rfind("<w:p>") if "<w:p>" in toc else toc.rfind("<w:p "):]


# G2: нет ни Word, ни LibreOffice — документ готов, без предпросмотра, с предупреждением
def test_finalize_without_any_office(sample_doc, tmp_path, monkeypatch):
    out = build_lab(sample_doc, tmp_path / "report.docx", author="x")
    monkeypatch.setattr(word_com, "available", lambda: False)
    monkeypatch.setattr(fin.libreoffice, "find_soffice", lambda configured=None: None)
    rep = fin.finalize(out, "x", tmp_path / "render")
    assert rep.engine == "none" and rep.pdf is None and rep.pages == []
    assert any("LibreOffice" in w for w in rep.warnings) and out.exists()


# H2: Word один раз не справился — до перезапуска не ждём его снова по 5 минут
def test_word_failure_remembered(sample_doc, tmp_path, monkeypatch):
    out = build_lab(sample_doc, tmp_path / "report.docx", author="x")
    calls = []

    def boom(docx, pdf_out, author, timeout=300.0):
        calls.append(1)
        raise TimeoutError("Word не ответил")

    monkeypatch.setattr(word_com, "_failed", False, raising=False)
    monkeypatch.setattr(word_com, "_installed", lambda: True, raising=False)
    monkeypatch.setattr(word_com, "finalize_isolated", boom)
    monkeypatch.setattr(fin.libreoffice, "find_soffice", lambda configured=None: None)
    fin.finalize(out, "x", tmp_path / "r1")
    fin.finalize(out, "x", tmp_path / "r2")
    assert calls == [1]


# G4: после вставки «Продолжения» в листинг k проверка идёт с него же, а не с первого листинга
def test_listing_continuations_do_not_restart(monkeypatch):
    seen, fixes = [], {"b": 1}

    class Doc:
        def Repaginate(self):
            pass

    def fix(doc, name):
        seen.append(name)
        if fixes.get(name):
            fixes[name] -= 1
            return True
        return False

    monkeypatch.setattr(word_com, "listing_bookmarks", lambda doc: ["a", "b", "c"])
    monkeypatch.setattr(word_com, "_fix_listing_once", fix)
    assert word_com.insert_listing_continuations(Doc()) == 1
    assert seen == ["a", "b", "b", "c"]


# G5: время в свойствах — настоящее UTC; статистика и идентификаторы эталонного файла не переносятся
def test_core_times_are_utc_and_template_leftovers_removed(sample_doc, tmp_path):
    out = build_lab(sample_doc, tmp_path / "report.docx", author="x")
    core = xml(out, "docProps/core.xml")
    created = re.search(r"<dcterms:created[^>]*>([^<]+)<", core).group(1)
    dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - dt).total_seconds()) < 300
    app = xml(out, "docProps/app.xml")
    assert "<Pages>" not in app and "<Words>" not in app and "<Characters>" not in app
    settings = xml(out, "word/settings.xml")
    assert "docId" not in settings and "rsidRoot" not in settings


# G6: столбец не уже самого длинного слова
def test_column_fits_longest_word():
    widths = column_widths([["Параметр", "x " * 80], ["Переменная", "y"]], 2)
    assert widths[0] >= int((len("Переменная") * 0.23 + 0.3) * 567)


# G8, E7: строка кода не рвётся между страницами; заголовок задания и вопрос — с продолжением
def test_keep_lines_and_keep_next(sample_doc, tmp_path):
    out = build_lab(sample_doc, tmp_path / "report.docx", author="x")
    body = xml(out)
    paras = re.findall(r"<w:p[ >].*?</w:p>", body, re.S)
    code = [p for p in paras if "fmt.Scan" in p]
    assert code and all("keepLines" in p for p in code)
    heads = [p for p in paras if "Задание 1. " in p]
    assert heads and all("keepNext" in p for p in heads)


# G10: схема, снятая с масштабом 2, не растягивается вдвое
def test_image_size_honours_dpi(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    Image.new("RGB", (400, 200)).save(a)
    Image.new("RGB", (400, 200)).save(b, dpi=(192, 192))
    assert image_size_cm(str(b))[0] == pytest.approx(image_size_cm(str(a))[0] / 2, rel=0.02)


# G11: рамка в колонтитуле не делит номер рисунка с картинками документа
def test_header_drawing_id_is_high():
    with zipfile.ZipFile(ASSETS / "base.docx") as z:
        ids = [int(x) for name in z.namelist() if name.startswith("word/header")
               for x in re.findall(r'<wp:docPr id="(\d+)"', z.read(name).decode("utf-8"))]
    assert ids and min(ids) >= 1000


# G14: пустая таблица, переводы строк, имя файла в начале подписи
def test_small_robustness(tmp_path):
    from forlabs.report.derive import upper_first

    doc = ReportDoc(lab_number="1", header={"Тема": "т"}, conclusion="Вывод.", sections=[TaskSection(
        n=1, title="t", blocks=[Paragraph(text="Первая строка\nвторая строка"), Table(topic="пусто", rows=[]),
                                Listing(topic="код", code="x = 1")])])
    out = build_lab(doc, tmp_path / "r.docx", author="x")
    assert "<w:br/>" in xml(out)
    assert upper_first("index.html со стилями") == "index.html со стилями"
    assert upper_first("главная страница") == "Главная страница"
