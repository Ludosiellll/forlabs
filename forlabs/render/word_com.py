"""Доводка через Microsoft Word (только Windows): содержание, «Продолжение листинга/таблицы N»,
свойства документа, PDF для предпросмотра. Работает с реальной разбивкой Word на страницы."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

WD_EXPORT_PDF = 17
WD_ACTIVE_END_PAGE = 3
MAX_PASSES = 200


@dataclass
class WordReport:
    continuations: int = 0
    warnings: list[str] = field(default_factory=list)


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import win32com.client  # noqa: F401
        import winreg

        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID")
        return True
    except Exception:
        return False


def _page(rng) -> int:
    return int(rng.Information(WD_ACTIVE_END_PAGE))


def _start_page(paragraph) -> int:
    return _page(paragraph.Range.Characters(1))


def _style_continuation(par, text: str) -> None:
    rng = par.Range
    rng.Text = text
    fmt = par.Format
    par.Borders.Enable = False
    fmt.LeftIndent = 0
    fmt.RightIndent = 0
    fmt.FirstLineIndent = 0
    fmt.SpaceBefore = 0
    fmt.SpaceAfter = 0
    fmt.LineSpacingRule = 0  # одинарный
    fmt.Alignment = 3  # по ширине
    fmt.KeepWithNext = True
    rng = par.Range
    rng.Font.Name = "Times New Roman"
    rng.Font.Size = 14
    rng.Font.Bold = False


def _insert_listing_continuations(doc) -> int:
    added = 0
    for _ in range(MAX_PASSES):
        changed = False
        names = [b.Name for b in doc.Bookmarks if b.Name.startswith("_ForLabsL")]
        for name in names:
            m = re.match(r"_ForLabsL(.+)_(\d+)$", name)
            n = m.group(2) if m else "?"
            rng = doc.Bookmarks(name).Range
            pars = list(rng.Paragraphs)
            prev_page = None
            for par in pars:
                if par.Range.Text.strip().startswith("Продолжение листинга"):
                    prev_page = _start_page(par)
                    continue
                page = _start_page(par)
                if prev_page is not None and page != prev_page:
                    par.Range.InsertParagraphBefore()
                    new_par = par.Previous()
                    _style_continuation(new_par, f"Продолжение листинга {n}")
                    doc.Repaginate()
                    added += 1
                    changed = True
                    break
                prev_page = page
            if changed:
                break
        if not changed:
            return added
    return added


def _insert_table_continuations(doc) -> int:
    added = 0
    for _ in range(MAX_PASSES):
        changed = False
        for ti in range(1, doc.Tables.Count + 1):
            tbl = doc.Tables(ti)
            prev = None
            for ri in range(1, tbl.Rows.Count + 1):
                row = tbl.Rows(ri)
                page = _page(row.Range.Cells(1).Range.Characters(1))
                if prev is not None and page != prev and ri > 1:
                    header_texts = [c.Range.Text[:-2] for c in tbl.Rows(1).Cells]
                    caption_n = _table_number(doc, tbl)
                    new_tbl = tbl.Split(ri)
                    gap = new_tbl.Range.Paragraphs(1).Previous()
                    _style_continuation(gap, f"Продолжение таблицы {caption_n}")
                    new_tbl.Rows.Add(new_tbl.Rows(1))
                    for ci, text in enumerate(header_texts, start=1):
                        new_tbl.Rows(1).Cells(ci).Range.Text = text
                    new_tbl.Rows(1).HeadingFormat = True
                    doc.Repaginate()
                    added += 1
                    changed = True
                    break
                prev = page
            if changed:
                break
        if not changed:
            return added
    return added


def _table_number(doc, tbl) -> str:
    par = tbl.Range.Paragraphs(1).Previous()
    for _ in range(4):
        if par is None:
            break
        m = re.match(r"\s*(?:Таблица|Продолжение таблицы)\s+(\d+)", par.Range.Text)
        if m:
            return m.group(1)
        par = par.Previous()
    return "?"


def finalize(docx: Path, pdf_out: Path, author: str) -> WordReport:
    import pythoncom
    import win32com.client

    report = WordReport()
    pythoncom.CoInitialize()
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
    doc = None
    try:
        doc = word.Documents.Open(str(Path(docx).resolve()), False, False, False)
        doc.Repaginate()
        for toc in doc.TablesOfContents:
            toc.Update()
        report.continuations += _insert_listing_continuations(doc)
        report.continuations += _insert_table_continuations(doc)
        for toc in doc.TablesOfContents:
            toc.UpdatePageNumbers()
        try:
            doc.BuiltInDocumentProperties("Author").Value = author
            doc.BuiltInDocumentProperties("Last author").Value = author
        except Exception as e:  # свойства не критичны
            report.warnings.append(f"Не удалось записать автора: {e}")
        doc.Save()
        doc.ExportAsFixedFormat(str(Path(pdf_out).resolve()), WD_EXPORT_PDF)
    finally:
        if doc is not None:
            doc.Close(False)
        word.Quit()
        pythoncom.CoUninitialize()
    return report
