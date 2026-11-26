"""Смена вида работы и папка сдачи: переезжает только своя папка, и только вместе с сохранённым состоянием.
Сценарии — из проверки кода перед выпуском: по имени папки нельзя судить, чья она."""

import json
from pathlib import Path

import pytest
from test_chat_run import ready
from test_project_stage import PLAN, make
from test_run import FIXTURE, RAW
from test_work_kind_stage import _journal, _png

from forlabs.core.llm import LLMError
from forlabs.core.models import finding_key

EDIT_AND_KIND = {"kind": "edit", "reply": "Сделаю.", "work_kind": "session",
                 "tasks": [{"n": 2, "instruction": "короче"}]}


def _root(tmp_path) -> Path:
    return tmp_path / "сдача" / "ПССИП"


def test_second_work_with_the_same_number_does_not_take_the_folder_of_the_first(tmp_home, tmp_path):
    """ЛР 7 выполнена. Вторая работа с тем же номером («7-2») только разобрана; ей меняют вид — папка «ЛР7»
    с проектом первой работы остаётся на месте."""
    pipe, fake, lab = make(tmp_home, tmp_path, [PLAN, {**RAW}])
    lab = pipe.execute(lab)
    second = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())]), date="24.09.2026")
    assert second.number == "7-2"
    pipe.confirm(second, second.spec.model_copy(update={"work_kind": "session"}))
    assert (_root(tmp_path) / "ЛР7" / "Проект Сумма" / "main.py").is_file() and not (_root(tmp_path) / "ПЗ7").exists()
    assert Path(pipe.store.load_lab("ivanov", "psip", "7").project.folder).is_dir()


def test_failed_chat_edit_leaves_a_foreign_folder_and_the_students_choice_alone(tmp_home, tmp_path):
    """У ЛР 7 своей папки ещё нет, «ПЗ7» — папка другой работы. Чат меняет вид и задание, правка задания падает:
    чужая папка не трогается, а выбор вида не закрепляется за студентом."""
    pipe, fake, st = ready(tmp_home, tmp_path, [EDIT_AND_KIND, LLMError("network", "нет сети")])
    foreign = _root(tmp_path) / "ПЗ7"
    foreign.mkdir(parents=True)
    (foreign / "ПЗ7_ПССИП.docx").write_bytes(b"other work")
    assert st.work_kind is None
    st = pipe.chat(st, "это практическое занятие, и второе задание короче")
    assert st.chat[-1].kind == "failed" and st.spec.work_kind == "lab" and st.work_kind is None
    assert (foreign / "ПЗ7_ПССИП.docx").read_bytes() == b"other work" and not (_root(tmp_path) / "ЛР7").exists()


def test_revert_does_not_rename_a_foreign_folder(tmp_home, tmp_path):
    kind = {"kind": "edit", "reply": "Теперь это практическое занятие.", "work_kind": "session"}
    pipe, fake, st = ready(tmp_home, tmp_path, [kind])
    st = pipe.chat(st, "это практическое занятие")
    assert st.spec.work_kind == "session"
    foreign = _root(tmp_path) / "ПЗ7"  # своей папки у работы нет: программы нет, не сдана
    foreign.mkdir(parents=True)
    (foreign / "чужое.txt").write_text("другая работа", encoding="utf-8")
    st = pipe.revert(st, st.chat[-1].version)
    assert st.spec.work_kind == "lab" and (foreign / "чужое.txt").is_file() and not (_root(tmp_path) / "ЛР7").exists()


def test_folder_goes_back_when_the_state_was_not_saved(tmp_home, tmp_path, monkeypatch):
    """Облако отказало в сохранении (лабу изменили на другом компьютере): вид в сохранённой лабе прежний —
    значит и папка сдачи прежняя, а не «ПЗ7» при состоянии «лабораторная»."""
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    st = pipe.execute(st)
    real = pipe.store.save_lab

    def boom(state):
        raise RuntimeError("облако: лабу изменили на другом компьютере")

    monkeypatch.setattr(pipe.store, "save_lab", boom)
    with pytest.raises(RuntimeError):
        pipe.confirm(st, st.spec.model_copy(update={"work_kind": "session"}))
    monkeypatch.setattr(pipe.store, "save_lab", real)
    saved = pipe.store.load_lab("ivanov", "psip", "7")
    assert saved.spec.work_kind == "lab" and Path(saved.project.folder).is_dir()
    assert (_root(tmp_path) / "ЛР7").is_dir() and not (_root(tmp_path) / "ПЗ7").exists()
    manifest = json.loads((pipe.lab_dir(saved) / "project" / "hand_in.json").read_text(encoding="utf-8"))
    assert manifest["folder"] == saved.project.folder


def test_handed_in_report_is_renamed_with_the_folder(tmp_home, tmp_path):
    """После смены вида в папке не остаётся отчёта под старым именем рядом с новым."""
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    st = pipe.execute(st)
    for sec_n, blk, _ in pipe.slots(st):
        st = pipe.attach_screenshot(st, sec_n, blk, _png())
    accept = [finding_key(f) for f in st.findings]
    st = pipe.mark_final(st, accept_rules=accept)
    assert (_root(tmp_path) / "ЛР7" / "ЛР7_ПССИП.docx").is_file()
    st = pipe.confirm(st, st.spec.model_copy(update={"work_kind": "session"}))
    assert "Сдать" in _journal(pipe, st)  # в папке пока отчёт с прежним заголовком — об этом сказано
    st = pipe.mark_final(pipe.render(st), accept_rules=accept + [finding_key(f) for f in st.findings])
    assert sorted(f.name for f in (_root(tmp_path) / "ПЗ7").glob("*.docx")) == ["ПЗ7_ПССИП.docx"]


