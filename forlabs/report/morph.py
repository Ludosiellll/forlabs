"""Морфология русского языка: вывод из цели по полу профиля, согласование подводок, проверка рода глаголов."""

from __future__ import annotations

import re
from functools import lru_cache

import pymorphy3
from pydantic import BaseModel

from forlabs.core.models import Gender

_WORD = re.compile(r"[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*")
_PREFIX = re.compile(r"^\s*(?:Цел[иь](?:\s+работы)?)\s*:\s*", re.IGNORECASE)
# В методичках цель часто сформулирована от лица преподавателя («Научить…»):
# студент в выводе пишет о себе — «Научился…».
_TEACHER_VERBS = {"научить": "научиться", "обучить": "обучиться"}
_GENDER_TAG = {"m": "masc", "f": "femn"}


@lru_cache(maxsize=1)
def _morph() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()


class ConclusionResult(BaseModel):
    text: str
    needs_confirm: bool = False
    note: str | None = None


def _infn_parse(word: str):
    for p in _morph().parse(word.lower()):
        if p.tag.POS == "INFN":
            return p
    return None


def _match_case(src: str, dst: str) -> str:
    return dst[:1].upper() + dst[1:] if src[:1].isupper() else dst


def past_tense(infinitive: str, gender: Gender) -> str:
    p = _infn_parse(infinitive)
    if p is None:
        raise ValueError(f"«{infinitive}» — не инфинитив")
    form = p.inflect({"past", "sing", _GENDER_TAG[gender]})
    if form is None:
        raise ValueError(f"Нет формы прошедшего времени для «{infinitive}»")
    return _match_case(infinitive, form.word.replace("ё", "е"))


def performed_word(gender: Gender) -> str:
    return "Выполнил" if gender == "m" else "Выполнила"


def goal_to_conclusion(goal: str, gender: Gender) -> ConclusionResult:
    text = _PREFIX.sub("", goal.strip()).strip().rstrip(".").strip()
    words = list(_WORD.finditer(text))
    if not words or _infn_parse(words[0].group()) is None:
        return ConclusionResult(text=f"{text}.", needs_confirm=True,
                                note="Цель не начинается с инфинитива — проверь вывод ниже и исправь его, если нужно.")
    replacements: list[tuple[int, int, str]] = []
    first = words[0].group()
    main = _TEACHER_VERBS.get(first.lower(), first)
    replacements.append((words[0].start(), words[0].end(), _match_case(first, past_tense(main, gender))))
    infinitive_seen = False
    for i, m in enumerate(words[1:], start=1):
        w = m.group()
        if _infn_parse(w) is None:
            continue
        before = text[words[i - 1].end():m.start()]
        prev_word = words[i - 1].group().lower()
        coordinated = prev_word == "и" or "," in before or ";" in before
        if not infinitive_seen and coordinated and prev_word != "не":
            replacements.append((m.start(), m.end(), past_tense(w, gender)))
        infinitive_seen = True
    out = text
    for start, end, new in sorted(replacements, reverse=True):
        out = out[:start] + new + out[end:]
    return ConclusionResult(text=f"{out}.")


def agree_short_participle(base: str, topic: str) -> str:
    """Согласует «представлен/приведен/показан» с главным словом темы."""
    gender_number = None
    tokens = _WORD.findall(topic)[:6]
    for w in tokens:
        parses = [p for p in _morph().parse(w.lower()) if p.tag.POS == "NOUN" and "nomn" in p.tag]
        if parses:
            t = parses[0].tag
            gender_number = "plur" if t.number == "plur" else t.gender
            break
    if gender_number is None and tokens:
        t = _morph().parse(tokens[0].lower())[0].tag
        gender_number = "plur" if t.number == "plur" else (t.gender or "masc")
    suffix = {"masc": "", "femn": "а", "neut": "о", "plur": "ы"}.get(gender_number or "masc", "")
    return base + suffix


_SENT = re.compile(r"[^.!?]+[.!?]?")


def wrong_gender_verbs(text: str, gender: Gender) -> list[str]:
    """Глаголы прошедшего времени ед. ч. «от первого лица» с родом, не совпадающим с профилем."""
    wrong_tag = "femn" if gender == "m" else "masc"
    found: list[str] = []
    for sent in _SENT.findall(text):
        has_subject = False
        for m in _WORD.finditer(sent):
            w = m.group()
            low = w.lower()
            top = _morph().parse(low)[0]
            if top.tag.POS == "VERB" and "past" in top.tag and "sing" in top.tag:
                if not has_subject and wrong_tag in top.tag:
                    found.append(w)
                continue
            if low == "я" or top.tag.POS in ("ADVB", "CONJ", "PRCL", "PREP"):
                continue
            # у неодушевлённых существительных им. и вин. падежи совпадают — смотрим все разборы
            if any(p.tag.POS in ("NOUN", "NPRO") and "nomn" in p.tag for p in _morph().parse(low)[:3]):
                has_subject = True
    return found
