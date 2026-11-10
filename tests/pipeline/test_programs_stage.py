"""Несколько программ в лабе на этапе «Проект»: каждый снимок запускает свою программу, старый план с двумя точками
входа делится сам, исправление сборки может разнести файлы по программам, ошибки сборки остаются студенту."""

from pathlib import Path

import pytest
from test_run import ANS, FIXTURE, RAW, setup

from forlabs.pipeline import project as proj
from forlabs.runner import languages
from forlabs.runner.plan import ProjectPlan
from forlabs.runner.shots import FakeShooter

TOOLS = languages.detect()
ONE = "a = int(input())\nprint(\"Квадрат:\", a * a)\n"
TWO = "b = int(input())\nprint(\"Куб:\", b * b * b)\n"


def section(topic, code, shot, lang="python"):
    return {"blocks": [{"type": "paragraph", "text": "Программа считает значение."},
                       {"type": "listing", "topic": topic, "language": lang, "code": code.rstrip()},
                       {"type": "figure", "kind": "screenshot", "topic": shot, "instruction": "Запусти программу"}]}


SECS = [section("Исходный код первой программы", ONE, "Результат первой программы"),
        section("Исходный код второй программы", TWO, "Результат второй программы")]
PROGRAMS = [{"name": "Задание1", "kind": "console", "entry": "Задание1/main.py"},
            {"name": "Задание2", "kind": "console", "entry": "Задание2/main.py"}]
PLAN = {"language": "python", "kind": "console", "name": "Квадрат и куб", "entry": "Задание1/main.py",
        "programs": PROGRAMS,
        "files": [{"path": "Задание1/main.py", "content": ONE}, {"path": "Задание2/main.py", "content": TWO}],
        "listings": [{"section": 1, "block": 2, "file": "Задание1/main.py"},
                     {"section": 2, "block": 2, "file": "Задание2/main.py"}],
        "shots": [{"section": 1, "block": 3, "mode": "console", "program": "Задание1", "input": "4\n",
                   "expect": "Квадрат:"},
                  {"section": 2, "block": 3, "mode": "console", "program": "Задание2", "input": "2\n",
                   "expect": "Куб:"}]}


def make(tmp_home, tmp_path, responses, secs=SECS, shooter=None):
    pipe, fake = setup(tmp_home, tmp_path, [{**RAW, "main_tasks": ["Первая программа", "Вторая программа"]}, *secs,
                                            ANS, *responses])
    pipe.projects = True
    pipe.shooter = shooter if shooter is not None else FakeShooter()
    st = pipe.create_lab("ivanov", "psip", [(FIXTURE.name, FIXTURE.read_bytes())], number="7")
    return pipe, fake, pipe.confirm(pipe.parse(st, date="24.09.2026"), None)


def figures(st):
    return [b for s in st.report.sections for b in s.blocks if b.type == "figure"]


def hand_in(tmp_path) -> Path:
    return tmp_path / "сдача" / "ПССИП" / "ЛР7" / "Проект Квадрат_и_куб"


