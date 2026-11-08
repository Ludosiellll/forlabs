"""Снимает с эталонных отчётов образцы абзацев и канон оформления.

Запуск (один раз, эталоны в репозиторий не кладутся — в них личные данные):
    python tools/extract_exemplars.py BASE.docx [OTHER.docx ...]

BASE.docx — отчёт, из которого берутся стили, тема, настройки и титул.
Результат: forlabs/render/assets/{base.docx, exemplars.xml, canon.json, title.xml}.
"""

from __future__ import annotations

import copy
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from forlabs.render.conformance import DocxModel, iter_paragraphs, q, W_NS  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "forlabs" / "render" / "assets"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
ROLES = ["lab_heading", "header_field", "results_title", "task_heading", "body", "dash_item", "numbered_item",
         "figure_ref", "image", "figure_caption", "listing_ref", "listing_caption", "code", "code_blank",
         "continuation", "table_ref", "table_caption", "table_cell", "cq_title", "cq_question", "cq_answer",
         "conclusion", "page_break"]
LABEL_ROLES = {"header_field", "task_heading", "conclusion"}


def strip_ids(el):
    for e in el.iter():
        for a in list(e.attrib):
            if "rsid" in a or a.startswith("{%s}" % W14):
                del e.attrib[a]
    for tag in ("lastRenderedPageBreak", "proofErr"):
        for e in list(el.iter(q(tag))):
            e.getparent().remove(e)
    return el


def run_text(r) -> str:
    return "".join(t.text or "" for t in r.iter(q("t")))


def pick_runs(p, role):
    runs = [r for r in p.iter(q("r")) if run_text(r).strip()]
    if role == "image":
        runs = [r for r in p.iter(q("r")) if r.find(".//" + q("drawing")) is not None]
    label = value = None
    for r in runs:
        rp = r.find(q("rPr"))
        bold = rp is not None and rp.find(q("b")) is not None and rp.find(q("b")).get(q("val")) not in ("0", "false")
        if bold and label is None:
            label = rp
        if not bold and value is None:
            value = rp
    if role not in LABEL_ROLES:
        first = runs[0].find(q("rPr")) if runs else None
        return None, first
    return label, value


