"""Чат правок: что сказал преподаватель → точечная правка отчёта (или ответ, или уточняющий вопрос)."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from forlabs.core.models import (
    PROFILE_FIELDS,
    QA,
    DashList,
    Figure,
    LabSpec,
    LabState,
    Listing,
    NumberedList,
    Paragraph,
    ProfileChange,
    Profile,
    Table,
    TaskSection,
)
from forlabs.core.rules import RuleBook
from forlabs.core.subjects import Subject
from forlabs.pipeline.prompts import (
    CHAT_ANSWERS_SYSTEM,
    CHAT_PLAN_SYSTEM,
    CHAT_REVISE_SYSTEM,
)
from forlabs.pipeline.write import (
    BlockDraft,
    SectionDraft,
    _gender_ru,
    draft_to_blocks,
    student_block,
    wishes_text,
)

SOURCE_LIMIT = 30_000
MAX_LOOKS = 3  # сколько раз за одно сообщение чат может заглянуть в файлы проекта, прежде чем ответить
LOST = "Не получилось разобраться с первого раза. Напиши, пожалуйста, точнее, что поменять или что не так."
_NEEDS_SOURCE = re.compile(r"вариант|приложени|методичк|по заданию|из задания|дословно", re.IGNORECASE)
HISTORY = 6


class TaskEdit(BaseModel):
    n: int
    instruction: str


class QuestionEdit(BaseModel):
    n: int
    instruction: str


class Look(BaseModel):
    """Что чат хочет посмотреть сам: файл проекта, текст в файлах проекта или задание отчёта целиком."""
    file: str = ""
    line: int = 1
    find: str = ""
    task: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _loose(cls, v):
        if isinstance(v, str):  # модель иногда пишет просто путь
            return {"file": v}
        if isinstance(v, dict):
            v = {k: v[k] for k in ("file", "line", "find", "task") if v.get(k) not in (None, "")}
            v.update({k: str(v[k]) for k in ("file", "find") if k in v})
            for k in ("line", "task"):
                if k in v and not str(v[k]).strip().lstrip("-").isdigit():
                    v.pop(k)
        return v


class EditPlan(BaseModel):
    kind: Literal["edit", "answer", "question", "offtopic"] = "question"

    @model_validator(mode="before")
    @classmethod
    def _type_alias(cls, v):  # модель иногда пишет type вместо kind
        if isinstance(v, dict) and "kind" not in v and "type" in v:
            v = {**v, "kind": v["type"]}
        if isinstance(v, dict):
            kind = str(v.get("kind") or "").strip().lower()
            if kind not in ("edit", "answer", "question", "offtopic"):  # непонятное: есть правки — правка
                kind = "edit" if (v.get("tasks") or v.get("questions") or v.get("conclusion")) else "question"
            v = {**v, "kind": kind, "profile": v.get("profile") if isinstance(v.get("profile"), list) else [],
                 "look": v.get("look") if isinstance(v.get("look"), list) else [],
                 "project": v.get("project") if isinstance(v.get("project"), str) else None}
        return v

    profile: list[dict] = Field(default_factory=list)  # [{"field": "teacher", "value": "…"}]

    reply: str = ""
    tasks: list[TaskEdit] = Field(default_factory=list)
    questions: list[QuestionEdit] = Field(default_factory=list)
    variant: int | None = None
    variant_text: str | None = None
    conclusion: str | None = None
    remember: str | None = None
    reshoot: bool = False  # студент просит ForLabs самому снять скриншоты окон программы ещё раз
    project: str | None = None  # что исправить в самой программе: ForLabs чинит файлы проекта и пересобирает
    look: list[Look] = Field(default_factory=list)  # что посмотреть, прежде чем отвечать

    @property
    def changes_anything(self) -> bool:
        return bool(self.tasks or self.questions or self.variant is not None or self.variant_text or self.conclusion)


class ReviseBlock(BaseModel):
    """Блок ответа правки: ссылка на исходный ({"keep": "b3"}) или новый блок в формате написания."""
    type: Literal["paragraph", "dash_list", "numbered_list", "listing", "figure", "table"] | None = None
    keep: str | None = None
    retake: bool = False
    text: str | None = None
    items: list[str] | None = None
    topic: str | None = None
    language: str | None = None
    code: str | None = None
    kind: Literal["screenshot", "diagram"] | None = None
    instruction: str | None = None
    dot: str | None = None
    rows: list[list[str]] | None = None


class ReviseDraft(BaseModel):
    blocks: list[ReviseBlock] = Field(default_factory=list)


_KEEP = re.compile(r"^\s*b?\s*(\d+)\s*\.?\s*$", re.IGNORECASE)


def _keep_label(raw: str) -> str | None:
    m = _KEEP.match(raw or "")
    return f"b{int(m.group(1))}" if m else None


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
            why = f"не снят — {b.auto_note.strip()} " if b.auto_note else ""
            state = f"{why}ждёт скриншот: {b.slot or ''}".strip()
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


ANSWER_LIMIT = 1200  # ответ на вопрос в чате правок — коротко, длинный текст из чата не вытянуть
PLAN_MAX_TOKENS = 4000  # план копирует текст варианта дословно — длинному варианту нужен запас


def _profile_now(profile: Profile, enrollment) -> str:
    e = enrollment
    return (f"ФИО: {profile.full_name}; на титуле: {profile.title_name}; пол: {_gender_ru(profile.gender)}; "
            f"группа: {profile.group}; руководитель: {getattr(e, 'teacher', '') or '—'}; предмет на титуле: "
            f"{getattr(e, 'title_subject', '') or '—'}; учебный год: {getattr(e, 'academic_year', '') or '—'}; "
            f"вариант по умолчанию: {getattr(e, 'variant', None) or '—'}; рабочее место: {getattr(e, 'workplace', None) or '—'}")


def _short(text: str, limit: int = ANSWER_LIMIT) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind("\n"))
    return (cut[:end + 1] if end > limit // 2 else cut).rstrip() + " …"


def profile_changes(raw: list[dict], profile: Profile, enrollment) -> list[ProfileChange]:
    """Предложенные моделью изменения профиля: только известные поля, непустые и отличные от текущих."""
    out: list[ProfileChange] = []
    seen = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        field, value = str(item.get("field") or "").strip(), " ".join(str(item.get("value") or "").split())
        if field not in PROFILE_FIELDS or not value or field in seen:
            continue
        if field == "gender":
            low = value.lower()
            value = "f" if low.startswith(("ж", "f")) else "m" if low.startswith(("м", "m")) else ""
            if not value:
                continue
        if field in ("variant", "workplace"):
            numbers = re.findall(r"\d+", value)
            if len(numbers) != 1 or "." in value or "," in value or not 1 <= int(numbers[0]) <= 999:
                continue  # «12.5», «3 или 4» — не номер варианта
            value = str(int(numbers[0]))
        holder = profile if field in ("full_name", "title_name", "gender", "group") else enrollment
        old = getattr(holder, field, None) if holder is not None else None
        old = "" if old is None else str(old)
        if old == value:
            continue
        seen.add(field)
        out.append(ProfileChange(field=field, value=value, old=old, label=PROFILE_FIELDS[field]))
    return out


def shown(change: ProfileChange, which: str = "value") -> str:
    v = getattr(change, which)
    return {"m": "мужской", "f": "женский"}.get(v, v) if change.field == "gender" else v


def plan_edit(st: LabState, message: str, profile: Profile, subject: Subject, llm, rules: RuleBook,
              teacher: str, enrollment=None, view=None) -> EditPlan:
    spec, report = st.spec, st.report
    qa = "\n".join(f"{q.n}. {q.question}\n   Ответ: {q.answer}" for q in report.questions) or "нет"
    prior = st.chat[:-1] if st.chat and st.chat[-1].role == "user" else st.chat  # текущее сообщение — ниже
    history = "\n".join(f"{'Студент' if m.role == 'user' else 'Помощник'}: {m.text}" for m in prior[-HISTORY:])
    # методичка нужна, только когда речь о варианте или заданиях из неё — иначе это лишние тысячи токенов
    source = (spec.source_text or "")[:SOURCE_LIMIT] if _NEEDS_SOURCE.search(message) else "не нужен для этого сообщения"
    user = (f"Предмет: {subject.name}. Пол студента: {_gender_ru(profile.gender)}.\n"
            f"Данные профиля сейчас: {_profile_now(profile, enrollment)}\n"
            f"Тема: {spec.topic}\nЦель: {spec.goal}\n"
            f"Вариант по журналу: {spec.variant or '—'}. Номер рабочего места: {spec.workplace or '—'}.\n"
            f"Текст варианта в отчёте: {spec.variant_text or 'нет'}\n"
            f"Вывод сейчас: {report.conclusion}\n"
            f"Требования преподавателя, которые уже действуют: {teacher or 'нет'}\n"
            f"Пожелания и отклонения от методички для этой лабы: {spec.wishes or 'нет'}\n"
            f"Файлы к лабе от преподавателя (их целиком видит правка заданий): "
            f"{', '.join(m.name for m in st.materials) or 'нет'}\n\n"
            f"{view.summary() if view is not None else 'Программа лабы: нет сведений.'}\n\n"
            f"Отчёт сейчас (блоки по заданиям):\n{_outline(st)}\n\n"
            f"Контрольные вопросы и ответы:\n{qa}\n\n"
            f"Последние сообщения чата:\n{history or 'нет'}\n\n"
            f"Текст методички (для вариантов):\n{source}\n\n"
            f"Новое сообщение студента:\n{message}")
    messages = [{"role": "system", "content": CHAT_PLAN_SYSTEM.format(rules=rules.load("repair", subject.id))},
                {"role": "user", "content": user}]
    for rounds in range(MAX_LOOKS + 1):
        plan = llm.json(messages, EditPlan, max_tokens=PLAN_MAX_TOKENS, think=True)
        if not plan.look or view is None or rounds == MAX_LOOKS:
            break
        asked = json.dumps({"look": [x.model_dump(exclude_defaults=True) for x in plan.look]}, ensure_ascii=False)
        last = rounds == MAX_LOOKS - 1
        messages = [*messages, {"role": "assistant", "content": asked},
                    {"role": "user", "content": f"Вот что нашлось:\n\n{view.answer(plan.look)}\n\n" + (
                        "Больше смотреть нельзя: ответь по формату, без look." if last else
                        "Теперь ответь по формату. Если без этого не ответить, посмотри ещё.")}]
    if plan.look and not (plan.reply.strip() or plan.changes_anything or plan.project or plan.reshoot):
        plan.kind, plan.reply = "question", LOST  # так ничего и не решил: студент не остаётся без ответа
    plan.look = []
    plan.project = " ".join((plan.project or "").split())[:600] or None
    task_numbers = {t.n for t in spec.tasks}
    dropped = sorted({t.n for t in plan.tasks if t.n not in task_numbers})
    plan.tasks = [t for t in plan.tasks if t.n in task_numbers and t.instruction.strip()]
    plan.questions = [q for q in plan.questions if 1 <= q.n <= len(report.questions) and q.instruction.strip()]
    plan.reply = plan.reply.strip()
    if plan.kind in ("answer", "offtopic", "question"):
        plan.reply = _short(plan.reply)
    plan.remember = (plan.remember or "").strip() or None
    if plan.kind == "offtopic":
        plan.reshoot, plan.project = False, None
    elif (plan.reshoot or plan.project) and plan.kind == "question":  # просьба понятна: снять заново, исправить
        plan.kind = "answer"
    if plan.kind != "edit":
        plan.tasks, plan.questions = [], []
        plan.variant = plan.variant_text = plan.conclusion = None
    elif not plan.changes_anything and (plan.reshoot or plan.project):
        plan.kind = "answer"
    elif not plan.changes_anything:
        plan.kind = "question"
        if dropped:  # модель обещала правку задания, которого нет — не повторяем обещание
            listed = ", ".join(str(n) for n in dropped)
            plan.reply = (f"В отчёте нет задания {listed} — уточни номер "
                          f"(есть задания {', '.join(str(n) for n in sorted(task_numbers))}).")
        else:
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
            f"{memory_block}{wishes_text(spec)}{student_block(spec, profile)}"
            f"Задание {section.n}: «{section.title}»\n\nБлоки сейчас:\n{blocks_text}\n\n"
            f"Что поменять по замечанию преподавателя:\n{instruction}")
    draft = llm.json([{"role": "system", "content": CHAT_REVISE_SYSTEM.format(rules=rules.load("write", subject.id))},
                      {"role": "user", "content": user}], ReviseDraft, think=True)
    out, retake, used = [], [], set()
    suffix = f"_r{uuid.uuid4().hex[:6]}"
    for rb in draft.blocks:
        if rb.keep:
            key = _keep_label(rb.keep)
            orig = labels.get(key) if key else None
            if orig is None or key in used:
                continue
            used.add(key)
            b = orig.model_copy(deep=True)
            if rb.retake and isinstance(b, Figure) and not is_diagram(b):
                b.image_path = None
                hint = "Пересними после правки — поменялись код или результат."
                b.slot = hint + (f" {orig.slot}" if orig.slot and not orig.slot.startswith("Пересними") else "")
                retake.append(b.topic)
            out.append(b)
            continue
        if rb.type is None:
            continue
        block = BlockDraft.model_validate(rb.model_dump(exclude={"keep", "retake"}))
        for nb in draft_to_blocks(SectionDraft(blocks=[block]), Path(assets_dir), section.n, render_diagrams,
                                  name_suffix=suffix):
            if isinstance(nb, Figure) and not nb.image_path:
                retake.append(nb.topic)
            out.append(nb)
    if not out:
        raise ValueError("Модель вернула пустое задание — попробуй сформулировать правку ещё раз")
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
                     AnswersEditDraft, think=True)
    new = {a.n: a.answer.strip() for a in draft.answers if a.n in wanted and a.answer.strip()}
    return [q.model_copy(update={"answer": new[q.n]}) if q.n in new else q for q in qas]
