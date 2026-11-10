"""Обращение к читателю в тексте отчёта, «ответ-отговорка» и слова об успехе."""

import pytest

from forlabs.antiai.text_ru import scan_report_voice, scan_text
from forlabs.report.standard import REFUSAL


@pytest.mark.parametrize("text", [
    "Введите год 2023 и проверьте, что программа выводит «Год не високосный».",
    "Программа считает век. Запустите её и убедитесь, что результат верный.",
    "Откройте файл index.html в браузере.",
])
def test_instruction_to_the_reader_is_flagged(text):
    found = scan_report_voice(text, "Задание 1, блок 2")
    assert [f.rule_id for f in found] == ["voice.imperative"]


@pytest.mark.parametrize("text", [
    "Ввёл год 2023 и проверил, что программа выводит «Год не високосный».",
    "Программа выводит приглашение «Введите целое число:» и ждёт ввода.",
    "В поле «Введите имя» записал свою фамилию.",
    "Кнопка «Выберите файл» открывает окно выбора.",
])
def test_report_voice_and_quoted_program_text_are_fine(text):
    assert scan_report_voice(text, "Задание 1, блок 2") == []


@pytest.mark.parametrize("answer", [
    "Условие вопроса без текста программы, поэтому вывод определить нельзя.",
    "В вопросе не приведён код программы, ответить невозможно.",
    "Текст программы в вопросе отсутствует.",
])
def test_answer_that_is_an_excuse_counts_as_no_answer(answer):
    assert REFUSAL.search(answer)


def test_normal_answers_are_not_excuses():
    for answer in ("Выведется 7: case 5 и case 6 идут без break.", "Условие цикла проверяется до его тела.",
                   "Определить тип переменной нельзя без объявления."):
        assert not REFUSAL.search(answer)


@pytest.mark.parametrize("text", ["Результат успешного выполнения теста регистрации", "Тест успешно пройден",
                                  "Успешная сборка проекта"])
def test_success_words_in_any_form(text):
    assert any(f.rule_id == "ai.cliche" for f in scan_text(text, "x"))


# ---------- комментарии, которых требует методичка ----------

def _doc_with_commented_sql():
    from forlabs.core.models import Listing, ReportDoc, TaskSection

    code = "\n".join(f"-- {i}. Запрос номер {i}\nSELECT {i};" for i in range(1, 6))
    sec = TaskSection(n=1, title="Выполните запросы", blocks=[Listing(topic="Запросы", language="sql", code=code)])
    return ReportDoc(lab_number="9", header={}, conclusion="в", sections=[sec])


def _check(source_text="", wishes=None):
    from forlabs.core.models import LabSpec, Profile, Task
    from forlabs.pipeline.repair import check_all

    spec = LabSpec(subject_id="subd", number="9", date="01.10.2026", topic="Запросы", goal="Научиться",
                   tasks=[Task(n=1, text="Выполните запросы")], source_text=source_text, wishes=wishes)
    profile = Profile(id="p", full_name="Иванова Анна Петровна", title_name="А.П. Иванова", gender="f")
    return [f.rule_id for f in check_all(_doc_with_commented_sql(), spec, profile, {})]


def test_many_comments_are_flagged_by_default():
    assert "code.comment_density" in _check()


@pytest.mark.parametrize("text", [
    "Каждый запрос сопроводите комментарием с номером задания.",
    "Код должен быть с комментариями к каждой строке.",
    "Прокомментируйте все запросы.",
])
def test_comments_the_methodology_asks_for_are_kept(text):
    assert "code.comment_density" not in _check(source_text=text)
    assert "code.comment_density" not in _check(wishes=text)


# ---------- вопрос из двух частей ----------

