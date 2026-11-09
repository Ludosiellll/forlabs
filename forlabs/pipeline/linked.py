"""Поиск нужных лаб в общем отчёте, когда заголовков «Лабораторная работа №N» нет или они другие.

Модели уходит не весь отчёт, а его скелет: номер строки и начало строк, похожих на заголовки и ключевые
(«Тема», «Цель», «Вывод», подписи рисунков). Она отвечает, с какой по какую строку идёт каждая нужная лаба,
а текст вырезает программа — так дорого читается только скелет, а модель видит лабу дословно.
"""

from __future__ import annotations

import math
import re

from pydantic import BaseModel, Field

OUTLINE_MAX = 40_000  # знаков скелета (≈13 тыс. токенов) — даже на отчёт в сотню страниц один недорогой запрос
_STRONG = re.compile(r"\bработ|\bЛР\b|\bл/р|\bПР\b|\bтема\b|\bцель\b|\bвывод|\bзадани|\bход\s+работы|"
                     r"\bконтрольн|\bвариант|\bотч[её]т", re.IGNORECASE)
_CODE = re.compile(r"[{};=<>]|^(?:def|class|public|private|protected|import|using|return|#include|var|let|const|"
                   r"function|if|for|while|else|try|catch|static|void|int|string)\b", re.IGNORECASE)

LOCATE_SYSTEM = """Ты находишь в общем отчёте студента (несколько лабораторных подряд) нужные работы.
Тебе дан скелет отчёта: номер строки и начало строки. Показаны не все строки, а только похожие на заголовки
и ключевые («Тема», «Цель», «Вывод», рисунки). Заголовки лаб могли быть оформлены как угодно или пропущены.
Ищи по порядку важности:
1) номер работы в любом виде («ЛР 27», «Работа 27», «27.», номер на титуле);
2) порядок работ: если номеров нет, а работы идут подряд (каждая начинается с «Тема»/«Цель» и кончается
   «Выводом»), посчитай их по порядку;
3) смысл: если ни номера, ни порядка не понять — выбери часть, которая ближе всего по теме к текущей лабе
   (её тема дана), и отметь how = "по смыслу".
Не выдумывай: если похожей части нет — found = false. Границы: start — первая строка работы (заголовок или «Тема»),
end — последняя строка перед следующей работой (или последняя строка отчёта).
Ответ — только JSON: {"labs": [{"number": "27", "found": true, "start": 120, "end": 185,
"how": "по номеру" | "по порядку" | "по смыслу", "why": "коротко, по чему нашёл"}]}"""


class Located(BaseModel):
    number: str | int | float = ""
    found: bool = False
    start: int | None = None
    end: int | None = None
    how: str = ""
    why: str = ""


class Location(BaseModel):
    labs: list[Located] = Field(default_factory=list)


def report_lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def outline(lines: list[str]) -> str:
    """Скелет отчёта: «номер: начало строки» для заголовков и ключевых строк, код и сплошной текст не попадают."""
    strong = {i for i, ln in enumerate(lines) if _STRONG.search(ln[:80])}
    weak = {i for i, ln in enumerate(lines)
            if i not in strong and len(ln) <= 100 and ln[:1].isupper() and not _CODE.search(ln)}
    for keep, width in ((strong | weak, 100), (strong, 70), (strong, 45)):
        rows = [f"{i}: {lines[i][:width]}" for i in sorted(keep)]
        text = "\n".join(rows)
        if len(text) <= OUTLINE_MAX:
            return text
    step = math.ceil(len(text) / OUTLINE_MAX)  # огромный отчёт: равномерно по всему, а не только начало
    return "\n".join(rows[::step])


def locate_labs(text: str, wanted: list[str], context: str, llm) -> dict[str, tuple[str, str]]:
    """Номер → (дословный текст лабы из отчёта, как найдена). Чего модель не нашла или указала неверно — нет."""
    lines = report_lines(text)
    if not lines or not wanted:
        return {}
    user = (f"Нужны работы: {', '.join(wanted)}\n"
            f"Текущая лаба, для которой они нужны (для поиска по смыслу):\n{(context or 'не известна')[:2500]}\n\n"
            f"Всего строк в отчёте: {len(lines)} (с 0 по {len(lines) - 1}).\nСкелет отчёта:\n{outline(lines)}")
    res = llm.json([{"role": "system", "content": LOCATE_SYSTEM}, {"role": "user", "content": user}],
                   Location, max_tokens=1500)
    out: dict[str, tuple[str, str]] = {}
    for x in res.labs:
        n = str(x.number).strip().removesuffix(".0")
        if n not in wanted or n in out or not x.found or x.start is None:
            continue
        end = len(lines) - 1 if x.end is None else min(x.end, len(lines) - 1)  # «до конца» с промахом на строку
        if not (0 <= x.start <= end):
            continue
        how = x.how.strip() if x.how.strip() in ("по номеру", "по порядку", "по смыслу") else "по смыслу"
        out[n] = ("\n".join(lines[x.start:end + 1]), how)
    return out
