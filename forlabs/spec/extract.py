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
    basis: str = "journal"  # journal | workplace | other | all (приложение без вариантов: задания для всех)
    number: int | str | None = None
    total: int | None = None  # сколько вариантов в таблице
    asked: int | None = None  # номер студента, если он больше total и вариант взят по кругу (ставит разбор)
    text: str = ""

    @field_validator("total", "asked", mode="before")
    @classmethod
    def _count(cls, v):
        try:
            return int(v) if v is not None and int(v) > 0 else None
        except (TypeError, ValueError):
            return None

    @field_validator("appendix", "text", mode="before")
    @classmethod
    def _str(cls, v):  # null вместо строки — не повод для платной починки ответа
        return "" if v is None else str(v)

    @field_validator("basis", mode="before")
    @classmethod
    def _basis(cls, v):
        return v or "journal"


class NotALab(Exception):
    """Загружена не методичка лабораторной (курсовой, реферат, произвольный текст)."""

    def __init__(self, reason: str):
        self.message_ru = ((reason.rstrip(". ") + ". ") if reason else "") + (
            "ForLabs делает только лабораторные и практические работы по методичке. Если это всё-таки лабораторная, "
            "загрузи страницы методички с темой, целью и заданиями; если вопрос — напиши в «Поддержку».")
        super().__init__(self.message_ru)


class RawSpec(BaseModel):
    kind: str = "lab"  # lab | not_lab
    not_lab_reason: str = ""
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
    needs: list[str] = Field(default_factory=list)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):  # null и всё непонятное — лаба (сомневаешься — не отказывать)
        return "not_lab" if str(v or "").strip().lower() == "not_lab" else "lab"

    @field_validator("not_lab_reason", mode="before")
    @classmethod
    def _reason(cls, v):
        return "" if v is None else str(v)

    @field_validator("number", mode="before")
    @classmethod
    def _num(cls, v):
        return None if v is None else str(v)

    @field_validator("topic", "goal", "assignment", mode="before")
    @classmethod
    def _str(cls, v):  # промпт разрешает null, когда поля нет в методичке
        return "" if v is None else str(v)

    @field_validator("needs", mode="before")
    @classmethod
    def _needs(cls, v):
        items = [" ".join(str(x).split()).rstrip(".;") for x in v if x is not None] if isinstance(v, list) else []
        lab = re.compile(r"\bЛР\b|\bл/р\b|лабораторн\w*\s+работ", re.IGNORECASE)  # их ищет связь с прошлыми лабами
        return [x for x in items if len(x) > 4 and x.lower() not in ("нет", "ничего", "не требуется")
                and not lab.search(x)][:4]

    @field_validator("main_tasks", "extra_tasks", "questions", mode="before")
    @classmethod
    def _list(cls, v):
        return [str(x) for x in v if x is not None] if isinstance(v, list) else ([] if v is None else v)

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
        if v.basis == "all":  # приложение без вариантов: его задания выполняют все
            lines.append(f"{v.appendix.strip() or 'Приложение'} (вариантов нет, выполняются все задания): "
                         f"{one_line(v.text)}")
            continue
        basis = _BASIS.get(v.basis, "")
        number = f"вариант {v.number}" if v.number not in (None, "", 0, "0") else ""
        if number and v.asked and v.total:
            number += f": номер {v.asked} больше числа вариантов ({v.total}), взят по кругу"
        meta = ", ".join(x for x in (basis, number) if x)
        head = (v.appendix.strip() or "Вариант") + (f" ({meta})" if meta else "")
        lines.append(f"{head}: {one_line(v.text)}")
    return "\n".join(lines) or None


# признаки таблицы вариантов в методичке: без неё «вариантов нет» — правда, с ней — повод сказать студенту
_HAS_VARIANTS = re.compile(r"№\s*вариант|номер\w*\s+вариант|вариант\w*\s*\||согласно\s+вариант|по\s+вариантам|"
                           r"сво(?:его|ему|й)\s+вариант|вашего\s+вариант|в\s+соответствии\s+с\s+вариант",
                           re.IGNORECASE)
_ROW_NUMBER = re.compile(r"\s*(\d{1,3})\s*(?:[.)]\s*\|?|\|)\s*")
_BARE_NUMBER = re.compile(r"\s*(\d{1,3})\s*[.)]?\s*\|?\s*")
NO_VARIANT = ("Вариант не определён: в методичке есть варианты, но строку для номера {n} выбрать не удалось. Не "
              "выбирай вариант сам и не называй его номер: впиши сюда условие своего варианта из методички.")


