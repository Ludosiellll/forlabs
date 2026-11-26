"""Вид работы в конвейере: заголовок отчёта, папка сдачи и имя файла, номер рядом с лабораторной."""

from docx import Document
from test_project_stage import PLAN, make

from forlabs.core.models import finding_key
from forlabs.pipeline.appshots import hand_in_dir


def _as_session(pipe, st):
    st.spec.work_kind = "session"
    return pipe.confirm(st, st.spec)


def test_practice_report_heading_folder_and_file(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    st = pipe.execute(_as_session(pipe, st))
    assert st.report.work_kind == "session"
    out = tmp_path / "сдача" / "ПССИП" / "ПЗ7"
    assert hand_in_dir(pipe, st) == out
    assert (out / "Проект Сумма" / "main.py").is_file()  # проект — в папке практического, а не «ЛР7»
    for sec_n, blk, _ in pipe.slots(st):
        st = pipe.attach_screenshot(st, sec_n, blk, _png())
    st = pipe.mark_final(st, accept_rules=[finding_key(f) for f in st.findings])
    target = out / "ПЗ7_ПССИП.docx"
    assert target.is_file() and not (tmp_path / "сдача" / "ПССИП" / "ЛР7").exists()
    assert Document(str(target)).paragraphs[0].text == "Практическое занятие №7"


def _png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (640, 360), (240, 240, 240)).save(buf, "PNG")
    return buf.getvalue()


def _journal(pipe, st) -> str:
    return " ".join(e.text for e in pipe.bus.history(pipe.key(st)))


def test_practice_with_the_number_of_a_lab_sits_next_to_it(tmp_home, tmp_path):
    """В одном предмете есть и ЛР 7, и ПЗ 7: вторая не становится «7-2» и не пугает «уже есть»."""
    from test_run import FIXTURE, RAW, setup

    from forlabs.report.derive import lab_label

    pipe, fake = setup(tmp_home, tmp_path, [RAW, RAW, RAW, RAW])
    text = FIXTURE.read_bytes()
    lab = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, text)]), date="24.09.2026")
    assert (lab.number, lab.spec.work_kind) == ("7", "lab")
    source = "Практическое занятие №7\n".encode() + text
    pz = pipe.parse(pipe.create_lab("ivanov", "psip", [("пз7.txt", source)]), date="24.09.2026")
    assert (pz.number, pz.spec.number, pz.spec.work_kind) == ("7-пз", "7", "session")
    assert lab_label(pz.spec.work_kind, pz.number) == "ПЗ 7"
    assert "уже есть" not in _journal(pipe, pz)
    assert hand_in_dir(pipe, pz).name == "ПЗ7" and hand_in_dir(pipe, lab).name == "ЛР7"  # папки сдачи разные
    again = pipe.parse(pipe.create_lab("ivanov", "psip", [("пз7.txt", source)]), date="24.09.2026")
    assert again.number == "7-пз-2" and "ПЗ 7 уже есть" in _journal(pipe, again)  # та же работа второй раз
    dup = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, text)]), date="24.09.2026")
    assert dup.number == "7-2" and "ЛР 7 уже есть" in _journal(pipe, dup)


def test_kind_chosen_by_the_student_wins_over_the_methodology(tmp_home, tmp_path):
    from test_run import FIXTURE, RAW, setup

    pipe, fake = setup(tmp_home, tmp_path, [RAW, RAW])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], work_kind="practice")
    st = pipe.parse(st, date="24.09.2026")
    assert st.spec.work_kind == "practice" and st.work_kind == "practice"
    st = pipe.parse(st, date="24.09.2026")  # повторный разбор выбор не теряет
    assert st.spec.work_kind == "practice"


def test_typed_number_of_a_lab_is_free_for_a_practice(tmp_home, tmp_path):
    import pytest
    from test_run import FIXTURE, RAW, setup

    from forlabs.pipeline.run import PipelineError

    pipe, fake = setup(tmp_home, tmp_path, [RAW, RAW])
    pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7"), date="24.09.2026")
    with pytest.raises(PipelineError, match="ЛР 7 уже есть"):
        pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    pz = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7", work_kind="session")
    pz = pipe.parse(pz, date="24.09.2026")
    assert (pz.number, pz.spec.number, pz.spec.work_kind) == ("7-пз", "7", "session")


