"""Сценарий снимка не прошёл (в программе нет пункта списка, не открыта вторая форма, вывод не тот): ForLabs
отдаёт модели причину и то, что на самом деле было в окне, получает исправленные файлы или шаги и снимает заново."""

from pathlib import Path

from PIL import Image
from test_project_stage import GOOD, PLAN, figures, make

from forlabs.pipeline import project as proj
from forlabs.runner.shots import ShotResult

HAND_IN = ("сдача", "ПССИП", "ЛР7", "Проект Сумма", "main.py")
EMPTY_LIST = "в списке «cmbOperation» нет пункта «Сложение» (список пуст)"
WINDOW = "Окно «Калькулятор»:\n  [поле] txtFirst = «12»\n  [список] cmbOperation — пунктов нет"
BETTER = GOOD.replace("Сумма:", "Сумма чисел:")


class ScriptShooter:
    """Снимки по сценарию теста: каждому вызову — свой ответ (True — снимок есть, иначе ShotResult с причиной)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.container = True

    def shoot(self, target, shot, out: Path) -> ShotResult:
        self.calls.append((target, shot))
        a = self.answers.pop(0) if self.answers else True
        if a is True:
            out.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (320, 200), (240, 240, 240)).save(out)
            return ShotResult(True, image=out)
        return a


def ui_fail(reason=EMPTY_LIST, ui=WINDOW):
    return ShotResult(False, reason=reason, kind="ui", ui=ui)


def hand_in(tmp_path) -> str:
    return tmp_path.joinpath(*HAND_IN).read_text(encoding="utf-8")


def test_failed_scenario_goes_back_to_model_with_what_was_in_the_window(tmp_home, tmp_path):
    fix = {"files": [{"path": "main.py", "content": BETTER}]}
    shooter = ScriptShooter(ui_fail(), True)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, fix], shooter=shooter)
    st = pipe.execute(st)
    fig = figures(st)[0]
    assert fig.image_path and not fig.auto_note  # со второго раза снял сам
    assert st.project.shot_fixes == 1
    asked = "\n".join(m["content"] for m in fake.calls[5])
    assert EMPTY_LIST in asked and "пунктов нет" in asked  # причина и настоящее окно программы
    assert "Результат работы программы" in asked and "main.py" in asked  # какой рисунок и файлы проекта
    assert hand_in(tmp_path) == BETTER  # студенту — уже исправленная программа


def test_fix_may_change_only_the_scenario(tmp_home, tmp_path):
    fix = {"shots": [{"section": 1, "block": 3, "mode": "console", "input": "7\n5\n", "expect": "Сумма: 12.0"}]}
    shooter = ScriptShooter(ShotResult(False, reason="программа вывела не то, что ожидалось («Сумма: 17»)",
                                       kind="expect", seen="Введите a: 12\nВведите b: 5\nСумма: 17.0"), True)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, fix], shooter=shooter)
    st = pipe.execute(st)
    assert figures(st)[0].image_path
    assert shooter.calls[1][1].input == "7\n5\n"
    assert shooter.calls[1][1].expect == "Сумма:"  # число, которое модель посчитала сама, в проверку не идёт
    assert "Сумма: 17.0" in "\n".join(m["content"] for m in fake.calls[5])  # модель видела экран консоли
    assert hand_in(tmp_path) == GOOD  # программа не менялась


def test_after_two_fixes_the_figure_goes_to_the_student_with_the_reason(tmp_home, tmp_path):
    fix = {"files": [{"path": "main.py", "content": BETTER}]}
    fix2 = {"files": [{"path": "main.py", "content": BETTER + "\n"}]}
    shooter = ScriptShooter(ui_fail(), ui_fail(), ui_fail())
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, fix, fix2], shooter=shooter)
    st = pipe.execute(st)
    fig = figures(st)[0]
    assert not fig.image_path and "нет пункта «Сложение»" in fig.auto_note
    assert st.project.shot_fixes == proj.MAX_SHOT_FIXES == 2 and len(shooter.calls) == 3
    assert st.status in ("rendered", "needs_manual")  # лаба не сломалась


def test_launch_problems_are_not_sent_to_the_model(tmp_home, tmp_path):
    shooter = ScriptShooter(ShotResult(False, reason="окно программы не появилось"))
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN], shooter=shooter)
    st = pipe.execute(st)
    assert len(fake.calls) == 5 and st.project.shot_fixes == 0  # модель тут не поможет — запроса нет
    assert "окно программы не появилось" in figures(st)[0].auto_note


def test_fix_that_does_not_build_is_dropped(tmp_home, tmp_path):
    fix = {"files": [{"path": "main.py", "content": "print(\"Сумма:\"\n"}]}
    shooter = ScriptShooter(ui_fail())
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, fix], shooter=shooter)
    st = pipe.execute(st)
    assert st.project.built and hand_in(tmp_path) == GOOD  # осталась прежняя, рабочая программа
    assert len(shooter.calls) == 1 and "нет пункта" in figures(st)[0].auto_note


def test_fix_with_dangerous_code_is_dropped(tmp_home, tmp_path):
    fix = {"files": [{"path": "main.py", "content": "import os\nos.system(\"del /q C:\\\\*\")\n" + GOOD}]}
    shooter = ScriptShooter(ui_fail())
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, fix], shooter=shooter)
    st = pipe.execute(st)
    assert not st.project.blocked and hand_in(tmp_path) == GOOD
    assert len(shooter.calls) == 1


def test_no_connection_during_fix_leaves_the_reason(tmp_home, tmp_path):
    from forlabs.core.llm import LLMError

    shooter = ScriptShooter(ui_fail())
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, LLMError("network", "нет связи")], shooter=shooter)
    st = pipe.execute(st)
    assert st.project.built and "нет пункта" in figures(st)[0].auto_note
    assert st.status in ("rendered", "needs_manual")


TWO = {**PLAN, "shots": [PLAN["shots"][0],
                         {"section": 2, "block": 2, "mode": "console", "input": "1\n2\n", "expect": "Сумма:"}]}


def test_changed_program_is_shot_again_and_old_picture_stays_if_retake_fails(tmp_home, tmp_path):
    fix = {"files": [{"path": "main.py", "content": BETTER}]}
    # круг 1: первый рисунок снят, второй — нет; круг 2 (программа изменилась): первый не вышел, второй снят
    shooter = ScriptShooter(True, ui_fail(), ShotResult(False, reason="окно консоли не появилось"), True)
    pipe, fake, st = make(tmp_home, tmp_path, [TWO, fix], shooter=shooter)
    st = pipe.execute(st)
    first, second = figures(st)
    assert len(shooter.calls) == 4
    assert first.image_path and (pipe.lab_dir(st) / first.image_path).exists()  # прежний снимок не потерян
    assert second.image_path and not second.auto_note


def test_scenario_only_fix_does_not_retake_good_pictures(tmp_home, tmp_path):
    fix = {"shots": [{"section": 2, "block": 2, "mode": "console", "input": "3\n4\n", "expect": "Сумма:"}]}
    shooter = ScriptShooter(True, ui_fail(), True)
    pipe, fake, st = make(tmp_home, tmp_path, [TWO, fix], shooter=shooter)
    st = pipe.execute(st)
    assert len(shooter.calls) == 3 and shooter.calls[2][1].input == "3\n4\n"
    assert all(f.image_path for f in figures(st))


def test_rebuild_button_repairs_and_retakes_what_is_missing(tmp_home, tmp_path):
    fix = {"files": [{"path": "main.py", "content": BETTER}]}
    shooter = ScriptShooter(ShotResult(False, reason="окно консоли не появилось"), ui_fail(), True)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, fix], shooter=shooter)
    st = pipe.execute(st)
    assert not figures(st)[0].image_path
    st = pipe.retry_project(st)  # «Снять ещё раз»: тот же план, а сценарий чинит модель
    assert figures(st)[0].image_path and hand_in(tmp_path) == BETTER


def test_fixed_plan_is_saved_for_the_next_rebuild(tmp_home, tmp_path):
    fix = {"files": [{"path": "main.py", "content": BETTER}]}
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, fix], shooter=ScriptShooter(ui_fail(), True))
    st = pipe.execute(st)
    assert proj.load_plan(pipe, st).file("main.py").content == BETTER


# --- находки ревью


def test_report_listing_follows_the_repaired_file_to_its_last_line(tmp_home, tmp_path):
    fix = {"files": [{"path": "main.py", "content": BETTER}]}  # модель поменяла последнюю строку и не вернула листинг
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, fix], shooter=ScriptShooter(ui_fail(), True))
    st = pipe.execute(st)
    assert st.report.sections[0].blocks[1].code == BETTER.rstrip("\n")  # в отчёте — весь код, а не обрезок


def test_scenario_only_fix_is_checked_before_anything_is_typed(tmp_home, tmp_path, monkeypatch):
    real = proj._blocked
    monkeypatch.setattr(proj, "_blocked", lambda pipe, plan: (
        (["сценарий запускает команду системы"], False) if any("system" in s.input for s in plan.shots)
        else real(pipe, plan)))
    fix = {"shots": [{"section": 1, "block": 3, "mode": "console", "input": "system whoami\n", "expect": "Сумма:"}]}
    shooter = ScriptShooter(ui_fail(), True)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, fix], shooter=shooter)
    st = pipe.execute(st)
    assert len(shooter.calls) == 1  # опасный ввод не набирался
    assert proj.load_plan(pipe, st).shots[0].input == "12\n5\n" and not st.project.blocked


def test_project_info_survives_a_crash_inside_the_repair_round(tmp_home, tmp_path, monkeypatch):
    from forlabs.runner import languages

    real, calls = languages.build, []

    def build(work, plan, tools, *a, **k):
        calls.append(1)
        if len(calls) == 2:  # сборка исправленной программы падает не ошибкой сборки, а исключением
            raise OSError("диск переполнен")
        return real(work, plan, tools, *a, **k)

    monkeypatch.setattr(languages, "build", build)
    fix = {"files": [{"path": "main.py", "content": BETTER}]}
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, fix], shooter=ScriptShooter(ui_fail()))
    st = pipe.execute(st)
    assert st.project is not None and st.project.built  # программа есть, кнопка «Снять ещё раз» доступна
    assert "нет пункта" in figures(st)[0].auto_note and hand_in(tmp_path) == GOOD


def test_repair_on_retry_retakes_pictures_from_the_earlier_run_too(tmp_home, tmp_path):
    fix = {"files": [{"path": "main.py", "content": BETTER}]}
    # первый запуск: рисунок 1 снят, рисунок 2 — окно не появилось; «Снять ещё раз»: рисунок 1 переснят, сценарий 2
    # не прошёл, модель поменяла программу — значит, и рисунок 1 снимается ещё раз, уже с новой программой
    shooter = ScriptShooter(True, ShotResult(False, reason="окно консоли не появилось"), True, ui_fail(), True, True)
    pipe, fake, st = make(tmp_home, tmp_path, [TWO, fix], shooter=shooter)
    st = pipe.execute(st)
    old = figures(st)[0].image_path
    st = pipe.retry_project(st)
    first, second = figures(st)
    assert len(shooter.calls) == 6 and first.image_path != old and second.image_path
    assert hand_in(tmp_path) == BETTER
    assert not (pipe.lab_dir(st) / old).exists()  # прежний снимок убран, а не лежит рядом


def test_retake_works_when_plan_file_is_not_on_this_computer(tmp_home, tmp_path):
    shooter = ScriptShooter(ShotResult(False, reason="окно консоли не появилось"), True)
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, PLAN], shooter=shooter)
    st = pipe.execute(st)
    (pipe.lab_dir(st) / "project" / "plan.json").unlink()  # лабу открыли на другом компьютере
    st = pipe.retry_project(st)  # план составляется заново, а не «проекта ещё нет»
    assert figures(st)[0].image_path


def test_rebuild_button_retakes_pictures_forlabs_took(tmp_home, tmp_path):
    shooter = ScriptShooter(True, True, True, True)
    pipe, fake, st = make(tmp_home, tmp_path, [TWO], shooter=shooter)
    st = pipe.execute(st)
    old = [f.image_path for f in figures(st)]
    st = pipe.retry_project(st)  # «Пересобрать»: программа и снимки заново (снимки прошлой версии ForLabs — тоже)
    new = [f.image_path for f in figures(st)]
    assert len(shooter.calls) == 4 and all(new) and not set(new) & set(old)