def test_two_part_questions_get_room_for_both_parts():
    from forlabs.report.standard import answer_limits

    one = answer_limits("Что такое класс?")
    for q in ("Что будет выведено на консоль в результате выполнения следующей программы и почему?",
              "Какие циклы предусмотрены в языке Java? Опишите принцип работы каждой?",
              "Какими способами можно добавить новый объект конфигурации?",
              "В чём разница между while и do-while?",
              "Сколько раз выполнится следующий цикл и почему: int i = 5; while (i > 0) { i *= -2; }"):
        words, chars = answer_limits(q)
        assert words > one[0] and chars > one[1], q
    assert one == (22, 165)


def test_two_sentence_answer_to_a_two_part_question_passes_the_check():
    from forlabs.core.models import LabSpec, QA, ReportDoc
    from forlabs.report.standard import check_standard

    q = "Что будет выведено на консоль в результате выполнения следующей программы и почему?"
    a = ("Выведется число 7. Значение 5 совпадает с case 5, после него нет break, поэтому выполняются увеличения "
         "из case 5 и case 8, и переменная a растёт дважды.")
    spec = LabSpec(subject_id="ssp", number="3", date="01.10.2026", topic="Циклы", goal="Научиться", questions=[q])
    doc = ReportDoc(lab_number="3", header={}, conclusion="в", questions=[QA(n=1, question=q, answer=a)])
    rules = [f.rule_id for f in check_standard(doc, spec, "m")]
    assert "qa.answer_too_long" not in rules
    short_q = LabSpec(subject_id="ssp", number="3", date="01.10.2026", topic="Циклы", goal="Научиться",
                      questions=["Что такое цикл?"])
    doc2 = ReportDoc(lab_number="3", header={}, conclusion="в",
                     questions=[QA(n=1, question="Что такое цикл?", answer=a)])
    assert "qa.answer_too_long" in [f.rule_id for f in check_standard(doc2, short_q, "m")]


def test_answers_prompt_tells_about_two_parts_and_program_output():
    from forlabs.pipeline.prompts import ANSWERS_SYSTEM

    assert "две части" in ANSWERS_SYSTEM and "по шагам" in ANSWERS_SYSTEM


# ---------- перечни из программы, которые модель пишет по памяти ----------

def test_writer_is_told_to_mark_lists_taken_from_memory():
    from forlabs.pipeline.prompts import WRITE_SYSTEM

    assert "Сверь перечень" in WRITE_SYSTEM and "не выдумывай" in WRITE_SYSTEM.lower()


def test_figure_asking_to_check_the_screen_becomes_a_note_for_the_student():
    from forlabs.core.models import Figure, LabSpec, Paragraph, Profile, ReportDoc, Task, TaskSection
    from forlabs.pipeline.repair import check_all

    sec = TaskSection(n=2, title="Перечислить подпункты меню", blocks=[
        Paragraph(text="В главном меню конфигуратора есть пункты «Файл», «Правка» и «Конфигурация»."),
        Figure(topic="Главное меню конфигуратора",
               slot="Сверь перечень в тексте со своим экраном: открой каждый пункт главного меню конфигуратора")])
    doc = ReportDoc(lab_number="1", header={}, conclusion="в", sections=[sec])
    spec = LabSpec(subject_id="1c", number="1", date="01.10.2026", topic="Платформа", goal="Научиться",
                   tasks=[Task(n=2, text="Перечислить подпункты меню")])
    profile = Profile(id="p", full_name="Иванова Анна Петровна", title_name="А.П. Иванова", gender="f")
    notes = [f for f in check_all(doc, spec, profile, {}) if f.rule_id == "text.check_screen"]
    assert len(notes) == 1 and notes[0].severity == "warn" and "Задание 2" in notes[0].location
    assert "по памяти" in notes[0].message and "своим экраном" in notes[0].message
    # снимок вставлен — студент уже смотрел на экран: напоминание больше не нужно
    sec.blocks[1].image_path = "figures/menu.png"
    assert not [f for f in check_all(doc, spec, profile, {}) if f.rule_id == "text.check_screen"]