def test_changing_the_kind_moves_the_hand_in_folder(tmp_home, tmp_path):
    """Вид поменяли после выполнения: папка сдачи переезжает целиком, проект остаётся «своим» для ForLabs."""
    import json

    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    st = pipe.execute(st)
    old, new = tmp_path / "сдача" / "ПССИП" / "ЛР7", tmp_path / "сдача" / "ПССИП" / "ПЗ7"
    (old / "мои заметки.txt").write_text("не терять", encoding="utf-8")
    spec = st.spec.model_copy(update={"work_kind": "session"})
    st = pipe.confirm(st, spec)
    assert (st.spec.work_kind, st.work_kind, st.report.work_kind) == ("session", "session", "session")
    assert not old.exists() and (new / "Проект Сумма" / "main.py").is_file()
    assert (new / "мои заметки.txt").read_text(encoding="utf-8") == "не терять"
    assert st.project.folder == str(new / "Проект Сумма")
    manifest = json.loads((pipe.lab_dir(st) / "project" / "hand_in.json").read_text(encoding="utf-8"))
    assert manifest["folder"] == str(new / "Проект Сумма")
    assert st.status in ("rendered", "needs_manual") and st.docx_sha is None  # документ пересоберётся с заголовком


def test_changing_the_kind_never_merges_into_a_folder_of_another_work(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    st = pipe.execute(st)
    old, new = tmp_path / "сдача" / "ПССИП" / "ЛР7", tmp_path / "сдача" / "ПССИП" / "ПЗ7"
    (new).mkdir(parents=True)
    (new / "чужое.txt").write_text("другая работа", encoding="utf-8")
    st = pipe.confirm(st, st.spec.model_copy(update={"work_kind": "session"}))
    assert (old / "Проект Сумма" / "main.py").is_file() and [f.name for f in new.iterdir()] == ["чужое.txt"]
    assert "ЛР7" in _journal(pipe, st) and "ПЗ7" in _journal(pipe, st)  # студент знает, что папка осталась прежней


def test_practice_references_are_found_only_for_a_practice():
    from forlabs.pipeline.memory import referenced_labs

    assert referenced_labs("Согласно предметной области, выбранной в ПЗ№1, реализуйте задания.", "2", "session") == ["1"]
    assert referenced_labs("Выполните по теме из практической работы №1.", "2", "practice") == ["1"]
    assert referenced_labs("Продолжите предыдущее практическое занятие.", "3", "session") == ["previous"]
    # ссылка на работу другого вида сама не подставляется: по номеру нашлась бы чужая работа
    assert referenced_labs("Возьмите сценарии из практической работы №6.", "8", "lab") == []
    assert referenced_labs("Используйте базу из ЛР №2.", "3", "session") == []
    assert referenced_labs("Используйте базу из ЛР №2.", "3") == ["2"]  # лабораторные — как раньше


def test_linked_work_is_taken_from_the_same_kind(tmp_home, tmp_path):
    """У предмета есть ЛР 1 и ПЗ 1: «прошлая работа №1» для практического занятия — это ПЗ 1."""
    from test_run import setup

    from forlabs.core.models import LabSpec, LabState, Paragraph, ReportDoc, TaskSection

    pipe, _ = setup(tmp_home, tmp_path, [])

    def lab(key, number, kind, text, linked=()):
        spec = LabSpec(subject_id="psip", number=number, date="1.09.2026", topic=text, goal="Изучить", work_kind=kind)
        report = ReportDoc(lab_number=number, work_kind=kind, sections=[
            TaskSection(n=1, title="Задание", blocks=[Paragraph(text=text)])])
        st = LabState(profile_id="ivanov", subject_id="psip", number=key, status="rendered", spec=spec, report=report,
                      linked=list(linked))
        pipe.store.save_lab(st)
        return st

    lab("1", "1", "lab", "Лабораторная про сети")
    lab("1-пз", "1", "session", "Практическое про планировщик интерьера")
    pz2 = lab("2-пз", "2", "session", "Техническое задание", linked=["1"])
    found, missing = pipe._linked_labs(pz2)
    assert missing == [] and len(found) == 1 and "планировщик интерьера" in found[0][3]
    lr2 = lab("2", "2", "lab", "Вторая лабораторная", linked=["1"])
    found, _ = pipe._linked_labs(lr2)
    assert "про сети" in found[0][3]
    # «предыдущая работа» — тоже своего вида
    pz2.spec.source_text, pz2.linked = "Продолжите предыдущее практическое занятие.", []
    found, _ = pipe._linked_labs(pz2)
    assert len(found) == 1 and "планировщик интерьера" in found[0][3]


def test_writer_is_told_the_kind_of_work(tmp_home, tmp_path):
    """Текст отчёта называет работу так же, как заголовок: «в практическом занятии», а не «в лабораторной»."""
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    st = pipe.execute(_as_session(pipe, st))
    wrote = [m["content"] for call in fake.calls for m in call if "Напиши содержимое задания" in m["content"]]
    assert wrote and all("Вид работы: практическое занятие" in text for text in wrote)


def test_screenshots_of_the_past_work_in_the_common_report_are_read(tmp_home, tmp_path):
    """Прошлая работа в общем отчёте вставлена снимками документов (так теперь требуют): её текст читает модель,
    причём только у нужной работы — снимки остальных работ отчёта не трогаются."""
    import io

    import docx as docx_lib
    from docx.shared import Inches
    from PIL import Image
    from test_run import FIXTURE, RAW, setup

    def png(color):
        buf = io.BytesIO()
        Image.new("RGB", (400, 300), color).save(buf, "PNG")
        return buf.getvalue()

    d = docx_lib.Document()
    d.add_paragraph("Практическое занятие №1")
    d.add_paragraph("Задание 1. Опишите предметную область.")
    d.add_picture(io.BytesIO(png((200, 10, 10))), width=Inches(3))
    d.add_paragraph("Рисунок 1 – Описание предметной области")
    d.add_paragraph("Практическое занятие №2")
    d.add_picture(io.BytesIO(png((10, 200, 10))), width=Inches(3))
    buf = io.BytesIO()
    d.save(buf)

    read = {"pictures": ["Предметная область: планировщик интерьера «Макет 3D»"]}
    pipe, fake = setup(tmp_home, tmp_path, [RAW, read])
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], work_kind="session")
    st = pipe.parse(st, date="24.09.2026")
    calls = len(fake.calls)
    st, missing = pipe.set_links(st, ["1"], ("общий.docx", buf.getvalue()))
    assert missing == [] and "планировщик интерьера «Макет 3D»" in st.linked_texts["1"]
    assert "[[картинка" not in st.linked_texts["1"]
    assert len(fake.calls) == calls + 1  # один запрос на один снимок ПЗ 1; снимок ПЗ 2 не читался
    assert "Читаю снимки в общем отчёте: 1" in _journal(pipe, st)


