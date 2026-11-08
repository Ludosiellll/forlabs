"""Проверки стандарта РС02-23 по структуре отчёта (а не по сырому тексту)."""

from __future__ import annotations

import re

from forlabs.core.models import DashList, Finding, Gender, LabSpec, NumberedList, Paragraph, ReportDoc
from forlabs.core.text import normalize_for_compare
from forlabs.report.morph import goal_to_conclusion, wrong_gender_verbs

REQUIRED_HEADER = [("Номер учебной группы",), ("Дата выполнения работы",), ("Тема работы", "Тема"),
                   ("Цель работы", "Цель"), ("Задание",)]
MAX_ANSWER_WORDS = 22
MAX_ANSWER_CHARS = 165

_DATE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_MANUAL_REF = re.compile(r"(на\s+рисунк\w*|в\s+листинг\w*|в\s+таблиц\w*)\s+\d+|^(Рисунок|Листинг|Таблица)\s+\d+\s*[–—-]",
                         re.IGNORECASE)
# «__» — только как жирный markdown (__слово__), а не имена вроде __init__
_MARKDOWN = re.compile(r"\*\*|(?<![\w.])__(?=[А-Яа-яЁё\s])|^#{1,6}\s|`|\|\s*-{3,}")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF⭐✅❌]")
_CURLY_QUOTES = re.compile(r"[“”„‟]")
_STRAIGHT_RU = re.compile(r"\"[^\"]*[А-Яа-яЁё][^\"]*\"")
_REF_IN_ITEM = re.compile(r"(на\s+рисунк|в\s+листинг|в\s+таблиц)", re.IGNORECASE)
_VARIANT_MENTION = re.compile(r"вариант\w*\s+\d+|приложени[еяи]\s+[А-ЯA-Z]\b", re.IGNORECASE)


def _f(rule: str, msg: str, location: str = "", text: str = "", severity: str = "error") -> Finding:
    return Finding(stage="standard", rule_id=rule, severity=severity, location=location, text=text, message=msg)


def _starts_lower(item: str) -> bool:
    body = item.lstrip("-–— ").strip()
    if not body:
        return True
    first = body[0]
    if not ("А" <= first <= "я" or first in "Ёё"):
        return True  # латиница, цифры, идентификаторы — допустимо
    if len(body) > 1 and (body[1].isupper() or not body[1].isalpha()):
        return True  # аббревиатура
    return first.islower()


def text_items(doc: ReportDoc):
    """(где, текст) для всего текста, написанного моделью."""
    for sec in doc.sections:
        for bi, b in enumerate(sec.blocks, start=1):
            loc = f"Задание {sec.n}, блок {bi}"
            if isinstance(b, Paragraph):
                yield loc, b.text
            elif isinstance(b, (DashList, NumberedList)):
                for item in b.items:
                    yield loc, item
    for qa in doc.questions:
        yield f"Вопрос {qa.n}", qa.answer


def check_standard(doc: ReportDoc, spec: LabSpec, gender: Gender) -> list[Finding]:
    out: list[Finding] = []

    for variants in REQUIRED_HEADER:
        if not any(doc.header.get(k, "").strip() for k in variants):
            out.append(_f("header.missing", f"В шапке нет поля «{variants[0]}»", "Шапка"))
    date = doc.header.get("Дата выполнения работы", "").strip()
    if date and not _DATE.match(date):
        out.append(_f("header.date_format", "Дата должна быть в формате ДД.ММ.ГГГГ", "Шапка", date))

    expected = list(range(1, len(spec.tasks) + 1))
    if [s.n for s in doc.sections] != expected:
        out.append(_f("task.sequence", f"Задания должны идти подряд: {expected}", "Задания"))
    for sec, task in zip(doc.sections, spec.tasks):
        if normalize_for_compare(sec.title) != normalize_for_compare(task.text):
            out.append(_f("task.title_mismatch", "Формулировка задания не совпадает с методичкой",
                          f"Задание {sec.n}", sec.title))

    for sec in doc.sections:
        for bi, b in enumerate(sec.blocks, start=1):
            loc = f"Задание {sec.n}, блок {bi}"
            if isinstance(b, Paragraph) and _MANUAL_REF.search(b.text):
                out.append(_f("ref.manual", "Ссылки на рисунки, листинги и таблицы ставит программа — убери их из текста",
                              loc, b.text))
            if isinstance(b, DashList):
                items = [i.strip() for i in b.items if i.strip()]
                for k, item in enumerate(items):
                    if not _starts_lower(item):
                        out.append(_f("list.lowercase", "Пункт списка начинается со строчной буквы", loc, item))
                    last = k == len(items) - 1
                    if _REF_IN_ITEM.search(item):
                        out.append(_f("list.reference_inside", "Ссылка на рисунок/листинг не ставится внутри пункта",
                                      loc, item))
                    elif last and not item.endswith("."):
                        out.append(_f("list.punctuation", "Последний пункт списка заканчивается точкой", loc, item))
                    elif not last and not item.endswith(";"):
                        out.append(_f("list.punctuation", "Промежуточный пункт списка заканчивается «;»", loc, item))

    spec_q = {normalize_for_compare(q) for q in spec.questions}
    for qa in doc.questions:
        if normalize_for_compare(qa.question) not in spec_q:
            out.append(_f("qa.not_in_spec", "Такого вопроса нет в методичке", f"Вопрос {qa.n}", qa.question))
        ans = qa.answer.strip()
        if len(ans.split()) > MAX_ANSWER_WORDS or len(ans) > MAX_ANSWER_CHARS:
            out.append(_f("qa.answer_too_long", "Ответ должен быть коротким: до 22 слов, одно предложение",
                          f"Вопрос {qa.n}", ans))

    expected_conclusion = spec.conclusion or goal_to_conclusion(spec.goal, gender).text
    if normalize_for_compare(doc.conclusion) != normalize_for_compare(expected_conclusion):
        out.append(_f("conclusion.formula", f"Вывод должен быть: «{expected_conclusion}»", "Вывод", doc.conclusion))

    for loc, text in text_items(doc):
        if not spec.variant_text and _VARIANT_MENTION.search(text):
            out.append(_f("text.phantom_variant", "В методичке нет вариантов и приложений — не упоминай их", loc, text))
        if _MARKDOWN.search(text):
            out.append(_f("text.markdown", "Без markdown-разметки", loc, text))
        if _EMOJI.search(text):
            out.append(_f("text.emoji", "Без эмодзи", loc, text))
        if _CURLY_QUOTES.search(text) or _STRAIGHT_RU.search(text):
            out.append(_f("text.english_quotes", "Кавычки — «ёлочки»", loc, text))
        wrong = wrong_gender_verbs(text, gender)
        if wrong:
            out.append(_f("text.gender", f"Род глаголов не совпадает с профилем: {', '.join(wrong)}", loc, text))
    return out
