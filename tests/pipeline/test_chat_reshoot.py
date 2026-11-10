"""Чат правок и снимки программы: чат знает, почему рисунок не снят, по просьбе снимает заново, а после правки кода
пересобирает программу и переснимает сам (в папке сдачи — тот же код, что в отчёте)."""

from test_project_stage import GOOD, PLAN, figures, make
from test_shot_repair import ScriptShooter, hand_in

from forlabs.pipeline import project as proj
from forlabs.runner.shots import ShotResult

NO_WINDOW = ShotResult(False, reason="окно консоли не появилось")
ASK_RESHOOT = {"kind": "answer", "reply": "Попробую снять заново.", "reshoot": True}
NEW_CODE = GOOD.replace("Сумма:", "Итог:").rstrip("\n")
EDIT = {"kind": "edit", "reply": "Поменяю подпись результата.",
        "tasks": [{"n": 1, "instruction": "в программе подпись результата — «Итог:»"}]}
REVISE = {"blocks": [{"keep": "b1"},
                     {"type": "listing", "topic": "Исходный код программы", "language": "python", "code": NEW_CODE},
                     {"keep": "b3", "retake": True}]}
PLAN2 = {**PLAN, "files": [{"path": "main.py", "content": NEW_CODE + "\n"}],
         "shots": [{"section": 1, "block": 3, "mode": "console", "input": "12\n5\n", "expect": "Итог:"},
                   PLAN["shots"][1]]}


def test_chat_retakes_screenshots_when_asked(tmp_home, tmp_path):
    shooter = ScriptShooter(NO_WINDOW, True)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, ASK_RESHOOT], shooter=shooter)
    st = pipe.execute(st)
    assert not figures(st)[0].image_path
    st = pipe.chat(st, "попробуй заново сделать скрины сам")
    assert figures(st)[0].image_path and len(shooter.calls) == 2
    reply = st.chat[-1].text
    assert "Снял сам" in reply and "Результат работы программы" in reply
    assert st.status in ("rendered", "needs_manual") and st.docx_path  # документ пересобран с новым рисунком


def test_chat_sees_why_the_picture_was_not_taken(tmp_home, tmp_path):
    answer = {"kind": "answer", "reply": "Окно консоли не появилось, поэтому снимка нет."}
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, answer], shooter=ScriptShooter(NO_WINDOW))
    st = pipe.execute(st)
    st = pipe.chat(st, "в чем была проблема со скриншотом?")
    asked = "\n".join(m["content"] for m in fake.calls[-1])
    assert "окно консоли не появилось" in asked  # причина — в описании рисунка
    assert "reshoot" in asked  # и модель знает, что ForLabs может снять сам


def test_chat_says_what_still_failed(tmp_home, tmp_path):
    shooter = ScriptShooter(NO_WINDOW, NO_WINDOW)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, ASK_RESHOOT], shooter=shooter)
    st = pipe.chat(pipe.execute(st), "сними скриншоты сам ещё раз")
    assert not figures(st)[0].image_path
    assert "Не получилось" in st.chat[-1].text and "окно консоли не появилось" in st.chat[-1].text


def test_code_edit_in_chat_rebuilds_program_and_retakes_pictures(tmp_home, tmp_path):
    shooter = ScriptShooter(True, True)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, EDIT, REVISE, PLAN2], shooter=shooter)
    st = pipe.execute(st)
    old = figures(st)[0].image_path
    st = pipe.chat(st, "преподаватель сказал подписать результат словом Итог")
    fig = figures(st)[0]
    assert fig.image_path and fig.image_path != old  # снимок уже от новой программы
    assert hand_in(tmp_path) == NEW_CODE + "\n"  # в папке сдачи — код из отчёта
    assert shooter.calls[1][1].expect == "Итог:"
    assert "Пересними" not in st.chat[-1].text and "Снял сам" in st.chat[-1].text


def test_rebuild_button_after_code_edit_plans_program_again(tmp_home, tmp_path):
    # правка кода без пересборки (этап «Проект» был выключен), затем «Пересобрать»: прежний план вернул бы старый код
    shooter = ScriptShooter(True, True)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, EDIT, REVISE, PLAN2], shooter=shooter)
    st = pipe.execute(st)
    pipe.projects = False
    st = pipe.chat(st, "подпиши результат словом Итог")
    assert proj.stale(st) and hand_in(tmp_path) == GOOD
    pipe.projects = True
    st = pipe.retry_project(st)
    assert st.report.sections[0].blocks[1].code == NEW_CODE  # код отчёта не откатился к прежнему
    assert hand_in(tmp_path) == NEW_CODE + "\n" and not proj.stale(st)


def test_text_only_edit_does_not_touch_the_program(tmp_home, tmp_path):
    edit = {"kind": "edit", "reply": "Сокращу абзац.", "tasks": [{"n": 1, "instruction": "первый абзац короче"}]}
    revise = {"blocks": [{"type": "paragraph", "text": "Программа складывает два числа."}, {"keep": "b2"},
                         {"keep": "b3"}]}
    shooter = ScriptShooter(True)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, edit, revise], shooter=shooter)
    st = pipe.execute(st)
    calls = len(fake.calls)
    st = pipe.chat(st, "первый абзац короче")
    assert len(fake.calls) == calls + 2 and len(shooter.calls) == 1  # ни нового плана, ни пересъёмки


# --- находки ревью

TWO = {**PLAN, "shots": [PLAN["shots"][0],
                         {"section": 2, "block": 2, "mode": "console", "input": "1\n2\n", "expect": "Сумма:"}]}
REVISE_KEEP = {"blocks": [{"keep": "b1"},
                          {"type": "listing", "topic": "Исходный код программы", "language": "python", "code": NEW_CODE},
                          {"keep": "b3"}]}
