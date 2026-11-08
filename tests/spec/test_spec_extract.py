import os
from pathlib import Path

import pytest

from forlabs.core.llm import LLM, FakeLLM
from forlabs.core.models import Enrollment
from forlabs.core.settings import Settings
from forlabs.core.subjects import SUBJECTS
from forlabs.ingest.extract import SourceText, normalize
from forlabs.spec.extract import extract_spec, merge_subitems, one_line, verify_verbatim

SRC = SourceText(normalize((Path(__file__).parents[1] / "fixtures" / "methodology_psip_lr7.txt").read_text("utf-8")))
ENR = Enrollment(subject_id="psip", title_subject="ПССИП", variant=14, header_style="short")
TASKS = ["Создайте 2 простых анимации к выбранной тематике.", "Создайте анимацию с эффектом параллакса.",
         "Создайте анимированное меню.", "Создайте анимированные кнопки.", "Создайте слайдер изображений.",
         "Создайте анимированный заголовок."]
RAW = {"number": "7", "topic": "Создание визуальных эффектов и анимации на HTML странице.",
       "goal": "Сформировать умение создавать визуальные эффекты и анимации на HTML – странице.",
       "equipment": "Методические указания к выполнению, ОС Windows, Sublime Text, MS Word.",
       "assignment": "Задания выполняются к своей теме из ЛР №2", "main_tasks": TASKS[:5], "extra_tasks": TASKS[5:],
       "variant_text": None,
       "questions": ["Hover-эффекты и их применение.", "Назовите наиболее распространенные hover-эффекты.",
                     "Алгоритм действия эффекта перекатывания.", "Применение hover-эффектов к кнопкам."]}


def test_extract_with_fake_llm():
    spec, findings = extract_spec(SRC, SUBJECTS["psip"], ENR, FakeLLM([RAW]), lab_number=None, date="24.09.2026")
    assert findings == []
    assert [t.n for t in spec.tasks] == [1, 2, 3, 4, 5, 6]
    assert spec.tasks[5].kind == "extra" and spec.tasks[0].kind == "main"
    assert spec.number == "7" and spec.variant == 14 and spec.date == "24.09.2026"
    assert spec.topic == "Создание визуальных эффектов и анимации на HTML странице"
    assert spec.goal.endswith("HTML – странице")
    assert len(spec.questions) == 4


def test_numbers_stripped_and_lines_joined():
    raw = dict(RAW, main_tasks=["1. Создайте 2 простых анимации\nк выбранной тематике."] + TASKS[1:5])
    spec, findings = extract_spec(SRC, SUBJECTS["psip"], ENR, FakeLLM([raw]), lab_number="7", date="24.09.2026")
    assert spec.tasks[0].text == TASKS[0]
    assert findings == []


def test_verbatim_detects_paraphrase():
    raw = dict(RAW, main_tasks=["Сделать две анимации по теме."] + TASKS[1:5])
    spec, findings = extract_spec(SRC, SUBJECTS["psip"], ENR, FakeLLM([raw]), lab_number="7", date="24.09.2026")
    assert [f.rule_id for f in findings] == ["spec.not_verbatim"]
    assert findings[0].location == "Задание 1"


def test_verify_question_paraphrase():
    raw = dict(RAW, questions=["Что такое hover?"] + RAW["questions"][1:])
    spec, _ = extract_spec(SRC, SUBJECTS["psip"], ENR, FakeLLM([raw]), lab_number="7", date="24.09.2026")
    assert [f.location for f in verify_verbatim(spec, SRC)] == ["Вопрос 1"]


def test_prompt_mentions_variant_and_verbatim():
    fake = FakeLLM([RAW])
    extract_spec(SRC, SUBJECTS["psip"], ENR, fake, lab_number=None, date="24.09.2026")
    prompt = "\n".join(m["content"] for m in fake.calls[0])
    assert "дословно" in prompt.lower() and "14" in prompt
    assert "Создайте анимированное меню" in prompt


@pytest.mark.live
def test_extract_live():
    s = Settings(base_url=os.environ.get("FORLABS_BASE_URL", Settings().base_url),
                 model=os.environ.get("FORLABS_MODEL", Settings().model))
    llm = LLM(s, api_key=os.environ["FORLABS_API_KEY"])
    spec, findings = extract_spec(SRC, SUBJECTS["psip"], ENR, llm, lab_number=None, date="24.09.2026")
    assert spec.number == "7"
    assert "визуальных эффектов" in spec.topic
    assert len(spec.tasks) == 6
    assert len(spec.questions) == 4
    assert [f for f in findings if f.severity == "error"] == []


