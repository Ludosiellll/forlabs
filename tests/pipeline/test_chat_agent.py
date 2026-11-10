"""Чат правок работает как помощник с руками: видит ошибки сборки и файлы проекта, сам заглядывает в нужный файл,
а на «исправь» чинит программу, пересобирает её и переснимает окна — не объясняет студенту, что тому сделать."""

from pathlib import Path

import pytest
from test_project_stage import GOOD, PLAN, figures, make
from test_shot_repair import ScriptShooter

from forlabs.pipeline import chat as chat_mod
from forlabs.pipeline.lookup import ProjectView
from forlabs.runner.plan import ProjectPlan

BAD = "print(\"Сумма:\"\n"
BROKEN = {**PLAN, "files": [{"path": "main.py", "content": BAD}]}
STILL = {"files": [{"path": "main.py", "content": BAD}]}
ANSWER = {"kind": "answer", "reply": "В main.py не закрыта скобка в print."}
FIX_IT = {"kind": "answer", "reply": "Исправлю ошибку в main.py и соберу заново.",
          "project": "закрыть скобку в print в файле main.py"}
REPAIRED = {"files": [{"path": "main.py", "content": GOOD}]}


def broken_lab(tmp_home, tmp_path, then, shooter=None):
    """Лаба, у которой программа не собралась и три круга исправлений не помогли."""
    pipe, fake, st = make(tmp_home, tmp_path, [BROKEN, STILL, STILL, STILL, *then], shooter=shooter)
    st = pipe.execute(st)
    assert not st.project.built and "SyntaxError" in st.project.build_log
    return pipe, fake, st


def said(call) -> str:
    return "\n".join(m["content"] for m in call)


def hand_in_dir(tmp_path) -> Path:
    return tmp_path / "сдача" / "ПССИП" / "ЛР7" / "Проект Сумма"


# ---------- что чат видит ----------

def test_chat_is_given_build_errors_and_project_files(tmp_home, tmp_path):
    pipe, fake, st = broken_lab(tmp_home, tmp_path, [ANSWER])
    st = pipe.chat(st, "почему не собирается")
    asked = said(fake.calls[-1])
    assert "SyntaxError" in asked and "не собралась" in asked  # сам текст ошибки компилятора
    assert "main.py" in asked and "Файлы проекта" in asked
    assert st.chat[-1].text.startswith("В main.py не закрыта скобка")


def test_chat_is_told_it_can_look_and_fix(tmp_home, tmp_path):
    pipe, fake, st = broken_lab(tmp_home, tmp_path, [ANSWER])
    pipe.chat(st, "почему не собирается")
    system = fake.calls[-1][0]["content"]
    assert '"look"' in system and '"project"' in system and "не объясняй" in system.lower()


def test_chat_reads_a_project_file_before_answering(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, {"look": [{"file": "main.py"}]},
                                               {"kind": "answer", "reply": "Числа читает функция input."}])
    st = pipe.execute(st)
    before = len(st.chat)
    st = pipe.chat(st, "чем программа читает числа?")
    read = "Файл main.py (как лежит в папке проекта у студента)"
    assert read in said(fake.calls[-1]) and '1: a = float(input("Введите a: "))' in said(fake.calls[-1])
    assert read not in said(fake.calls[-2])  # в первом запросе файла ещё не было: чат попросил его сам
    assert len(st.chat) == before + 2 and st.chat[-1].text == "Числа читает функция input."


def test_chat_can_search_the_project(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, {"look": [{"find": "Сумма"}]},
                                               {"kind": "answer", "reply": "В третьей строке."}])
    st = pipe.chat(pipe.execute(st), "где печатается сумма?")
    assert "main.py:3:" in said(fake.calls[-1])


def test_chat_sees_what_the_student_changed_in_the_project_folder(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, {"look": [{"file": "main.py"}]},
                                               {"kind": "answer", "reply": "Да, вижу твою строку."}])
    st = pipe.execute(st)
    mine = hand_in_dir(tmp_path) / "main.py"
    mine.write_text(GOOD + "print(\"Моя строка\")\n", encoding="utf-8")
    pipe.chat(st, "я дописал строку в main.py, видишь?")
    assert "Моя строка" in said(fake.calls[-1])


def test_looking_is_limited_to_a_few_rounds(tmp_home, tmp_path):
    again = {"look": [{"file": "main.py"}]}
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, *[again] * (chat_mod.MAX_LOOKS + 1)])
    st = pipe.execute(st)
    calls = len(fake.calls)
    st = pipe.chat(st, "посмотри код")
    assert len(fake.calls) - calls == chat_mod.MAX_LOOKS + 1
    assert st.chat[-1].role == "assistant" and st.chat[-1].text  # студент получил ответ, а не тишину


