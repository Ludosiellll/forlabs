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
