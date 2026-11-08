"""То, что делает код, а не модель: нумерация объектов, подводки и подписи из одной темы, шапка отчёта."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from forlabs.core.models import Figure, LabSpec, Listing, ReportDoc, Table
from forlabs.report.morph import agree_short_participle

Kind = Literal["figure", "listing", "table"]
_CAPTION_WORD = {"figure": "Рисунок", "listing": "Листинг", "table": "Таблица"}
DEFAULT_ASSIGNMENT = ("Изучите методические указания и рекомендации по выполнению работы, "
                      "выполните практические задания.")


@dataclass(frozen=True)
class NumberedBlock:
    section_index: int
    block_index: int
    kind: Kind
    n: int


def numbered_blocks(doc: ReportDoc) -> list[NumberedBlock]:
    counters = {"figure": 0, "listing": 0, "table": 0}
    out: list[NumberedBlock] = []
    for si, sec in enumerate(doc.sections):
        for bi, b in enumerate(sec.blocks):
            kind = {Figure: "figure", Listing: "listing", Table: "table"}.get(type(b))
            if kind:
                counters[kind] += 1
                out.append(NumberedBlock(si, bi, kind, counters[kind]))
    return out


def clean_topic(topic: str) -> str:
    return " ".join(topic.split()).rstrip(" .;:")


def upper_first(s: str) -> str:
    return s[:1].upper() + s[1:]


def lower_first(s: str) -> str:
    if len(s) > 1 and (s[1].isupper() or not s[1].isalpha()):
        return s
    return s[:1].lower() + s[1:]


def figure_intro(topic: str, n: int) -> str:
    t = upper_first(clean_topic(topic))
    return f"{t} {agree_short_participle('представлен', t)} на рисунке {n}"


def listing_intro(topic: str, n: int) -> str:
    t = upper_first(clean_topic(topic))
    return f"{t} {agree_short_participle('представлен', t)} в листинге {n}"


def table_intro(topic: str, n: int) -> str:
    t = lower_first(clean_topic(topic))
    return f"В таблице {n} {agree_short_participle('представлен', t)} {t}"


def caption(kind: Kind, n: int, topic: str) -> str:
    return f"{_CAPTION_WORD[kind]} {n} – {upper_first(clean_topic(topic))}"


def build_header(spec: LabSpec, style: str, group: str) -> dict[str, str]:
    assignment = spec.assignment.strip() or DEFAULT_ASSIGNMENT
    h: dict[str, str] = {"Номер учебной группы": group, "Дата выполнения работы": spec.date}
    if style == "short":
        h["Тема"] = spec.topic
        h["Цель"] = spec.goal
        h["Задание"] = assignment
        if spec.equipment:
            h["Оснащение работы"] = spec.equipment
    else:
        h["Тема работы"] = spec.topic
        h["Цель работы"] = spec.goal
        if spec.equipment:
            h["Оснащение"] = spec.equipment
        h["Задание"] = assignment
    return h
