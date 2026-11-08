"""Образцы абзацев, снятые с эталонных отчётов (pPr/rPr по ролям)."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from lxml import etree

from forlabs.render.conformance import q

ASSETS = Path(__file__).parent / "assets"
BASE_DOCX = ASSETS / "base.docx"
ROLES = ("lab_heading", "header_field", "results_title", "task_heading", "body", "dash_item", "numbered_item",
         "figure_ref", "image", "figure_caption", "listing_ref", "listing_caption", "code", "code_blank",
         "continuation", "table_ref", "table_caption", "table_cell", "cq_title", "cq_question", "cq_answer",
         "conclusion", "page_break")
TEXT_WIDTH_TWIPS = 9345  # 210 − 30 − 15 мм, как сетка таблиц в эталоне
TEXT_WIDTH_CM = 16.5


@dataclass(frozen=True)
class Exemplar:
    ppr: etree._Element | None
    rpr: etree._Element | None
    label_rpr: etree._Element | None

    def new_ppr(self):
        return copy.deepcopy(self.ppr) if self.ppr is not None else None

    def new_rpr(self, label: bool = False):
        src = self.label_rpr if label else self.rpr
        return copy.deepcopy(src) if src is not None else None


_PARSER = etree.XMLParser(remove_blank_text=True)


@lru_cache(maxsize=1)
def _root():
    return etree.parse(str(ASSETS / "exemplars.xml"), _PARSER).getroot()


@lru_cache(maxsize=None)
def exemplar(role: str) -> Exemplar:
    el = _root().find(f"role[@name='{role}']")
    if el is None:
        raise KeyError(f"Нет образца для роли {role}")
    ppr = el.find(q("pPr"))
    label = el.find("label")
    value = el.find("value")
    return Exemplar(ppr=ppr,
                    rpr=value.find(q("rPr")) if value is not None else None,
                    label_rpr=label.find(q("rPr")) if label is not None else None)


def table_props():
    t = _root().find("table")
    return copy.deepcopy(t.find(q("tblPr"))), copy.deepcopy(t.find(q("trPr")))


def toc_parts() -> dict:
    t = _root().find("toc")
    parts = {p.get("name"): copy.deepcopy(p.find(q("p"))) for p in t.findall("part")}
    parts["sdtPr"] = copy.deepcopy(t.find(q("sdtPr")))
    end_pr = t.find(q("sdtEndPr"))
    parts["sdtEndPr"] = copy.deepcopy(end_pr) if end_pr is not None else None
    return parts


@lru_cache(maxsize=1)
def title_xml() -> bytes:
    return (ASSETS / "title.xml").read_bytes()
