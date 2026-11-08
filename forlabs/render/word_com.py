"""Доводка через Microsoft Word (только Windows): содержание, «Продолжение листинга/таблицы N»,
свойства документа, PDF для предпросмотра. Работает с реальной разбивкой Word на страницы.

Важно про объектную модель Word:
- закладки с именем на «_» скрыты: их видно в коллекции только при Bookmarks.ShowHidden = True;
- Range абзаца включает знак абзаца: присваивание Range.Text склеит абзац со следующим,
  поэтому текст вставляется в пустой новый абзац через InsertBefore.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

WD_EXPORT_PDF = 17
WD_ACTIVE_END_PAGE = 3
WD_CHARACTER = 1
MAX_ROUNDS = 10
MAX_INSERTS = 300
LISTING_PREFIX = "_ForLabsL"
CONT_LISTING = "Продолжение листинга"
CONT_TABLE = "Продолжение таблицы"


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


def _start_page(rng_owner) -> int:
    """Страница начала абзаца/строки (по первому символу)."""
    r = rng_owner.Range
    first = r.Document.Range(r.Start, r.Start)
    return int(first.Information(WD_ACTIVE_END_PAGE))


def _text(par) -> str:
    return (par.Range.Text or "").replace("\r", "").replace("\x07", "")


def _style_continuation(par) -> None:
    par.Borders.Enable = False
    fmt = par.Format
    fmt.LeftIndent = 0
    fmt.RightIndent = 0
    fmt.FirstLineIndent = 0
    fmt.SpaceBefore = 0
    fmt.SpaceAfter = 0
    fmt.LineSpacingRule = 0  # одинарный
    fmt.Alignment = 3  # по ширине
    fmt.KeepWithNext = True
    font = par.Range.Font
    font.Name = "Times New Roman"
    font.Size = 14
    font.Bold = False


def _insert_paragraph_before(doc, par, text: str):
    """Новый абзац перед par с текстом; знак абзаца par не трогаем."""
    start = par.Range.Start
    doc.Range(start, start).InsertParagraphBefore()
    new_par = doc.Range(start, start).Paragraphs(1)
    r = new_par.Range
    r.MoveEnd(WD_CHARACTER, -1)  # без знака абзаца
    r.InsertBefore(text)
    _style_continuation(new_par)
    return new_par


def listing_bookmarks(doc) -> list[str]:
    doc.Bookmarks.ShowHidden = True
    names = [doc.Bookmarks(i).Name for i in range(1, doc.Bookmarks.Count + 1)]
    return [n for n in names if n.startswith(LISTING_PREFIX)]


def _fix_listing_once(doc, name: str) -> bool:
    """Одна правка в листинге: вставить недостающее или убрать лишнее «Продолжение». True — была правка."""
    m = re.match(rf"{LISTING_PREFIX}.+_(\d+)$", name)
    n = m.group(1) if m else "?"
    pars = doc.Bookmarks(name).Range.Paragraphs
    prev_page = None
    for i in range(1, pars.Count + 1):
        par = pars(i)
        page = _start_page(par)
        if _text(par).startswith(CONT_LISTING):
            if prev_page is not None and page == prev_page:
                par.Range.Delete()  # уже не вверху страницы — лишнее
                return True
            prev_page = page
            continue
        if prev_page is not None and page != prev_page:
            _insert_paragraph_before(doc, par, f"{CONT_LISTING} {n}")
            return True
        prev_page = page
    return False


def insert_listing_continuations(doc) -> int:
    count = 0
    for _ in range(MAX_INSERTS):
        changed = False
        for name in listing_bookmarks(doc):
            if _fix_listing_once(doc, name):
                doc.Repaginate()
                count += 1
                changed = True
                break
        if not changed:
            break
    return count


def _table_number(tbl) -> str:
    par = tbl.Range.Paragraphs(1).Previous()
    for _ in range(4):
        if par is None:
            break
        m = re.match(rf"\s*(?:Таблица|{CONT_TABLE})\s+(\d+)", _text(par))
        if m:
            return m.group(1)
        par = par.Previous()
    return "?"


def insert_table_continuations(doc) -> int:
    count = 0
    for _ in range(MAX_INSERTS):
        changed = False
        for ti in range(1, doc.Tables.Count + 1):
            tbl = doc.Tables(ti)
            prev = None
            for ri in range(1, tbl.Rows.Count + 1):
                page = _start_page(tbl.Rows(ri).Range.Cells(1))
                if prev is not None and page != prev and ri > 1:
                    header = [c.Range.Text[:-2] for c in tbl.Rows(1).Range.Cells]
                    number = _table_number(tbl)
                    new_tbl = tbl.Split(ri)
                    gap = new_tbl.Range.Paragraphs(1).Previous()
                    r = gap.Range
                    r.MoveEnd(WD_CHARACTER, -1)
                    r.InsertBefore(f"{CONT_TABLE} {number}")
                    _style_continuation(gap)
                    new_tbl.Rows.Add(new_tbl.Rows(1))
                    for ci, text in enumerate(header, start=1):
                        new_tbl.Rows(1).Cells(ci).Range.Text = text
                    new_tbl.Rows(1).HeadingFormat = True
                    new_tbl.Rows(1).Range.ParagraphFormat.KeepWithNext = True
                    doc.Repaginate()
                    count += 1
                    changed = True
                    break
                prev = page
            if changed:
                break
        if not changed:
            break
    return count


def place_continuations(doc) -> int:
    """Листинги и таблицы влияют друг на друга — повторяем до стабилизации."""
    total = 0
    for _ in range(MAX_ROUNDS):
        added = insert_listing_continuations(doc) + insert_table_continuations(doc)
        total += added
        if not added:
            break
    return total


def finalize(docx: Path, pdf_out: Path, author: str) -> WordReport:
    import pythoncom
    import win32com.client

    report = WordReport()
    pythoncom.CoInitialize()
    word = doc = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(Path(docx).resolve()), False, False, False)
        doc.Repaginate()
        for toc in doc.TablesOfContents:
            toc.Update()
        report.continuations = place_continuations(doc)
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
        try:
            if doc is not None:
                doc.Close(False)
        finally:
            try:
                if word is not None:
                    word.Quit()
            finally:
                pythoncom.CoUninitialize()
    return report


def _word_pids() -> set[int]:
    """PID запущенных WINWORD.EXE (только Windows)."""
    if sys.platform != "win32":
        return set()
    import subprocess

    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WINWORD.EXE", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return set()
    pids = set()
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def _kill_pids(pids) -> None:
    """Завершить именно тот Word, который запустило приложение (не трогая Word студента)."""
    if sys.platform != "win32":
        return
    import subprocess

    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass


def _child(docx: str, pdf_out: str, author: str, queue) -> None:
    import win32com.client  # noqa: F401 — заранее, чтобы снимок PID был сразу перед запуском Word

    before = _word_pids()
    real_dispatch = win32com.client.DispatchEx

    def dispatch_and_report(name):  # сообщаем родителю PID нашего Word, чтобы при зависании завершить его
        app = real_dispatch(name)
        queue.put(("pids", sorted(_word_pids() - before)))
        return app

    win32com.client.DispatchEx = dispatch_and_report
    try:
        rep = finalize(Path(docx), Path(pdf_out), author)
        queue.put(("ok", rep.continuations, rep.warnings))
    except Exception as e:  # отдаём текст ошибки родителю
        queue.put(("error", 0, [f"{type(e).__name__}: {e}"]))


def finalize_isolated(docx: Path, pdf_out: Path, author: str, timeout: float = 300.0, target=None) -> WordReport:
    """Word в отдельном процессе с тайм-аутом: зависший диалог Word не вешает приложение, а сам зависший
    WINWORD.EXE завершается (иначе он держит документ до перезагрузки)."""
    import multiprocessing as mp
    import queue as queue_mod

    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=target or _child, args=(str(docx), str(pdf_out), author, q), daemon=True)
    p.start()
    pids: list[int] = []
    result = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and result is None:
        try:
            msg = q.get(timeout=0.5)
        except queue_mod.Empty:
            if not p.is_alive():
                break
            continue
        if msg[0] == "pids":
            pids = list(msg[1])
        else:
            result = msg
    if result is None:
        if p.is_alive():
            p.terminate()
            _kill_pids(pids)
            raise TimeoutError("Word не ответил за отведённое время (возможно, открыто окно-предупреждение Word)")
        _kill_pids(pids)
        raise RuntimeError("Word завершился без результата")
    p.join(10)
    status, cont, warnings = result
    if status != "ok":
        _kill_pids(pids)
        raise RuntimeError("; ".join(warnings))
    return WordReport(cont, warnings)