def wrapped(n: int, total: int) -> int:
    """Номер по журналу больше числа вариантов — вариант берётся по кругу: при 8 вариантах 9 → 1, 14 → 6."""
    return (n - 1) % total + 1


def _as_int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def row_number(source: str, text: str) -> int | None:
    """Номер строки таблицы вариантов, из которой взят текст: число в начале той же строки методички («6. | Дано…»)
    или на строке выше (PDF кладёт ячейки таблицы на разные строки). None — номера рядом нет."""
    need = normalize_for_compare(text)[:40]
    if len(need) < 8:
        return None
    lines = source.split("\n")
    for i, line in enumerate(lines):
        m = _ROW_NUMBER.match(line)
        if m and normalize_for_compare(line[m.end():]).startswith(need):
            return int(m.group(1))
        if normalize_for_compare(line).startswith(need):
            above = next((x for x in reversed(lines[:i]) if x.strip()), "")
            alone = _BARE_NUMBER.fullmatch(above)
            return int(alone.group(1)) if alone else None
    return None


def variant_problems(picks: list[VariantPick], source: str, variant: int, workplace: int) -> list[tuple[str, str]]:
    """Что не так с выбранными вариантами: (приложение, что нужно). Пусто — строки взяты верно."""
    hay = normalize_for_compare(source)
    out = []
    for v in picks:
        name = v.appendix.strip() or "Приложение"
        if not v.text.strip():
            out.append((name, "нет текста варианта"))
            continue
        if normalize_for_compare(v.text) not in hay and normalize_for_compare(v.text).replace(" ", "") not in \
                hay.replace(" ", ""):
            out.append((name, "текст варианта должен быть дословно из методички"))
            continue
        if v.basis not in ("journal", "workplace"):
            continue
        asked = workplace if v.basis == "workplace" else variant
        need = wrapped(asked, v.total) if v.total and asked > v.total else asked
        got = _as_int(v.number)
        row = row_number(source, v.text)
        if got != need or (row is not None and row != need):
            why = f"вариантов {v.total}, номер студента {asked}: " if v.total and asked > v.total else ""
            out.append((name, f"{why}нужен вариант {need}" + (f", а текст взят из строки {row}"
                                                               if row is not None and row != need else "")))
    return out


_NEXT_STEP = re.compile(r"(?:^|\s)2[.)]\s*\S")
_STEP_MARK = re.compile(r"(?<![\w.,])\d{1,2}[.)](?=\s|[^\W\d])")  # «2.», «3)» — номер шага внутри задания
_TASK_HEADING = re.compile(r"^\s*Задание\s*(?:№\s*)?(\d{1,2})\s*[.:)]?(?=\s|$)", re.IGNORECASE | re.MULTILINE)


# раздел вопросов — отдельная строка-заголовок; «…и ответить на контрольные вопросы» в задании разделом не считается
_QUESTIONS_HEADING = re.compile(r"^[ \t]*(?:\d+[.)]?[ \t]*)?контрольные[ \t]+вопросы(?:[ \t]+(?:и|к)[ \t]+[^\n]{0,40})?"
                                r"[ \t]*[:.]?[ \t]*$", re.IGNORECASE | re.MULTILINE)


def clean_item(s: str) -> str:
    """Задание одной строкой, без своего номера в списке. Если задание — это нумерованные шаги («1. … 2. … 3. …»),
    первый номер остаётся: без него шаги читались бы как «… 2. … 3. …»."""
    line = one_line(s)
    m = _LEADING_NUMBER.match(line) or re.match(r"\s*\d+[.)]", line)
    if m and re.match(r"\s*1[.)]", line) and _NEXT_STEP.search(line[m.end():]):
        return line.strip()
    return _LEADING_NUMBER.sub("", line).strip()


def task_headings(text: str) -> int:
    """Сколько заданий в методичке по заголовкам «Задание 1», «Задание 2»… (подряд с первого). 0 — заголовков нет
    или они не по порядку: тогда число заданий определяет модель."""
    seen: list[int] = []
    for m in _TASK_HEADING.finditer(text):
        n = int(m[1])
        if n not in seen:
            seen.append(n)
    return len(seen) if len(seen) >= 2 and seen == list(range(1, len(seen) + 1)) else 0


def clean_field(s: str | None) -> str:
    return _LABEL.sub("", one_line(s or "")).strip()


_PICTURE_WRAP = re.compile(r"\[Картинка в методичке:|^\][ \t]*$", re.MULTILINE)