PLAN2_TWO = {**PLAN2, "shots": [PLAN2["shots"][0], TWO["shots"][1]]}


def test_code_edit_in_a_lab_made_by_an_older_version_plans_the_program_again(tmp_home, tmp_path):
    shooter = ScriptShooter(True, True)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, EDIT, REVISE, PLAN2], shooter=shooter)
    st = pipe.execute(st)
    st.project.report_sha = ""  # лаба сделана до этой версии: отпечатка отчёта в ней нет
    st = pipe._save(st)
    st = pipe.chat(st, "подпиши результат словом Итог")
    assert st.report.sections[0].blocks[1].code == NEW_CODE  # правка кода не откатилась к прежнему плану
    assert hand_in(tmp_path) == NEW_CODE + "\n"


def test_rebuild_in_an_older_lab_after_code_edit_plans_again(tmp_home, tmp_path):
    shooter = ScriptShooter(True, True)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, EDIT, REVISE, PLAN2], shooter=shooter)
    st = pipe.execute(st)
    st.project.report_sha = ""
    st = pipe._save(st)
    pipe.projects = False
    st = pipe.chat(st, "подпиши результат словом Итог")
    pipe.projects = True
    st = pipe.retry_project(st)
    assert st.report.sections[0].blocks[1].code == NEW_CODE and hand_in(tmp_path) == NEW_CODE + "\n"


def test_code_edit_rebuilds_program_even_when_no_picture_is_missing(tmp_home, tmp_path):
    shooter = ScriptShooter(True, True)
    pipe, fake, st = make(tmp_home, tmp_path, [TWO, EDIT, REVISE_KEEP, PLAN2_TWO], shooter=shooter)
    st = pipe.execute(st)
    assert all(f.image_path for f in figures(st))
    st = pipe.chat(st, "подпиши результат словом Итог")
    assert hand_in(tmp_path) == NEW_CODE + "\n" and not proj.stale(st)  # в папке сдачи — новый код
    assert "Программу пересобрал" in st.chat[-1].text


def test_new_plan_keeps_the_project_folder(tmp_home, tmp_path):
    renamed = {**PLAN2, "name": "Сумма чисел"}
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, EDIT, REVISE, renamed], shooter=ScriptShooter(True, True))
    st = pipe.chat(pipe.execute(st), "подпиши результат словом Итог")
    folders = sorted(p.name for p in (tmp_path / "сдача" / "ПССИП" / "ЛР7").iterdir() if p.is_dir())
    assert folders == ["Проект Сумма"] and hand_in(tmp_path) == NEW_CODE + "\n"  # одна папка, с новым кодом


def test_asking_to_reshoot_retakes_pictures_forlabs_took(tmp_home, tmp_path):
    shooter = ScriptShooter(True, True, True, True)
    pipe, fake, st = make(tmp_home, tmp_path, [TWO, ASK_RESHOOT], shooter=shooter)
    st = pipe.execute(st)
    old = [f.image_path for f in figures(st)]
    st = pipe.chat(st, "переснимай скриншоты")
    new = [f.image_path for f in figures(st)]
    assert len(shooter.calls) == 4 and all(new) and not set(new) & set(old)  # оба рисунка сняты заново
    assert not any((pipe.lab_dir(st) / o).exists() for o in old)  # прежние снимки убраны
    assert "Снял сам" in st.chat[-1].text


def test_reshoot_that_fails_keeps_the_picture_that_was_there(tmp_home, tmp_path):
    shooter = ScriptShooter(True, True, ShotResult(False, reason="окно консоли не появилось"), True)
    pipe, fake, st = make(tmp_home, tmp_path, [TWO, ASK_RESHOOT], shooter=shooter)
    st = pipe.execute(st)
    old = figures(st)[0].image_path
    st = pipe.chat(st, "переснимай скриншоты")
    first, second = figures(st)
    assert first.image_path == old and (pipe.lab_dir(st) / old).exists()  # не вышло — остался прежний
    assert second.image_path and "Не переснял" in st.chat[-1].text


def test_pictures_the_student_attached_are_never_retaken(tmp_home, tmp_path):
    from test_run import png_bytes

    shooter = ScriptShooter(ShotResult(False, reason="окно консоли не появилось"), True, True)
    pipe, fake, st = make(tmp_home, tmp_path, [TWO, ASK_RESHOOT], shooter=shooter)
    st = pipe.execute(st)
    st = pipe.attach_screenshot(st, section=1, block=3, image=png_bytes())  # первый рисунок вставил студент
    mine = figures(st)[0].image_path
    st = pipe.chat(st, "переснимай скриншоты")
    assert figures(st)[0].image_path == mine and len(shooter.calls) == 3  # переснят только второй



def test_new_plan_after_a_code_edit_is_made_from_the_previous_project(tmp_home, tmp_path):
    # раньше проект составлялся с нуля: имена файлов и сценарии «плыли», и прежние снимки расходились с кодом
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, EDIT, REVISE, PLAN2])
    st = pipe.execute(st)
    st = pipe.chat(st, "подпись результата должна быть «Итог:»")
    asked = "\n".join(m["content"] for m in fake.calls[-1] if isinstance(m["content"], str))
    assert "Прежний проект" in asked and "main.py" in asked
    assert "Сумма:" in asked  # прежний код файла — чтобы менять только то, чего коснулась правка
    assert '"input": "12\\n5\\n"' in asked or "12\\n5\\n" in asked  # и прежние сценарии снимков


def test_first_plan_has_no_previous_project(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    st = pipe.execute(st)
    asked = "\n".join(m["content"] for m in fake.calls[4] if isinstance(m["content"], str))
    assert "Прежний проект" not in asked