def main(paths: list[str]) -> None:
    base = paths[0]
    by_role: dict[str, Counter] = defaultdict(Counter)
    samples: dict[tuple, list] = defaultdict(list)
    models = []
    for i, path in enumerate(paths):
        m = DocxModel(path)
        models.append(m)
        for role, key, p in iter_paragraphs(m):
            by_role[role][key] += 1
            samples[(role, key)].append((i, p))

    canon = {}
    ex_root = etree.Element("exemplars", nsmap={"w": W_NS})
    for role in ROLES:
        if not by_role[role]:
            print("нет образца для роли", role)
            continue
        key, count = by_role[role].most_common(1)[0]
        total = sum(by_role[role].values())
        canon[role] = list(key)
        cands = sorted(samples[(role, key)], key=lambda t: t[0])  # сначала из базового документа
        label = value = None
        chosen = cands[0][1]
        for _, p in cands:
            label, value = pick_runs(p, role)
            if (role not in LABEL_ROLES or (label is not None and value is not None)) and (
                    value is not None or role in ("code_blank", "page_break", "image")):
                chosen = p
                break
        el = etree.SubElement(ex_root, "role", name=role, share=f"{count}/{total}")
        ppr = chosen.find(q("pPr"))
        if ppr is not None:
            el.append(strip_ids(copy.deepcopy(ppr)))
        if label is not None:
            lab = etree.SubElement(el, "label")
            lab.append(strip_ids(copy.deepcopy(label)))
        if value is not None:
            val = etree.SubElement(el, "value")
            val.append(strip_ids(copy.deepcopy(value)))
        if role == "page_break":
            br_run = next((r for r in chosen.iter(q("r")) if r.find(q("br")) is not None), None)
            if br_run is not None and br_run.find(q("rPr")) is not None:
                val = etree.SubElement(el, "value")
                val.append(strip_ids(copy.deepcopy(br_run.find(q("rPr")))))
        print(f"{role:16s} {count:5d}/{total:<5d} {key}")

    # таблица: свойства из первой таблицы базового документа
    bdoc = models[0].doc
    tbl = bdoc.find(".//" + q("tbl"))
    if tbl is not None:
        t_el = etree.SubElement(ex_root, "table")
        t_el.append(strip_ids(copy.deepcopy(tbl.find(q("tblPr")))))
        tr = tbl.find(q("tr"))
        if tr is not None and tr.find(q("trPr")) is not None:
            t_el.append(strip_ids(copy.deepcopy(tr.find(q("trPr")))))

    # содержание: заголовок, первая запись (начало поля), обычная запись, закрывающий абзац
    sdt = bdoc.find(".//" + q("sdt"))
    if sdt is not None:
        toc = etree.SubElement(ex_root, "toc")
        sdt_pr = sdt.find(q("sdtPr"))
        toc.append(strip_ids(copy.deepcopy(sdt_pr)))
        if sdt.find(q("sdtEndPr")) is not None:
            toc.append(strip_ids(copy.deepcopy(sdt.find(q("sdtEndPr")))))
        ps = sdt.find(q("sdtContent")).findall(q("p"))
        for name, p in (("title", ps[0]), ("first", ps[1]), ("entry", ps[2]), ("end", ps[-1])):
            holder = etree.SubElement(toc, "part", name=name)
            holder.append(strip_ids(copy.deepcopy(p)))

    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "exemplars.xml").write_bytes(etree.tostring(ex_root, encoding="utf-8", xml_declaration=True,
                                                          pretty_print=True))
    (ASSETS / "canon.json").write_text(json.dumps({"roles": canon}, ensure_ascii=False, indent=1), encoding="utf-8")
    write_title(models[0])
    write_base(base)
    print("готово:", ASSETS)


def merge_runs(p):
    runs = p.findall(q("r"))
    prev = None
    for r in runs:
        children = [c for c in r if c.tag != q("rPr")]
        simple = len(children) == 1 and children[0].tag == q("t")
        if prev is not None and simple:
            rp, pp = r.find(q("rPr")), prev.find(q("rPr"))
            if etree.tostring(rp if rp is not None else etree.Element("x")) == etree.tostring(
                    pp if pp is not None else etree.Element("x")):
                pt = prev.find(q("t"))
                pt.text = (pt.text or "") + (children[0].text or "")
                pt.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                p.remove(r)
                continue
        prev = r if simple else None


TITLE_REPLACE = [
    (r"МИНИСТЕРСТВО ОБРАЗОВАНИЯ РЕСПУБЛИКИ БЕЛАРУСЬ", "{{COLLEGE1}}"),
    (r"ФИЛИАЛ БГТУ", "{{COLLEGE2}}"),
    (r"«БОБРУЙСКИЙ ГОСУДАРСТВЕННЫЙ ЛЕСОТЕХНИЧЕСКИЙ КОЛЛЕДЖ»", "{{COLLEGE3}}"),
    (r" 5-04-0612-02", " {{SPECIALTY_CODE}}"),
    (r"«Разработка и сопровождение", "{{SPEC1}}"),
    (r"^ программного обеспечения ?$", "{{SPEC2}}"),
    (r"^ информационных технологий»$", "{{SPEC3}}"),
    (r"Выполнил", "{{PERFORMED}}"),
    (r"РС02-23", "{{GROUP}}"),
    (r"Бобруйск", "{{CITY}}"),
    (r"^\d{4}/\d{4}$", "{{YEAR}}"),
]


