"""Разбор методички в LabSpec: модель извлекает, код нумерует и проверяет дословность."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from forlabs.core.models import Enrollment, Finding, LabSpec, Task
from forlabs.core.subjects import Subject
from forlabs.core.text import normalize_for_compare
from forlabs.ingest.extract import SourceText
from forlabs.spec.prompts import SPEC_SYSTEM, SPEC_USER

MAX_SOURCE_CHARS = 400_000


class RawSpec(BaseModel):
    number: str | None = None
    topic: str = ""
    goal: str = ""
    equipment: str | None = None
    assignment: str = ""
    main_tasks: list[str] = Field(default_factory=list)
    extra_tasks: list[str] = Field(default_factory=list)
    variant_text: str | None = None
    questions: list[str] = Field(default_factory=list)

    @field_validator("number", mode="before")
    @classmethod
    def _num(cls, v):
        return None if v is None else str(v)

    @field_validator("main_tasks", "extra_tasks", "questions", mode="before")
    @classmethod
    def _lists(cls, v):
        return [] if v is None else v


_LEADING_NUMBER = re.compile(r"^\s*(?:\d+[.)]|[-–—•])\s+")
_LABEL = re.compile(r"^\s*(?:Тема(?:\s+работы)?|Цел[ьи](?:\s+работы)?|Оснащение(?:\s+работы)?|Задание)\s*:\s*",
                    re.IGNORECASE)


def one_line(s: str) -> str:
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    return " ".join(s.split())


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


def extract_spec(src: SourceText, subject: Subject, enrollment: Enrollment, llm, lab_number: str | None,
                 date: str) -> tuple[LabSpec, list[Finding]]:
    variant = enrollment.variant if enrollment.variant is not None else subject.default_variant
    hint = f" Номер работы: {lab_number}." if lab_number else ""
    messages = [
        {"role": "system", "content": SPEC_SYSTEM.replace("{variant}", str(variant))},
        {"role": "user", "content": SPEC_USER.format(subject=subject.name, variant=variant, number_hint=hint,
                                                     text=src.text[:MAX_SOURCE_CHARS])},
    ]
    raw: RawSpec = llm.json(messages, RawSpec, temperature=0.0)
    tasks = [Task(n=i, text=clean_item(t), kind="main") for i, t in enumerate(raw.main_tasks, start=1) if t.strip()]
    tasks += [Task(n=len(tasks) + i, text=clean_item(t), kind="extra")
              for i, t in enumerate([t for t in raw.extra_tasks if t.strip()], start=1)]
    spec = LabSpec(
        subject_id=subject.id,
        number=(lab_number or raw.number or "").strip() or "1",
        date=date,
        topic=clean_field(raw.topic).rstrip(". "),
        goal=clean_field(raw.goal).rstrip(". "),
        equipment=clean_field(raw.equipment).rstrip(". ") or None,
        assignment=clean_field(raw.assignment),
        tasks=tasks,
        variant=variant,
        variant_text=one_line(raw.variant_text) if raw.variant_text else None,
        questions=[clean_item(q) for q in raw.questions if q.strip()],
        source_text=src.text,
    )
    findings = verify_verbatim(spec, src)
    if not spec.tasks:
        findings.append(Finding(stage="spec", rule_id="spec.no_tasks", location="Задания",
                                message="В методичке не нашлось заданий — добавь их вручную"))
    return spec, findings
