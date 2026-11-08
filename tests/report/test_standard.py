import pytest

from forlabs.core.models import QA, DashList, Figure, LabSpec, Listing, Paragraph, ReportDoc, Task, TaskSection
from forlabs.report.derive import build_header
from forlabs.report.standard import check_standard

GOAL = "Сформировать умения создавать простейшие программы работы со ссылочными типами данных"


def spec() -> LabSpec:
    return LabSpec(
        subject_id="ssp", number="8", date="29.09.2026",
        topic="Создание простейших программ работы со ссылочными типами данных Golang", goal=GOAL,
        equipment="Методические указания к выполнению, ОС Windows, VS Code",
        tasks=[Task(n=1, text="По данному натуральному n ≤ 30 выведите лесенку из n ступенек."),
               Task(n=2, text="Напишите программу согласно приведенному перечню в приложении А.")],
        questions=["Что называется функцией?", "Дайте определение понятию сигнатура?"],
        variant_text="Приложение А (по журналу, вариант 14): двумерный массив 5 х 5",
    )


def clean_doc() -> ReportDoc:
    s = spec()
    return ReportDoc(
        lab_number="8", header=build_header(s, "long", "РС02-23"),
        sections=[
            TaskSection(n=1, title=s.tasks[0].text, blocks=[
                Paragraph(text="В среде Visual Studio Code создан файл task1.go с двумя алгоритмами:"),
                DashList(items=["- вывод лесенки через двумерный срез stairs;",
                                "- вычисление квадрата числа как суммы нечетных чисел."]),
                Listing(topic="Исходный код программы вывода лесенки", code="package main"),
                Figure(topic="Результат выполнения программы в консоли", slot="Консоль"),
            ]),
            TaskSection(n=2, title=s.tasks[1].text, blocks=[
                Paragraph(text="Согласно варианту 14 создан двумерный массив 5 х 5 и функции поиска экстремумов."),
            ]),
        ],
        questions=[QA(n=1, question="Что называется функцией?", answer="Именованный блок кода для многократного вызова."),
                   QA(n=2, question="Дайте определение понятию сигнатура?", answer="Имя функции с типами параметров и результата.")],
        conclusion="Сформировал умения создавать простейшие программы работы со ссылочными типами данных.",
    )


def ids(findings):
    return sorted({f.rule_id for f in findings})


def test_clean_doc_passes():
    assert check_standard(clean_doc(), spec(), "m") == []


def test_header_missing():
    d = clean_doc(); d.header.pop("Цель работы")
    assert ids(check_standard(d, spec(), "m")) == ["header.missing"]


def test_header_date_format():
    d = clean_doc(); d.header["Дата выполнения работы"] = "29.9.26"
    assert ids(check_standard(d, spec(), "m")) == ["header.date_format"]


def test_task_sequence():
    d = clean_doc(); d.sections[1].n = 3
    assert "task.sequence" in ids(check_standard(d, spec(), "m"))


def test_task_title_mismatch():
    d = clean_doc(); d.sections[0].title = "Вывод лесенки из n ступенек"
    assert ids(check_standard(d, spec(), "m")) == ["task.title_mismatch"]


def test_manual_reference_in_text():
    d = clean_doc(); d.sections[1].blocks.append(Paragraph(text="Результат представлен на рисунке 2."))
    assert ids(check_standard(d, spec(), "m")) == ["ref.manual"]


@pytest.mark.parametrize("items,rule", [
    (["- Вывод лесенки;", "- вычисление."], "list.lowercase"),
    (["- вывод лесенки.", "- вычисление."], "list.punctuation"),
    (["- вывод лесенки;", "- вычисление"], "list.punctuation"),
    (["- вывод лесенки показан на рисунке 1;", "- вычисление."], "list.reference_inside"),
])
def test_dash_list_rules(items, rule):
    d = clean_doc(); d.sections[0].blocks[1] = DashList(items=items)
    assert ids(check_standard(d, spec(), "m")) == [rule]


def test_dash_list_acronym_start_allowed():
    d = clean_doc(); d.sections[0].blocks[1] = DashList(items=["- HTML-страница;", "- CSS-файл."])
    assert check_standard(d, spec(), "m") == []


def test_question_not_in_spec():
    d = clean_doc(); d.questions[1].question = "Что такое срез?"
    assert ids(check_standard(d, spec(), "m")) == ["qa.not_in_spec"]


def test_answer_too_long():
    d = clean_doc(); d.questions[0].answer = " ".join(["слово"] * 23) + "."
    assert ids(check_standard(d, spec(), "m")) == ["qa.answer_too_long"]


def test_conclusion_formula_and_gender():
    d = clean_doc()
    assert ids(check_standard(d, spec(), "f")) == ["conclusion.formula"]
    d.conclusion = "В ходе работы сформировал умения."
    assert ids(check_standard(d, spec(), "m")) == ["conclusion.formula"]


@pytest.mark.parametrize("text,rule", [
    ("Создан **файл** программы.", "text.markdown"),
    ("Создан файл программы 🚀.", "text.emoji"),
    ("Нажата кнопка \"Войти\".", "text.english_quotes"),
    ("Нажата кнопка “Войти”.", "text.english_quotes"),
])
def test_text_rules(text, rule):
    d = clean_doc(); d.sections[1].blocks[0] = Paragraph(text=text)
    assert ids(check_standard(d, spec(), "m")) == [rule]


def test_code_like_quotes_allowed():
    d = clean_doc(); d.sections[1].blocks[0] = Paragraph(text="Поле ввода с атрибутом type=\"text\" добавлено в форму.")
    assert check_standard(d, spec(), "m") == []


def test_text_gender():
    d = clean_doc(); d.sections[1].blocks[0] = Paragraph(text="Разработал программу поиска экстремумов.")
    d.conclusion = "Сформировала умения создавать простейшие программы работы со ссылочными типами данных."
    assert ids(check_standard(d, spec(), "f")) == ["text.gender"]


def test_phantom_variant_when_spec_has_none():
    d = clean_doc(); d.sections[1].blocks[0] = Paragraph(text="Согласно варианту 2 из приложения А необходимо собрать данные.")
    s = spec()
    s.variant_text = None
    assert ids(check_standard(d, s, "m")) == ["text.phantom_variant"]
    s.variant_text = "Приложение А (по журналу, вариант 14): Организация"
    assert check_standard(d, s, "m") == []


def test_user_conclusion_override_is_accepted():
    s = spec()
    s.goal = "Формирование навыков работы со ссылочными типами"
    s.conclusion = "Сформировал навыки работы со ссылочными типами."
    d = clean_doc(); d.conclusion = s.conclusion
    assert check_standard(d, s, "m") == []
