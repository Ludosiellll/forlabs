"""Сверка .docx с каноном: роль каждого абзаца + его эффективное форматирование
(docDefaults → цепочка стилей → прямое форматирование)."""

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

from forlabs.core.models import Finding

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS
CANON_FILE = Path(__file__).parent / "assets" / "canon.json"

KEY_FIELDS = ("style", "jc", "first_mm", "left_mm", "hanging_mm", "before_pt", "after_pt", "line", "rule",
              "fonts", "bold", "border")


def q(tag: str) -> str:
    return W + tag


class DocxModel:
    def __init__(self, path: str | Path):
        with zipfile.ZipFile(path) as z:
            self.doc = etree.fromstring(z.read("word/document.xml"))
            st = etree.fromstring(z.read("word/styles.xml"))
        self.styles = {s.get(q("styleId")): s for s in st.findall(q("style"))}
        dd = st.find(q("docDefaults"))
        self.dppr = dd.find(".//" + q("pPr")) if dd is not None else None
        self.drpr = dd.find(".//" + q("rPr")) if dd is not None else None
        self.default_pstyle = next((k for k, s in self.styles.items()
                                    if s.get(q("type")) == "paragraph" and s.get(q("default")) == "1"), None)

    def style_name(self, sid: str | None) -> str:
        s = self.styles.get(sid or "")
        if s is None:
            return sid or ""
        n = s.find(q("name"))
        return n.get(q("val")) if n is not None else sid

    def chain(self, sid: str | None) -> list:
        out, seen = [], set()
        while sid and sid in self.styles and sid not in seen:
            seen.add(sid)
            s = self.styles[sid]
            out.append(s)
            b = s.find(q("basedOn"))
            sid = b.get(q("val")) if b is not None else None
        return list(reversed(out))

    def para_style(self, p) -> str | None:
        ps = p.find(q("pPr"))
        if ps is not None and ps.find(q("pStyle")) is not None:
            return ps.find(q("pStyle")).get(q("val"))
        return self.default_pstyle

    def ppr_layers(self, p) -> list:
        sid = self.para_style(p)
        layers = [self.dppr] + [s.find(q("pPr")) for s in self.chain(sid)] + [p.find(q("pPr"))]
        return [x for x in layers if x is not None]

    def rpr_layers(self, p, r) -> list:
        sid = self.para_style(p)
        layers = [self.drpr] + [s.find(q("rPr")) for s in self.chain(sid)]
        rp = r.find(q("rPr"))
        if rp is not None and rp.find(q("rStyle")) is not None:
            layers += [s.find(q("rPr")) for s in self.chain(rp.find(q("rStyle")).get(q("val")))]
        layers.append(rp)
        return [x for x in layers if x is not None]


def effective_ppr(layers) -> dict:
    e = {"jc": "left", "first": 0, "left": 0, "hanging": 0, "before": 0, "after": 0, "line": 240,
         "rule": "auto", "border": ""}
    for lay in layers:
        jc = lay.find(q("jc"))
        if jc is not None:
            e["jc"] = jc.get(q("val"))
        ind = lay.find(q("ind"))
        if ind is not None:
            if ind.get(q("firstLine")) is not None:
                e["first"], e["hanging"] = int(ind.get(q("firstLine"))), 0
            if ind.get(q("hanging")) is not None:
                e["hanging"], e["first"] = int(ind.get(q("hanging"))), 0
            for k in ("left", "start"):
                if ind.get(q(k)) is not None:
                    e["left"] = int(ind.get(q(k)))
        sp = lay.find(q("spacing"))
        if sp is not None:
            for k in ("before", "after", "line"):
                if sp.get(q(k)) is not None:
                    e[k] = int(sp.get(q(k)))
            if sp.get(q("lineRule")) is not None:
                e["rule"] = sp.get(q("lineRule"))
        bd = lay.find(q("pBdr"))
        if bd is not None:
            parts = []
            for side in ("top", "left", "bottom", "right"):
                el = bd.find(q(side))
                if el is not None:
                    parts.append(f"{side}:{el.get(q('val'))}/{el.get(q('sz'))}/{el.get(q('space'))}")
            e["border"] = ",".join(parts)
    return e


def effective_rpr(layers) -> dict:
    e = {"font": None, "sz": None, "b": False}
    for lay in layers:
        f = lay.find(q("rFonts"))
        if f is not None:
            v = f.get(q("ascii")) or (("theme:" + f.get(q("asciiTheme"))) if f.get(q("asciiTheme")) else None)
            if v:
                e["font"] = v
        s = lay.find(q("sz"))
        if s is not None:
            e["sz"] = int(s.get(q("val"))) / 2
        b = lay.find(q("b"))
        if b is not None:
            e["b"] = b.get(q("val")) not in ("0", "false")
    return e


def _mm(twips: int) -> float:
    return round(twips / 56.7, 1)


_HEADER_LABEL = re.compile(r"^(Номер учебной группы|Дата выполнения работы|Тема работы|Тема|Цель работы|Цели|Цель|"
                           r"Задание|Оснащение работы|Оснащение)\s*:")


