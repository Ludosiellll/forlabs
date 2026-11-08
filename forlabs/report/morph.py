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


def is_known_word(word: str) -> bool:
    """Есть ли слово в словаре (для склейки переносов в методичке)."""
    return _morph().word_is_known(word.lower())


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


_CLAUSE_SPLIT = re.compile(r"[,;:()—–]")
_SERVICE_POS = {"ADVB", "CONJ", "PRCL", "INTJ"}


def _is_past_singular_verb(p) -> bool:
    return p.tag.POS == "VERB" and "past" in p.tag and "sing" in p.tag


def _agreeing_subject_after(words: list[str], verb_gender: str) -> bool:
    """«появилась надпись»: подлежащее стоит после глагола и согласовано с ним по роду."""
    for w in words[:2]:
        for p in _morph().parse(w)[:3]:
            if p.tag.POS in ("NOUN", "NPRO") and "nomn" in p.tag and "sing" in p.tag and p.tag.gender == verb_gender:
                return True
        if _morph().parse(w)[0].tag.POS not in ("ADJF", "PRTF", "NUMR"):
            break
    return False


def wrong_gender_verbs(text: str, gender: Gender) -> list[str]:
    """Глаголы прошедшего времени ед. ч. от первого лица, род которых не совпадает с профилем.

    Глагол считается «от первого лица», если в его части предложения перед ним стоит «я» или только
    служебные слова («Затем проверил…»). Пассив «был создан» и инверсия «появилась надпись» не считаются."""
    wrong_tag = "femn" if gender == "m" else "masc"
    found: list[str] = []
    for sent in _SENT.findall(text):
        for clause in _CLAUSE_SPLIT.split(sent):
            words = _WORD.findall(clause)
            lows = [w.lower() for w in words]
            for i, (w, low) in enumerate(zip(words, lows)):
                top = _morph().parse(low)[0]
                if not _is_past_singular_verb(top) or wrong_tag not in top.tag:
                    continue
                after = lows[i + 1:i + 3]
                if top.normal_form == "быть" and any(_morph().parse(a)[0].tag.POS == "PRTS" for a in after):
                    continue
                # Инверсия бывает у непереходных глаголов («появилась надпись»); у переходного
                # существительное после глагола — дополнение («проверил вывод»).
                intransitive = "intr" in top.tag or low.endswith(("ся", "сь"))
                if intransitive and _agreeing_subject_after(after, top.tag.gender):
                    continue
                before = lows[:i]
                if "я" in before or all(_morph().parse(b)[0].tag.POS in _SERVICE_POS for b in before):
                    found.append(w)
    return found
