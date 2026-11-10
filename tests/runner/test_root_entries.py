"""Несколько программ одной папкой без programs: три класса Java с main или три скрипта в корне. Каждый снимок
запускает свой файл — раньше все запускали первый, и на рисунках стояла чужая программа."""

import subprocess

import pytest

from forlabs.runner import languages as L
from forlabs.runner.plan import ProjectPlan, entry_files, entry_of, validate

TOOLS = L.detect()


def need(key):
    return pytest.mark.skipif(key not in TOOLS, reason=f"нет инструмента {key}")


def java(cls, body):
    return (f"import java.util.Scanner;\n\npublic class {cls} {{\n    public static void main(String[] args) {{\n"
            f"        {body}\n    }}\n}}\n")


EVEN = java("EvenOddChecker", 'System.out.println("Число чётное");')
FIVE = java("MultiplesOfFive", 'System.out.println("5 10 15");')
LEAP = java("LeapYearCentury", 'System.out.println("Век: 20");')
HELPER = "public class Helper {\n    static int twice(int x) {\n        return x * 2;\n    }\n}\n"


def java_plan(shots=None, files=None):
    files = files or [("EvenOddChecker.java", EVEN), ("MultiplesOfFive.java", FIVE), ("LeapYearCentury.java", LEAP),
                      ("Helper.java", HELPER)]
    return ProjectPlan.model_validate({
        "language": "java", "kind": "console", "name": "Lab3", "entry": "EvenOddChecker.java",
        "files": [{"path": p, "content": c} for p, c in files],
        "listings": [{"section": 2, "block": i + 1, "file": p} for i, (p, _) in enumerate(files[:3])],
        "shots": shots or []})


def shot(program="", block=5, **kw):
    return {"section": 2, "block": block, "mode": "console", "expect": "", "program": program, **kw}


def test_java_classes_with_main_are_the_entry_files_main_one_first():
    plan = java_plan(files=[("MultiplesOfFive.java", FIVE), ("Helper.java", HELPER), ("EvenOddChecker.java", EVEN)])
    assert entry_files(plan) == ["EvenOddChecker.java", "MultiplesOfFive.java"]


def test_program_is_found_by_class_name_file_name_or_path():
    plan = java_plan()
    assert entry_of(plan, "MultiplesOfFive") == "MultiplesOfFive.java"
    assert entry_of(plan, "multiplesoffive.java") == "MultiplesOfFive.java"
    assert entry_of(plan, "Helper") is None and entry_of(plan, "Nope") is None and entry_of(plan, "") is None


def test_shot_naming_a_program_that_is_not_there_is_rejected():
    problems = validate(java_plan([shot("Calculator")]))
    assert any("Calculator" in p and "MultiplesOfFive.java" in p for p in problems)


def test_shot_naming_its_class_passes():
    assert validate(java_plan([shot("MultiplesOfFive"), shot("LeapYearCentury", block=6), shot("", block=7)])) == []


def test_single_program_ignores_the_name_as_before():
    plan = java_plan([shot("Lab3")], files=[("EvenOddChecker.java", EVEN), ("Helper.java", HELPER)])
    assert validate(plan) == []


def test_python_scripts_that_nobody_imports_are_entries():
    plan = ProjectPlan.model_validate({
        "language": "python", "kind": "console", "name": "lab", "entry": "task1.py",
        "files": [{"path": "task1.py", "content": "from utils import twice\nprint(twice(2))\n"},
                  {"path": "task2.py", "content": "import utils\nprint(utils.twice(3))\n"},
                  {"path": "utils.py", "content": "def twice(x):\n    return x * 2\n"}],
        "listings": [], "shots": [shot("task2")]})
    assert entry_files(plan) == ["task1.py", "task2.py"] and validate(plan) == []


def test_run_spec_runs_the_named_class(tmp_path):
    tools = {"java": L.Toolchain("java", "java", "21")}
    plan = java_plan()
    out = tmp_path / "out"
    spec = L.run_spec_for(plan, L.BuildResult(True, artifact=out), tmp_path, tools, "MultiplesOfFive")
    assert spec.cmd[-1] == "MultiplesOfFive"
    assert L.run_spec_for(plan, L.BuildResult(True, artifact=out), tmp_path, tools, "").cmd[-1] == "EvenOddChecker"
    assert L.run_spec_for(plan, L.BuildResult(True, artifact=out), tmp_path, tools, "Nope").cmd[-1] == "EvenOddChecker"


def test_run_spec_window_class_among_console_ones_is_not_a_console(tmp_path):
    swing = ("import javax.swing.JFrame;\n\npublic class House extends JFrame {\n"
             "    public static void main(String[] args) {\n        new House().setVisible(true);\n    }\n}\n")
    plan = java_plan(files=[("EvenOddChecker.java", EVEN), ("House.java", swing)])
    tools = {"java": L.Toolchain("java", "java", "21")}
    result = L.BuildResult(True, artifact=tmp_path / "out")
    assert L.run_spec_for(plan, result, tmp_path, tools, "House").console is False
    assert L.run_spec_for(plan, result, tmp_path, tools, "EvenOddChecker").console is True


def test_run_spec_python_script_by_name(tmp_path):
    plan = ProjectPlan.model_validate({
        "language": "python", "kind": "console", "name": "lab", "entry": "task1.py",
        "files": [{"path": "task1.py", "content": "print(1)\n"}, {"path": "task2.py", "content": "print(2)\n"}],
        "listings": [], "shots": []})
    tools = {"python": L.Toolchain("python", "python", "3.12")}
    assert L.run_spec_for(plan, None, tmp_path, tools, "task2").cmd[-1] == "task2.py"


@need("java")
def test_each_java_class_really_prints_its_own_output(tmp_path):
    plan = java_plan()
    for f in plan.files:
        (tmp_path / f.path).write_text(f.content, encoding="utf-8")
    result = L.build(tmp_path, plan, TOOLS)
    assert result.ok, result.log
    seen = {}
    for name in ("EvenOddChecker", "MultiplesOfFive", "LeapYearCentury"):
        spec = L.run_spec_for(plan, result, tmp_path, TOOLS, name)
        seen[name] = subprocess.run(spec.cmd, cwd=spec.cwd, capture_output=True, text=True, timeout=60).stdout
    assert "чётное" in seen["EvenOddChecker"] and "5 10 15" in seen["MultiplesOfFive"]
    assert "Век: 20" in seen["LeapYearCentury"]
