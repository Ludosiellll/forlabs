"""Доводка документа: Word, если есть; иначе LibreOffice с честным предупреждением."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from forlabs.render import libreoffice, word_com
from forlabs.render.conformance import q
from forlabs.render.preview import heading_pages, pdf_to_pngs

NO_WORD_WARNING = ("Word на этом компьютере не найден, поэтому «Продолжение листинга» не расставлено. "
                   "Открой лабу в ForLabs на ПК с Word и нажми «Пересобрать документ».")


@dataclass
class FinalizeReport:
    engine: str
    pdf: Path
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
    tmp.replace(docx)


def finalize(docx: Path, author: str, workdir: Path, soffice: str | None = None,
             prefer_word: bool = True) -> FinalizeReport:
    docx, workdir = Path(docx), Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    pdf = workdir / (docx.stem + ".pdf")
    if prefer_word and word_com.available():
        rep = word_com.finalize(docx, pdf, author)
        return FinalizeReport("word", pdf, pdf_to_pngs(pdf, workdir / "pages"), rep.continuations, rep.warnings)

    pdf = libreoffice.to_pdf(docx, workdir, soffice)
    marks = toc_bookmarks(docx)
    warnings = [NO_WORD_WARNING]
    if marks:
        hp = heading_pages(pdf, list(marks.values()))
        patch_toc_pages(docx, {name: hp[text] for name, text in marks.items() if text in hp})
        pdf = libreoffice.to_pdf(docx, workdir, soffice)
    return FinalizeReport("libreoffice", pdf, pdf_to_pngs(pdf, workdir / "pages"), 0, warnings)


def has_slots(docx: Path) -> bool:
    with zipfile.ZipFile(docx) as z:
        return bool(re.search(r"\[Вставить скриншот", z.read("word/document.xml").decode("utf-8")))
