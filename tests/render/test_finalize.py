import re
import shutil
import sys
import zipfile

import pytest

from forlabs.render import word_com
from forlabs.render.docx_builder import build_lab, build_semester
from forlabs.render.finalize import finalize, patch_toc_pages, toc_bookmarks
from forlabs.render.preview import heading_pages

needs_lo = pytest.mark.skipif(shutil.which("soffice") is None, reason="нет LibreOffice")


def test_word_com_skipped_off_windows():
    if sys.platform != "win32":
        assert word_com.available() is False


def test_listing_bookmarks_present(sample_doc, tmp_path):
    out = build_lab(sample_doc, tmp_path / "lab.docx", author="x")
    xml = zipfile.ZipFile(out).read("word/document.xml").decode()
    assert re.search(r'w:name="_ForLabsL8_1"', xml)


def test_patch_toc_pages(sample_doc, profile_f, tmp_path):
    docs = [sample_doc, sample_doc.model_copy(update={"lab_number": "9"})]
    out = build_semester(docs, profile_f, profile_f.enrollment("ssp"), tmp_path / "sem.docx")
    marks = toc_bookmarks(out)
    assert list(marks.values()) == ["Лабораторная работа №8", "Лабораторная работа №9"]
    patch_toc_pages(out, {name: i + 3 for i, name in enumerate(marks)})
    xml = zipfile.ZipFile(out).read("word/document.xml").decode()
    nums = re.findall(r"PAGEREF (_Toc\d+) \\h .*?<w:t>(\d+)</w:t>", xml)
    assert [n for _, n in nums] == ["3", "4"]


@needs_lo
def test_finalize_libreoffice_semester(sample_doc, profile_f, tmp_path, monkeypatch):
    monkeypatch.setattr(word_com, "available", lambda: False)
    docs = [sample_doc, sample_doc.model_copy(update={"lab_number": "9"})]
    out = build_semester(docs, profile_f, profile_f.enrollment("ssp"), tmp_path / "sem.docx")
    rep = finalize(out, author="Иванова Анна", workdir=tmp_path / "fin")
    assert rep.engine == "libreoffice"
    assert rep.pdf.exists() and len(rep.pages) >= 4 and all(p.exists() for p in rep.pages)
    assert any("Word" in w for w in rep.warnings)
    pages = heading_pages(rep.pdf, ["Лабораторная работа №8", "Лабораторная работа №9"])
    xml = zipfile.ZipFile(out).read("word/document.xml").decode()
    nums = [int(n) for _, n in re.findall(r"PAGEREF (_Toc\d+) \\h .*?<w:t>(\d+)</w:t>", xml)]
    assert nums == [pages["Лабораторная работа №8"], pages["Лабораторная работа №9"]]
    assert nums[0] == 3


@needs_lo
def test_word_failure_falls_back_to_libreoffice(sample_doc, tmp_path, monkeypatch):
    monkeypatch.setattr(word_com, "available", lambda: True)

    def boom(*a, **k):
        raise TimeoutError("Word не ответил")

    monkeypatch.setattr(word_com, "finalize_isolated", boom)
    out = build_lab(sample_doc, tmp_path / "lab.docx", author="x")
    rep = finalize(out, author="x", workdir=tmp_path / "fin")
    assert rep.engine == "libreoffice" and rep.pdf.exists()
    assert any("Word не ответил" in w for w in rep.warnings)
