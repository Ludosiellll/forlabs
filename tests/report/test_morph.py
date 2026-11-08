import json
import re
from pathlib import Path

import pytest

from forlabs.report.morph import (
    agree_short_participle,
    goal_to_conclusion,
    past_tense,
    performed_word,
    wrong_gender_verbs,
)

PAIRS = json.loads((Path(__file__).parent.parent / "fixtures" / "goal_conclusion_pairs.json").read_text("utf-8"))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().rstrip(".")


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p["goal"][:40])
def test_reference_pairs(pair):
    res = goal_to_conclusion(pair["goal"], "m")
    assert _norm(res.text) == _norm(pair["conclusion"])
    assert res.text.endswith(".")


def test_female_forms():
    assert goal_to_conclusion("Исследовать функциональность систем", "f").text == "Исследовала функциональность систем."
    assert goal_to_conclusion("Научиться настраивать сеть", "f").text == "Научилась настраивать сеть."
    assert goal_to_conclusion("Сформировать умения создавать программы", "f").text == "Сформировала умения создавать программы."
    assert goal_to_conclusion("Научить создавать страницы", "f").text == "Научилась создавать страницы."
    assert goal_to_conclusion("Исследовать особенности и провести анализ кода", "f").text == \
        "Исследовала особенности и провела анализ кода."


def test_prefix_is_stripped():
    assert goal_to_conclusion("Цель работы: Изучить протокол FTP.", "m").text == "Изучил протокол FTP."


def test_non_infinitive_goal_needs_confirm():
    res = goal_to_conclusion("Формирование навыков работы с сетью", "m")
    assert res.needs_confirm is True
    assert res.text.endswith(".")


def test_past_tense_and_performed():
    assert past_tense("научиться", "f") == "научилась"
    assert past_tense("провести", "m") == "провел"
    assert performed_word("m") == "Выполнил"
    assert performed_word("f") == "Выполнила"


@pytest.mark.parametrize("topic,expected", [
    ("Схема классификации систем программирования", "представлена"),
    ("Исходный код программы", "представлен"),
    ("Результаты выполнения запросов", "представлены"),
    ("Окно среды разработки VS Code", "представлено"),
    ("Интерфейс среды разработки IntelliJ IDEA", "представлен"),
])
def test_participle_agreement(topic, expected):
    assert agree_short_participle("представлен", topic) == expected


def test_wrong_gender_verbs():
    assert wrong_gender_verbs("Разработал программу для вывода лесенки.", "f") == ["Разработал"]
    assert wrong_gender_verbs("Программа разработана в среде VS Code.", "f") == []
    assert wrong_gender_verbs("Я создала форму. Затем проверил вывод.", "f") == ["проверил"]
    assert wrong_gender_verbs("Сервер вернул ответ 200.", "f") == []
    assert wrong_gender_verbs("Создала таблицу.", "m") == ["Создала"]


@pytest.mark.parametrize("text,gender,expected", [
    ("Была создана форма входа.", "m", []),
    ("Был создан файл программы.", "f", []),
    ("В окне появилась надпись.", "m", []),
    ("На экране отобразилась таблица.", "m", []),
    ("Сначала был написан код, затем я его запустил.", "f", ["запустил"]),
    ("Затем проверил вывод программы.", "f", ["проверил"]),
    ("Создала таблицу, затем заполнила её.", "m", ["Создала", "заполнила"]),
])
def test_wrong_gender_verbs_precision(text, gender, expected):
    assert wrong_gender_verbs(text, gender) == expected
