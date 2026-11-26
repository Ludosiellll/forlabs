"""Вид работы: лабораторная работа, практическая работа или практическое занятие."""

from forlabs.core.models import LabSpec, ReportDoc
from forlabs.report.derive import detect_work_kind, lab_label, work_short, work_title, works_phrase


def test_titles_and_short_names():
    assert [work_title(k) for k in ("lab", "practice", "session")] == [
        "Лабораторная работа", "Практическая работа", "Практическое занятие"]
    assert [work_short(k) for k in ("lab", "practice", "session")] == ["ЛР", "ПР", "ПЗ"]
    assert LabSpec(subject_id="s", number="1", date="", topic="", goal="").work_kind == "lab"  # прежние лабы
    assert ReportDoc(lab_number="1").work_kind == "lab"


def test_detect_by_heading_of_the_work():
    assert detect_work_kind("Практическая работа №5\nТема: массивы", "5") == "practice"
    assert detect_work_kind("ПРАКТИЧЕСКОЕ ЗАНЯТИЕ № 3.\nТема: циклы", "3") == "session"
    assert detect_work_kind("Лабораторная работа №7\nТема: формы", "7") == "lab"
    assert detect_work_kind("  ПЗ №4\nТема: строки", "4") == "session"
    assert detect_work_kind("ПР 4. Строки\nЦель: …", "4") == "practice"


def test_detect_takes_the_heading_with_this_number():
    text = "Лабораторная работа №4\nТема: а\n\nПрактическая работа №5\nТема: б\n"
    assert detect_work_kind(text, "5") == "practice"
    assert detect_work_kind(text, "4") == "lab"
    assert detect_work_kind(text, "9") == "lab"  # своего заголовка нет — первый в тексте


def test_detect_ignores_mentions_inside_text():
    text = ("Методические указания к выполнению практических работ\nЛабораторная работа №6\n"
            "Задание 1. Возьмите тестовые сценарии из практической работы №6.\n")
    assert detect_work_kind(text, "6") == "lab"
    assert detect_work_kind("Тема: массивы\nЦель: изучить\nЗадание 1. Проверить, как прошла практическая работа.", "1") == "lab"


def test_detect_heading_without_number():
    assert detect_work_kind("Практическое занятие\nТема: массивы", "1") == "session"
    assert detect_work_kind("", None) == "lab"


def test_label_hides_the_suffix_of_a_taken_number():
    assert lab_label("lab", "5") == "ЛР 5"
    assert lab_label("session", "5-пз") == "ПЗ 5"  # номер 5 занят лабораторной: в списке — «ПЗ 5»
    assert lab_label("practice", "5-пр-2") == "ПР 5-2"
    assert lab_label("lab", "5-лр") == "ЛР 5"
    assert lab_label("practice", "5-2") == "ПР 5-2"
    assert lab_label("lab", "новая-ab12cd") == "ЛР новая-ab12cd"


def test_semester_phrase():
    assert works_phrase({"lab"}) == "по лабораторным работам"
    assert works_phrase(set()) == "по лабораторным работам"
    assert works_phrase({"practice"}) == "по практическим работам"
    assert works_phrase({"session"}) == "по практическим занятиям"
    assert works_phrase({"lab", "practice"}) == "по лабораторным и практическим работам"
    assert works_phrase({"lab", "session"}) == "по лабораторным работам и практическим занятиям"
    assert works_phrase({"practice", "session"}) == "по практическим работам и практическим занятиям"
    assert works_phrase({"lab", "practice", "session"}) == "по лабораторным и практическим работам и практическим занятиям"
