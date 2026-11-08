from forlabs.core.models import (
    Figure,
    LabSpec,
    Listing,
    Paragraph,
    ReportDoc,
    Table,
    Task,
    TaskSection,
)
from forlabs.report.derive import (
    build_header,
    caption,
    figure_intro,
    listing_intro,
    lower_first,
    numbered_blocks,
    table_intro,
)


def _doc():
    return ReportDoc(lab_number="3", sections=[
        TaskSection(n=1, title="A", blocks=[Paragraph(text="x"), Figure(topic="Окно программы", slot="s"),
                                           Listing(topic="Исходный код", code="a")]),
        TaskSection(n=2, title="B", blocks=[Table(topic="параметры", rows=[["a"]]),
                                           Figure(topic="Схема сети", slot="s")]),
    ])


def test_numbering_per_lab():
    nb = numbered_blocks(_doc())
    assert [(b.kind, b.n) for b in nb] == [("figure", 1), ("listing", 1), ("table", 1), ("figure", 2)]
    assert (nb[3].section_index, nb[3].block_index) == (1, 1)


def test_intro_caption_consistency():
    assert figure_intro("Окно среды разработки VS Code с исходным кодом проекта", 3) == \
        "Окно среды разработки VS Code с исходным кодом проекта представлено на рисунке 3"
    assert caption("figure", 3, "Окно среды разработки VS Code с исходным кодом проекта.") == \
        "Рисунок 3 – Окно среды разработки VS Code с исходным кодом проекта"
    assert listing_intro("Исходный код программы работы с двумерным массивом 5 х 5", 2) == \
        "Исходный код программы работы с двумерным массивом 5 х 5 представлен в листинге 2"
    assert caption("listing", 2, "Исходный код программы") == "Листинг 2 – Исходный код программы"
    assert table_intro("параметры созданных подсистем конфигурации", 1) == \
        "В таблице 1 представлены параметры созданных подсистем конфигурации"
    assert caption("table", 1, "параметры созданных подсистем") == "Таблица 1 – Параметры созданных подсистем"


def test_lower_first_keeps_acronyms():
    assert lower_first("HTML-документ") == "HTML-документ"
    assert lower_first("Параметры") == "параметры"


def _spec(equipment=None):
    return LabSpec(subject_id="ssp", number="8", date="29.09.2026", topic="Тема", goal="Изучить X",
                   equipment=equipment, assignment="", tasks=[Task(n=1, text="t")])


def test_header_styles():
    long = build_header(_spec("ОС Windows, VS Code"), "long", "РС02-23")
    assert list(long) == ["Номер учебной группы", "Дата выполнения работы", "Тема работы", "Цель работы",
                          "Оснащение", "Задание"]
    assert long["Задание"] == "Изучите методические указания и рекомендации по выполнению работы, выполните практические задания."
    assert "Оснащение" not in build_header(_spec(), "long", "РС02-23")
    short = build_header(_spec("ПК"), "short", "РС02-23")
    assert list(short) == ["Номер учебной группы", "Дата выполнения работы", "Тема", "Цель", "Задание",
                           "Оснащение работы"]
    assert short["Номер учебной группы"] == "РС02-23"
