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
