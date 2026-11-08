"""Сборка .docx по канону: шаблон эталона + клоны образцов абзацев."""

from __future__ import annotations

import copy
import os
import random
import re
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.shared import Cm
from lxml import etree
from PIL import Image

from forlabs.core.fs import durable_replace
from forlabs.core.models import (
    DashList,
    Enrollment,
    Figure,
    Listing,
    NumberedList,
    Paragraph,
    Profile,
    ReportDoc,
    Table,
)
from forlabs.render import canon
from forlabs.render.conformance import q
from forlabs.report.derive import (
    caption,
    figure_intro,
    listing_intro,
    numbered_blocks,
    table_intro,
)
from forlabs.report.morph import performed_word

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
MAX_IMAGE_HEIGHT_CM = 22.0
SCREEN_DPI = 96


_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\ud800-\udfff]")


def xml_safe(text: str) -> str:
    """Символы, которых не бывает в XML (мусор из PDF или ответа модели), — вон: иначе документ не соберётся."""
    return _XML_BAD.sub("", text or "")


def _run(text: str, rpr) -> etree._Element:
    text = xml_safe(text)
    r = etree.Element(q("r"))
    if rpr is not None:
        r.append(rpr)
    t = etree.SubElement(r, q("t"))
    t.text = text
    if text != text.strip() or "  " in text:
        t.set(XML_SPACE, "preserve")
    return r


class _Writer:
    def __init__(self, document):
        self.document = document
        self.body = document.element.body
        self.sect = self.body.find(q("sectPr"))
        self._bm_id = 100

    def _append(self, el):
        if self.sect is not None:
            self.sect.addprevious(el)
        else:
            self.body.append(el)
        return el

    def para(self, role: str, text: str | None = None, label: str | None = None):
        ex = canon.exemplar(role)
        p = etree.Element(q("p"))
        ppr = ex.new_ppr()
        if ppr is not None:
            p.append(ppr)
        if label:
            p.append(_run(label, ex.new_rpr(label=True)))
        if text:
            p.append(_run(text, ex.new_rpr()))
        return self._append(p)

    def heading(self, text: str, bookmark: str | None):
        p = self.para("lab_heading")
        if bookmark:
            self._bm_id += 1
            bs = etree.SubElement(p, q("bookmarkStart"))
            bs.set(q("id"), str(self._bm_id))
            bs.set(q("name"), bookmark)
        p.append(_run(text, canon.exemplar("lab_heading").new_rpr()))
        if bookmark:
            be = etree.SubElement(p, q("bookmarkEnd"))
            be.set(q("id"), str(self._bm_id))
        return p

    def page_break(self):
        ex = canon.exemplar("page_break")
        p = etree.Element(q("p"))
        if ex.ppr is not None:
            p.append(ex.new_ppr())
        r = etree.SubElement(p, q("r"))
        if ex.rpr is not None:
            r.append(ex.new_rpr())
        br = etree.SubElement(r, q("br"))
        br.set(q("type"), "page")
        return self._append(p)

    def image(self, path: str, width_cm: float | None):
        w_cm, h_cm = image_size_cm(path, width_cm)
        para = self.document.add_paragraph()
        p = para._p
        ex = canon.exemplar("image")
        for child in list(p):
            p.remove(child)
        if ex.ppr is not None:
            p.append(ex.new_ppr())
        run = para.add_run()
        if ex.rpr is not None:
            run._r.insert(0, ex.new_rpr())
        run.add_picture(str(path), width=Cm(w_cm), height=Cm(h_cm))
        # python-docx кладёт абзац в конец body — переносим перед sectPr (он уже там)
        return p

    def bookmark(self, p, name: str, start: bool):
        self._bm_id += 1 if start else 0
        el = etree.Element(q("bookmarkStart" if start else "bookmarkEnd"))
        el.set(q("id"), str(self._bm_id))
        if start:
            el.set(q("name"), name)
            ppr = p.find(q("pPr"))
            (ppr.addnext if ppr is not None else lambda e: p.insert(0, e))(el)
        else:
            p.append(el)

    def code(self, code: str, bookmark: str | None = None):
        lines = code.replace("\r\n", "\n").replace("\t", "    ").strip("\n").split("\n")
        made = []
        for line in lines:
            made.append(self.para("code", line.rstrip()) if line.strip() else self.para("code_blank"))
        if bookmark and made:
            self.bookmark(made[0], bookmark, start=True)
            self.bookmark(made[-1], bookmark, start=False)

    def table(self, rows: list[list[str]], header_rows: int):
        tbl_pr, tr_pr = canon.table_props()
        ncols = max(len(r) for r in rows)
        widths = column_widths(rows, ncols)
        tbl = etree.Element(q("tbl"))
        tbl.append(tbl_pr)
        grid = etree.SubElement(tbl, q("tblGrid"))
        for w in widths:
            gc = etree.SubElement(grid, q("gridCol"))
            gc.set(q("w"), str(w))
        ex = canon.exemplar("table_cell")
        for ri, row in enumerate(rows):
            tr = etree.SubElement(tbl, q("tr"))
            trp = etree.SubElement(tr, q("trPr"))
            etree.SubElement(trp, q("cantSplit"))
            if ri < header_rows:
                etree.SubElement(trp, q("tblHeader"))
            for ci in range(ncols):
                tc = etree.SubElement(tr, q("tc"))
                tcp = etree.SubElement(tc, q("tcPr"))
                tcw = etree.SubElement(tcp, q("tcW"))
                tcw.set(q("w"), str(widths[ci]))
                tcw.set(q("type"), "dxa")
                p = etree.SubElement(tc, q("p"))
                if ex.ppr is not None:
                    p.append(ex.new_ppr())
                value = row[ci] if ci < len(row) else ""
                if value.strip():
                    p.append(_run(value.strip(), ex.new_rpr()))
        return self._append(tbl)


