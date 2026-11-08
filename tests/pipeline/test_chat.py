"""Движок чата правок: план, точечная переделка задания, ответы на вопросы."""

import pytest
from test_write import PROFILE, SPEC

from forlabs.core.llm import FakeLLM
from forlabs.core.models import (
    QA,
    Figure,
    LabState,
    Listing,
    Paragraph,
    ReportDoc,
    TaskSection,
)
from forlabs.core.rules import RuleBook
from forlabs.core.subjects import SUBJECTS
from forlabs.pipeline.chat import (
    QuestionEdit,
    plan_edit,
    revise_answers,
    revise_section,
)

SSP = SUBJECTS["ssp"]


def section():
    return TaskSection(n=1, title=SPEC.tasks[0].text, blocks=[
        Paragraph(text="В VS Code создан файл task1.go с выводом лесенки."),
        Listing(topic="Исходный код программы вывода лесенки", language="go", code="for i := 0; i < n; i++ {}"),
        Figure(topic="Результат выполнения программы в консоли", image_path="assets/fig_1_3.png"),
    ])


def state():
    return LabState(profile_id="ivanova", subject_id="ssp", number="8", status="rendered", spec=SPEC,
                    report=ReportDoc(lab_number="8", sections=[section()],
                                     questions=[QA(n=1, question="Что называется функцией?", answer="Блок кода.")]))


def test_plan_edit_question():
    fake = FakeLLM([{"kind": "answer", "reply": "Цикл for удобнее, когда известно число шагов.",
                     "tasks": [{"n": 1, "instruction": "лишнее"}]}])
    plan = plan_edit(state(), "почему тут for?", PROFILE, SSP, fake, RuleBook(), teacher="")
    assert plan.kind == "answer" and plan.tasks == [] and "for" in plan.reply
    prompt = fake.calls[0][-1]["content"]
    assert "почему тут for?" in prompt and "task1.go" in prompt


def test_plan_ignores_unknown_task():
    fake = FakeLLM([{"kind": "edit", "reply": "Поправлю задание 1.",
                     "tasks": [{"n": 1, "instruction": "while вместо for"}, {"n": 9, "instruction": "?"}],
                     "questions": [{"n": 5, "instruction": "?"}]}])
    plan = plan_edit(state(), "перепиши цикл на while", PROFILE, SSP, fake, RuleBook(), teacher="")
    assert [t.n for t in plan.tasks] == [1] and plan.questions == []


def test_plan_without_changes_is_a_question():
    fake = FakeLLM([{"kind": "edit", "reply": "Что именно поменять в выводе?"}])
    plan = plan_edit(state(), "сделай лучше", PROFILE, SSP, fake, RuleBook(), teacher="")
    assert plan.kind == "question"


def test_revise_keeps_blocks_and_screenshot(tmp_path):
    fake = FakeLLM([{"blocks": [{"keep": "b1"},
                                {"type": "listing", "topic": "Исходный код программы вывода лесенки",
                                 "language": "go", "code": "i := 0\nfor i < n {\n\ti++\n}"},
                                {"keep": "b3"}]}])
    new, retake = revise_section(SPEC, section(), "перепиши цикл на while", PROFILE, SSP, fake, RuleBook(),
                                 tmp_path, memory="", render_diagrams=False)
    assert new.blocks[0] == section().blocks[0]
    assert isinstance(new.blocks[1], Listing) and "for i < n" in new.blocks[1].code
    assert new.blocks[2].image_path == "assets/fig_1_3.png" and retake == []
    prompt = fake.calls[0][-1]["content"]
    assert "b1" in prompt and "b3" in prompt and "скриншот вставлен" in prompt


def test_retake_clears_image(tmp_path):
    fake = FakeLLM([{"blocks": [{"keep": "b1"}, {"keep": "b2"}, {"keep": "b3", "retake": True}]}])
    new, retake = revise_section(SPEC, section(), "вывод другой", PROFILE, SSP, fake, RuleBook(), tmp_path,
                                 memory="", render_diagrams=False)
    fig = new.blocks[2]
    assert fig.image_path is None and fig.slot and retake == ["Результат выполнения программы в консоли"]


def test_new_screenshot_counts_as_retake(tmp_path):
    fake = FakeLLM([{"blocks": [{"keep": "b1"}, {"keep": "b2"}, {"keep": "b3"},
                                {"type": "figure", "kind": "screenshot", "topic": "Ошибка при вводе нуля",
                                 "instruction": "Введи 0 и сделай снимок"}]}])
    new, retake = revise_section(SPEC, section(), "добавь проверку", PROFILE, SSP, fake, RuleBook(), tmp_path,
                                 memory="", render_diagrams=False)
    assert len(new.blocks) == 4 and retake == ["Ошибка при вводе нуля"]


