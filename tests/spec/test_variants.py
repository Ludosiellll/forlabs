"""Вариант из таблицы: номер больше числа строк берётся по кругу и никогда — молча; строка сверяется с методичкой."""

from forlabs.core.llm import FakeLLM
from forlabs.core.models import Enrollment
from forlabs.core.subjects import SUBJECTS
from forlabs.ingest.extract import SourceText, normalize
from forlabs.spec.extract import extract_spec

ROWS_A = ["Проверьте на чётность число, введённое с клавиатуры.", "Передать на вход программы число от 1 до 7.",
          "Составить программу, которая по числу выводит месяц.", "Введите целое число и проверьте диапазон.",
          "Написать программу перевода единиц массы.", "Дано 3 числа. Найти минимальное среди них и вывести на экран.",
          "Найти среднее геометрическое чисел x и y.", "Вывести день недели по номеру."]
SRC = SourceText(normalize(
    "Лабораторная работа №3\nТема работы: Установка языка программирования Java.\nЦель работы: Сформировать умения.\n"
    "Задание из таблиц выбрать по порядковому номеру учащегося в списке журнала группы.\n"
    "1. Напишите программу используя операторы ветвления, согласно варианта, который приведен в приложении А.\n"
    "Приложение А\nВарианты для задания\n№ варианта | Условие задания\n"
    + "\n".join(f"{i}. | {t}" for i, t in enumerate(ROWS_A, start=1)) + "\n"))
ENR = Enrollment(subject_id="ssp", title_subject="ССП", variant=14)
TASK = "Напишите программу используя операторы ветвления, согласно варианта, который приведен в приложении А."


def raw(variants):
    return {"number": "3", "topic": "Установка языка программирования Java", "goal": "Сформировать умения",
            "assignment": "", "main_tasks": [TASK], "extra_tasks": [], "variants": variants, "questions": []}


def pick(number, text, total=8, basis="journal", appendix="Приложение А"):
    return {"appendix": appendix, "basis": basis, "number": number, "total": total, "text": text}


def rules(findings):
    return [f.rule_id for f in findings]


def test_prompt_tells_how_to_wrap_and_asks_for_total():
    fake = FakeLLM([raw([pick(6, ROWS_A[5])])])
    extract_spec(SRC, SUBJECTS["ssp"], ENR, fake, lab_number=None, date="16.09.2026")
    system = fake.calls[0][0]["content"]
    assert "total" in system and "по кругу" in system and "14 → 6" in system


def test_number_beyond_the_table_wraps_and_is_always_reported():
    fake = FakeLLM([raw([pick(6, ROWS_A[5])])])
    spec, findings = extract_spec(SRC, SUBJECTS["ssp"], ENR, fake, lab_number=None, date="16.09.2026")
    assert len(fake.calls) == 1
    assert "вариант 6" in spec.variant_text and ROWS_A[5] in spec.variant_text
    assert "14" in spec.variant_text and "по кругу" in spec.variant_text  # писатель называет вариант 6, а не 14
    note = [f for f in findings if f.rule_id == "spec.variant_wrapped"]
    assert len(note) == 1 and note[0].severity == "warn"
    assert "8" in note[0].message and "14" in note[0].message and "6" in note[0].message


def test_wrong_row_is_asked_again_with_the_exact_number():
    # модель подписала «вариант 14» и взяла последнюю строку — как в прогоне ЛР 3
    fake = FakeLLM([raw([pick(14, ROWS_A[7])]), raw([pick(6, ROWS_A[5])])])
    spec, findings = extract_spec(SRC, SUBJECTS["ssp"], ENR, fake, lab_number=None, date="16.09.2026")
    assert len(fake.calls) == 2
    again = fake.calls[1][-1]["content"]
    assert "Приложение А" in again and "вариант 6" in again
    assert ROWS_A[5] in spec.variant_text and ROWS_A[7] not in spec.variant_text
    assert "spec.variant_unsure" not in rules(findings)