def verify_verbatim(spec: LabSpec, src: SourceText) -> list[Finding]:
    # обёртка блока картинки — служебные слова: формулировка, которая продолжается на картинке, с ними не сверяется
    src = SourceText(_PICTURE_WRAP.sub(" ", src.text))
    hay = normalize_for_compare(src.text)
    hay_tight = hay.replace(" ", "")  # PDF рвёт слова пробелами («Лич ные»): сверяем и без пробелов
    out = []
    checks = [(f"Задание {t.n}", t.text, "error") for t in spec.tasks]
    checks += [(f"Вопрос {i}", q, "error") for i, q in enumerate(spec.questions, start=1)]
    checks += [("Тема", spec.topic, "warn"), ("Цель", spec.goal, "warn")]
    bare = normalize_for_compare(_STEP_MARK.sub(" ", src.text)).replace(" ", "")  # без номеров шагов

    def same_but_numbers(text: str) -> bool:
        """Текст тот же, выпали или сдвинулись только номера шагов внутри задания — это не другая формулировка."""
        mine = normalize_for_compare(_STEP_MARK.sub(" ", text)).replace(" ", "")
        return bool(mine) and mine in bare

    for loc, text, severity in checks:
        n = normalize_for_compare(text)
        if n and n not in hay and n.replace(" ", "") not in hay_tight and not same_but_numbers(text):
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
    hint = (f" Номер работы: {lab_number}. Если в тексте несколько работ — разбирай только работу №{lab_number}."
            if lab_number else "")
    text, truncated = _cut_source(src.text, lab_number)
    headed = task_headings(text)  # заголовки «Задание 1», «Задание 2»… — столько и заданий
    if headed:
        hint += (f" В методичке есть заголовки «Задание 1» … «Задание {headed}»: основных заданий ровно {headed}. "
                 "Нумерованные шаги под заголовком — части этого задания, а не отдельные задания: запиши их в текст "
                 "задания одной строкой с их номерами, включая первый («1. … 2. … 3. …»).")
    messages = [
        {"role": "system", "content": SPEC_SYSTEM.format(variant=variant, workplace=workplace)},
        {"role": "user", "content": SPEC_USER.format(subject=subject.name, variant=variant, number_hint=hint,
                                                     workplace=enrollment.workplace or "не указан",
                                                     wishes_block=_wishes_block(wishes),
                                                     text=text)},
    ]
    raw: RawSpec = llm.json(messages, RawSpec, temperature=0.0)
    if str(raw.kind).strip().lower() == "not_lab":
        raise NotALab(clean_field(raw.not_lab_reason))
    got = len([t for t in raw.main_tasks if t.strip()])
    if headed and got != headed:  # шаги одного задания разошлись по отдельным (или задания слиплись): ещё раз
        again = [*messages, {"role": "user", "content":
                 f"В твоём ответе основных заданий {got}, а в методичке их ровно {headed} — по заголовкам «Задание 1» "
                 f"… «Задание {headed}». Верни JSON заново: в main_tasks {headed} строк, в каждой — всё задание "
                 "целиком со всеми его нумерованными шагами."}]
        try:
            second: RawSpec = llm.json(again, RawSpec, temperature=0.0)
            if str(second.kind).strip().lower() != "not_lab" and second.main_tasks:
                raw = second
        except Exception:  # noqa: BLE001 — второй ответ не получился: остаётся первый, студент увидит замечание
            pass
    asked_variant = variant if isinstance(variant, int) else _as_int(variant) or 1
    asked_place = workplace if isinstance(workplace, int) else _as_int(workplace) or asked_variant
    has_variants = bool(_HAS_VARIANTS.search(text))
    problems = variant_problems(raw.variants, src.text, asked_variant, asked_place)
    if problems or (has_variants and not raw.variants and not raw.variant_text):
        wrong = ("; ".join(f"«{name}»: {what}" for name, what in problems) if problems else
                 f"в методичке есть таблица вариантов, а variants пуст: номер студента по журналу — {asked_variant}, "
                 f"номер рабочего места — {asked_place}; если номер больше числа вариантов, вариант берётся по кругу")
        again = [*messages, {"role": "user", "content":
                 f"Варианты выбраны неверно — {wrong}. Верни JSON заново: в variants для каждого приложения с "
                 "вариантами — номер взятой строки (number), число вариантов в таблице (total) и дословный текст "
                 "именно этой строки."}]
        try:
            second: RawSpec = llm.json(again, RawSpec, temperature=0.0)
            if str(second.kind).strip().lower() != "not_lab" and (second.variants or not raw.variants):
                raw.variants, raw.variant_text = second.variants, second.variant_text
        except Exception:  # noqa: BLE001 — остаётся первый ответ, студент увидит замечание
            pass
        problems = variant_problems(raw.variants, src.text, asked_variant, asked_place)
    for v in raw.variants:  # номер больше таблицы: в тексте варианта и в замечании — и свой номер, и взятый
        need = asked_place if v.basis == "workplace" else asked_variant
        if v.basis in ("journal", "workplace") and v.total and need > v.total:
            v.asked = need
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
        needs=list(raw.needs),
        source_text=src.text,
    )
    findings = verify_verbatim(spec, src)
    for v in raw.variants:
        if v.asked and v.total and v.text.strip():
            findings.append(Finding(
                stage="spec", rule_id="spec.variant_wrapped", severity="warn", location="Вариант",
                message=f"В «{v.appendix.strip() or 'приложении'}» вариантов {v.total}, а твой номер — {v.asked}: "
                        f"взят вариант {v.number} (по кругу). Если преподаватель назначил другой — поправь условие "
                        "варианта на этой вкладке"))
    for name, what in problems:
        findings.append(Finding(
            stage="spec", rule_id="spec.variant_unsure", severity="warn", location="Вариант",
            message=f"«{name}»: не уверен, что взята твоя строка ({what}). Сверь условие варианта с методичкой и "
                    "поправь его на этой вкладке"))
    if has_variants and not spec.variant_text:
        spec.variant_text = NO_VARIANT.format(n=asked_variant)
        findings.append(Finding(
            stage="spec", rule_id="spec.variant_missing", severity="warn", location="Вариант",
            message="В методичке есть варианты, но выбрать твой не получилось — впиши условие своего варианта "
                    "в поле «Текст варианта», иначе отчёт выйдет без него"))
    if spec.needs:
        findings.append(Finding(
            stage="spec", rule_id="spec.needs_material", severity="warn", location="Материалы",
            message="Для работы нужно то, чего нет в методичке: " + "; ".join(spec.needs) + ". Прикрепи это файлом "
                    "к лабе или опиши в пожелания — иначе ForLabs составит свой пример, и он может не совпасть с твоим"))
    if any(v.basis == "workplace" for v in raw.variants) and enrollment.workplace is None:
        findings.append(Finding(stage="spec", rule_id="spec.workplace_missing", severity="warn", location="Вариант",
                                message="Методичка выбирает вариант по номеру рабочего места — укажи его в профиле "
                                        "(пока взят вариант по журналу)"))
    if headed and len(main) != headed:
        findings.append(Finding(stage="spec", rule_id="spec.tasks_count", severity="warn", location="Задания",
                                message=f"В методичке заданий по заголовкам: {headed}, а разобрано: {len(main)}. "
                                        "Проверь, не разошлись ли шаги одного задания по разным строкам"))
    if not spec.tasks:
        findings.append(Finding(stage="spec", rule_id="spec.no_tasks", location="Задания",
                                message="В методичке не нашлось заданий — добавь их вручную"))
    if truncated:
        findings.append(Finding(stage="spec", rule_id="spec.truncated", severity="warn", location="Методичка",
                                message="Методичка очень длинная — модели отдана только часть рядом с этой работой. "
                                        "Проверь, что задания и вопросы — из нужной лабы"))
    if not spec.questions and _QUESTIONS_HEADING.search(src.text):
        findings.append(Finding(stage="spec", rule_id="spec.questions_missing", severity="warn", location="Вопросы",
                                message="В методичке есть «Контрольные вопросы», но прочитать их не удалось "
                                        "(возможно, они картинкой) — впиши вопросы на этой вкладке"))
    return spec, findings


def _cut_source(text: str, lab_number: str | None) -> tuple[str, bool]:
    """Длинный сборник: кусок вокруг «Лабораторная работа №N» (последнее упоминание — не оглавление), а не начало."""
    if len(text) <= MAX_SOURCE_CHARS:
        return text, False
    if lab_number:
        pat = re.compile(rf"(лабораторн\w*\s+работ\w*|практическ\w*\s+работ\w*|\bЛР)\s*№?\s*{re.escape(lab_number)}"
                         rf"(?![\d.])", re.I)
        hits = [m.start() for m in pat.finditer(text)]
        if hits:
            start = max(0, hits[-1] - 500)
            return text[start:start + MAX_SOURCE_CHARS], True
    return text[:MAX_SOURCE_CHARS], True
