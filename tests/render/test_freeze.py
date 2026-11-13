"""Готовый документ без чёрных квадратов: настройки «не отрывать от следующего», «не разрывать абзац» и
«с новой страницы» заменяются обычными разрывами страниц там, где они перенесли абзац, а сами снимаются."""

import re
import shutil
import zipfile

import pymupdf
import pytest
from lxml import etree

from forlabs.core.models import Listing, Paragraph, ReportDoc, TaskSection
from forlabs.render import freeze, word_com
from forlabs.render import finalize as fin
from forlabs.render.conformance import q
from forlabs.render.docx_builder import build_lab, build_semester


def _root(docx):
    with zipfile.ZipFile(docx) as z:
        return etree.fromstring(z.read("word/document.xml"))


def _items(docx):
    return freeze._body_items(_root(docx).find(q("body")))


def _text(p):
    return "".join(t.text or "" for t in p.iter(q("t")))


def _ends_with_break(p):
    runs = p.findall(q("r"))
    return bool(runs) and any(br.get(q("type")) == "page" for br in runs[-1].iter(q("br")))


def _mark(docx, index, name=f"{freeze.MARK_PREFIX}1"):
    """Как Word: закладка в начале абзаца, с которого начинается страница."""
    with zipfile.ZipFile(docx) as z:
        infos = [(i, z.read(i.filename)) for i in z.infolist()]
    root = etree.fromstring(dict((i.filename, d) for i, d in infos)["word/document.xml"])
    p = freeze._body_items(root.find(q("body")))[index]
    bs, be = etree.Element(q("bookmarkStart")), etree.Element(q("bookmarkEnd"))
    bs.set(q("id"), "9001"), bs.set(q("name"), name), be.set(q("id"), "9001")
    ppr = p.find(q("pPr"))
    pos = 1 if ppr is not None else 0
    p.insert(pos, be)
    p.insert(pos, bs)
    data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    tmp = docx.with_name("x.docx")
    with zipfile.ZipFile(tmp, "w") as out:
        for info, d in infos:
            out.writestr(info, data if info.filename == "word/document.xml" else d)
    shutil.move(tmp, docx)


def test_marked_paragraph_gets_a_page_break_and_all_flags_go(sample_doc, tmp_path):
    out = build_lab(sample_doc, tmp_path / "report.docx", author="x")
    assert freeze.flags_left(out) > 0  # при вёрстке настройки нужны
    items = _items(out)
    ref = next(i for i, el in enumerate(items) if "представлен в листинге" in _text(el))
    _mark(out, ref)
    assert freeze.apply(out) == 1
    items = _items(out)
    assert _ends_with_break(items[ref - 1]) and not _ends_with_break(items[ref])
    assert freeze.flags_left(out) == 0  # ни в абзацах, ни в стилях
    names = [b.get(q("name")) for b in _root(out).iter(q("bookmarkStart"))]
    assert not any(n.startswith(freeze.MARK_PREFIX) for n in names)
    assert any(n.startswith("_ForLabsL") for n in names)  # закладки листингов для «Продолжения» на месте


def test_unmarked_document_only_loses_the_flags(sample_doc, tmp_path):
    out = build_lab(sample_doc, tmp_path / "report.docx", author="x")
    before = [_text(el) for el in _items(out)]
    assert freeze.apply(out) == 0
    assert [_text(el) for el in _items(out)] == before and freeze.flags_left(out) == 0
    assert not any(_ends_with_break(el) for el in _items(out) if el.tag == q("p"))


def test_new_page_setting_becomes_a_break_and_breaks_are_not_doubled(sample_doc, profile_f, tmp_path):
    from forlabs.core.models import Enrollment

    enr = Enrollment(subject_id="go", title_subject="Программирование", teacher="Т.Т. Тестова",
                     academic_year="2026/2027", variant=1, header_style="long")
    out = build_semester([sample_doc, sample_doc], profile_f, enr, tmp_path / "sem.docx")
    items = _items(out)
    toc = next(i for i, el in enumerate(items) if _text(el).strip() == "Содержание")
    heads = [i for i, el in enumerate(items) if _text(el).startswith("Лабораторная работа №") and i > toc
             and _ends_with_break(items[i - 1])]  # заголовки лаб, а не строки содержания
    assert len(heads) == 2
    _mark(out, heads[1])  # Word отметил заголовок второй лабы — перед ним уже стоит разрыв между лабами
    breaks_before = sum(_ends_with_break(el) for el in items if el.tag == q("p"))
    freeze.apply(out)
    items = _items(out)
    assert _ends_with_break(items[toc - 1]) or items[toc].find(f".//{q('br')}") is not None
    assert sum(_ends_with_break(el) for el in items if el.tag == q("p")) == breaks_before + 1  # только «Содержание»
    assert freeze.flags_left(out) == 0


def test_paragraph_after_a_table_gets_the_break_at_its_start(sample_doc, tmp_path):
    out = build_lab(sample_doc, tmp_path / "report.docx", author="x")
    items = _items(out)
    after = next(i for i, el in enumerate(items) if i and items[i - 1].tag == q("tbl"))
    _mark(out, after)
    assert freeze.apply(out) == 1
    p = _items(out)[after]
    first_run = p.find(q("r"))
    assert any(br.get(q("type")) == "page" for br in first_run.iter(q("br")))


class _Fmt:
    def __init__(self, keep):
        self.KeepWithNext, self.KeepTogether, self.PageBreakBefore = keep, False, False


class _Range:
    def __init__(self, start, in_table=False):
        self.Start, self._table = start, in_table

    def Information(self, what):
        assert what == word_com.WD_WITHIN_TABLE
        return self._table


class _Par:
    def __init__(self, start, keep, in_table=False):
        self.Range, self.Format = _Range(start, in_table), _Fmt(keep)