def test_each_shot_runs_its_own_program_from_its_own_folder(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    st = pipe.execute(st)
    assert st.project.built and all(f.image_path for f in figures(st))
    (t1, s1), (t2, s2) = pipe.shooter.calls
    assert Path(t1.cwd).name == "Задание1" and Path(t2.cwd).name == "Задание2"
    assert s1.input == "4\n" and s2.input == "2\n"
    assert hand_in(tmp_path).joinpath("Задание1", "main.py").read_text(encoding="utf-8") == ONE
    assert hand_in(tmp_path).joinpath("Задание2", "main.py").read_text(encoding="utf-8") == TWO
    assert st.project.programs == ["Задание1", "Задание2"]


def test_model_is_told_how_to_describe_several_programs(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    pipe.execute(st)
    system = fake.calls[4][0]["content"]
    assert "programs" in system and "library" in system and "uses" in system and '"program"' in system


def test_shot_without_program_goes_to_the_program_of_its_listing(tmp_home, tmp_path):
    plan = PLAN | {"shots": [{k: v for k, v in s.items() if k != "program"} for s in PLAN["shots"]]}
    pipe, fake, st = make(tmp_home, tmp_path, [plan])
    st = pipe.execute(st)
    (t1, _), (t2, _) = pipe.shooter.calls
    assert Path(t1.cwd).name == "Задание1" and Path(t2.cwd).name == "Задание2"


def test_teacher_data_file_is_put_next_to_every_program(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    st, problems = pipe.add_materials(st, [("числа.txt", "1 2 3\n".encode())])
    assert not problems
    st = pipe.execute(st)
    for name in ("Задание1", "Задание2"):
        assert hand_in(tmp_path).joinpath(name, "числа.txt").read_text(encoding="utf-8").strip() == "1 2 3"


def test_build_fix_can_split_one_project_into_programs(tmp_home, tmp_path):
    broken = {"language": "python", "kind": "console", "name": "Квадрат и куб", "entry": "main.py",
              "files": [{"path": "main.py", "content": "print(\"Квадрат:\"\n"}, {"path": "second.py", "content": TWO}],
              "listings": PLAN["listings"][:0], "shots": [s | {"program": ""} for s in PLAN["shots"]]}
    fix = {"programs": PROGRAMS, "remove": ["main.py", "second.py"], "files": PLAN["files"],
           "shots": PLAN["shots"]}
    pipe, fake, st = make(tmp_home, tmp_path, [broken, fix])
    st = pipe.execute(st)
    assert st.project.built and st.project.fixes == 1 and st.project.programs == ["Задание1", "Задание2"]
    assert not hand_in(tmp_path).joinpath("main.py").exists() and not hand_in(tmp_path).joinpath("second.py").exists()
    assert Path(pipe.shooter.calls[1][0].cwd).name == "Задание2"
    asked = fake.calls[5][0]["content"]
    assert "programs" in asked and "remove" in asked  # модели сказано, что так можно


def test_build_errors_stay_with_the_project(tmp_home, tmp_path):
    bad = "print(\"Квадрат:\"\n"
    broken = PLAN | {"files": [{"path": "Задание1/main.py", "content": bad}, PLAN["files"][1]]}
    still = {"files": [{"path": "Задание1/main.py", "content": bad}]}
    pipe, fake, st = make(tmp_home, tmp_path, [broken, still, still, still])
    st = pipe.execute(st)
    assert not st.project.built
    assert "SyntaxError" in st.project.build_log and "Задание1" in st.project.build_log
    assert len(st.project.build_log) <= proj.BUILD_LOG_LIMIT


def test_build_log_is_cleared_when_the_project_builds_again(tmp_home, tmp_path):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN])
    st = pipe.execute(st)
    assert st.project.built and st.project.build_log == ""


CPP = '#include <iostream>\nint main() {{\n    std::cout << "{0}" << std::endl;\n    return 0;\n}}\n'


@pytest.mark.skipif(not any(k in TOOLS for k in ("gpp", "clang", "msvc")), reason="нет компилятора C++")
def test_old_plan_with_two_entry_points_is_split_and_built(tmp_home, tmp_path):
    secs = [section("Исходный код первой программы", CPP.format("one"), "Результат первой программы", "cpp"),
            section("Исходный код второй программы", CPP.format("two"), "Результат второй программы", "cpp")]
    old = {"language": "cpp", "kind": "console", "name": "Квадрат и куб", "entry": "first/main.cpp",
           "files": [{"path": "first/main.cpp", "content": CPP.format("one")},
                     {"path": "second/main.cpp", "content": CPP.format("two")}],
           "listings": [{"section": 1, "block": 2, "file": "first/main.cpp"},
                        {"section": 2, "block": 2, "file": "second/main.cpp"}],
           "shots": [{"section": 1, "block": 3, "mode": "console", "expect": "one"},
                     {"section": 2, "block": 3, "mode": "console", "expect": "two"}]}
    pipe, fake, st = make(tmp_home, tmp_path, [old], secs=secs)
    st = pipe.execute(st)
    assert st.project.built and st.project.fixes == 0  # модель не понадобилась
    assert st.project.programs == ["first", "second"]
    (t1, _), (t2, _) = pipe.shooter.calls
    assert t1.cmd != t2.cmd and Path(t2.cmd[0]).parent.parent.name == "second"
    saved = ProjectPlan.model_validate_json((pipe.lab_dir(st) / "project" / "plan.json").read_text(encoding="utf-8"))
    assert [p.name for p in saved.programs] == ["first", "second"]
