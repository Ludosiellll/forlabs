"""Сходство работ разных профилей одной лабы (шинглы по 5 слов, мера Жаккара)."""

from __future__ import annotations

import re

from forlabs.core.models import Finding

_WORD = re.compile(r"[0-9a-zа-яё]+")
SHINGLE = 5


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = _WORD.findall(text.lower().replace("ё", "е"))
    if len(words) < SHINGLE:
        return {tuple(words)} if words else set()
    return {tuple(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def similarity(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def scan_similarity(text: str, others: dict[str, str], threshold: float = 0.35) -> list[Finding]:
    out = []
    for who, other in others.items():
        s = similarity(text, other)
        if s >= threshold:
            out.append(Finding(stage="antiai", rule_id="ai.similar_profile", severity="error", location="Отчёт",
                               text=who, message=f"Текст на {round(s * 100)}% совпадает с работой профиля «{who}»",
                               suggestion="переписать своими словами, другие примеры"))
    return out