SSP_ENR = Enrollment(subject_id="ssp", title_subject="ССП", variant=14, workplace=2)
SSP_SRC = SourceText(normalize(
    "Лабораторная работа №4\nТема работы: Ссылочные типы Java.\nЦель работы: Сформировать умения.\n"
    "Задание №1:\nВыполните задание согласно варианту , указанному в приложении А.\n"
    "Вариант выбрать в соответствии с номером рабочего места.\nЗадание №2.\n"
    "Построить иерархию классов в соответствии с вариантом задания, указанным в приложении Б. Вариант выбрать в "
    "соответствии с номером по журналу.\nДля разработанной Вами иерархии классов в методе Main:\n"
    "1) описать массив объектов базового класса;\n2) занести в этот массив объекты дочерних классов;\n"
    "3) продемонстрировать работу методов класса у всех элементов этого массива.\n"
    "Приложение А\n2. Создать класс Payment (покупка) с внутренним классом.\nПриложение Б\n14 Организация\n"))
SSP_RAW = {"number": "4", "topic": "Ссылочные типы Java", "goal": "Сформировать умения",
           "assignment": "Выполните задание согласно варианту , указанному в приложении А.",
           "main_tasks": ["Выполните задание согласно варианту , указанному в приложении А. Вариант выбрать в "
                          "соответствии с номером рабочего места.",
                          "Построить иерархию классов в соответствии с вариантом задания, указанным в приложении Б. "
                          "Вариант выбрать в соответствии с номером по журналу. Для разработанной Вами иерархии "
                          "классов в методе Main:",
                          "описать массив объектов базового класса;",
                          "занести в этот массив объекты дочерних классов;",
                          "продемонстрировать работу методов класса у всех элементов этого массива."],
           "extra_tasks": [],
           "variants": [{"appendix": "Приложение А", "basis": "workplace", "number": 2,
                         "text": "Создать класс Payment (покупка) с внутренним классом."},
                        {"appendix": "Приложение Б", "basis": "journal", "number": 14, "text": "Организация"}],
           "questions": []}


def test_subitems_merged_into_parent_task():
    spec, findings = extract_spec(SSP_SRC, SUBJECTS["ssp"], SSP_ENR, FakeLLM([SSP_RAW]), lab_number="4",
                                  date="16.09.2026")
    assert len(spec.tasks) == 2
    assert spec.tasks[1].text.endswith("в методе Main: 1) описать массив объектов базового класса; "
                                       "2) занести в этот массив объекты дочерних классов; 3) продемонстрировать "
                                       "работу методов класса у всех элементов этого массива.")
    assert findings == []


def test_lowercase_item_without_colon_parent_stays_separate():
    assert merge_subitems(["Создать форму входа.", "добавить кнопку выхода."]) == \
        ["Создать форму входа.", "добавить кнопку выхода."]


def test_subitems_keep_letter_markers():
    assert merge_subitems(["Сделать в методе Main:", "а) описать массив;", "б) заполнить его."]) == \
        ["Сделать в методе Main: а) описать массив; б) заполнить его."]


def test_one_line_keeps_compound_hyphen():
    assert one_line("Создать веб-\nстраницу") == "Создать веб-страницу"


def test_space_before_comma_and_assignment_not_a_task():
    spec, _ = extract_spec(SSP_SRC, SUBJECTS["ssp"], SSP_ENR, FakeLLM([SSP_RAW]), lab_number="4", date="16.09.2026")
    assert spec.tasks[0].text.startswith("Выполните задание согласно варианту, указанному")
    assert spec.assignment == ""


def test_variants_by_workplace_and_journal():
    fake = FakeLLM([SSP_RAW])
    spec, _ = extract_spec(SSP_SRC, SUBJECTS["ssp"], SSP_ENR, fake, lab_number="4", date="16.09.2026")
    assert "Приложение А (по номеру рабочего места, вариант 2): Создать класс Payment" in spec.variant_text
    assert "Приложение Б (по журналу, вариант 14): Организация" in spec.variant_text
    prompt = "\n".join(m["content"] for m in fake.calls[0])
    assert "рабочего места" in prompt and "2" in prompt


def test_workplace_missing_warns():
    enr = Enrollment(subject_id="ssp", title_subject="ССП", variant=14)
    spec, findings = extract_spec(SSP_SRC, SUBJECTS["ssp"], enr, FakeLLM([SSP_RAW]), lab_number="4",
                                  date="16.09.2026")
    assert [f.rule_id for f in findings] == ["spec.workplace_missing"]