class _Doc:
    """Страницы начинаются: 2 — с абзаца «не отрывать», 3 — посреди абзаца, 4 — с обычного абзаца, 5 — в таблице."""

    def __init__(self):
        self.starts = {2: (100, _Par(100, True)), 3: (250, _Par(200, True)), 4: (400, _Par(400, False)),
                       5: (500, _Par(500, True, in_table=True))}
        self.added = []
        self.Bookmarks = self

    def Repaginate(self):
        pass

    def ComputeStatistics(self, what):
        return 5

    def GoTo(self, what, which, count):
        start, par = self.starts[count]
        r = _Range(start)
        r.Paragraphs = lambda i: par
        return r

    def Range(self, a, b):
        return (a, b)

    def Add(self, name, rng):
        self.added.append((name, rng))


def test_word_marks_only_paragraphs_moved_by_the_settings():
    doc = _Doc()
    assert word_com.mark_page_starts(doc) == 1
    assert doc.added == [(f"{freeze.MARK_PREFIX}1", (100, 100))]


def _long_doc(n=14):
    """Листинги вперемешку с текстом разной длины: часть подписей листингов попадёт на конец страницы."""
    code = "\n".join(f"    Console.WriteLine(\"строка {k}\");" for k in range(18))
    sections = []
    for i in range(1, n + 1):
        filler = " ".join(["Текст пояснения к заданию, который занимает место на странице."] * (i % 7 + 1))
        sections.append(TaskSection(n=i, title=f"Задание номер {i}", blocks=[
            Paragraph(text=filler), Listing(topic=f"Исходный код программы {i}", code=code, language="csharp")]))
    return ReportDoc(lab_number="5", header={"Тема работы": "Проверка вёрстки"}, sections=sections,
                     conclusion="Вывод.")


def _caption_alone(pdf):
    """Подписи и ссылки на листинг, оставшиеся последней строкой страницы (код ушёл на следующую)."""
    bad = []
    with pymupdf.open(pdf) as doc:
        for n, page in enumerate(doc, start=1):
            lines = [ln.strip() for ln in page.get_text("text").splitlines() if ln.strip() and not ln.strip().isdigit()]
            if lines and re.match(r"^(Листинг \d+ –|.*представлен в листинге\s*\d+$|Задание \d+\.)", lines[-1]):
                bad.append((n, lines[-1]))
    return bad


@pytest.mark.skipif(not shutil.which("soffice"), reason="нужен LibreOffice")
def test_without_word_layout_is_kept_by_page_breaks(tmp_path, monkeypatch):
    monkeypatch.setattr(word_com, "available", lambda: False)
    out = build_lab(_long_doc(), tmp_path / "report.docx", author="x")
    ref_pdf = fin.libreoffice.to_pdf(out, tmp_path / "ref")  # как верстает LibreOffice с настройками
    starts = freeze.page_starts_from_pdf(out, ref_pdf)
    assert starts, "в образце нет ни одной подписи, перенесённой на новую страницу"
    with pymupdf.open(ref_pdf) as d:
        pages_before = d.page_count
    rep = fin.finalize(out, "x", tmp_path / "render", prefer_word=False)
    assert rep.engine == "libreoffice" and freeze.flags_left(out) == 0
    with pymupdf.open(rep.pdf) as d:
        assert d.page_count == pages_before  # разбивка та же
    assert _caption_alone(rep.pdf) == []


def _pdf_stub(path):
    with pymupdf.open() as d:
        d.new_page()
        d.save(path)


def test_without_word_and_libreoffice_the_flags_are_still_removed(sample_doc, tmp_path, monkeypatch):
    monkeypatch.setattr(word_com, "available", lambda: False)
    monkeypatch.setattr(fin.libreoffice, "find_soffice", lambda path=None: None)
    out = build_lab(sample_doc, tmp_path / "report.docx", author="x")
    assert freeze.flags_left(out) > 0
    rep = fin.finalize(out, "x", tmp_path / "render")
    assert rep.engine == "none" and freeze.flags_left(out) == 0


def test_libreoffice_failure_still_removes_the_flags(sample_doc, tmp_path, monkeypatch):
    monkeypatch.setattr(word_com, "available", lambda: False)
    monkeypatch.setattr(fin.libreoffice, "find_soffice", lambda path=None: "soffice")

    def boom(docx, workdir, soffice=None):
        raise RuntimeError("soffice упал")

    monkeypatch.setattr(fin.libreoffice, "to_pdf", boom)
    out = build_lab(sample_doc, tmp_path / "report.docx", author="x")
    rep = fin.finalize(out, "x", tmp_path / "render")
    assert rep.engine == "none" and freeze.flags_left(out) == 0


def test_word_that_could_not_mark_pages_still_leaves_no_flags(sample_doc, tmp_path, monkeypatch):
    def fake_isolated(docx, pdf_out, author, timeout=300.0):
        _pdf_stub(pdf_out)
        return word_com.WordReport(0, [])  # marks = -1: разметка страниц не удалась

    monkeypatch.setattr(word_com, "available", lambda: True)
    monkeypatch.setattr(word_com, "finalize_isolated", fake_isolated)
    out = build_lab(sample_doc, tmp_path / "report.docx", author="x")
    rep = fin.finalize(out, "x", tmp_path / "render")
    assert rep.engine == "word" and freeze.flags_left(out) == 0


def test_clean_copy_has_no_flags_and_the_original_is_untouched(sample_doc, tmp_path):
    out = build_lab(sample_doc, tmp_path / "report.docx", author="x")
    before = out.read_bytes()
    dst = freeze.clean_copy(out, tmp_path / "copy.docx")
    assert freeze.flags_left(dst) == 0 and out.read_bytes() == before
