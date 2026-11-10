"""Общие текстовые утилиты: нормализация для сравнения, разбиение на абзацы."""

from __future__ import annotations

import re

_NON_WORD = re.compile(r"[^0-9a-zа-я]+")


def normalize_for_compare(s: str) -> str:
    """Нижний регистр, ё→е, склейка переносов, только буквы и цифры через один пробел."""
    s = s.replace("­", "")
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)
    s = s.lower().replace("ё", "е")
    return _NON_WORD.sub(" ", s).strip()


def contains_normalized(haystack: str, needle: str) -> bool:
    n = normalize_for_compare(needle)
    return bool(n) and n in normalize_for_compare(haystack)


def plural(n: int, one: str, few: str, many: str) -> str:
    """«1 задание», «3 задания», «6 заданий» — число с существительным в нужной форме."""
    tail = abs(n) % 100
    form = many if 11 <= tail <= 14 else one if tail % 10 == 1 else few if 2 <= tail % 10 <= 4 else many
    return f"{n} {form}"
