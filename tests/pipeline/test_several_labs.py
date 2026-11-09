"""Несколько методичек в одной форме: номер лабы по имени файла и заголовку; сборник лаб; замена методички."""

import io
from pathlib import Path

import docx
import pytest
from test_run import ANS, RAW, SEC, SEC2, png_bytes, setup

from forlabs.pipeline.labnum import labs_in_text, number_in_name, number_in_text, split_by_lab


def lab_text(n, topic="Работа с файлами"):
    return (f"Лабораторная работа №{n}\nТема: {topic}\nЦель: научиться решать задачи по теме «{topic}» на Python.\n"
            "Оснащение: ПК, Python 3, IDLE.\nЗадания:\n"
            f"1. Напишите программу к теме «{topic}», которая читает данные и выводит результат.\n"
            "2. Дополните программу из задания 1 проверкой ввода.\n"
            "Контрольные вопросы\n1. Что такое модуль?\n")


def as_docx(text):
    d = docx.Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


THREE = [("ЛР19 библиотеки.docx", as_docx(lab_text(19, "Библиотеки"))),
         ("ЛР20 файлы.docx", as_docx(lab_text(20, "Файлы"))),
         ("ЛР21 работа с JSON.docx", as_docx(lab_text(21, "Работа с JSON")))]


@pytest.mark.parametrize("name, number", [
    ("ЛР19 библиотеки.docx", "19"), ("ЛР 20 файлы.docx", "20"), ("лаб_21.pdf", "21"),
    ("Лабораторная работа 3.pdf", "3"), ("Лабораторная_работа_№5.docx", "5"), ("ПР-4.pdf", "4"),
    ("Практическая работа 6.docx", "6"), ("Практика 8.pdf", "8"), ("Лаб.раб.7.pdf", "7"), ("lab05.pdf", "5"), ("ЛР 5.2.docx", "5.2"),
    ("лр_03_Иванов.docx", "3"), ("ЛР3 12.10.2026.docx", "3"),
    ("IMG_2024.jpg", None), ("приложение.docx", None), ("ЛР 1-5.pdf", None), ("Лабораторные 1–10.pdf", None),
    ("ЛР1 и ЛР2.docx", None), ("ЛР 1, 2.docx", None), ("Проект 3.docx", None), ("пример2.txt", None),
    ("лабиринт 3.pdf", None), ("методичка.pdf", None)])
def test_number_in_name(name, number):
    assert number_in_name(name) == number


def test_number_in_text():
    assert number_in_text(lab_text(19)) == "19"
    assert number_in_text("Практическая работа № 4\nТема: сети") == "4"
    assert number_in_text(lab_text(1) + lab_text(2)) is None  # сборник: какая — решит разбор
    toc = "Содержание\nЛабораторная работа №1 ........ 3\nЛабораторная работа №2 ........ 9\n"
    assert number_in_text(toc + lab_text(2)) == "2"  # строки оглавления — не лабы
    assert number_in_text("Тема: файлы\nЗадания: …") is None
    assert labs_in_text(toc + lab_text(1) + lab_text(2) + lab_text(3)) == ["1", "2", "3"]


def test_split_three_methodologies():
    groups = split_by_lab(THREE)
    assert [(n, [name for name, _ in files]) for n, files in groups] == [
        ("19", ["ЛР19 библиотеки.docx"]), ("20", ["ЛР20 файлы.docx"]), ("21", ["ЛР21 работа с JSON.docx"])]


def test_split_by_heading_and_same_number_together():
    files = [("методичка.docx", as_docx(lab_text(5))), ("ЛР6.txt", lab_text(6).encode()),
             ("ЛР 5 приложение.txt", "Приложение А. Варианты\n1. Книги\n2. Фильмы\n".encode())]
    groups = split_by_lab(files)
    assert [(n, [name for name, _ in f]) for n, f in groups] == [
        ("5", ["методичка.docx", "ЛР 5 приложение.txt"]), ("6", ["ЛР6.txt"])]


