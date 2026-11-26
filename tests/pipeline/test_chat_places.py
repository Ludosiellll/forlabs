"""Чат знает, где на самом деле лежат файлы работы, и не выдаёт за существующие документы, которых ForLabs не
создавал. Случай из практики: студентка просила сделать задания отдельными файлами Word, чат отвечал «файлы уже
лежат в папке, перешлю заново», а на просьбу назвать путь — «придумывать не буду»."""

from test_chat_agent import said
from test_chat_run import ready
from test_project_stage import PLAN, make
from test_work_kind_stage import _png

from forlabs.core.models import finding_key

ANSWER = {"kind": "answer", "reply": "Проект лежит в папке проекта."}


def _folder(tmp_path):
    return tmp_path / "сдача" / "ПССИП" / "ЛР7"


def test_chat_is_given_the_real_paths(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, ANSWER])
    st = pipe.execute(st)
    pipe.chat(st, "укажи путь к файлам")
    asked = said(fake.calls[-1])
    assert "Где что лежит" in asked
    assert str(_folder(tmp_path) / "Проект Сумма") in asked  # папка проекта — полным путём
    assert str(_folder(tmp_path) / "ЛР7_ПССИП.docx") in asked and "появится после кнопки «Сдать»" in asked
    assert "Других файлов в папке сдачи нет" in asked


def test_chat_sees_the_handed_in_report_and_the_students_own_files(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, ANSWER])
    st = pipe.execute(st)
    for sec_n, blk, _ in pipe.slots(st):
        st = pipe.attach_screenshot(st, sec_n, blk, _png())
    st = pipe.mark_final(st, accept_rules=[finding_key(f) for f in st.findings])
    (_folder(tmp_path) / "Задание 2.docx").write_bytes(b"PK")
    (_folder(tmp_path) / "~$дание 2.docx").write_bytes(b"lock")  # замок открытого в Word файла — не документ
    pipe.chat(st, "где отчёт")
    asked = said(fake.calls[-1])
    assert str(_folder(tmp_path) / "ЛР7_ПССИП.docx") + " (уже лежит)" in asked
    assert "Задание 2.docx" in asked and "~$" not in asked
    assert "main.py" not in asked.split("Другие файлы в папке сдачи")[1].split("\n")[0]  # проект назван отдельно


def test_lab_without_a_program_says_so(tmp_home, tmp_path):
    pipe, fake, st = ready(tmp_home, tmp_path, [ANSWER])
    pipe.chat(st, "где файлы")
    asked = said(fake.calls[-1])
    assert "Где что лежит" in asked and "Проекта программы нет" in asked


def test_chat_is_told_not_to_invent_files(tmp_home, tmp_path):
    pipe, fake, st = ready(tmp_home, tmp_path, [ANSWER])
    pipe.chat(st, "сделай задания отдельными файлами word")
    system = fake.calls[-1][0]["content"]
    assert "Где что лежит" in system and "не пересылает" in system
    assert "отдельных документов" in system.lower()  # Word, PowerPoint, Excel ForLabs сам не создаёт
    assert "дословно" in system  # путь — как записан, а не «точный путь не записан»


KIND = {"kind": "answer", "reply": "Теперь это практическое занятие №7.", "work_kind": "session"}


def test_chat_renames_the_work_and_revert_brings_it_back(tmp_home, tmp_path):
    """«Это практическое занятие, а не лабораторная» — меняются заголовок, папка сдачи и подпись в списке."""
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, KIND])
    st = pipe.execute(st)
    st = pipe.chat(st, "это практическое занятие, а не лабораторная")
    asked = said(fake.calls[-1])
    assert "Вид работы: лабораторная работа №7" in asked  # чат знал, как работа называлась
    reply = st.chat[-1]
    assert reply.kind == "edit" and "вид работы — практическое занятие" in reply.changed
    assert (st.spec.work_kind, st.work_kind, st.report.work_kind) == ("session", "session", "session")
    new = tmp_path / "сдача" / "ПССИП" / "ПЗ7"
    assert (new / "Проект Сумма" / "main.py").is_file() and not _folder(tmp_path).exists()
    st = pipe.revert(st, reply.version)
    assert st.spec.work_kind == "lab" and (_folder(tmp_path) / "Проект Сумма" / "main.py").is_file()
    assert not new.exists()


def test_chat_does_not_change_the_kind_to_the_same_or_unknown(tmp_home, tmp_path):
    same = {"kind": "edit", "reply": "Готово.", "work_kind": "lab"}
    odd = {"kind": "edit", "reply": "Готово.", "work_kind": "курсовая"}
    pipe, fake, st = ready(tmp_home, tmp_path, [same, odd])
    for _ in range(2):
        st = pipe.chat(st, "назови работу иначе")
        assert st.spec.work_kind == "lab" and st.chat[-1].kind == "question"  # менять нечего — чат переспросит


def test_writer_and_reviser_do_not_invent_saved_files():
    """В свежем отчёте по практическому модель писала «документ оформлен в виде файла tz_maket3d.docx» — файла нет."""
    from forlabs.pipeline.prompts import CHAT_REVISE_SYSTEM, WRITE_SYSTEM

    for prompt in (WRITE_SYSTEM, CHAT_REVISE_SYSTEM):
        assert "отдельных файлов не создаёт" in prompt and "Имена файлов называй только" in prompt
        prompt.format(rules="")  # фигурные скобки подсказки не сломаны