# ---------- что чат делает ----------

def test_fix_it_repairs_the_program_rebuilds_and_takes_the_pictures(tmp_home, tmp_path):
    shooter = ScriptShooter(True)
    pipe, fake, st = broken_lab(tmp_home, tmp_path, [FIX_IT, REPAIRED], shooter=shooter)
    assert not figures(st)[0].image_path
    st = pipe.chat(st, "так сделай это")
    assert st.project.built and st.project.build_log == ""
    assert figures(st)[0].image_path and len(shooter.calls) == 1
    reply = st.chat[-1].text
    assert "Программу исправил" in reply and "Снял сам" in reply
    assert (hand_in_dir(tmp_path) / "main.py").read_text(encoding="utf-8") == GOOD
    repair = said(fake.calls[-1])
    assert "закрыть скобку" in repair and "SyntaxError" in repair and "main.py" in repair


def test_fix_that_still_does_not_build_tells_the_error(tmp_home, tmp_path):
    pipe, fake, st = broken_lab(tmp_home, tmp_path, [FIX_IT, STILL, STILL, STILL, STILL])
    st = pipe.chat(st, "исправь ошибки сборки")
    assert not st.project.built
    reply = st.chat[-1].text
    assert "Сборка не прошла" in reply and "SyntaxError" in reply  # не «ошибки остались», а сама ошибка


def test_fix_can_be_reverted_like_any_other_edit(tmp_home, tmp_path):
    pipe, fake, st = broken_lab(tmp_home, tmp_path, [FIX_IT, REPAIRED], shooter=ScriptShooter(True))
    versions = len(st.versions)
    st = pipe.chat(st, "исправь")
    assert len(st.versions) == versions + 1 and st.chat[-1].version is not None


def test_try_again_tells_why_the_build_fails(tmp_home, tmp_path):
    again = {"kind": "answer", "reply": "Попробую снять заново.", "reshoot": True}
    pipe, fake, st = broken_lab(tmp_home, tmp_path, [again, STILL, STILL, STILL])
    st = pipe.chat(st, "попробуй еще раз")
    assert "Сборка не прошла" in st.chat[-1].text and "SyntaxError" in st.chat[-1].text


# ---------- что можно посмотреть ----------

@pytest.fixture
def view(tmp_path):
    plan = ProjectPlan.model_validate({**PLAN, "files": [{"path": "main.py", "content": GOOD},
                                                          {"path": "data/числа.txt", "content": "1 2 3\n"}]})
    folder = tmp_path / "Проект"
    (folder / "data").mkdir(parents=True)
    (folder / "main.py").write_text(GOOD, encoding="utf-8")
    (folder / "data" / "числа.txt").write_text("1 2 3\n", encoding="utf-8")
    (folder / "bin").mkdir()
    (folder / "bin" / "main.exe").write_bytes(b"MZ\x00\x00")
    (tmp_path / "secret.txt").write_text("пароль 12345", encoding="utf-8")
    return ProjectView(plan=plan, folder=folder)


def test_view_lists_files_without_build_output(view):
    assert set(view.files()) == {"main.py", "data/числа.txt"}


def test_view_never_reads_outside_the_project_folder(view):
    for path in ("../secret.txt", "..\\secret.txt", "/etc/passwd", "data/../../secret.txt", "C:\\Windows\\win.ini"):
        text = view.read(path)
        assert "пароль" not in text and "нет в проекте" in text, path


def test_view_does_not_follow_a_link_out_of_the_folder(view, tmp_path):
    link = view.folder / "link.txt"
    try:
        link.symlink_to(tmp_path / "secret.txt")
    except OSError:
        pytest.skip("ссылки здесь создать нельзя")
    assert "пароль" not in view.read("link.txt") and "link.txt" not in view.files()


def test_view_does_not_read_binaries_and_cuts_long_files(view):
    (view.folder / "big.py").write_text("x = 1\n" * 20000, encoding="utf-8")
    text = view.read("big.py")
    assert len(text) < 14000 and "обрезано" in text
    assert "нет в проекте" in view.read("bin/main.exe")


def test_view_reads_from_a_line(view):
    assert view.read("main.py", line=3).splitlines()[1].startswith("3: print(")