def test_unknown_keep_is_skipped(tmp_path):
    fake = FakeLLM([{"blocks": [{"keep": "b9"}, {"keep": "b1"}, {"keep": "b1"}]}])
    new, _ = revise_section(SPEC, section(), "убери лишнее", PROFILE, SSP, fake, RuleBook(), tmp_path,
                            memory="", render_diagrams=False)
    assert len(new.blocks) == 1 and isinstance(new.blocks[0], Paragraph)


def test_revise_answers_only_listed():
    qas = [QA(n=1, question="Что такое функция?", answer="Блок кода."),
           QA(n=2, question="Что такое цикл?", answer="Повтор.")]
    fake = FakeLLM([{"answers": [{"n": 2, "answer": "Многократное выполнение блока кода."}, {"n": 7, "answer": "?"}]}])
    out = revise_answers(SPEC, qas, [QuestionEdit(n=2, instruction="подробнее")], PROFILE, SSP, fake, RuleBook())
    assert out[0].answer == "Блок кода." and out[1].answer == "Многократное выполнение блока кода."


def test_methodology_text_only_when_variant_is_mentioned():
    st = state()
    st.spec = st.spec.model_copy(update={"source_text": "ПРИЛОЖЕНИЕ А. Вариант 7: страница о Гомеле"})
    fake = FakeLLM([{"kind": "answer", "reply": "Так проще."}, {"kind": "answer", "reply": "Ок."}])
    plan_edit(st, "почему тут for?", PROFILE, SSP, fake, RuleBook(), teacher="")
    assert "ПРИЛОЖЕНИЕ А" not in fake.calls[0][-1]["content"]
    plan_edit(st, "поменяй вариант на 7", PROFILE, SSP, fake, RuleBook(), teacher="")
    assert "ПРИЛОЖЕНИЕ А" in fake.calls[1][-1]["content"]


def test_prompts_ask_for_honest_reply_and_careful_retake():
    from forlabs.pipeline.prompts import CHAT_PLAN_SYSTEM, CHAT_REVISE_SYSTEM

    assert "только то, что перечислено" in CHAT_PLAN_SYSTEM
    assert "только вывод программы" in CHAT_REVISE_SYSTEM


def test_unknown_task_number_is_asked_back():
    fake = FakeLLM([{"kind": "edit", "reply": "Добавлю в четвёртом задании проверку.",
                     "tasks": [{"n": 4, "instruction": "проверка"}]}])
    plan = plan_edit(state(), "в 4 задании добавь проверку", PROFILE, SSP, fake, RuleBook(), teacher="")
    assert plan.kind == "question" and "нет задания 4" in plan.reply


@pytest.mark.parametrize("label", ["B1", "1", "b1.", " b1 "])
def test_keep_labels_are_normalized(tmp_path, label):
    fake = FakeLLM([{"blocks": [{"keep": label}, {"keep": "b2"}, {"keep": "b3"}]}])
    new, _ = revise_section(SPEC, section(), "…", PROFILE, SSP, fake, RuleBook(), tmp_path, render_diagrams=False)
    assert len(new.blocks) == 3


def test_empty_revision_is_an_error(tmp_path):
    fake = FakeLLM([{"blocks": []}])
    with pytest.raises(ValueError):
        revise_section(SPEC, section(), "…", PROFILE, SSP, fake, RuleBook(), tmp_path, render_diagrams=False)


def test_retake_keeps_original_instruction(tmp_path):
    sec = section()
    sec.blocks[2].slot = "Запусти программу и сними консоль"
    fake = FakeLLM([{"blocks": [{"keep": "b1"}, {"keep": "b2"}, {"keep": "b3", "retake": True}]}])
    new, _ = revise_section(SPEC, sec, "…", PROFILE, SSP, fake, RuleBook(), tmp_path, render_diagrams=False)
    assert "Пересними" in new.blocks[2].slot and "Запусти программу" in new.blocks[2].slot


def test_current_message_appears_once_in_plan_prompt():
    from forlabs.core.models import ChatMessage

    st = state()
    st.chat.append(ChatMessage(id="u1", role="user", text="уникальное сообщение 42", at="2026-11-08T10:00:00"))
    fake = FakeLLM([{"kind": "answer", "reply": "Ок."}])
    plan_edit(st, "уникальное сообщение 42", PROFILE, SSP, fake, RuleBook(), teacher="")
    assert fake.calls[0][-1]["content"].count("уникальное сообщение 42") == 1


def test_write_still_requires_block_type():
    from pydantic import ValidationError

    from forlabs.pipeline.write import SectionDraft

    with pytest.raises(ValidationError):
        SectionDraft.model_validate({"blocks": [{"text": "без типа"}]})