@pytest.mark.parametrize("files, pasted", [
    (THREE[:1], ""),  # один файл
    ([("ЛР19 стр1.docx", THREE[0][1]), ("ЛР 19 стр2.docx", THREE[0][1])], ""),  # номер один
    (THREE[:2] + [("приложение.docx", as_docx("Приложение А. Варианты заданий"))], ""),  # часть без номера
    ([("ЛР19.jpg", png_bytes()), ("ЛР20.jpg", png_bytes())], ""),  # фото страниц
    (THREE[:2], "Вариант 14: книги о Беларуси"),  # вставленный текст без номера
    ([("сборник.docx", as_docx(lab_text(1) + lab_text(2))), ("ЛР3.docx", THREE[0][1])], ""),  # сборник целиком
])
def test_split_keeps_one_lab(files, pasted):
    assert split_by_lab(files, pasted) == []


def test_split_unreadable_file_keeps_one_lab():
    assert split_by_lab([("ЛР19.docx", THREE[0][1]), ("битый.docx", b"not a zip")]) == []


def test_manual_with_several_labs_warns_which_was_parsed(tmp_home, tmp_path):
    manual = lab_text(1, "Ввод") + lab_text(2, "Списки") + lab_text(3, "Словари")
    pipe, fake = setup(tmp_home, tmp_path, [dict(RAW, number="2")])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [("сборник.txt", manual.encode())]), date="24.09.2026")
    assert st.status == "parsed" and st.number == "2"
    warn = [f for f in st.findings if f.rule_id == "spec.several_labs"]
    assert len(warn) == 1 and warn[0].severity == "warn"
    assert "1, 2, 3" in warn[0].message and "ЛР 2" in warn[0].message


def test_typed_number_means_no_several_labs_warning(tmp_home, tmp_path):
    manual = lab_text(1) + lab_text(2) + lab_text(3)
    pipe, fake = setup(tmp_home, tmp_path, [dict(RAW, number="2")])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [("сборник.txt", manual.encode())], number="2"),
                    date="24.09.2026")
    assert not [f for f in st.findings if f.rule_id == "spec.several_labs"]


def test_parse_takes_number_hint_for_draft(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [dict(RAW, number="7")])
    st = pipe.create_lab("ivanov", "psip", [THREE[1]])
    st = pipe.parse(st, date="24.09.2026", number="20")
    assert st.number == "20" and st.spec.number == "20"
    assert "Номер работы: 20" in fake.calls[0][1]["content"]


def source_names(pipe, st):
    return sorted(p.name for p in (pipe.lab_dir(st) / "source").iterdir())


def test_replace_source_moves_lab_without_report_to_new_number(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [dict(RAW, number="20"), dict(RAW, number="19", topic="Библиотеки")])
    st = pipe.create_lab("ivanov", "psip", [THREE[1]], wishes="код на C#")
    st = pipe.parse(st, date="24.09.2026")
    assert st.number == "20"
    st = pipe.replace_source(st, [THREE[0]])
    assert st.status == "parsed", st.error
    assert st.number == "19" and st.spec.topic == "Библиотеки" and st.spec.date == "24.09.2026"
    assert pipe.store.load_lab("ivanov", "psip", "20") is None
    assert source_names(pipe, st) == ["ЛР19_библиотеки.docx"]
    sent = fake.calls[1][1]["content"]
    assert "Номер работы: 19" in sent and "Тема: Библиотеки" in sent and "Тема: Файлы" not in sent
    assert st.spec.wishes == "код на C#" and st.versions == []
    assert pipe.renamed["ivanov/psip/20"] == "19"


