"""Студент сам поправил код в папке проекта (в Visual Studio, в редакторе). Пересборка и чат не затирают его
правки: его текст становится текстом проекта, листинг отчёта идёт за ним."""

from pathlib import Path

from test_project_stage import GOOD, PLAN, figures, make
from test_shot_repair import ScriptShooter

from forlabs.pipeline import project as proj

MINE = GOOD.replace("Сумма:", "Итог:")


def folder(tmp_path) -> Path:
    return tmp_path / "сдача" / "ПССИП" / "ЛР7" / "Проект Сумма"


def journal(pipe, st) -> str:
    return "\n".join(e.text for e in pipe.journal(st))


def test_students_edit_survives_rebuild_and_reaches_the_report(tmp_home, tmp_path):
    shooter = ScriptShooter(True, True)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN], shooter=shooter)
    st = pipe.execute(st)
    main = folder(tmp_path) / "main.py"
    main.write_text(MINE, encoding="utf-8")
    st = pipe.retry_project(st)
    assert main.read_text(encoding="utf-8") == MINE  # правка студента на месте
    assert proj.load_plan(pipe, st).file("main.py").content == MINE
    assert "Итог:" in st.report.sections[0].blocks[1].code  # листинг отчёта — уже его код
    assert "main.py" in journal(pipe, st) and "твои правки" in journal(pipe, st)
    assert len(shooter.calls) == 2  # и снимок сделан заново, от его программы


def test_file_saved_with_windows_line_ends_is_not_an_edit(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN], shooter=ScriptShooter(True, True))
    st = pipe.execute(st)
    main = folder(tmp_path) / "main.py"
    main.write_bytes(b"\xef\xbb\xbf" + GOOD.replace("\n", "\r\n").encode("utf-8"))  # так пересохраняет редактор
    st = pipe.retry_project(st)
    assert "твои правки" not in journal(pipe, st)
    assert proj.load_plan(pipe, st).file("main.py").content == GOOD


def test_untouched_project_is_rebuilt_as_before(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN], shooter=ScriptShooter(True, True))
    st = pipe.retry_project(pipe.execute(st))
    assert "твои правки" not in journal(pipe, st) and (folder(tmp_path) / "main.py").read_text(encoding="utf-8") == GOOD


def test_students_dangerous_edit_is_not_run(tmp_home, tmp_path):
    shooter = ScriptShooter(True, True)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN], shooter=shooter)
    st = pipe.execute(st)
    (folder(tmp_path) / "main.py").write_text("import os\nos.system(\"del /q C:\\\\*\")\n" + GOOD, encoding="utf-8")
    st = pipe.retry_project(st)
    assert st.project.blocked and len(shooter.calls) == 1  # его код проверяется так же, как код модели


def test_chat_fix_starts_from_the_students_text(tmp_home, tmp_path):
    fix_it = {"kind": "answer", "reply": "Поправлю.", "project": "добавить пустую строку в конец вывода"}
    repaired = {"files": [{"path": "main.py", "content": MINE + "print()\n"}]}
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, fix_it, repaired], shooter=ScriptShooter(True, True))
    st = pipe.execute(st)
    (folder(tmp_path) / "main.py").write_text(MINE, encoding="utf-8")
    st = pipe.chat(st, "добавь пустую строку после результата")
    asked = "\n".join(m["content"] for m in fake.calls[-1])
    assert 'print("Итог:", a + b)' in asked  # модель чинит уже его текст, а не прежний
    assert (folder(tmp_path) / "main.py").read_text(encoding="utf-8") == MINE + "print()\n"


# ---------- по замечаниям проверки ----------

import sys  # noqa: E402

import pytest  # noqa: E402

RU = GOOD.replace('"Сумма:"', '"Итоговая сумма:"')


def rebuilt(tmp_home, tmp_path, write, extra=()):
    """Лаба выполнена → студент меняет папку проекта (write) → «Пересобрать»."""
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, *extra], shooter=ScriptShooter(True, True, True))
    st = pipe.execute(st)
    write(folder(tmp_path))
    return pipe, fake, pipe.retry_project(st)


def test_file_saved_in_windows_1251_is_read_as_text_not_garbage(tmp_home, tmp_path):
    pipe, fake, st = rebuilt(tmp_home, tmp_path, lambda d: (d / "main.py").write_bytes(RU.encode("cp1251")))
    assert proj.load_plan(pipe, st).file("main.py").content == RU
    assert (folder(tmp_path) / "main.py").read_text(encoding="utf-8") == RU  # и в папке — тот же текст, читаемый
    assert "Итоговая сумма" in st.report.sections[0].blocks[1].code