# ---------- ссылки, номера и заголовки (из той же проверки)

def _lab(pipe, key, number, kind, text, linked=(), source=""):
    from forlabs.core.models import LabSpec, LabState, Paragraph, ReportDoc, TaskSection

    spec = LabSpec(subject_id="psip", number=number, date="1.09.2026", topic=text, goal="Изучить", work_kind=kind,
                   source_text=source)
    report = ReportDoc(lab_number=number, work_kind=kind, sections=[
        TaskSection(n=1, title="Задание", blocks=[Paragraph(text=text)])])
    st = LabState(profile_id="ivanov", subject_id="psip", number=key, status="rendered", spec=spec, report=report,
                  linked=list(linked))
    pipe.store.save_lab(st)
    return st


def test_reference_from_the_methodology_never_lands_on_a_work_of_another_kind(tmp_home, tmp_path):
    """Методичка ПЗ 5 ссылается на «ПЗ№4», а в ForLabs есть только ЛР 4: это не та работа. Номер, который вписал
    сам студент, — его решение: тогда ЛР 4 берётся, и названа она лабораторной."""
    from test_run import setup

    pipe, _ = setup(tmp_home, tmp_path, [])
    _lab(pipe, "4", "4", "lab", "Лабораторная про сети")
    pz = _lab(pipe, "5-пз", "5", "session", "Пятое занятие", source="Выполните по теме из ПЗ№4.")
    assert pipe._auto_refs(pz) == ["4"]
    found, missing = pipe._linked_labs(pz)
    assert found == [] and missing == ["ПЗ 4"]
    pz.linked = ["4"]
    found, missing = pipe._linked_labs(pz)
    assert missing == [] and "про сети" in found[0][3]
    assert any(f.message.startswith("ЛР 4 ") for f in pipe._link_findings(pz))


def test_numbers_and_headings_of_practical_works(tmp_home, tmp_path):
    from test_run import setup

    from forlabs.pipeline.appshots import hand_in_dir
    from forlabs.pipeline.memory import referenced_labs
    from forlabs.pipeline.run import normalize_number, plain_number
    from forlabs.report.derive import detect_work_kind

    assert normalize_number("ПЗ 4") == "4" and normalize_number("Практическое занятие №3") == "3"
    assert normalize_number("пр.5") == "5" and normalize_number("ЛР 7.") == "7"
    assert plain_number("7-пз-2") == "7" and plain_number("7-2") == "7-2" and plain_number("5.2") == "5.2"
    pipe, fake = setup(tmp_home, tmp_path, [RAW])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="ПЗ 4",
                                    work_kind="session"), date="24.09.2026")
    assert (st.number, st.spec.number, hand_in_dir(pipe, st).name) == ("4", "4", "ПЗ4")
    # сборник с короткими заголовками «ПЗ №N»: чужие заголовки — не ссылки
    manual = "ПЗ №1\nТема: а\nПЗ №2\nТема: б\nПЗ №3\nТема: в\nПЗ №4\nТема: г. Используйте результат ПЗ №2.\n"
    assert referenced_labs(manual, "4", "session") == ["2"]
    # заголовки, как их на самом деле пишут
    assert detect_work_kind("Инструкционная карта\nна выполнение практического занятия № 4\nТема", "4") == "session"
    assert detect_work_kind("1. Практическое занятие №3\nТема", "3") == "session"
    assert detect_work_kind("Группа 21 | Практическое занятие №3\nТема", "3") == "session"
    assert detect_work_kind("ПЗ1 Тема: анализ", "1") == "session"
    assert detect_work_kind("Отчёт по практической работе №6\nТема", "6") == "practice"
    assert detect_work_kind("Лабораторная работа №2\nПР 2 часа на выполнение\nТема", "5") == "lab"  # «ПР 2 часа» — время


def test_screenshots_of_an_ordinary_report_are_not_sent_to_the_model(tmp_home, tmp_path):
    """Обычный отчёт: много своего текста и снимки окон программы — читать их незачем (это минуты и токены).
    Читаются снимки там, где текста на снимок мало: работа вставлена снимками документов."""
    import io

    import docx as docx_lib
    from docx.shared import Inches
    from PIL import Image
    from test_run import setup

    d = docx_lib.Document()
    d.add_paragraph("Лабораторная работа №1")
    d.add_paragraph("Программа считает сумму. " * 120)
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (10, 10, 10)).save(buf, "PNG")
    d.add_picture(io.BytesIO(buf.getvalue()), width=Inches(3))
    out = io.BytesIO()
    d.save(out)
    pipe, fake = setup(tmp_home, tmp_path, [RAW, {"labs": []}])  # второй ответ — поиск ЛР 2, на которую ссылается методичка
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())]), date="24.09.2026")
    calls = len(fake.calls)
    st, _ = pipe.set_links(st, ["1"], ("общий.docx", out.getvalue()))
    with_pictures = [c for c in fake.calls[calls:] if any(isinstance(m["content"], list) for m in c)]
    assert with_pictures == [] and "Читаю снимки" not in _journal(pipe, st)
    assert "Программа считает сумму" in st.linked_texts["1"] and "[[картинка" not in st.linked_texts["1"]
