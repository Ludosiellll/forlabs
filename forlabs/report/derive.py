"""То, что делает код, а не модель: нумерация объектов, подводки и подписи из одной темы, шапка отчёта."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from forlabs.core.models import Figure, LabSpec, Listing, ReportDoc, Table
from forlabs.report.morph import agree_short_participle, ends_with_participle_clause

Kind = Literal["figure", "listing", "table"]
WORK_TITLES = {"lab": "Лабораторная работа", "practice": "Практическая работа", "session": "Практическое занятие"}
WORK_SHORT = {"lab": "ЛР", "practice": "ПР", "session": "ПЗ"}
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
            if kind == "table" and not any(b.rows):
                continue  # пустую таблицу не верстаем — и номер ей не нужен
            if kind:
                counters[kind] += 1
                out.append(NumberedBlock(si, bi, kind, counters[kind]))
    return out


def clean_topic(topic: str) -> str:
    return " ".join(topic.split()).rstrip(" .;:")


_FILE_START = re.compile(r"^[a-z0-9_.-]+\.[a-z0-9]{1,5}\b")


def upper_first(s: str) -> str:
    if _FILE_START.match(s):  # «index.html …» — имя файла, его регистр не трогаем
        return s
    return s[:1].upper() + s[1:]


def lower_first(s: str) -> str:
    if len(s) > 1 and (s[1].isupper() or not s[1].isalpha()):
        return s
    return s[:1].lower() + s[1:]


NBSP = "\u00a0"  # номер не уезжает на следующую строку отдельно от «рисунке», «листинге», «таблице»


def _closed(t: str) -> str:
    """Запятая после причастного оборота в конце темы: «…чисел, делящихся на 5, представлен…»."""
    return t + "," if ends_with_participle_clause(t) and not t.endswith(",") else t


def figure_intro(topic: str, n: int) -> str:
    t = upper_first(clean_topic(topic))
    return f"{_closed(t)} {agree_short_participle('представлен', t)} на рисунке{NBSP}{n}"


def listing_intro(topic: str, n: int) -> str:
    t = upper_first(clean_topic(topic))
    return f"{_closed(t)} {agree_short_participle('представлен', t)} в листинге{NBSP}{n}"


def table_intro(topic: str, n: int) -> str:
    t = lower_first(clean_topic(topic))
    return f"В таблице{NBSP}{n} {agree_short_participle('представлен', t)} {t}"


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


def work_title(kind: str) -> str:
    """Заголовок работы в отчёте: «Практическая работа» (без номера)."""
    return WORK_TITLES.get(kind, WORK_TITLES["lab"])


def work_short(kind: str) -> str:
    """Короткое имя для списков, папки сдачи и файла отчёта: ЛР, ПР, ПЗ."""
    return WORK_SHORT.get(kind, WORK_SHORT["lab"])


def key_suffix(kind: str) -> str:
    """Хвост номера, когда тот же номер уже занят работой другого вида: «5-пз» рядом с лабораторной «5»."""
    return work_short(kind).lower()


def lab_label(kind: str, number: str) -> str:
    """Как работа названа в списках: «ПЗ 5». Хвост «-пз» у занятого номера — служебный, его не показываем."""
    shown = re.sub(rf"-{key_suffix(kind)}(?=-|$)", "", number or "", count=1, flags=re.IGNORECASE)
    return f"{work_short(kind)} {shown}"


def key_label(number: str) -> str:
    """Подпись по одному номеру записи, когда вида работы под рукой нет (карточка участника у администратора):
    «7-пз» → «ПЗ 7», без хвоста — «ЛР 7»."""
    m = re.search(r"-(лр|пр|пз)(?=-|$)", number or "", re.IGNORECASE)
    kind = {"лр": "lab", "пр": "practice", "пз": "session"}[m.group(1).lower()] if m else "lab"
    return lab_label(kind, number)


_WORK_HEADING = re.compile(
    r"^(?:[^|\n]{0,80}\|[ \t]*)*[ \t]*(?:\d+[.)][ \t]*)?"  # ячейка таблицы слева, номер пункта
    r"(Отч[её]т[ \t]+(?:по|о)[ \t]+(?:выполнении[ \t]+)?|к[ \t]+|на[ \t]+выполнение[ \t]+)?"
    r"(?:(лабораторн\w*[ \t]+работ\w*)|(практическ\w*[ \t]+работ\w*)|(практическ\w*[ \t]+заняти\w*)|(ЛР)|(ПР)|(ПЗ))"
    r"(?![А-Яа-яЁёA-Za-z])[ \t]*(?:№|N|No\.?)?[ \t]*(\d+(?:[.\-]\d+)?)?"
    r"(?![ \t]*(?:час|ак\w*[ \t]*час|мин|стр|балл|недел|%))", re.IGNORECASE | re.MULTILINE)
_HEADING_KINDS = ("lab", "practice", "session", "lab", "practice", "session")
_NOMINATIVE = re.compile(r"(?:лабораторная|практическая)[ \t]+работа|практическое[ \t]+занятие", re.IGNORECASE)


def detect_work_kind(text: str, number: str | None) -> str:
    """Вид работы по заголовку методички («Практическое занятие №3», «Отчёт по практической работе №6», «ПЗ№4»).
    Считаются только заголовки с начала строки (или ячейки таблицы): упоминание посреди текста («из практической
    работы №6») вид не меняет. Название в косвенном падеже — заголовок, только если перед ним «Отчёт по», «к»,
    «на выполнение»: иначе это перенесённая строка обычного предложения. В сборнике берётся заголовок с номером
    этой работы, иначе первый в тексте; заголовка нет — лабораторная работа. Сборник, где под одним номером есть
    и лабораторная, и практическая, по номеру не различить — вид тогда выбирает студент."""
    want = (number or "").strip().rstrip(".")
    first = None
    for m in _WORK_HEADING.finditer(text or ""):
        i = next(k for k in range(6) if m.group(k + 2))
        n = (m.group(8) or "").rstrip(".-")
        if i >= 3 and not n:  # «ЛР», «ПР», «ПЗ» без номера — не заголовок, а часть слова или фразы («ПР 2 часа»)
            continue
        if i < 3 and not m.group(1) and not _NOMINATIVE.fullmatch(m.group(i + 2)):
            continue
        first = first or _HEADING_KINDS[i]
        if want and n == want:
            return _HEADING_KINDS[i]
    return first or "lab"


def works_phrase(kinds) -> str:
    """Строка титульного листа общего отчёта: «по лабораторным и практическим работам»."""
    kinds = set(kinds) or {"lab"}
    works = [w for k, w in (("lab", "лабораторным"), ("practice", "практическим")) if k in kinds]
    parts = [" и ".join(works) + " работам"] if works else []
    if "session" in kinds:
        parts.append("практическим занятиям")
    return "по " + " и ".join(parts)
