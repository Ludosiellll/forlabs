"""Чего нет в методичке, а для работы нужно (файл преподавателя, база данных, результат прошлой работы): студент
узнаёт об этом при разборе, а не из выдуманного отчёта."""

from test_spec_extract import ENR, RAW, SRC

from forlabs.core.llm import FakeLLM
from forlabs.core.subjects import SUBJECTS
from forlabs.spec.extract import extract_spec


def run(raw, wishes=""):
    fake = FakeLLM([raw])
    spec, findings = extract_spec(SRC, SUBJECTS["psip"], ENR, fake, lab_number=None, date="24.09.2026", wishes=wishes)
    return spec, [f for f in findings if f.rule_id == "spec.needs_material"], fake


def test_prompt_asks_what_the_work_needs_from_outside():
    _, _, fake = run(RAW)
    system = fake.calls[0][0]["content"]
    assert "needs" in system and "прошл" in system and "преподавател" in system


def test_each_need_becomes_a_note_for_the_student():
    spec, notes, _ = run({**RAW, "needs": ["тестовые сценарии из практической работы №6",
                                           "файл базы данных World от преподавателя"]})
    assert len(notes) == 1 and notes[0].severity == "warn"
    assert "практической работы №6" in notes[0].message and "World" in notes[0].message
    assert "пожелания" in notes[0].message
    assert spec.needs == ["тестовые сценарии из практической работы №6", "файл базы данных World от преподавателя"]


def test_no_needs_no_note_and_junk_is_ignored():
    assert run(RAW)[1] == []
    spec, notes, _ = run({**RAW, "needs": None})
    assert notes == [] and spec.needs == []
    spec, notes, _ = run({**RAW, "needs": ["", "  ", "нет"]})
    assert notes == [] and spec.needs == []


def test_numbered_previous_labs_are_not_needs():
    # прошлые лабы ForLabs ищет сам и говорит о них отдельным замечанием — здесь они были бы повтором
    spec, notes, _ = run({**RAW, "needs": ["тема из ЛР №2", "сайт из лабораторной работы 3",
                                           "архив с изображениями от преподавателя"]})
    assert spec.needs == ["архив с изображениями от преподавателя"] and len(notes) == 1
