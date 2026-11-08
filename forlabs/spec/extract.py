"""Разбор методички в LabSpec: модель извлекает, код нумерует и проверяет дословность."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from forlabs.core.models import Enrollment, Finding, LabSpec, Task
from forlabs.core.subjects import Subject
from forlabs.core.text import normalize_for_compare
from forlabs.ingest.extract import SourceText, join_hyphenation
from forlabs.spec.prompts import SPEC_SYSTEM, SPEC_USER

MAX_SOURCE_CHARS = 200_000  # ~100 тыс. токенов: дальше дорого и упирается в лимит модели


class VariantPick(BaseModel):
    appendix: str = ""
    basis: str = "journal"
    number: int | str | None = None
    text: str = ""


class RawSpec(BaseModel):
    number: str | None = None
    topic: str = ""
    goal: str = ""
    equipment: str | None = None
    assignment: str = ""
    main_tasks: list[str] = Field(default_factory=list)
    extra_tasks: list[str] = Field(default_factory=list)
    variant_text: str | None = None
    variants: list[VariantPick] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)

    @field_validator("number", mode="before")
    @classmethod
    def _num(cls, v):
        return None if v is None else str(v)

    @field_validator("main_tasks", "extra_tasks", "questions", "variants", mode="before")
    @classmethod
    def _lists(cls, v):
        return [] if v is None else v


_LEADING_NUMBER = re.compile(r"^\s*(?:\d+[.)]|[-–—•])\s+")
_LABEL = re.compile(r"^\s*(?:Тема(?:\s+работы)?|Цел[ьи](?:\s+работы)?|Оснащение(?:\s+работы)?|Задание)\s*:\s*",
                    re.IGNORECASE)


def one_line(s: str) -> str:
    s = join_hyphenation(s)
    s = " ".join(s.split())
    s = re.sub(r"\s+([,.;:!?)»])", r"\1", s)  # пробел перед знаком препинания из PDF
    return re.sub(r"([(«])\s+", r"\1", s)


def _starts_lower(s: str) -> bool:
    return bool(s) and s[0].isalpha() and s[0].islower()


_SUB_MARKER = re.compile(r"^\s*([а-яёa-z]\)|\d+\))\s+")


def merge_subitems(items: list[str]) -> list[str]:
    """Подпункты («описать …;») — часть предыдущего задания, если оно кончается двоеточием.

    Буквенные и скобочные маркеры методички («а)», «1)») сохраняются; тире, точки и пункты без маркера
    нумеруются «1) …». Строчное задание без такого «родителя» остаётся отдельным."""
    out: list[str] = []
    k = 0
    for raw in items:
        line = one_line(raw)
        marker = _SUB_MARKER.match(line)
        text = clean_item(line[marker.end():] if marker else line)
        opens = bool(out) and (out[-1].endswith(":") or k > 0)
        if opens and (marker or _starts_lower(text)):
            k += 1
            out[-1] = f"{out[-1]} {marker.group(1) if marker else f'{k})'} {text}"
        else:
            k = 0
            out.append(text)
    return out


_BASIS = {"workplace": "по номеру рабочего места", "journal": "по журналу"}


def variant_block(picks: list[VariantPick]) -> str | None:
    lines = []
    for v in picks:
        if not v.text.strip():
            continue
        basis = _BASIS.get(v.basis, "")
        meta = ", ".join(x for x in (basis, f"вариант {v.number}" if v.number not in (None, "") else "") if x)
        head = (v.appendix.strip() or "Вариант") + (f" ({meta})" if meta else "")
        lines.append(f"{head}: {one_line(v.text)}")
    return "\n".join(lines) or None


def clean_item(s: str) -> str:
    return _LEADING_NUMBER.sub("", one_line(s)).strip()


def clean_field(s: str | None) -> str:
    return _LABEL.sub("", one_line(s or "")).strip()


def verify_verbatim(spec: LabSpec, src: SourceText) -> list[Finding]:
    hay = normalize_for_compare(src.text)
    out = []
    checks = [(f"Задание {t.n}", t.text, "error") for t in spec.tasks]
    checks += [(f"Вопрос {i}", q, "error") for i, q in enumerate(spec.questions, start=1)]
    checks += [("Тема", spec.topic, "warn"), ("Цель", spec.goal, "warn")]
    for loc, text, severity in checks:
        n = normalize_for_compare(text)
        if n and n not in hay:
            out.append(Finding(stage="spec", rule_id="spec.not_verbatim", severity=severity, location=loc, text=text,
                               message="Формулировки нет в методичке дословно — проверь и поправь"))
    return out


def _wishes_block(wishes: str) -> str:
    if not wishes.strip():
        return ""
    return ("Пожелания студента и отклонения от методички (учти при выборе варианта; задания и вопросы всё равно "
            f"переписывай из методички дословно):\n{wishes.strip()}\n")


def extract_spec(src: SourceText, subject: Subject, enrollment: Enrollment, llm, lab_number: str | None,
                 date: str, wishes: str = "") -> tuple[LabSpec, list[Finding]]:
    variant = enrollment.variant if enrollment.variant is not None else subject.default_variant
    workplace = enrollment.workplace if enrollment.workplace is not None else variant
    hint = f" Номер работы: {lab_number}." if lab_number else ""
    messages = [
        {"role": "system", "content": SPEC_SYSTEM.format(variant=variant, workplace=workplace)},
        {"role": "user", "content": SPEC_USER.format(subject=subject.name, variant=variant, number_hint=hint,
                                                     workplace=enrollment.workplace or "не указан",
                                                     wishes_block=_wishes_block(wishes),
                                                     text=src.text[:MAX_SOURCE_CHARS])},
    ]
    raw: RawSpec = llm.json(messages, RawSpec, temperature=0.0)
    main = merge_subitems([t for t in raw.main_tasks if t.strip()])
    extra = merge_subitems([t for t in raw.extra_tasks if t.strip()])
    tasks = [Task(n=i, text=t, kind="main") for i, t in enumerate(main, start=1)]
    tasks += [Task(n=len(main) + i, text=t, kind="extra") for i, t in enumerate(extra, start=1)]
    assignment = clean_field(raw.assignment)
    if assignment and any(normalize_for_compare(assignment) in normalize_for_compare(t.text) or
                          normalize_for_compare(t.text) in normalize_for_compare(assignment) for t in tasks):
        assignment = ""  # это было первое задание, а не общая фраза
    spec = LabSpec(
        subject_id=subject.id,
        number=(lab_number or raw.number or "").strip() or "1",
        date=date,
        topic=clean_field(raw.topic).rstrip(". "),
        goal=clean_field(raw.goal).rstrip(". "),
        equipment=clean_field(raw.equipment).rstrip(". ") or None,
        assignment=assignment,
        tasks=tasks,
        variant=variant,
        workplace=enrollment.workplace,
        variant_text=variant_block(raw.variants) or (one_line(raw.variant_text) if raw.variant_text else None),
        questions=[clean_item(q) for q in raw.questions if q.strip()],
        wishes=wishes.strip() or None,
        source_text=src.text,
    )
    findings = verify_verbatim(spec, src)
    if any(v.basis == "workplace" for v in raw.variants) and enrollment.workplace is None:
        findings.append(Finding(stage="spec", rule_id="spec.workplace_missing", severity="warn", location="Вариант",
                                message="Методичка выбирает вариант по номеру рабочего места — укажи его в профиле "
                                        "(пока взят вариант по журналу)"))
    if not spec.tasks:
        findings.append(Finding(stage="spec", rule_id="spec.no_tasks", location="Задания",
                                message="В методичке не нашлось заданий — добавь их вручную"))
    return spec, findings
