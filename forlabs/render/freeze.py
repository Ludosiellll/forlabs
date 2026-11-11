"""Готовый документ без чёрных квадратов слева от абзацев.

Word рисует такой квадрат (при включённых знаках форматирования) у абзацев с настройками «не отрывать от
следующего», «не разрывать абзац» и «с новой страницы». Пока документ верстается, эти настройки нужны: подпись
держится со своим рисунком, таблицей или листингом, заголовок — со своим текстом. Когда разбивка на страницы уже
известна, они не нужны: там, где абзац ушёл на новую страницу из-за них, ставится обычный разрыв страницы, а сами
настройки снимаются. Разбивка от этого не меняется.

Где абзацы перенесены, говорит доводка: Word отмечает их закладками MARK_PREFIX…, без Word их находит
page_starts_from_pdf по PDF из LibreOffice.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

from forlabs.core.fs import durable_replace
from forlabs.render.conformance import q

MARK_PREFIX = "ForLabsPg"  # закладка Word: этот абзац начинает страницу из-за настроек — перед ним нужен разрыв
FLAGS = ("keepNext", "keepLines", "pageBreakBefore")
_FLAG_RE = re.compile(rb"<w:(?:keepNext|keepLines|pageBreakBefore)\b")


def _text(p) -> str:
    out = []
    for el in p.iter(q("t"), q("tab")):
        out.append(el.text or "" if el.tag == q("t") else " ")
    return re.sub(r"\s+", " ", "".join(out).replace("\xa0", " ")).strip()


def _flags(ppr) -> set[str]:
    return {f for f in FLAGS if ppr is not None and ppr.find(q(f)) is not None}


def _has_page_break(p) -> bool:
    return any(br.get(q("type")) == "page" for br in p.iter(q("br")))


def _break_run():
    r = etree.Element(q("r"))
    etree.SubElement(r, q("br")).set(q("type"), "page")
    return r


def _body_items(body) -> list:
    """Абзацы и таблицы тела по порядку, включая содержимое блоков (содержание лежит в w:sdt)."""
    out = []
    for el in body:
        if el.tag in (q("p"), q("tbl")):
            out.append(el)
        elif el.tag == q("sdt"):
            content = el.find(q("sdtContent"))
            if content is not None:
                out.extend(_body_items(content))
    return out


def _break_before(items: list, i: int) -> bool:
    """Разрыв страницы перед items[i]: в конце предыдущего абзаца, а после таблицы — в начале самого абзаца."""
    if i <= 0:
        return False
    prev, cur = items[i - 1], items[i]
    if prev.tag == q("p"):
        if _has_page_break(prev):
            return False
        prev.append(_break_run())
        return True
    if _has_page_break(cur):
        return False
    ppr = cur.find(q("pPr"))
    (ppr.addnext if ppr is not None else lambda r: cur.insert(0, r))(_break_run())
    return True


def _styles_with_flags(styles_root) -> set[str]:
    out = set()
    for st in styles_root.iter(q("style")):
        if _flags(st.find(q("pPr"))):
            out.add(st.get(q("styleId")))
    return out


def keepers(docx: Path) -> list[tuple[int, str]]:
    """Абзацы верхнего уровня, которые настройки могут перенести на новую страницу: (номер среди абзацев и таблиц
    тела, текст). Строки листингов («не разрывать абзац» без «не отрывать») сюда не входят: листинг, перешедший на
    новую страницу, — обычный переход."""
    with zipfile.ZipFile(docx) as z:
        root = etree.fromstring(z.read("word/document.xml"))
        styled = _styles_with_flags(etree.fromstring(z.read("word/styles.xml"))) if "word/styles.xml" in z.namelist() \
            else set()
    out = []
    for i, el in enumerate(_body_items(root.find(q("body")))):
        if el.tag != q("p"):
            continue
        ppr = el.find(q("pPr"))
        flags = _flags(ppr)
        style = ppr.find(q("pStyle")) if ppr is not None else None
        if style is not None and style.get(q("val")) in styled:
            flags.add("keepNext")
        if flags - {"keepLines"} and _text(el):
            out.append((i, _text(el)))
    return out


def page_starts_from_pdf(docx: Path, pdf: Path) -> set[int]:
    """Без Word: абзацы-«держатели» (заголовки, подписи, ссылки на рисунки и листинги), с которых в PDF начинается
    страница. Первая строка страницы сравнивается с началом текста абзаца — по порядку, без возврата назад."""
    import pymupdf

    cands = keepers(docx)
    found: set[int] = set()
    pos = 0
    with pymupdf.open(pdf) as doc:
        for page in list(doc)[1:]:
            h = page.rect.height
            lines = []
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") == 1:  # картинка
                    lines.append((block["bbox"][1], None))
                    continue
                for line in block.get("lines", []):
                    text = " ".join(s["text"] for s in line["spans"]).strip()
                    if text:
                        lines.append((line["bbox"][1], text))
            # номер страницы в колонтитуле — не текст страницы
            body = sorted((y, t) for y, t in lines if 0.06 * h < y < 0.94 * h and not (t and t.isdigit()))
            if not body or body[0][1] is None:  # страница начинается с рисунка
                continue
            first = re.sub(r"\s+", " ", body[0][1].replace("\xa0", " ")).strip()
            head = first[:40]
            for k in range(pos, len(cands)):
                i, text = cands[k]
                if head and text.startswith(head):
                    found.add(i)
                    pos = k + 1
                    break
    return found


def apply(docx: Path, before: set[int] | frozenset[int] = frozenset()) -> int:
    """Разрывы страниц вместо настроек и сами настройки — прочь. before — номера абзацев верхнего уровня (среди
    абзацев и таблиц тела), перед которыми нужен разрыв; к ним добавляются отмеченные закладками Word и абзацы
    «с новой страницы». Возвращает число поставленных разрывов."""
    docx = Path(docx)
    with zipfile.ZipFile(docx) as z:
        infos = [(i, z.read(i.filename)) for i in z.infolist()]
    parts = {i.filename: data for i, data in infos}
    root = etree.fromstring(parts["word/document.xml"])
    items = _body_items(root.find(q("body")))
    targets = set(before)
    marked_ids = set()
    for bs in list(root.iter(q("bookmarkStart"))):
        if (bs.get(q("name")) or "").startswith(MARK_PREFIX):
            marked_ids.add(bs.get(q("id")))
            p = next((a for a in bs.iterancestors(q("p"))), None)
            if p is not None and any(p is el for el in items):  # в ячейке таблицы — не наш случай
                targets.add(next(i for i, el in enumerate(items) if el is p))
            bs.getparent().remove(bs)
    for be in list(root.iter(q("bookmarkEnd"))):
        if be.get(q("id")) in marked_ids:
            be.getparent().remove(be)
    for i, el in enumerate(items):
        if el.tag == q("p") and "pageBreakBefore" in _flags(el.find(q("pPr"))):
            targets.add(i)
    made = sum(_break_before(items, i) for i in sorted(targets))
    for ppr in root.iter(q("pPr")):
        for f in FLAGS:
            for el in ppr.findall(q(f)):
                ppr.remove(el)
    parts["word/document.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    if "word/styles.xml" in parts:
        st = etree.fromstring(parts["word/styles.xml"])
        for ppr in st.iter(q("pPr")):
            for f in FLAGS:
                for el in ppr.findall(q(f)):
                    ppr.remove(el)
        parts["word/styles.xml"] = etree.tostring(st, xml_declaration=True, encoding="UTF-8", standalone=True)
    tmp = docx.with_name(docx.name + ".freeze.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
        for info, _ in infos:
            out.writestr(info, parts[info.filename])
    durable_replace(tmp, docx)
    return made


def flags_left(docx: Path) -> int:
    """Сколько настроек «с квадратом» осталось в документе и стилях."""
    with zipfile.ZipFile(docx) as z:
        return sum(len(_FLAG_RE.findall(z.read(n))) for n in ("word/document.xml", "word/styles.xml")
                   if n in z.namelist())