def test_long_screenshot_text_does_not_push_out_the_text_of_the_work(tmp_home, tmp_path):
    """Снимок презентации или теста даёт страницы текста. Свой текст работы (описание предметной области в конце)
    важнее: он остаётся целиком, а текст снимков сокращается под оставшийся объём."""
    import io

    import docx as docx_lib
    from docx.shared import Inches
    from PIL import Image
    from test_run import FIXTURE, RAW, setup

    from forlabs.pipeline.memory import LINKED_MAX

    d = docx_lib.Document()
    d.add_paragraph("Практическое занятие №1")
    for color in ((200, 10, 10), (10, 10, 200)):
        buf = io.BytesIO()
        Image.new("RGB", (400, 300), color).save(buf, "PNG")
        d.add_picture(io.BytesIO(buf.getvalue()), width=Inches(3))
    d.add_paragraph("Задание 4. " + "Предметная область — планировщик интерьера. " * 60)
    d.add_paragraph("Конец описания предметной области.")
    out = io.BytesIO()
    d.save(out)
    read = {"pictures": ["слайд " * 3000, "вопрос теста " * 2000]}
    pipe, fake = setup(tmp_home, tmp_path, [RAW, read])
    st = pipe.parse(pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], work_kind="session"),
                    date="24.09.2026")
    st, _ = pipe.set_links(st, ["1"], ("общий.docx", out.getvalue()))
    body = st.linked_texts["1"]
    assert len(body) <= LINKED_MAX and "Конец описания предметной области." in body
    assert body.count("[На снимке в отчёте:") == 2 and "слайд слайд" in body and "вопрос теста" in body
