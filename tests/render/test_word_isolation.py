"""Word зависает или падает: исходный report.docx не блокируется, зависший WINWORD завершается."""

import pymupdf
import pytest

from forlabs.render import finalize as fin
from forlabs.render import word_com
from forlabs.render.docx_builder import build_lab


def _pdf(path):
    doc = pymupdf.open()
    doc.new_page()
    doc.save(path)


def test_word_works_on_a_copy_and_result_replaces_original(sample_doc, tmp_path, monkeypatch):
    out = build_lab(sample_doc, tmp_path / "report.docx", author="x")
    seen = []

    def fake_isolated(docx, pdf_out, author, timeout=300.0):
        seen.append(docx)
        docx.write_bytes(docx.read_bytes() + b"")  # Word «сохранил» документ
        _pdf(pdf_out)
        return word_com.WordReport(2, [])

    monkeypatch.setattr(word_com, "available", lambda: True)
    monkeypatch.setattr(word_com, "finalize_isolated", fake_isolated)
    rep = fin.finalize(out, "x", tmp_path / "render")
    assert rep.engine == "word" and seen and seen[0] != out  # Word получил копию
    assert out.exists() and not list((tmp_path / "render").glob("word_*.docx"))  # копия ушла на место


def test_word_failure_leaves_original_untouched(sample_doc, tmp_path, monkeypatch):
    out = build_lab(sample_doc, tmp_path / "report.docx", author="x")
    before = out.read_bytes()

    def boom(docx, pdf_out, author, timeout=300.0):
        docx.write_bytes(b"half-written by Word")
        raise TimeoutError("Word не ответил")

    monkeypatch.setattr(word_com, "available", lambda: True)
    monkeypatch.setattr(word_com, "finalize_isolated", boom)
    monkeypatch.setattr(fin.libreoffice, "to_pdf", lambda docx, workdir, soffice=None: (_pdf(workdir / "report.pdf"),
                                                                                        workdir / "report.pdf")[1])
    rep = fin.finalize(out, "x", tmp_path / "render")
    assert rep.engine == "libreoffice" and out.read_bytes() == before


def test_hanging_word_is_killed_by_pid(tmp_path, monkeypatch):
    import fake_word_child

    killed = []
    monkeypatch.setattr(word_com, "_kill_pids", lambda pids: killed.extend(pids))
    with pytest.raises(TimeoutError):
        word_com.finalize_isolated(tmp_path / "a.docx", tmp_path / "a.pdf", "x", timeout=3,
                                   target=fake_word_child.hanging_child)
    assert killed == [424242]