def test_view_falls_back_to_the_plan_when_there_is_no_folder(tmp_path):
    plan = ProjectPlan.model_validate(PLAN)
    v = ProjectView(plan=plan, folder=tmp_path / "нет такой папки")
    assert 'float(input("Введите a: "))' in v.read("main.py") and v.files() == {"main.py": len(GOOD.encode())}


def test_fix_that_does_not_fit_the_rules_is_asked_again_with_the_reason(tmp_home, tmp_path):
    wrong = {"files": [{"path": "../main.py", "content": GOOD}]}  # путь вне проекта: такой план не годится
    pipe, fake, st = broken_lab(tmp_home, tmp_path, [FIX_IT, wrong, REPAIRED], shooter=ScriptShooter(True))
    st = pipe.chat(st, "исправь")
    assert st.project.built and "Программу исправил" in st.chat[-1].text
    again = said(fake.calls[-1])
    assert "Прошлое исправление не принято" in again and "выводит за папку проекта" in again


# ---------- по замечаниям проверки ----------

def test_reply_does_not_claim_a_fix_that_was_not_applied(tmp_home, tmp_path):
    wrong = {"files": [{"path": "../main.py", "content": GOOD}]}  # оба ответа не годятся: правка не применена
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, FIX_IT, wrong, wrong], shooter=ScriptShooter(True, True))
    st = pipe.execute(st)
    versions = len(st.versions)
    st = pipe.chat(st, "исправь программу")
    reply = st.chat[-1].text
    assert "Программу исправил" not in reply and "Исправить не получилось" in reply
    assert len(st.versions) == versions  # откатывать нечего


def test_fix_that_breaks_a_working_program_is_rolled_back(tmp_home, tmp_path):
    broke = {"files": [{"path": "main.py", "content": BAD}]}
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, FIX_IT, broke, STILL, STILL, STILL],
                          shooter=ScriptShooter(True, True, True))
    st = pipe.execute(st)
    assert st.project.built
    st = pipe.chat(st, "исправь программу")
    assert st.project.built  # осталась прежняя, рабочая программа
    assert (hand_in_dir(tmp_path) / "main.py").read_text(encoding="utf-8") == GOOD
    assert "Программу исправил" not in st.chat[-1].text and "верну" in st.chat[-1].text.lower()
    assert figures(st)[0].image_path and st.report.sections[0].blocks[1].code.strip() == GOOD.strip()


def test_fix_can_be_reverted_with_its_old_screenshot(tmp_home, tmp_path):
    better = {"files": [{"path": "main.py", "content": GOOD.replace("Сумма:", "Итог:")}],
              "shots": [{"section": 1, "block": 3, "mode": "console", "input": "12\n5\n", "expect": "Итог:"}]}
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, FIX_IT, better], shooter=ScriptShooter(True, True))
    st = pipe.execute(st)
    old = figures(st)[0].image_path
    st = pipe.chat(st, "исправь подпись")
    assert figures(st)[0].image_path != old and (pipe.lab_dir(st) / old).exists()  # прежний снимок нужен версии
    st = pipe.revert(st, st.chat[-1].version)
    assert st.status != "failed" and figures(st)[0].image_path == old


def test_students_edit_seen_by_the_chat_rebuilds_the_program_and_retakes_pictures(tmp_home, tmp_path):
    answer = {"kind": "answer", "reply": "Да, вижу."}
    shooter = ScriptShooter(True, True)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, answer], shooter=shooter)
    st = pipe.execute(st)
    old = figures(st)[0].image_path
    (hand_in_dir(tmp_path) / "main.py").write_text(GOOD.replace("Сумма:", "Итог:"), encoding="utf-8")
    st = pipe.chat(st, "я поменял подпись в main.py, видишь?")
    assert len(shooter.calls) == 2 and figures(st)[0].image_path != old  # снимок — уже от его программы
    assert "Взял твои правки" in st.chat[-1].text


def test_view_cuts_a_very_long_line_instead_of_returning_nothing(view):
    (view.folder / "min.js").write_text("var a = 1;" * 5000, encoding="utf-8")  # одна строка в 50 тысяч знаков
    text = view.read("min.js")
    assert "1: var a = 1;" in text and len(text) < 3000 and "дальше — тем же запросом" not in text


def test_view_walks_the_folder_once_per_search(view, monkeypatch):
    walks = []
    real = type(view)._on_disk
    monkeypatch.setattr(type(view), "_on_disk", lambda self: walks.append(1) or real(self))
    assert "main.py:3:" in view.find("Сумма")
    assert len(walks) == 1