def test_file_saved_in_utf16_is_read_as_text(tmp_home, tmp_path):
    pipe, fake, st = rebuilt(tmp_home, tmp_path, lambda d: (d / "main.py").write_bytes(RU.encode("utf-16")))
    assert proj.load_plan(pipe, st).file("main.py").content == RU


def test_unreadable_file_is_not_taken_and_not_lost(tmp_home, tmp_path):
    junk = b"\x00\x01\x02\xff\xfe\x00binary"
    pipe, fake, st = rebuilt(tmp_home, tmp_path, lambda d: (d / "main.py").write_bytes(junk))
    assert proj.load_plan(pipe, st).file("main.py").content == GOOD  # в проект мусор не попал
    assert (folder(tmp_path) / "main.py.bak").read_bytes() == junk  # а файл студента сохранён рядом
    assert ".bak" in journal(pipe, st)


def test_new_file_added_by_the_student_becomes_part_of_the_project(tmp_home, tmp_path):
    def write(d):
        (d / "util.py").write_text("def label():\n    return \"Сумма:\"\n", encoding="utf-8")
        (d / "main.py").write_text("from util import label\n" + GOOD.replace('"Сумма:"', "label()"), encoding="utf-8")

    pipe, fake, st = rebuilt(tmp_home, tmp_path, write)
    plan = proj.load_plan(pipe, st)
    assert plan.file("util.py") is not None and "from util import label" in plan.file("main.py").content
    assert st.project.built and (folder(tmp_path) / "util.py").read_text(encoding="utf-8").startswith("def label")
    assert not (folder(tmp_path) / "util.py.bak").exists()  # своё не затёрто — и копия не нужна


def test_data_file_changed_by_the_program_itself_is_not_an_edit(tmp_home, tmp_path):
    plan = {**PLAN, "files": [*PLAN["files"], {"path": "журнал.txt", "content": "пусто\n"}]}
    pipe, fake, st = make(tmp_home, tmp_path, [plan], shooter=ScriptShooter(True, True))
    st = pipe.execute(st)
    (folder(tmp_path) / "журнал.txt").write_text("пусто\nзапуск 1\nзапуск 2\n", encoding="utf-8")
    st = pipe.retry_project(st)
    assert proj.load_plan(pipe, st).file("журнал.txt").content == "пусто\n" and "твои правки" not in journal(pipe, st)


def test_listing_follows_even_a_heavy_rewrite(tmp_home, tmp_path):
    mine = "x = int(input(\"Первое: \"))\ny = int(input(\"Второе: \"))\nz = x + y\nprint(\"Всего:\", z)\n"
    pipe, fake, st = rebuilt(tmp_home, tmp_path, lambda d: (d / "main.py").write_text(mine, encoding="utf-8"))
    assert st.report.sections[0].blocks[1].code.strip() == mine.strip()
    assert not proj.stale(st, proj.load_plan(pipe, st))


def test_edit_does_not_hide_that_the_report_has_moved_on(tmp_home, tmp_path):
    # отчёт уже другой (откат, правка): проект составляется заново, а не подгоняется под правку файла
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, PLAN], shooter=ScriptShooter(True, True, True))
    st = pipe.execute(st)
    st.report.sections[0].blocks[1].code = GOOD.replace("Сумма:", "Результат:").rstrip()
    pipe._save(st)
    assert proj.stale(st, proj.load_plan(pipe, st))
    (folder(tmp_path) / "main.py").write_text(MINE, encoding="utf-8")
    calls = len(fake.calls)
    st = pipe.retry_project(st)
    assert len(fake.calls) == calls + 1  # проект составлен заново по отчёту
    assert (folder(tmp_path) / "main.py.bak").read_text(encoding="utf-8") == MINE  # правка студента не пропала


@pytest.mark.skipif(sys.platform != "win32", reason="имена без учёта регистра — только в Windows")
def test_file_renamed_only_by_letter_case_is_not_deleted(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN], shooter=ScriptShooter(True, True))
    st = pipe.execute(st)
    work = tmp_path / "work"
    work.mkdir()
    (work / "MAIN.py").write_text(GOOD, encoding="utf-8")  # исправление прислало файл с другим регистром букв
    out, locked, saved = proj._copy_to_hand_in(pipe, st, proj.load_plan(pipe, st), work)
    assert [x.name.lower() for x in out.iterdir() if x.is_file()] == ["main.py"]  # файл на месте, а не удалён
    assert (out / "MAIN.py").read_text(encoding="utf-8") == GOOD and not locked
