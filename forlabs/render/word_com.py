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
WD_GOTO_PAGE = 1
WD_GOTO_ABSOLUTE = 1
WD_STATISTIC_PAGES = 2
WD_WITHIN_TABLE = 12
MAX_ROUNDS = 10
MAX_INSERTS = 300
LISTING_PREFIX = "_ForLabsL"
CONT_LISTING = "Продолжение листинга"
CONT_TABLE = "Продолжение таблицы"


@dataclass
class WordReport:
    continuations: int = 0
    warnings: list[str] = field(default_factory=list)
    marks: int = -1  # абзацы, отмеченные для разрыва страницы (freeze); -1 — отметить не удалось


_failed = False  # Word в этом запуске уже зависал/падал — до перезапуска доводим через LibreOffice


def available() -> bool:
    return not _failed and _installed()


def _installed() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        import win32com.client  # noqa: F401

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
    """Правка в листинге k сдвигает только то, что после него: прежние листинги заново не проверяем
    (иначе отчёт за семестр с десятками листингов не успевал за 5 минут)."""
    count, k = 0, 0
    names = listing_bookmarks(doc)
    while k < len(names) and count < MAX_INSERTS:
        if _fix_listing_once(doc, names[k]):
            doc.Repaginate()
            count += 1
            continue  # тот же листинг: ниже может понадобиться ещё одна правка
        k += 1
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


def mark_page_starts(doc) -> int:
    """Абзацы, с которых начинается страница и у которых стоит «не отрывать от следующего», «не разрывать абзац»
    или «с новой страницы», отмечаются закладкой freeze.MARK_PREFIX…: после сохранения на их место встаёт обычный
    разрыв страницы, а настройки (у Word они видны как чёрные квадраты слева) снимаются. Разбивка не меняется."""
    from forlabs.render.freeze import MARK_PREFIX

    doc.Repaginate()
    pages = int(doc.ComputeStatistics(WD_STATISTIC_PAGES))
    count = 0
    for k in range(2, pages + 1):
        start = doc.GoTo(WD_GOTO_PAGE, WD_GOTO_ABSOLUTE, k)
        par = start.Paragraphs(1)
        if par.Range.Start != start.Start or par.Range.Information(WD_WITHIN_TABLE):
            continue  # страница начинается посреди абзаца или в таблице — обычный переход
        fmt = par.Format
        if fmt.KeepWithNext or fmt.KeepTogether or fmt.PageBreakBefore:
            count += 1
            doc.Bookmarks.Add(f"{MARK_PREFIX}{count}", doc.Range(start.Start, start.Start))
    return count


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
            report.marks = mark_page_starts(doc)
        except Exception as e:  # без отметок документ остаётся с прежними настройками — разбивка та же
            report.warnings.append(f"Не удалось закрепить разрывы страниц: {e}")
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
        raw = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WINWORD.EXE", "/FO", "CSV", "/NH"],
                             capture_output=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        out = raw.decode("cp866", errors="replace")  # консоль Windows — не UTF-8
    except Exception:
        return set()
    pids = set()
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def parse_word_processes(listing: str) -> set[int]:
    """Строки «PID командная_строка»: только Word, запущенный для автоматизации (/Automation -Embedding), —
    Word студента с его документами в этот список не попадает и не будет завершён."""
    out = set()
    for line in listing.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        low = cmd.lower()
        if pid.isdigit() and "winword" in low and ("/automation" in low or "-embedding" in low):
            out.add(int(pid))
    return out


SCAN_AFTER = 15.0  # секунд без вестей от дочернего процесса — тогда ищем зависший Word сами


def _automation_word_pids() -> set[int] | None:
    """PID Word, запущенных для автоматизации (наших), по командной строке процесса. None — узнать не удалось
    (PowerShell запрещён политикой колледжа или не ответил)."""
    if sys.platform != "win32":
        return set()
    import subprocess

    ps = ("Get-CimInstance Win32_Process -Filter \"Name='WINWORD.EXE'\" | "
          "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }")
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                             timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return None
    if res.returncode != 0:
        return None
    return parse_word_processes(res.stdout.decode("utf-8", errors="replace"))


def _pids_file():
    from forlabs.core import paths

    return paths.data_home() / "word_pids.json"


def _remembered_pids() -> set[int]:
    import json

    try:
        return {int(x) for x in json.loads(_pids_file().read_text(encoding="utf-8"))}
    except (OSError, ValueError, TypeError):
        return set()


def _remember_pids(pids) -> None:
    import json

    from forlabs.core.fs import atomic_write_text

    try:
        atomic_write_text(_pids_file(), json.dumps(sorted(set(pids))))
    except OSError:
        pass


def _kill_ours(pids) -> None:
    """Завершить наш зависший Word: на Windows — только процессы, запущенные для автоматизации (не Word студента,
    открытый в те же секунды)."""
    pids = set(pids)
    if sys.platform == "win32":
        scan = _automation_word_pids()
        if scan is not None:  # узнать не удалось — завершаем те, о которых сообщил дочерний процесс
            pids &= scan
    if pids:
        _kill_pids(sorted(pids))
    _remember_pids(_remembered_pids() - pids)


def cleanup_stale_word() -> None:
    """При запуске: Word, оставшийся от прошлой доводки (приложение закрыли во время неё), — завершить.
    Только те PID, что мы запомнили, и только если это всё ещё Word для автоматизации."""
    remembered = _remembered_pids()
    if not remembered:
        return
    scan = _automation_word_pids()
    if scan is None:  # проверить, что это всё ещё наш Word, нельзя — не трогаем
        return
    stale = remembered & scan
    if stale:
        _kill_pids(sorted(stale))
    _remember_pids(set())


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
        queue.put(("ok", rep.continuations, rep.warnings, rep.marks))
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
    baseline = _automation_word_pids() or set()  # Word для автоматизации, что уже были до нас
    p.start()
    pids: set[int] = set()
    reported = False
    result = None
    deadline = time.monotonic() + timeout
    next_scan = time.monotonic() + SCAN_AFTER
    while time.monotonic() < deadline and result is None:
        if not reported and time.monotonic() >= next_scan:  # Word завис при запуске (активация): ищем из родителя
            new = (_automation_word_pids() or set()) - baseline
            if new - pids:
                pids |= new
                _remember_pids(pids | _remembered_pids())
            next_scan = time.monotonic() + 10.0
        try:
            msg = q.get(timeout=0.5)
        except queue_mod.Empty:
            if not p.is_alive():
                break
            continue
        if msg[0] == "pids":
            pids |= set(msg[1])
            reported = True
            _remember_pids(pids | _remembered_pids())
        else:
            result = msg
    if result is None:
        if p.is_alive():
            p.terminate()
            _kill_ours(pids)
            raise TimeoutError("Word не ответил за отведённое время (возможно, открыто окно-предупреждение Word)")
        _kill_ours(pids)
        raise RuntimeError("Word завершился без результата")
    p.join(10)
    status, cont, warnings, *rest = result
    if status != "ok":
        _kill_ours(pids)
        raise RuntimeError("; ".join(warnings))
    _remember_pids(_remembered_pids() - pids)  # Word закрылся сам
    return WordReport(cont, warnings, rest[0] if rest else -1)
