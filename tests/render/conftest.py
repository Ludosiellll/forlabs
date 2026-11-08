import pytest
from PIL import Image

from forlabs.core.models import (
    QA,
    DashList,
    Enrollment,
    Figure,
    Listing,
    NumberedList,
    Paragraph,
    Profile,
    ReportDoc,
    Table,
    TaskSection,
)


@pytest.fixture
def png(tmp_path):
    path = tmp_path / "shot.png"
    Image.new("RGB", (1400, 800), (30, 30, 30)).save(path)
    return path


@pytest.fixture
def profile_f():
    return Profile(id="ivanova", full_name="Иванова Анна Петровна", title_name="А.П. Иванова", gender="f",
                   group="РС02-23", doc_author="Иванова Анна", output_root="/tmp/out",
                   enrollments=[Enrollment(subject_id="ssp", title_subject="Современные системы программирования",
                                           teacher="Б.Б. Сидорова", academic_year="2026/2027", variant=14)])


@pytest.fixture
def sample_doc(png):
    code = "package main\n\nimport \"fmt\"\n\nfunc main() {\n\tvar n int\n    fmt.Scan(&n)\n" \
           "    fmt.Println(\"очень длинная строка вывода, которая точно не поместится в одну строку листинга на странице\")\n}"
    return ReportDoc(
        lab_number="8",
        header={"Номер учебной группы": "РС02-23", "Дата выполнения работы": "29.09.2026",
                "Тема работы": "Создание простейших программ работы со ссылочными типами данных Golang",
                "Цель работы": "Сформировать умения создавать простейшие программы работы со ссылочными типами данных",
                "Оснащение": "Методические указания к выполнению, ОС Windows, VS Code",
                "Задание": "Изучите методические указания и рекомендации по выполнению работы, выполните практические задания."},
        sections=[
            TaskSection(n=1, title="По данному натуральному n ≤ 30 выведите лесенку из n ступенек.", blocks=[
                Paragraph(text="В среде Visual Studio Code создан файл task1.go с двумя алгоритмами:"),
                DashList(items=["вывод лесенки через двумерный срез stairs;", "вычисление квадрата числа."]),
                Listing(topic="Исходный код программы вывода лесенки", code=code, language="go"),
                Figure(topic="Результат выполнения программы первого задания в консоли", image_path=str(png)),
            ]),
            TaskSection(n=2, title="Напишите программу согласно приведенному перечню в приложении А.", blocks=[
                NumberedList(items=["1. Функция fillMatrix заполняет массив.", "2. Функция findMax ищет максимум."]),
                Table(topic="параметры функций программы", rows=[["Функция", "Назначение"], ["fillMatrix", "заполнение"],
                                                                 ["findMax", "поиск максимума"]]),
                Figure(topic="Окно среды разработки VS Code с исходным кодом проекта", slot="Открой VS Code"),
            ]),
        ],
        questions=[QA(n=1, question="Что называется функцией?", answer="Именованный блок кода."),
                   QA(n=2, question="Что такое срез?", answer="Последовательность переменной длины.")],
        conclusion="Сформировал умения создавать простейшие программы работы со ссылочными типами данных.",
    )