def column_widths(rows: list[list[str]], ncols: int) -> list[int]:
    lengths = []
    for ci in range(ncols):
        longest = max((len(r[ci]) if ci < len(r) else 0) for r in rows)
        lengths.append(max(6, min(longest, 60)))
    total = sum(lengths)
    widths = [max(900, int(canon.TEXT_WIDTH_TWIPS * n / total)) for n in lengths]
    scale = canon.TEXT_WIDTH_TWIPS / sum(widths)
    widths = [int(w * scale) for w in widths]
    widths[-1] += canon.TEXT_WIDTH_TWIPS - sum(widths)
    return widths


def image_size_cm(path: str, width_cm: float | None = None) -> tuple[float, float]:
    with Image.open(path) as im:
        w_px, h_px = im.size
    natural_w = w_px / SCREEN_DPI * 2.54
    w = min(width_cm or natural_w, canon.TEXT_WIDTH_CM)
    h = w * h_px / w_px
    if h > MAX_IMAGE_HEIGHT_CM:
        h = MAX_IMAGE_HEIGHT_CM
        w = h * w_px / h_px
    return round(w, 3), round(h, 3)


def keep_next(p):
    """«Не отрывать от следующего»: подводка, картинка и подпись не разъезжаются по страницам.
    На вид абзаца не влияет — так делают вручную при вёрстке."""
    ppr = p.find(q("pPr"))
    if ppr is None:
        ppr = etree.Element(q("pPr"))
        p.insert(0, ppr)
    if ppr.find(q("keepNext")) is None:
        el = etree.Element(q("keepNext"))
        anchor = ppr.find(q("pStyle"))
        if anchor is not None:
            anchor.addnext(el)
        else:
            ppr.insert(0, el)
    return p


def listing_bookmark(lab_number: str, n: int) -> str:
    return f"_ForLabsL{re.sub(r'[^0-9A-Za-z]', '_', lab_number)}_{n}"


def _dash(item: str) -> str:
    return "- " + re.sub(r"^\s*[-–—•]\s*", "", item).strip()


def _numbered(i: int, item: str) -> str:
    item = item.strip()
    return item if re.match(r"^\d+[.)]\s", item) else f"{i}. {item}"


def write_lab(w: _Writer, doc: ReportDoc, bookmark: str | None = None) -> None:
    w.heading(f"Лабораторная работа №{doc.lab_number}", bookmark)
    for label, value in doc.header.items():
        w.para("header_field", value, label=f"{label}: ")
    w.para("results_title", "Результаты выполнения работы:")
    numbers = {(b.section_index, b.block_index): b.n for b in numbered_blocks(doc)}
    for si, sec in enumerate(doc.sections):
        w.para("task_heading", sec.title.strip(), label=f"Задание {sec.n}. ")
        for bi, b in enumerate(sec.blocks):
            n = numbers.get((si, bi))
            if isinstance(b, Paragraph):
                if b.text.strip():
                    w.para("body", b.text.strip())
            elif isinstance(b, DashList):
                for item in b.items:
                    if item.strip():
                        w.para("dash_item", _dash(item))
            elif isinstance(b, NumberedList):
                for i, item in enumerate(b.items, start=1):
                    if item.strip():
                        w.para("numbered_item", _numbered(i, item))
            elif isinstance(b, Figure):
                keep_next(w.para("figure_ref", figure_intro(b.topic, n)))
                if b.image_path and Path(b.image_path).exists():
                    keep_next(w.image(b.image_path, b.width_cm))
                else:
                    keep_next(w.para("body", f"[Вставить скриншот: {b.slot or b.topic}]"))
                w.para("figure_caption", caption("figure", n, b.topic))
            elif isinstance(b, Listing):
                keep_next(w.para("listing_ref", listing_intro(b.topic, n)))
                keep_next(w.para("listing_caption", caption("listing", n, b.topic)))
                w.code(b.code, bookmark=listing_bookmark(doc.lab_number, n))
            elif isinstance(b, Table):
                keep_next(w.para("table_ref", table_intro(b.topic, n)))
                keep_next(w.para("table_caption", caption("table", n, b.topic)))
                w.table(b.rows, b.header_rows)
    if doc.questions:
        w.para("cq_title", "Ответы на контрольные вопросы:")
        for qa in doc.questions:
            w.para("cq_question", f"{qa.n}. {qa.question.strip()}")
            w.para("cq_answer", "Ответ: " + re.sub(r"^\s*Ответ:\s*", "", qa.answer.strip()))
    w.para("conclusion", re.sub(r"^\s*Вывод:\s*", "", doc.conclusion.strip()), label="Вывод: ")


