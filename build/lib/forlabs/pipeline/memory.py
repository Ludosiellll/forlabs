"""Память предмета: короткая выжимка прошлых лаб (тема сайта, проект, база), чтобы следующие лабы продолжали её."""

from __future__ import annotations

import re
from typing import Callable

from forlabs.core import paths
from forlabs.core.fs import atomic_write_text
from forlabs.core.models import LabState
from forlabs.core.store import safe_name
from forlabs.pipeline.write import section_text

MAX_CHARS = 8000  # ~13 лаб по 600 символов: хватает на семестр
MAX_LAB_SUMMARY = 600  # одна лаба не вытесняет память о прежних


def _merge(old: str, number: str, summary: str) -> str:
    """Строка «ЛР №N: …» заменяет прежнюю строку этой лабы; старейшие строки уходят целиком, а не обрезаются."""
    line = f"ЛР №{number}: " + " ".join(summary.split())[:MAX_LAB_SUMMARY]
    lines = [ln for ln in old.splitlines() if ln.strip() and not ln.startswith(f"ЛР №{number}:")] + [line]
    while len("\n".join(lines)) > MAX_CHARS and len(lines) > 1:
        lines.pop(0)
    return "\n".join(lines)
PROMPT = ("Сожми выполненную лабораторную работу в 2–5 коротких строк для памяти следующих работ этого предмета: "
          "выбранная тема/проект/предметная область, ключевые имена (файлы, классы, объекты, страницы), "
          "что уже сделано. Если работа задаёт или продолжает сквозную тему или проект, который продолжат следующие "
          "работы (например сайт кофейни «Зерно», база данных библиотеки), первой строкой напиши «Тематика: …» "
          "коротко. Без оценок и воды, по-русски, обычным текстом без markdown.")

_LAB_REF = re.compile(r"(?:лабораторн\w*\s+работ\w*|\bЛР|\bл\.?\s*/?\s*р\.?)\s*(?:№\s*|No\.?\s*)?(\d+(?:\.\d+)?)"
                      r"(?![\d])(?!\s*(?:час|ак\w*\s*час|мин|стр|балл|недел|%))",  # «работы 2 часа» — не ссылка
                      re.IGNORECASE)
_PREV_REF = re.compile(r"(?:предыдущ|прошл)\w*\s+(?:лабораторн\w*\s+)?работ", re.IGNORECASE)


_NUM = re.compile(r"\d+(?:\.\d+)?")
# заголовок лабы в любом привычном виде, в начале короткой строки или ячейки таблицы: «ЛАБОРАТОРНАЯ РАБОТА 1»,
# «Лаб. работа № 2», «ЛР 3.», «Л/р №4», «Практическая работа 5», «Отчёт о выполнении лабораторной работы №6»,
# «к лабораторной работе №7», «Иванов | Лабораторная работа No 8»
_HEAD = re.compile(r"^(?:[^|\n]{0,80}\|\s*)*[ \t]*(?:Отч[её]т\s+(?:по|о)\s+(?:выполнении\s+)?|к\s+)?"
                   r"(?:(?:лабораторн|практическ)\w*\.?\s+(?:работ|заняти)\w*|лаб\.?\s*раб\w*\.?|"
                   r"пр(?:акт)?\.\s*раб\w*\.?|ЛР|ПР|Л/Р|П/Р)\s*(?:№|No\.?)?\s*(\d+(?:\.\d+)?)(?![\d])"
                   r"(?!\s*(?:[-–—]|,|и\b)\s*\d)(?=[^\n]{0,200}$)",  # «работам 24–28», «работы 1, 2» — не заголовок
                   re.IGNORECASE | re.MULTILINE)
LINKED_MAX = 12000  # символов одной прошлой лабы — модель видит её, но лимиты не съедаются
LINKED_TOTAL = 36000  # на все связанные лабы вместе (уходят в каждое задание)


def norm_number(n: str) -> str:
    """«04» → «4», «05.2» → «5.2»: номер из отчёта совпадает с набранным."""
    head, _, tail = (n or "").partition(".")
    head = head.lstrip("0") or ("0" if head else "")
    return f"{head}.{tail}" if tail else head


def parse_numbers(raw: str) -> list[str]:
    """«1, 27; ЛР 5.2 и 30» → ["1", "27", "5.2", "30"] (без повторов, не больше пяти)."""
    out: list[str] = []
    for n in map(norm_number, _NUM.findall(raw or "")):
        if n not in out:
            out.append(n)
    return out[:5]


def split_labs(text: str) -> dict[str, str]:
    """Общий отчёт → {номер: текст этой лабы} по заголовкам. Заголовок, повторённый на каждой странице PDF, не режет
    лабу на страницы: подряд идущие куски с одним номером склеиваются. Из оставшихся кусков одного номера (строка
    оглавления и сама лаба) берётся самый длинный."""
    text = text or ""
    heads = list(_HEAD.finditer(text))
    pieces: list[tuple[str, int, int]] = []
    for i, m in enumerate(heads):
        n, end = norm_number(m.group(1)), heads[i + 1].start() if i + 1 < len(heads) else len(text)
        if pieces and pieces[-1][0] == n:
            pieces[-1] = (n, pieces[-1][1], end)
        else:
            pieces.append((n, m.start(), end))
    out: dict[str, str] = {}
    for n, start, end in pieces:
        part = text[start:end].strip()
        if len(part) > len(out.get(n, "")):
            out[n] = part
    return out


