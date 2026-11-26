"""Вёрстка «без разрывов»: студенту нужна цельная таблица или листинг (перенести в свой файл) — документ
собирается без закреплённых разрывов страниц и без «Продолжение таблицы». По умолчанию всё по образцу."""

import shutil
import zipfile

import pytest

from forlabs.core.models import Paragraph, ReportDoc, Table, TaskSection
from forlabs.render import word_com
from forlabs.render.docx_builder import build_lab
from forlabs.render.finalize import finalize


def _long(tmp_path):
    rows = [["Понятие", "Описание"]] + [[f"Стрелка {i}", "Стрелка, уходящая за границу поля диаграммы. " * 3]
                                        for i in range(60)]
    doc = ReportDoc(lab_number="4", header={"Тема работы": "Функциональное моделирование"}, conclusion="Изучил.",
                    sections=[TaskSection(n=1, title="Опишите элементы диаграммы.", blocks=[
                        Paragraph(text="Элементы диаграммы сведены в таблицу."),
                        Table(topic="элементы диаграммы", rows=rows)])])
    return build_lab(doc, tmp_path / "lab.docx", author="Иванова Анна")


def _page_breaks(docx) -> int:
    with zipfile.ZipFile(docx) as z:
        return z.read("word/document.xml").count(b'w:type="page"')


@pytest.mark.skipif(shutil.which("soffice") is None, reason="нет LibreOffice")
def test_plain_layout_has_no_page_breaks(tmp_path):
    usual = _long(tmp_path / "a")
    finalize(usual, "Иванова Анна", tmp_path / "wa", prefer_word=False)
    plain = _long(tmp_path / "b")
    rep = finalize(plain, "Иванова Анна", tmp_path / "wb", prefer_word=False, plain=True)
    assert _page_breaks(plain) == 0 and rep.pdf is not None and len(rep.pages) >= 2  # таблица идёт сплошь
    from forlabs.render import freeze

    assert freeze.flags_left(plain) == 0  # чёрных квадратов нет и в этом виде


def test_word_is_told_to_skip_continuations(sample_doc, tmp_path, monkeypatch):
    """С Word: в виде «без разрывов» доводка не режет таблицы и листинги и не отмечает начала страниц."""
    seen = {}

    def fake_isolated(docx, pdf_out, author, timeout=300.0, target=None, plain=False):
        seen["plain"] = plain
        shutil.copyfile(docx, pdf_out)
        return word_com.WordReport()

    monkeypatch.setattr(word_com, "available", lambda: True)
    monkeypatch.setattr(word_com, "finalize_isolated", fake_isolated)
    monkeypatch.setattr("forlabs.render.finalize.pdf_to_pngs", lambda pdf, out: [])
    docx = build_lab(sample_doc, tmp_path / "lab.docx", author="Иванова Анна")
    finalize(docx, "Иванова Анна", tmp_path / "w", prefer_word=True, plain=True)
    assert seen["plain"] is True
    finalize(docx, "Иванова Анна", tmp_path / "w2", prefer_word=True)
    assert seen["plain"] is False


def test_word_steps_skipped_in_plain_layout(monkeypatch, tmp_path):
    """Сама доводка в Word: «Продолжение таблицы» и отметки страниц — только в обычном виде."""
    import sys
    import types

    calls = []

    class Doc:
        TablesOfContents = ()

        def Repaginate(self): pass
        def BuiltInDocumentProperties(self, name): return types.SimpleNamespace(Value="")
        def Save(self): pass
        def ExportAsFixedFormat(self, *a): pass
        def Close(self, *a): pass

    class Word:
        Visible = False
        DisplayAlerts = 0
        Documents = types.SimpleNamespace(Open=lambda *a: Doc())

        def Quit(self): pass

    client = types.ModuleType("win32com.client")
    client.DispatchEx = lambda name: Word()
    monkeypatch.setitem(sys.modules, "pythoncom", types.SimpleNamespace(CoInitialize=lambda: None,
                                                                         CoUninitialize=lambda: None))
    monkeypatch.setitem(sys.modules, "win32com", types.SimpleNamespace(client=client))
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    monkeypatch.setattr(word_com, "place_continuations", lambda doc: calls.append("continuations") or 2)
    monkeypatch.setattr(word_com, "mark_page_starts", lambda doc: calls.append("marks") or 3)
    rep = word_com.finalize(tmp_path / "a.docx", tmp_path / "a.pdf", "Автор", plain=True)
    assert calls == [] and rep.continuations == 0 and rep.marks == 0
    rep = word_com.finalize(tmp_path / "a.docx", tmp_path / "a.pdf", "Автор")
    assert calls == ["continuations", "marks"] and (rep.continuations, rep.marks) == (2, 3)