def _new_document():
    return Document(str(canon.BASE_DOCX))


def _set_properties(document, author: str) -> None:
    now = datetime.now().replace(microsecond=0)
    cp = document.core_properties
    author = xml_safe(author)
    cp.author = author
    cp.last_modified_by = author
    cp.created = now
    cp.modified = now
    cp.revision = random.randint(3, 9)
    cp.title = ""


def _save(document, out: Path, author: str) -> Path:
    _set_properties(document, author)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.{os.getpid()}.tmp.docx")
    try:  # выключение посреди записи не должно испортить прежний отчёт
        document.save(str(tmp))
        _set_total_time(tmp)
        durable_replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def _set_total_time(path: Path) -> None:
    import zipfile

    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "docProps/app.xml":
                s = data.decode("utf-8")
                s = re.sub(r"<TotalTime>\d*</TotalTime>", f"<TotalTime>{random.randint(40, 75)}</TotalTime>", s)
                data = s.encode("utf-8")
            zout.writestr(item, data)
    tmp.replace(path)


def build_lab(doc: ReportDoc, out: Path, author: str = "") -> Path:
    document = _new_document()
    write_lab(_Writer(document), doc)
    return _save(document, out, author)


def _fill_title(profile: Profile, enrollment: Enrollment) -> list:
    values = {
        "COLLEGE1": profile.college_lines[0] if len(profile.college_lines) > 0 else "",
        "COLLEGE2": profile.college_lines[1] if len(profile.college_lines) > 1 else "",
        "COLLEGE3": profile.college_lines[2] if len(profile.college_lines) > 2 else "",
        "SPECIALTY_CODE": profile.specialty_code,
        "SPEC1": profile.specialty_lines[0] if len(profile.specialty_lines) > 0 else "",
        "SPEC2": profile.specialty_lines[1] if len(profile.specialty_lines) > 1 else "",
        "SPEC3": profile.specialty_lines[2] if len(profile.specialty_lines) > 2 else "",
        "SUBJECT": enrollment.title_subject,
        "PERFORMED": performed_word(profile.gender),
        "GROUP": profile.group,
        "NAME": profile.title_name,
        "TEACHER": enrollment.teacher,
        "CITY": profile.city,
        "YEAR": enrollment.academic_year,
    }
    root = etree.fromstring(canon.title_xml(), canon._PARSER)
    for t in root.iter(q("t")):
        t.text = re.sub(r"\{\{([A-Z0-9_]+)\}\}", lambda m: values.get(m.group(1), ""), t.text or "")
    return list(root)


def _toc(entries: list[tuple[str, str, str]]):
    """entries: (текст, имя закладки, номер страницы)."""
    parts = canon.toc_parts()
    sdt = etree.Element(q("sdt"))
    sdt.append(parts["sdtPr"])
    if parts["sdtEndPr"] is not None:
        sdt.append(parts["sdtEndPr"])
    content = etree.SubElement(sdt, q("sdtContent"))
    content.append(parts["title"])
    for i, (text, bookmark, page) in enumerate(entries):
        p = copy.deepcopy(parts["first"] if i == 0 else parts["entry"])
        hl = p.find(q("hyperlink"))
        hl.set(q("anchor"), bookmark)
        text_runs = [r for r in hl.findall(q("r")) if r.find(q("t")) is not None and r.find(q("rPr")) is not None
                     and r.find(q("rPr")).find(q("rStyle")) is not None]
        text_runs[0].find(q("t")).text = text
        for extra in text_runs[1:]:
            hl.remove(extra)
        for it in hl.iter(q("instrText")):
            if "PAGEREF" in (it.text or ""):
                it.text = f" PAGEREF {bookmark} \\h "
        page_t = [r.find(q("t")) for r in hl.findall(q("r")) if r.find(q("t")) is not None][-1]
        page_t.text = page
        content.append(p)
    content.append(parts["end"])
    return sdt


def bookmark_name(seed: str, i: int) -> str:
    base = 194000000 + (abs(hash(seed)) % 50000000)
    return f"_Toc{base + i}"


def build_semester(docs: list[ReportDoc], profile: Profile, enrollment: Enrollment, out: Path,
                   toc_pages: dict[str, int] | None = None) -> Path:
    document = _new_document()
    w = _Writer(document)
    for el in _fill_title(profile, enrollment):
        w._append(el)
    seed = f"{profile.id}:{enrollment.subject_id}"
    entries = [(f"Лабораторная работа №{d.lab_number}", bookmark_name(seed, i),
                str((toc_pages or {}).get(d.lab_number, ""))) for i, d in enumerate(docs)]
    w._append(_toc(entries))
    for i, d in enumerate(docs):
        w.page_break()
        write_lab(w, d, bookmark=entries[i][1])
    return _save(document, out, profile.doc_author or profile.full_name)