def _as_float(n: str) -> float | None:
    try:
        return float(n.split("-")[0])
    except ValueError:
        return None


def referenced_labs(text: str, current: str) -> list[str]:
    """Номера прошлых лаб, на которые ссылается эта лаба («из ЛР №1», «лабораторной работы 3»), и «previous» для
    «предыдущей работы». Смотрится только своя часть сборника; заголовки других лаб, будущие лабы и «работы 2 часа»
    ссылками не считаются; не больше трёх."""
    cur = norm_number((current or "").split("-")[0])
    text = text or ""
    own = split_labs(text).get(cur)
    if own:
        text = own
    heads = {m.start(1) for m in _HEAD.finditer(text)}
    cur_f = _as_float(cur)
    out: list[str] = []
    for m in _LAB_REF.finditer(text):
        n = norm_number(m.group(1))
        if m.start(1) in heads or n == cur or n in out:
            continue
        n_f = _as_float(n)
        if cur_f is not None and n_f is not None and n_f >= cur_f:
            continue
        out.append(n)
    if _PREV_REF.search(text) and "previous" not in out:
        out.append("previous")
    return out[:3]


def teacher_key(rule: str) -> str:
    """Короткий ключ требования (по тексту): удаление не зависит от порядка в списке."""
    import hashlib

    return hashlib.sha1(rule.strip().encode("utf-8")).hexdigest()[:10]


class SubjectMemory:
    def __init__(self, read_fn: Callable[[str, str], str] | None = None,
                 write_fn: Callable[[str, str, str], None] | None = None):
        self._read = read_fn or self._read_local
        self._write = write_fn or self._write_local

    @staticmethod
    def _file(profile_id: str, subject_id: str):
        return paths.data_home() / "memory" / safe_name(profile_id) / f"{safe_name(subject_id)}.md"

    def _read_local(self, profile_id: str, subject_id: str) -> str:
        f = self._file(profile_id, subject_id)
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def _write_local(self, profile_id: str, subject_id: str, text: str) -> None:
        atomic_write_text(self._file(profile_id, subject_id), text)

    def read(self, profile_id: str, subject_id: str) -> str:
        return self._read(profile_id, subject_id)

    def write(self, profile_id: str, subject_id: str, text: str) -> None:
        self._write(profile_id, subject_id, text[-MAX_CHARS:])

    def update(self, st: LabState, llm) -> str:
        if not st.report or not st.spec:
            return self.read(st.profile_id, st.subject_id)
        body = "\n\n".join(f"Задание {s.n}: {s.title}\n{section_text(s)}" for s in st.report.sections)
        summary = llm.chat([{"role": "system", "content": PROMPT},
                            {"role": "user", "content": f"ЛР №{st.number}. Тема: {st.spec.topic}\n\n{body[:20000]}"}],
                           temperature=0.2).strip()
        lines = summary.splitlines()
        if lines and lines[0].lower().startswith("тематика:"):
            theme = lines[0].split(":", 1)[1].strip(" .«»\"")
            summary = "\n".join(lines[1:]).strip()
            if theme and not self.theme(st.profile_id, st.subject_id):  # первая тематика; правка студента — главнее
                self.set_theme(st.profile_id, st.subject_id, lines[0].split(":", 1)[1].strip(" ."))
        text = _merge(self.read(st.profile_id, st.subject_id), st.number, summary)
        self.write(st.profile_id, st.subject_id, text)
        return text

    # тематика предмета (сквозной проект): отдельная запись, не вытесняется выжимками лаб, правится студентом
    def theme(self, profile_id: str, subject_id: str) -> str:
        return self._read(profile_id, f"{subject_id}.theme").strip()

    def set_theme(self, profile_id: str, subject_id: str, text: str) -> None:
        self._write(profile_id, f"{subject_id}.theme", " ".join((text or "").split())[:300])

    # требования преподавателя: отдельная запись, не обрезается вместе с выжимками лаб
    MAX_TEACHER = 30

    def teacher(self, profile_id: str, subject_id: str) -> str:
        return self._read(profile_id, f"{subject_id}.teacher")

    def add_teacher(self, profile_id: str, subject_id: str, rule: str) -> str:
        rule = " ".join(rule.split()).strip(" -—–").rstrip(".") + "."
        lines = [ln for ln in self.teacher(profile_id, subject_id).splitlines() if ln.strip()]
        if rule.lower() not in {ln.lstrip("- ").lower() for ln in lines}:
            lines.append(f"- {rule}")
        text = "\n".join(lines[-self.MAX_TEACHER:])
        self._write(profile_id, f"{subject_id}.teacher", text)
        return text

    def teacher_list(self, profile_id: str, subject_id: str) -> list[str]:
        return [ln.lstrip("- ").strip() for ln in self.teacher(profile_id, subject_id).splitlines() if ln.strip()]

    def remove_teacher(self, profile_id: str, subject_id: str, key: str) -> str:
        items = [x for x in self.teacher_list(profile_id, subject_id) if teacher_key(x) != key]
        text = "\n".join(f"- {x}" for x in items)
        self._write(profile_id, f"{subject_id}.teacher", text)
        return text

