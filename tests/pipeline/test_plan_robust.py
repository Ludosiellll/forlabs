"""План проекта от модели неидеален: файл данных лежит мимо папок программ, номер блока у снимка сдвинут, для
рисунка нет сценария, план не проходит проверку. Из-за таких мелочей студент не должен остаться без программы и без
объяснения: ForLabs правит то, что можно поправить самому, переспрашивает модель и всегда говорит, почему снимка нет."""

from test_project_stage import GOOD, PLAN, figures, make
from test_shot_repair import ScriptShooter

from forlabs.pipeline import project as proj
from forlabs.runner.plan import ProjectPlan, validate

TWO = {"language": "python", "kind": "console", "name": "Два задания", "entry": "Задание1/main.py",
       "programs": [{"name": "Задание1", "kind": "console", "entry": "Задание1/main.py"},
                    {"name": "Задание2", "kind": "console", "entry": "Задание2/main.py"}],
       "files": [{"path": "Задание1/main.py", "content": GOOD}, {"path": "Задание2/main.py", "content": "print(2)\n"},
                 {"path": "рейсы.txt", "content": "Минск;Москва\n"}],
       "listings": [{"section": 1, "block": 2, "file": "Задание1/main.py"}], "shots": []}
NO_SECOND = {**PLAN, "shots": PLAN["shots"][:1]}
ADDED = {"shots": [{"section": 2, "block": 2, "mode": "console", "input": "3\n4\n", "expect": "Сумма:"}]}


def said(call) -> str:
    return "\n".join(m["content"] for m in call)


def notes(st) -> list[str]:
    return [f.auto_note or "" for f in figures(st)]


def test_data_file_left_in_the_root_goes_to_every_program():
    plan = proj._tidy(ProjectPlan.model_validate(TWO))
    assert validate(plan) == []
    paths = {f.path for f in plan.files}
    assert {"Задание1/рейсы.txt", "Задание2/рейсы.txt"} <= paths and "рейсы.txt" not in paths


def test_plan_that_fails_the_check_is_asked_again_with_the_reason(tmp_home, tmp_path):
    wrong = {**PLAN, "files": [{"path": "../main.py", "content": GOOD}], "entry": "../main.py",
             "listings": [{"section": 1, "block": 2, "file": "../main.py"}]}
    pipe, fake, st = make(tmp_home, tmp_path, [wrong, PLAN], shooter=ScriptShooter(True))
    st = pipe.execute(st)
    assert st.project.built and figures(st)[0].image_path
    again = said(fake.calls[5])
    assert "не принят" in again and "выводит за папку проекта" in again


def test_when_the_project_cannot_be_made_every_picture_says_why(tmp_home, tmp_path):
    wrong = {**PLAN, "files": [{"path": "../main.py", "content": GOOD}], "entry": "../main.py",
             "listings": [{"section": 1, "block": 2, "file": "../main.py"}]}
    pipe, fake, st = make(tmp_home, tmp_path, [wrong, wrong], shooter=ScriptShooter(True))
    st = pipe.execute(st)
    assert st.status in ("rendered", "needs_manual")  # лаба не сломалась
    assert all("программу собрать не получилось" in n.lower() for n in notes(st)), notes(st)
    assert st.project is not None and not st.project.built and "папку проекта" in st.project.note


def test_shot_with_a_shifted_block_number_goes_to_the_free_figure_of_its_task(tmp_home, tmp_path):
    shifted = {**PLAN, "shots": [{**PLAN["shots"][0], "block": 4}, PLAN["shots"][1]]}  # рисунок — блок 3
    shooter = ScriptShooter(True)
    pipe, fake, st = make(tmp_home, tmp_path, [shifted], shooter=shooter)
    st = pipe.execute(st)
    assert figures(st)[0].image_path and len(shooter.calls) == 1 and len(fake.calls) == 5  # модель не понадобилась


def test_figure_without_a_scenario_gets_one(tmp_home, tmp_path):
    shooter = ScriptShooter(True, True)
    pipe, fake, st = make(tmp_home, tmp_path, [NO_SECOND, ADDED], shooter=shooter)
    st = pipe.execute(st)
    assert all(f.image_path for f in figures(st)) and len(shooter.calls) == 2
    asked = said(fake.calls[5])
    assert "Окно IDLE с кодом программы" in asked and "main.py" in asked  # какой рисунок и что за программа
    assert "Результат работы программы" not in asked.split("Рисунки без сценария")[1].split("Файлы проекта")[0]


def test_figure_that_still_has_no_scenario_says_so(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [NO_SECOND, {"shots": []}], shooter=ScriptShooter(True))
    st = pipe.execute(st)
    assert figures(st)[0].image_path and not figures(st)[1].image_path
    assert "сценари" in notes(st)[1]  # причина записана, а не пусто


def test_try_again_heals_a_saved_plan_without_a_scenario(tmp_home, tmp_path):
    shooter = ScriptShooter(True, True, True)
    pipe, fake, st = make(tmp_home, tmp_path, [NO_SECOND, {"shots": []}, ADDED], shooter=shooter)
    st = pipe.execute(st)
    assert not figures(st)[1].image_path
    st = pipe.retry_project(st)  # «Повторить»: сценарий досоставлен, рисунок снят
    assert figures(st)[1].image_path
