"""Написание отчёта: содержимое заданий и ответы — модель; шапка, нумерация, вывод — код."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field, field_validator

from forlabs.core.models import (
    QA,
    DashList,
    Enrollment,
    Figure,
    LabSpec,
    Listing,
    NumberedList,
    Paragraph,
    Profile,
    ReportDoc,
    Table,
    TaskSection,
)
from forlabs.core.rules import RuleBook
from forlabs.core.subjects import Subject
from forlabs.executors.diagrams import render_dot
from forlabs.pipeline.prompts import ANSWERS_SYSTEM, WRITE_SYSTEM, WRITE_USER
from forlabs.report.derive import build_header
from forlabs.report.morph import goal_to_conclusion


class BlockDraft(BaseModel):
    type: Literal["paragraph", "dash_list", "numbered_list", "listing", "figure", "table"]
    text: str | None = None
    items: list[str] | None = None
    topic: str | None = None
    language: str | None = None
    code: str | None = None
    kind: Literal["screenshot", "diagram"] | None = None
    instruction: str | None = None
    dot: str | None = None
    rows: list[list[str]] | None = None

    @field_validator("rows", mode="before")
    @classmethod
    def _cells(cls, v):  # числа и null в ячейках — строки, а не повод для платной починки
        if isinstance(v, list):
            return [[("" if c is None else str(c)) for c in r] if isinstance(r, list) else [str(r)] for r in v]
        return v

    @field_validator("items", mode="before")
    @classmethod
    def _items(cls, v):
        return [str(x) for x in v if x is not None] if isinstance(v, list) else v


class SectionDraft(BaseModel):
    blocks: list[BlockDraft] = Field(min_length=1)


class AnswerItem(BaseModel):
    n: int
    answer: str = ""


class AnswersDraft(BaseModel):
    answers: list[AnswerItem | str]

    def for_questions(self, numbers: list[int]) -> dict[int, str]:
        """Ответы по номерам вопросов: {n, answer} — по n, просто строки — по порядку; лишние отбрасываются."""
        got: dict[int, str] = {}
        plain = [a for a in self.answers if isinstance(a, str)]
        for n, a in zip(numbers, plain):
            got[n] = a.strip()
        for a in self.answers:
            if isinstance(a, AnswerItem) and a.n in numbers:
                got[a.n] = a.answer.strip()
        return got


PREV_CODE_LIMIT = 1500  # символов кода каждого прежнего листинга в запросе


def section_brief(sec: TaskSection) -> str:
    """Прежнее задание для следующего запроса: текст и начало кода (имена, тема, палитра — чтобы не разъезжались)."""
    out = []
    for b in sec.blocks:
        if isinstance(b, Listing):
            code = b.code if len(b.code) <= PREV_CODE_LIMIT else b.code[:PREV_CODE_LIMIT] + "\n…"
            out.append(f"[листинг: {b.topic}]\n{code}")
        else:
            out.append(section_text(TaskSection(n=sec.n, title=sec.title, blocks=[b])))
    return "\n".join(x for x in out if x.strip())


def section_has_content(sec: TaskSection) -> bool:
    """В задании есть текст, список, листинг или таблица (не только рисунки и не пусто)."""
    for b in sec.blocks:
        if isinstance(b, Paragraph) and b.text.strip():
            return True
        if isinstance(b, Listing) and b.code.strip():
            return True
        if isinstance(b, Table) and b.rows:
            return True
        if getattr(b, "items", None) and any(str(x).strip() for x in b.items):
            return True
    return False


def _gender_ru(g: str) -> str:
    return "женский" if g == "f" else "мужской"


def section_text(sec: TaskSection) -> str:
    out = []
    for b in sec.blocks:
        if isinstance(b, Paragraph):
            out.append(b.text)
        elif isinstance(b, (DashList, NumberedList)):
            out.extend(b.items)
        elif isinstance(b, (Figure, Listing, Table)):
            out.append(f"[{b.type}: {b.topic}]")
    return "\n".join(out)


_DOC_TOPIC = re.compile(r"записк|политик|положени|регламент|инструкци|приказ|\bплан\b|описани|концепци|памятк|"
                        r"требовани|устав|договор|соглашени", re.IGNORECASE)
_DATA_TOPIC = re.compile(r"\bкод|файл|вывод|скрипт|запрос|настро|конфиг|консол|журнал|\bлог|результат", re.IGNORECASE)
_BULLET = re.compile(r"^[-–—•*]\s+(.+)$")


def document_as_listing(topic: str, language: str, code: str) -> bool:
    """Модель оформила листингом связный русский текст (пояснительную записку, политику, регламент), который в
    отчёте должен идти обычными абзацами. Код, файлы данных и вывод программы остаются листингами."""
    if language.strip().lower() not in ("", "text", "txt", "plain", "markdown", "md"):
        return False
    if _DATA_TOPIC.search(topic) and not _DOC_TOPIC.search(topic):
        return False
    text = code.strip()
    letters = [c for c in text if c.isalpha()]
    if len(text) < 200 or not letters:
        return False
    if sum(c.lower() in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя" for c in letters) / len(letters) < 0.6:
        return False
    if sum(text.count(c) for c in "{};=<>[]#$\\") > len(text) * 0.01:
        return False
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    ends = sum(1 for ln in lines if re.search(r"[.!?;:]$", ln))
    return ends >= max(2, len(lines) // 2)


def text_blocks(text: str) -> list:
    """Строки текста — абзацами, подряд идущие пункты «- …» — маркированным списком."""
    out: list = []
    items: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        m = _BULLET.match(s)
        if m:
            items.append("- " + m.group(1).strip())
            continue
        if items:
            out.append(DashList(items=items))
            items = []
        if s:
            out.append(Paragraph(text=s))
    if items:
        out.append(DashList(items=items))
    return out


def draft_to_blocks(draft: SectionDraft, assets_dir: Path, sec_n: int, render_diagrams: bool = True,
                    name_suffix: str = "") -> list:
    blocks = []
    for i, b in enumerate(draft.blocks, start=1):
        if b.type == "paragraph" and (b.text or "").strip():
            blocks.append(Paragraph(text=b.text.strip()))
        elif b.type in ("dash_list", "numbered_list") and b.items:
            cls = DashList if b.type == "dash_list" else NumberedList
            blocks.append(cls(items=[x.strip() for x in b.items if x.strip()]))
        elif b.type == "listing" and (b.code or "").strip():
            if document_as_listing(b.topic or "", b.language or "", b.code):
                blocks.extend(text_blocks(b.code))  # пояснительная записка, политика — обычным текстом
            else:
                blocks.append(Listing(topic=b.topic or "Исходный код программы", code=b.code.rstrip(),
                                      language=b.language or ""))
        elif b.type == "table" and b.rows:
            rows = [[str(c).strip() for c in r] for r in b.rows]
            width = max(len(r) for r in rows)
            if width and any(c for r in rows for c in r):  # пустую таблицу модели не верстаем
                blocks.append(Table(topic=b.topic or "данные", rows=[r + [""] * (width - len(r)) for r in rows]))
        elif b.type == "figure":
            topic = b.topic or "Результат выполнения задания"
            if b.kind == "diagram" and b.dot:
                png = None
                if render_diagrams:
                    png = render_dot(b.dot, assets_dir / f"scheme_{sec_n}_{i}{name_suffix}_{uuid.uuid4().hex[:6]}.png")
                if png:
                    blocks.append(Figure(topic=topic, image_path=str(png)))
                else:
                    blocks.append(Figure(topic=topic, slot=f"Схема не нарисовалась автоматически. {b.instruction or ''}"
                                                           f" Код схемы: {b.dot[:300]}".strip()))
            else:
                blocks.append(Figure(topic=topic, slot=(b.instruction or f"Сделай снимок: {topic}").strip()))
    return blocks


def wishes_text(spec: LabSpec) -> str:
    if not (spec.wishes or "").strip():
        return ""
    return f"Пожелания и отклонения от методички — обязательно учти, они важнее методички:\n{spec.wishes.strip()}\n"


_OWN_DATA = re.compile(r"фамили|\bФИО\b|Ф\.\s*И\.\s*О|сво(?:ю|ей|им|ими|и|ё|е)\s+(?:им[яе]н?|данны)", re.IGNORECASE)


def student_block(spec: LabSpec, profile: Profile) -> str:
    """Методичка просит подставить свою фамилию («World_Фамилия», «проект назвать своей фамилией»): модели даётся
    фамилия студента — иначе она придумает чужую. Без такой просьбы данные студента в запрос не идут."""
    text = " ".join([spec.source_text or "", spec.assignment or "", spec.variant_text or "", *(t.text for t in spec.tasks)])
    surname = (profile.full_name or "").split()[0] if (profile.full_name or "").strip() else ""
    if not surname or not _OWN_DATA.search(text):
        return ""
    from forlabs.runner.languages import _translit

    return (f"Фамилия студента: {surname} (латиницей: {_translit(surname)}). Там, где методичка просит подставить "
            "свою фамилию («Фамилия», «ФИО», «свои данные» — в именах баз, файлов, проектов, в данных), подставляй "
            "именно её; чужих фамилий не придумывай.\n")


SOURCE_FOR_WRITING = 20_000  # символов методички в запросе на текст задания и ответы


def source_block(spec: LabSpec) -> str:
    """Строка «Задание» и сам текст методички: имена объектов, значения и порядок действий — оттуда, а не выдуманы."""
    out = f"Общее задание из методички: {spec.assignment}\n" if (spec.assignment or "").strip() else ""
    if spec.needs:
        out += ("Для работы нужно то, чего нет в методичке и чего студент не дал: " + "; ".join(spec.needs)
                + ". Составь свой правдоподобный пример и не утверждай, что он взят из прошлой работы или выдан "
                "преподавателем; результатов, которых не было, не описывай.\n")
    src = (spec.source_text or "").strip()
    if src:
        cut = src[:SOURCE_FOR_WRITING]
        out += ("Текст методички (имена объектов, файлов, значения, адреса и порядок действий бери отсюда, "
                f"ничего не придумывай вместо них):\n<<<\n{cut}\n>>>\n")
        if "[Картинка в методичке" in cut:
            out += ("Блоки «[Картинка в методичке: …]» — содержание картинок методички (код, запросы, таблицы, схемы "
                    "с её рисунков): выполняй задание по ним так же, как по тексту. «Прочитать не удалось» — "
                    "картинка не распознана: что на ней было, не выдумывай.\n")
    return out


def _context(spec: LabSpec, profile: Profile, subject: Subject, memory: str, previous: list[TaskSection]) -> dict:
    all_tasks = "\n".join(f"{t.n}. {t.text}" for t in spec.tasks)
    variant_block = (f"Варианты из методички:\n{spec.variant_text}\n" if spec.variant_text else
                     "В этой работе нет вариантов и приложений с вариантами: не упоминай вариант и приложение.\n")
    memory_block = f"Память предмета:\n{memory}\n" if memory.strip() else ""
    wishes_block = wishes_text(spec) + student_block(spec, profile)
    prev = "\n\n".join(f"Задание {s.n} (уже написано):\n{section_brief(s)}" for s in previous)
    previous_block = f"Уже написанные задания этой работы — не повторяйся и будь последователен:\n{prev}\n" if prev else ""
    return dict(subject=subject.name, gender=_gender_ru(profile.gender), variant=spec.variant or "—",
                workplace=spec.workplace or "не указан",
                topic=spec.topic, goal=spec.goal, equipment=spec.equipment or "—", all_tasks=all_tasks,
                variant_block=variant_block, wishes_block=wishes_block, memory_block=memory_block,
                previous_block=previous_block, source_block=source_block(spec))


def write_section(spec: LabSpec, n: int, profile: Profile, subject: Subject, llm, rules: RuleBook,
                  assets_dir: Path, memory: str = "", previous: list[TaskSection] | None = None,
                  render_diagrams: bool = True) -> TaskSection:
    task = next(t for t in spec.tasks if t.n == n)
    ctx = _context(spec, profile, subject, memory, previous or [])
    messages = [{"role": "system", "content": WRITE_SYSTEM.format(rules=rules.load("write", subject.id))},
                {"role": "user", "content": WRITE_USER.format(n=n, task=task.text, **ctx)}]
    draft = llm.json(messages, SectionDraft, think=True)
    return TaskSection(n=n, title=task.text, blocks=draft_to_blocks(draft, Path(assets_dir), n, render_diagrams))


def answer_questions(spec: LabSpec, profile: Profile, subject: Subject, llm, rules: RuleBook) -> list[QA]:
    if not spec.questions:
        return []
    system = ANSWERS_SYSTEM.format(rules=rules.load("repair", subject.id))
    source = source_block(spec)

    def ask(numbers: list[int]) -> dict[int, str]:
        qs = "\n".join(f"{i}. {spec.questions[i - 1]}" for i in numbers)
        draft = llm.json([{"role": "system", "content": system},
                          {"role": "user", "content": f"Предмет: {subject.name}. Тема работы: {spec.topic}.\n"
                                                      f"Пол студента: {_gender_ru(profile.gender)}.\n{source}"
                                                      f"Вопросы:\n{qs}"}], AnswersDraft, think=True)
        return draft.for_questions(numbers)

    numbers = list(range(1, len(spec.questions) + 1))
    answers = ask(numbers)
    missing = [i for i in numbers if not answers.get(i)]
    if missing:  # модель пропустила ответы — один раз переспрашиваем только их
        answers.update({i: a for i, a in ask(missing).items() if a})
    return [QA(n=i, question=q, answer=answers.get(i, "")) for i, q in enumerate(spec.questions, start=1)]


def write_report(spec: LabSpec, profile: Profile, enrollment: Enrollment, subject: Subject, llm, rules: RuleBook,
                 assets_dir: Path, memory: str = "", on_event: Callable[[str], None] | None = None,
                 render_diagrams: bool = True, existing: ReportDoc | None = None,
                 only: list[int] | None = None,
                 on_progress: Callable[[ReportDoc], None] | None = None) -> ReportDoc:
    """only — какие задания писать заново (остальные берутся из existing); on_progress — сохранить
    написанное после каждого задания, чтобы после сбоя продолжить с недописанного."""
    emit = on_event or (lambda s: None)
    sections: list[TaskSection] = []
    old = {s.n: s for s in (existing.sections if existing else [])}
    keep_old = only is not None
    for i, t in enumerate(spec.tasks):
        if keep_old and t.n not in only and t.n in old and section_has_content(old[t.n]):
            sections.append(old[t.n])
            continue
        emit(f"Задание {t.n} из {len(spec.tasks)}: пишу текст")
        sections.append(write_section(spec, t.n, profile, subject, llm, rules, assets_dir, memory, sections,
                                      render_diagrams))
        if on_progress:
            rest = [old[x.n] for x in spec.tasks[i + 1:] if keep_old and x.n in old]
            on_progress(refresh_derived(ReportDoc(lab_number=spec.number, sections=sections + rest,
                                                  questions=existing.questions if existing and keep_old else []),
                                        spec, profile, enrollment))
    if (existing and only is not None and [qa.question for qa in existing.questions] == list(spec.questions)
            and all(qa.answer.strip() for qa in existing.questions)):
        questions = existing.questions
    else:
        if spec.questions:
            emit("Ответы на контрольные вопросы")
        questions = answer_questions(spec, profile, subject, llm, rules)
    return refresh_derived(ReportDoc(lab_number=spec.number, sections=sections, questions=questions),
                           spec, profile, enrollment)


def refresh_derived(doc: ReportDoc, spec: LabSpec, profile: Profile, enrollment: Enrollment) -> ReportDoc:
    """Номер, шапка и вывод не пишутся моделью: они всегда берутся из текущего разбора и профиля."""
    return doc.model_copy(update={
        "lab_number": spec.number,
        "header": build_header(spec, enrollment.header_style, profile.group),
        "conclusion": spec.conclusion or goal_to_conclusion(spec.goal, profile.gender).text})