def test_text_from_another_row_is_caught_by_the_row_number():
    # номер верный, а текст — из чужой строки
    fake = FakeLLM([raw([pick(6, ROWS_A[0])]), raw([pick(6, ROWS_A[5])])])
    spec, _ = extract_spec(SRC, SUBJECTS["ssp"], ENR, fake, lab_number=None, date="16.09.2026")
    assert len(fake.calls) == 2 and ROWS_A[5] in spec.variant_text


def test_still_wrong_after_asking_again_is_told_to_the_student():
    fake = FakeLLM([raw([pick(14, ROWS_A[7])]), raw([pick(14, ROWS_A[7])])])
    _, findings = extract_spec(SRC, SUBJECTS["ssp"], ENR, fake, lab_number=None, date="16.09.2026")
    unsure = [f for f in findings if f.rule_id == "spec.variant_unsure"]
    assert len(unsure) == 1 and unsure[0].severity == "warn" and "Приложение А" in unsure[0].message


def test_variant_in_range_needs_no_note_and_no_second_request():
    enr = Enrollment(subject_id="ssp", title_subject="ССП", variant=3)
    fake = FakeLLM([raw([pick(3, ROWS_A[2])])])
    spec, findings = extract_spec(SRC, SUBJECTS["ssp"], enr, fake, lab_number=None, date="16.09.2026")
    assert len(fake.calls) == 1 and findings == []
    assert spec.variant_text == f"Приложение А (по журналу, вариант 3): {ROWS_A[2]}"


def test_variant_text_that_is_not_in_the_methodology_is_reported():
    enr = Enrollment(subject_id="ssp", title_subject="ССП", variant=3)
    fake = FakeLLM([raw([pick(3, "Напишите калькулятор с графическим интерфейсом.")]),
                    raw([pick(3, "Напишите калькулятор с графическим интерфейсом.")])])
    _, findings = extract_spec(SRC, SUBJECTS["ssp"], enr, fake, lab_number=None, date="16.09.2026")
    assert "spec.variant_unsure" in rules(findings)


def test_no_variant_picked_though_the_methodology_has_a_table():
    # как в ЛР 6: модель не вернула вариант, писатель потом выбрал строку сам
    fake = FakeLLM([raw([]), raw([])])
    spec, findings = extract_spec(SRC, SUBJECTS["ssp"], ENR, fake, lab_number=None, date="16.09.2026")
    assert "spec.variant_missing" in rules(findings)
    assert "не выбирай вариант сам" in spec.variant_text.lower()  # писателю запрещено выбирать строку самому


def test_appendix_for_everyone_has_no_variant_number():
    src = SourceText(SRC.text + "\n3. Выполните дополнительные задания из приложения С.\nПриложение С\n"
                     "1. Выведите знак числа.\n2. Проверьте, является ли год високосным.\n")
    everyone = {"appendix": "Приложение С", "basis": "all", "number": 0, "total": None,
                "text": "1. Выведите знак числа. 2. Проверьте, является ли год високосным."}
    fake = FakeLLM([raw([pick(6, ROWS_A[5]), everyone])])
    spec, findings = extract_spec(src, SUBJECTS["ssp"], ENR, fake, lab_number=None, date="16.09.2026")
    line = [ln for ln in spec.variant_text.split("\n") if ln.startswith("Приложение С")][0]
    assert "вариант 0" not in line and "все задания" in line and "високосным" in line
    assert "spec.variant_unsure" not in rules(findings)


def test_workplace_table_without_a_workplace_still_warns_when_nothing_was_picked():
    src = SourceText(SRC.text.replace("№ варианта", "№ варианта (номер рабочего места)"))
    fake = FakeLLM([raw([pick(6, ROWS_A[5], basis="workplace")])])
    _, findings = extract_spec(src, SUBJECTS["ssp"], ENR, fake, lab_number=None, date="16.09.2026")
    assert "spec.workplace_missing" in rules(findings)
