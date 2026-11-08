import os
from pathlib import Path

import pytest

from forlabs.core.llm import LLM, FakeLLM
from forlabs.core.models import Enrollment
from forlabs.core.settings import Settings
from forlabs.core.subjects import SUBJECTS
from forlabs.ingest.extract import SourceText, normalize
from forlabs.spec.extract import extract_spec, verify_verbatim

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
