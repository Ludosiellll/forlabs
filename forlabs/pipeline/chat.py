"""Чат правок: что сказал преподаватель → точечная правка отчёта (или ответ, или уточняющий вопрос)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from forlabs.core.models import (
    QA, DashList, Figure, LabSpec, LabState, Listing, NumberedList, Paragraph, Profile, Table, TaskSection,
)
from forlabs.core.rules import RuleBook
from forlabs.core.subjects import Subject
from forlabs.pipeline.prompts import CHAT_ANSWERS_SYSTEM, CHAT_PLAN_SYSTEM, CHAT_REVISE_SYSTEM
from forlabs.pipeline.write import SectionDraft, _gender_ru, draft_to_blocks, wishes_text

SOURCE_LIMIT = 30_000
_NEEDS_SOURCE = re.compile(r"вариант|приложени|методичк|по заданию|из задания|дословно", re.IGNORECASE)
HISTORY = 6


class TaskEdit(BaseModel):
    n: int
    instruction: str


class QuestionEdit(BaseModel):
    n: int
    instruction: str


class EditPlan(BaseModel):
    kind: Literal["edit", "answer", "question"] = "question"
    reply: str = ""
    tasks: list[TaskEdit] = Field(default_factory=list)
    questions: list[QuestionEdit] = Field(default_factory=list)
    variant: int | None = None
    variant_text: str | None = None
    conclusion: str | None = None
    remember: str | None = None

    @property
    def changes_anything(self) -> bool:
        return bool(self.tasks or self.questions or self.variant is not None or self.variant_text or self.conclusion)


class AnswerEdit(BaseModel):
    n: int
    answer: str


class AnswersEditDraft(BaseModel):
    answers: list[AnswerEdit] = Field(default_factory=list)


def is_diagram(fig: Figure) -> bool:
    return bool(fig.image_path) and Path(fig.image_path).name.startswith("scheme_")


def describe_block(label: str, b) -> str:
    if isinstance(b, Paragraph):
        return f"{label} [абзац] {b.text}"
    if isinstance(b, (DashList, NumberedList)):
        return f"{label} [список] " + " | ".join(b.items)
    if isinstance(b, Listing):
        return f"{label} [листинг: {b.topic}; {b.language or 'text'}]\n{b.code}"
    if isinstance(b, Figure):
        if is_diagram(b):
            state = "схема, нарисована программой"
        elif b.image_path:
            state = "скриншот вставлен"
        else:
            state = f"ждёт скриншот: {b.slot or ''}".strip()
        return f"{label} [рисунок: {b.topic}] ({state})"
    if isinstance(b, Table):
        return f"{label} [таблица: {b.topic}] " + " / ".join(" | ".join(r) for r in b.rows[:12])
    return f"{label} [{getattr(b, 'type', 'блок')}]"


def _outline(st: LabState, limit: int = 220) -> str:
    out = []
    for sec in st.report.sections:
        out.append(f"Задание {sec.n}. {sec.title}")
        for i, b in enumerate(sec.blocks, start=1):
            line = describe_block(f"  b{i}", b).replace("\n", " ⏎ ")
            out.append(line if len(line) <= limit else line[:limit] + "…")
    return "\n".join(out)


def plan_edit(st: LabState, message: str, profile: Profile, subject: Subject, llm, rules: RuleBook,
              teacher: str) -> EditPlan:
    spec, report = st.spec, st.report
    qa = "\n".join(f"{q.n}. {q.question}\n   Ответ: {q.answer}" for q in report.questions) or "нет"
    history = "\n".join(f"{'Студент' if m.role == 'user' else 'Помощник'}: {m.text}" for m in st.chat[-HISTORY:])
    # методичка нужна, только когда речь о варианте или заданиях из неё — иначе это лишние тысячи токенов
    source = (spec.source_text or "")[:SOURCE_LIMIT] if _NEEDS_SOURCE.search(message) else "не нужен для этого сообщения"
    user = (f"Предмет: {subject.name}. Пол студента: {_gender_ru(profile.gender)}.\n"
            f"Тема: {spec.topic}\nЦель: {spec.goal}\n"
            f"Вариант по журналу: {spec.variant or '—'}. Номер рабочего места: {spec.workplace or '—'}.\n"
            f"Текст варианта в отчёте: {spec.variant_text or 'нет'}\n"
            f"Вывод сейчас: {report.conclusion}\n"
            f"Требования преподавателя, которые уже действуют: {teacher or 'нет'}\n"
            f"Пожелания и отклонения от методички для этой лабы: {spec.wishes or 'нет'}\n\n"
            f"Отчёт сейчас (блоки по заданиям):\n{_outline(st)}\n\n"
            f"Контрольные вопросы и ответы:\n{qa}\n\n"
            f"Последние сообщения чата:\n{history or 'нет'}\n\n"
            f"Текст методички (для вариантов):\n{source}\n\n"
            f"Новое сообщение студента:\n{message}")
    plan = llm.json([{"role": "system", "content": CHAT_PLAN_SYSTEM.format(rules=rules.load("repair", subject.id))},
                     {"role": "user", "content": user}], EditPlan)
    task_numbers = {t.n for t in spec.tasks}
    plan.tasks = [t for t in plan.tasks if t.n in task_numbers and t.instruction.strip()]
    plan.questions = [q for q in plan.questions if 1 <= q.n <= len(report.questions) and q.instruction.strip()]
    plan.reply = plan.reply.strip()
    plan.remember = (plan.remember or "").strip() or None
    if plan.kind != "edit":
        plan.tasks, plan.questions = [], []
        plan.variant = plan.variant_text = plan.conclusion = None
    elif not plan.changes_anything:
        plan.kind = "question"
        plan.reply = plan.reply or "Уточни, пожалуйста, что именно поменять."
    return plan


def revise_section(spec: LabSpec, section: TaskSection, instruction: str, profile: Profile, subject: Subject,
                   llm, rules: RuleBook, assets_dir: Path, memory: str = "",
                   render_diagrams: bool = True) -> tuple[TaskSection, list[str]]:
    """Точечная правка задания: нетронутые блоки переносятся как есть (со скриншотами)."""
    labels = {f"b{i}": b for i, b in enumerate(section.blocks, start=1)}
    blocks_text = "\n".join(describe_block(k, b) for k, b in labels.items())
    memory_block = f"Память и требования по предмету:\n{memory}\n" if memory.strip() else ""
    user = (f"Предмет: {subject.name}. Пол студента: {_gender_ru(profile.gender)}.\n"
            f"Тема работы: {spec.topic}\nВариант: {spec.variant or '—'}\n"
            f"Текст варианта: {spec.variant_text or 'нет'}\n"
            f"{memory_block}{wishes_text(spec)}"
            f"Задание {section.n}: «{section.title}»\n\nБлоки сейчас:\n{blocks_text}\n\n"
            f"Что поменять по замечанию преподавателя:\n{instruction}")
    draft = llm.json([{"role": "system", "content": CHAT_REVISE_SYSTEM.format(rules=rules.load("write", subject.id))},
                      {"role": "user", "content": user}], SectionDraft)
    out, retake, used = [], [], set()
    suffix = f"_r{uuid.uuid4().hex[:6]}"
    for rb in draft.blocks:
        if rb.keep:
            key = rb.keep.strip()
            orig = labels.get(key)
            if orig is None or key in used:
                continue
            used.add(key)
            b = orig.model_copy(deep=True)
            if rb.retake and isinstance(b, Figure) and not is_diagram(b):
                b.image_path = None
                b.slot = "Пересними после правки — поменялись код или результат."
                retake.append(b.topic)
            out.append(b)
            continue
        for nb in draft_to_blocks(SectionDraft(blocks=[rb]), Path(assets_dir), section.n, render_diagrams,
                                  name_suffix=suffix):
            if isinstance(nb, Figure) and not nb.image_path:
                retake.append(nb.topic)
            out.append(nb)
    return TaskSection(n=section.n, title=section.title, blocks=out), retake


def revise_answers(spec: LabSpec, qas: list[QA], edits: list[QuestionEdit], profile: Profile, subject: Subject,
                   llm, rules: RuleBook) -> list[QA]:
    if not edits:
        return qas
    wanted = {e.n: e.instruction for e in edits}
    body = "\n".join(f"{q.n}. {q.question}\n   Ответ сейчас: {q.answer}\n   Замечание: {wanted[q.n]}"
                     for q in qas if q.n in wanted)
    draft = llm.json([{"role": "system", "content": CHAT_ANSWERS_SYSTEM.format(rules=rules.load("repair", subject.id))},
                      {"role": "user", "content": f"Предмет: {subject.name}. Тема работы: {spec.topic}.\n"
                                                  f"Пол студента: {_gender_ru(profile.gender)}.\n{body}"}],
                     AnswersEditDraft)
    new = {a.n: a.answer.strip() for a in draft.answers if a.n in wanted and a.answer.strip()}
    return [q.model_copy(update={"answer": new[q.n]}) if q.n in new else q for q in qas]