def test_replace_source_keeps_number_when_new_manual_has_this_lab(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [dict(RAW, number="2"), dict(RAW, number="2")])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [("фото.txt", lab_text(2).encode())]), date="24.09.2026")
    manual = lab_text(1) + lab_text(2) + lab_text(3)
    st = pipe.replace_source(st, [], pasted_text=manual)
    assert st.number == "2" and "Номер работы: 2" in fake.calls[1][1]["content"]
    assert source_names(pipe, st) == ["текст.txt"]
    assert not [f for f in st.findings if f.rule_id == "spec.several_labs"]


def test_replace_source_on_done_lab_keeps_report_in_version(tmp_home, tmp_path):
    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS, dict(RAW, topic="Новая тема")])
    from test_run import FIXTURE

    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    st = pipe.attach_screenshot(st, section=1, block=4, image=png_bytes())
    assert st.status == "rendered", st.error
    old_report, old_text = st.report.model_copy(deep=True), st.spec.source_text
    st = pipe.replace_source(st, [("ЛР7 новая.txt", lab_text(7, "Новая тема").encode())])
    assert st.status == "parsed", st.error
    assert st.number == "7" and st.spec.topic == "Новая тема" and st.report is None and not st.docx_sha
    assert "Номер работы: 7" in fake.calls[-1][1]["content"]
    assert source_names(pipe, st) == ["ЛР7_новая.txt"]
    v = st.versions[-1]
    assert v.label == "до замены методички" and v.report == old_report
    note = st.chat[-1]
    assert note.role == "assistant" and note.version == v.id
    assert FIXTURE.name in note.text and "ЛР7_новая.txt" in note.text
    saved = pipe.store.load_lab("ivanov", "psip", "7")
    assert saved.report is None and saved.versions[-1].id == v.id
    assert (pipe.lab_dir(st) / f"source.v{v.id}" / FIXTURE.name).exists()

    st = pipe.revert(saved, v.id)  # заменил по ошибке — всё возвращается, с прежней методичкой
    assert st.report is not None and st.spec.topic == RAW["topic"] and st.spec.source_text == old_text
    assert st.status in ("rendered", "needs_manual"), st.error
    assert source_names(pipe, st) == [FIXTURE.name]
    assert not list(pipe.lab_dir(st).glob("source.*"))  # отчёта по новой не было — её файлы не нужны


def test_other_lab_methodology_on_done_lab_keeps_number_and_warns(tmp_home, tmp_path):
    from test_run import FIXTURE

    pipe, fake = setup(tmp_home, tmp_path, [RAW, SEC, SEC2, ANS, RAW, SEC, SEC2, ANS, RAW])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    st = pipe.execute(pipe.confirm(pipe.parse(st, date="24.09.2026"), None))
    st = pipe.replace_source(st, [("ЛР7.txt", lab_text(7).encode())])
    st = pipe.execute(pipe.confirm(st, None))  # отчёт по новой методичке
    first = st.versions[-1].id
    (pipe.lab_dir(st) / "source.v99").mkdir()  # методичка версии, которой уже нет (хранятся 10 последних)
    st = pipe.replace_source(st, [("ЛР8.txt", lab_text(8).encode())])
    assert st.number == "7" and "Номер работы: 7" in fake.calls[-1][1]["content"]
    warn = [f.message for f in st.findings if f.rule_id == "spec.other_number"]
    assert warn and "ЛР 8" in warn[0] and "ЛР 7" in warn[0]
    kept = sorted(p.name for p in pipe.lab_dir(st).glob("source.v*"))
    assert len(kept) == 2 and all(int(k[8:]) in {v.id for v in st.versions} for k in kept)
    assert first in {v.id for v in st.versions}


def test_replace_source_needs_file_or_text(tmp_home, tmp_path):
    from forlabs.pipeline.run import PipelineError

    pipe, fake = setup(tmp_home, tmp_path, [RAW])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [THREE[1]], number="7"), date="24.09.2026")
    with pytest.raises(PipelineError):
        pipe.replace_source(st, [], pasted_text="  ")
    assert source_names(pipe, st) == ["ЛР20_файлы.docx"]
    assert Path(pipe.lab_dir(st) / "source").exists()