def write_title(model: DocxModel) -> None:
    body = model.doc.find(q("body"))
    root = etree.Element("title", nsmap={"w": W_NS})
    for el in body:
        if el.tag != q("p"):
            break
        p = strip_ids(copy.deepcopy(el))
        for b in list(p.iter(q("bookmarkStart"))) + list(p.iter(q("bookmarkEnd"))):
            b.getparent().remove(b)
        merge_runs(p)
        texts = [t for t in p.iter(q("t"))]
        full = "".join(t.text or "" for t in texts)
        if full.strip().startswith("Учебный предмет:"):
            last = texts[-1]
            last.text = re.sub(r"\S.*$", "{{SUBJECT}}", last.text or "")
        for t in texts:
            for pat, rep in TITLE_REPLACE:
                t.text = re.sub(pat, rep, t.text or "")
        if "Руководитель" in full or "уч-ся" in full:
            # всё, что после последней табуляции, — фамилия справа (может быть разбита на несколько фрагментов)
            placeholder = "{{TEACHER}}" if "Руководитель" in full else "{{NAME}}"
            runs = p.findall(q("r"))
            last_tab = max(i for i, r in enumerate(runs) if r.find(q("tab")) is not None)
            tail = [r for r in runs[last_tab + 1:] if r.find(q("t")) is not None]
            lead = re.match(r"\s*", "".join(r.find(q("t")).text or "" for r in tail)).group(0)
            tail[0].find(q("t")).text = lead + placeholder
            tail[0].find(q("t")).set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            for r in tail[1:]:
                p.remove(r)
        root.append(p)
    (ASSETS / "title.xml").write_bytes(etree.tostring(root, encoding="utf-8", xml_declaration=True, pretty_print=True))


def write_base(base: str) -> None:
    out = ASSETS / "base.docx"
    with zipfile.ZipFile(base) as zin:
        doc = etree.fromstring(zin.read("word/document.xml"))
        body = doc.find(q("body"))
        sect = body.find(q("sectPr"))
        for el in list(body):
            body.remove(el)
        body.append(strip_ids(copy.deepcopy(sect)))
        rels = etree.fromstring(zin.read("word/_rels/document.xml.rels"))
        keep_types = ("styles", "settings", "webSettings", "fontTable", "theme", "numbering", "footnotes",
                      "endnotes", "header", "footer", "customXml")
        removed = set()
        for rel in list(rels):
            t = rel.get("Type").rsplit("/", 1)[-1]
            if t not in keep_types:
                removed.add(rel.get("Target"))
                rels.remove(rel)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                name = item.filename
                if name.startswith("word/media/"):
                    continue
                data = zin.read(name)
                if name == "word/document.xml":
                    data = etree.tostring(doc, encoding="UTF-8", xml_declaration=True, standalone=True)
                elif name == "word/_rels/document.xml.rels":
                    data = etree.tostring(rels, encoding="UTF-8", xml_declaration=True, standalone=True)
                elif name == "docProps/core.xml":
                    s = data.decode("utf-8")
                    s = re.sub(r"<dc:creator>.*?</dc:creator>", "<dc:creator></dc:creator>", s)
                    s = re.sub(r"<cp:lastModifiedBy>.*?</cp:lastModifiedBy>", "<cp:lastModifiedBy></cp:lastModifiedBy>", s)
                    s = re.sub(r"<dc:title>.*?</dc:title>", "<dc:title></dc:title>", s)
                    s = re.sub(r"<cp:revision>.*?</cp:revision>", "<cp:revision>1</cp:revision>", s)
                    data = s.encode("utf-8")
                elif name == "docProps/app.xml":
                    s = data.decode("utf-8")
                    for tag in ("Company", "Manager"):
                        s = re.sub(rf"<{tag}>.*?</{tag}>", f"<{tag}></{tag}>", s)
                    s = re.sub(r"<TotalTime>.*?</TotalTime>", "<TotalTime>0</TotalTime>", s)
                    data = s.encode("utf-8")
                zout.writestr(item, data)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
