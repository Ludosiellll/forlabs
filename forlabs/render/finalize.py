"""Доводка документа: Word, если есть; иначе LibreOffice с честным предупреждением."""

from __future__ import annotations

import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from forlabs.core.fs import durable_replace
from forlabs.render import freeze, libreoffice, word_com
from forlabs.render.conformance import q
from forlabs.render.preview import heading_pages, pdf_to_pngs

NO_OFFICE_WARNING = ("Нет ни Word, ни LibreOffice: документ собран, но без предпросмотра и без «Продолжения листинга». "
                     "Поставь бесплатный LibreOffice (libreoffice.org) и нажми «Пересобрать документ».")
NO_WORD_WARNING = ("Word на этом компьютере не найден, поэтому «Продолжение листинга» и «Продолжение таблицы» не "
                   "расставлены. "
                   "Открой лабу в ForLabs на ПК с Word и нажми «Пересобрать документ».")


@dataclass
class FinalizeReport:
    engine: str
    pdf: Path | None
    pages: list[Path]
    continuations: int = 0
    warnings: list[str] = field(default_factory=list)


def toc_bookmarks(docx: Path) -> dict[str, str]:
    """Закладки заголовков лаб, на которые ссылается содержание: имя → текст заголовка."""
    with zipfile.ZipFile(docx) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    out = {}
    for bs in root.iter(q("bookmarkStart")):
        name = bs.get(q("name"), "")
        if name.startswith("_Toc"):
            p = bs.getparent()
            out[name] = "".join(t.text or "" for t in p.iter(q("t"))).strip()
    return out


def patch_toc_pages(docx: Path, pages: dict[str, int]) -> None:
    """Проставляет номера страниц в сохранённый результат полей PAGEREF содержания."""
    with zipfile.ZipFile(docx) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    out_items = []
    for info, data in items:
        if info.filename == "word/document.xml":
            root = etree.fromstring(data)
            for hl in root.iter(q("hyperlink")):
                name = hl.get(q("anchor"))
                if name in pages:
                    texts = [t for t in hl.iter(q("t"))]
                    if texts:
                        texts[-1].text = str(pages[name])
            data = etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)
        out_items.append((info, data))
    tmp = Path(str(docx) + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for info, data in out_items:
            zout.writestr(info, data)
    durable_replace(tmp, docx)


def _clean_old_copies(workdir: Path, age: float = 3600) -> None:
    """Копии для Word, оставшиеся от зависшего Word (он держал файл), — убрать, когда старше часа."""
    import time

    for f in workdir.glob("word_*.docx"):
        try:
            if time.time() - f.stat().st_mtime > age:
                f.unlink()
        except OSError:
            pass


def finalize(docx: Path, author: str, workdir: Path, soffice: str | None = None,
             prefer_word: bool = True) -> FinalizeReport:
    docx, workdir = Path(docx), Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    _clean_old_copies(workdir)
    pdf = workdir / (docx.stem + ".pdf")
    warnings: list[str] = []
    if prefer_word and word_com.available():
        # Word работает с копией: зависший WINWORD.EXE не заблокирует report.docx, а неудача его не испортит
        work = workdir / f"word_{uuid.uuid4().hex[:8]}.docx"
        shutil.copyfile(docx, work)
        try:
            rep = word_com.finalize_isolated(work, pdf, author)
            if rep.marks >= 0:  # Word отметил, где абзацы ушли на новую страницу: разрывы вместо «квадратов»
                freeze.apply(work)
            durable_replace(work, docx)
            return FinalizeReport("word", pdf, pdf_to_pngs(pdf, workdir / "pages"), rep.continuations, rep.warnings)
        except Exception as e:  # Word завис или упал — доводим исходный документ через LibreOffice
            word_com._failed = True  # следующая вёрстка не ждёт его снова по 5 минут
            warnings.append(f"Word не справился ({e}). Документ доведён без Word; «Продолжение листинга» и "
                            "«Продолжение таблицы» не расставлены. Закрой окна Word и перезапусти ForLabs, потом "
                            "нажми «Пересобрать документ».")
        finally:
            try:
                work.unlink(missing_ok=True)
            except OSError:
                pass  # копию держит зависший Word — удалится при следующей доводке
    else:
        warnings.append(NO_WORD_WARNING)

    if not libreoffice.find_soffice(soffice):
        # ни Word, ни LibreOffice: документ уже собран и годится для сдачи — только без предпросмотра
        return FinalizeReport("none", None, [], 0, warnings + [NO_OFFICE_WARNING])
    try:
        pdf = libreoffice.to_pdf(docx, workdir, soffice)
    except Exception as e:
        return FinalizeReport("none", None, [], 0, warnings + [f"Предпросмотр не получился ({e}). Документ готов."])
    marks = toc_bookmarks(docx)
    if marks:
        hp = heading_pages(pdf, list(marks.values()))
        patch_toc_pages(docx, {name: hp[text] for name, text in marks.items() if text in hp})
        pdf = libreoffice.to_pdf(docx, workdir, soffice)
    try:  # разрывы страниц вместо настроек «не отрывать» — без чёрных квадратов в Word
        freeze.apply(docx, freeze.page_starts_from_pdf(docx, pdf))
        pdf = libreoffice.to_pdf(docx, workdir, soffice)
    except Exception as e:  # noqa: BLE001 — документ остаётся с прежними настройками, разбивка та же
        warnings.append(f"Не удалось закрепить разрывы страниц ({e}).")
    return FinalizeReport("libreoffice", pdf, pdf_to_pngs(pdf, workdir / "pages"), 0, warnings)


def has_slots(docx: Path) -> bool:
    with zipfile.ZipFile(docx) as z:
        return bool(re.search(r"\[Вставить скриншот", z.read("word/document.xml").decode("utf-8")))