def classify(text: str, has_img: bool, fonts: list, style_name: str, prev: str, has_page_break: bool) -> str:
    t = text.strip()
    if has_img:
        return "image"
    if not t:
        if has_page_break:
            return "page_break"
        return "code_blank" if prev in ("code", "code_blank") else "empty"
    if style_name == "heading 1":
        return "lab_heading"
    if t == "Содержание":
        return "toc_title"
    if _HEADER_LABEL.match(t):
        return "header_field"
    if re.match(r"^Результаты выполнения работы:?$|^Выполнение заданий:?$", t):
        return "results_title"
    if re.match(r"^Задание\s+\d+\.", t):
        return "task_heading"
    if re.match(r"^(Ответы на контрольные вопросы|Контрольные вопросы):?$", t):
        return "cq_title"
    if t.startswith("Ответ:"):
        return "cq_answer"
    if t.startswith("Вывод:"):
        return "conclusion"
    if re.match(r"^Продолжение\s+(листинга|таблицы)\s+\d+", t):
        return "continuation"
    if re.match(r"^Рисунок\s+\d+\s*[–-]", t):
        return "figure_caption"
    if re.match(r"^Листинг\s+\d+\s*[–-]", t):
        return "listing_caption"
    if re.match(r"^Таблица\s+\d+\s*[–-]", t):
        return "table_caption"
    if fonts and all(f and ("Consolas" in f or "Courier" in f) for f in fonts):
        return "code"
    if re.search(r"(на рисунк\w*|рисунке)\s+[\d,\sи–-]+$", t):
        return "figure_ref"
    if re.search(r"в листинг\w*\s+[\d,\sи–-]+$", t):
        return "listing_ref"
    if re.search(r"(в таблиц\w*|таблице)\s+\d+", t) and len(t) < 200 and not t.endswith("."):
        return "table_ref"
    if re.match(r"^[-–—]\s", t):
        return "dash_item"
    if prev == "cq_title" or (prev == "cq_answer" and not t.startswith("Ответ")):
        return "cq_question"
    if re.match(r"^\d+[.)]\s", t):
        return "numbered_item"
    return "body"


def iter_paragraphs(model: DocxModel):
    """(role, key, element) для каждого абзаца документа."""
    body = model.doc.find(q("body"))
    prev, in_title = "start", True
    for p in body.iter(q("p")):
        anc = {a.tag for a in p.iterancestors()}
        in_tbl, in_sdt = q("tc") in anc, q("sdtContent") in anc
        text = "".join(t.text or "" for t in p.iter(q("t")))
        layers = model.ppr_layers(p)
        sid = model.para_style(p)
        sname = model.style_name(sid)
        has_img = p.find(".//" + q("drawing")) is not None or p.find(".//" + q("pict")) is not None
        has_pb = any(b.get(q("type")) == "page" for b in p.iter(q("br")))
        runs = [r for r in p.iter(q("r")) if "".join(t.text or "" for t in r.iter(q("t"))).strip()]
        reff = [effective_rpr(model.rpr_layers(p, r)) for r in runs]
        role = classify(text, has_img, [r["font"] for r in reff], sname, prev, has_pb)
        if in_sdt:
            role = "toc_title" if text.strip() == "Содержание" else "toc_entry"
        elif in_title:
            if sname == "heading 1":
                in_title = False
            elif role != "page_break":
                role = "title:" + role
        if in_tbl:
            role = "table_cell"
        e = effective_ppr(layers)
        fonts = sorted({(r["font"], r["sz"]) for r in reff}, key=str)
        bold = re.sub(r"(.)\1+", r"\1", "".join("B" if r["b"] else "r" for r in reff))
        key = (sname, e["jc"], _mm(e["first"]), _mm(e["left"]), _mm(e["hanging"]), e["before"] / 20,
               e["after"] / 20, e["line"], e["rule"], json.dumps(fonts, ensure_ascii=False), bold,
               e["border"] if role in ("code", "code_blank", "listing_caption", "continuation") else "")
        yield role, key, p
        if role not in ("empty",):
            prev = role.replace("title:", "")


def profile_docx(path: str | Path) -> dict[str, Counter]:
    model = DocxModel(path)
    out: dict[str, Counter] = defaultdict(Counter)
    for role, key, _ in iter_paragraphs(model):
        out[role][key] += 1
    return out


def load_canon() -> dict[str, tuple]:
    data = json.loads(CANON_FILE.read_text(encoding="utf-8"))
    return {role: tuple(v) for role, v in data["roles"].items()}


def key_dict(key: tuple) -> dict:
    return dict(zip(KEY_FIELDS, key))


def compare(path: str | Path, canon: dict[str, tuple] | None = None) -> list[Finding]:
    canon = canon or load_canon()
    model = DocxModel(path)
    out: list[Finding] = []
    for role, key, p in iter_paragraphs(model):
        if role.startswith("title:") or role in ("empty", "toc_entry", "toc_title"):
            continue
        expected = canon.get(role)
        if expected is None:
            continue
        if tuple(key) != tuple(expected):
            exp, act = key_dict(expected), key_dict(key)
            diff = {k: (exp[k], act[k]) for k in KEY_FIELDS if exp[k] != act[k]}
            text = "".join(t.text or "" for t in p.iter(q("t")))[:80]
            out.append(Finding(stage="render", rule_id="render.canon_mismatch", location=role, text=text,
                               message="Оформление отличается от эталона: " +
                                       "; ".join(f"{k}: ожидалось {v[0]}, получено {v[1]}" for k, v in diff.items())))
    return out
