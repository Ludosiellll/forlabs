"""Ответ модели на исправление проекта — в план. Модель экономит слова: переносит файл, не присылая его текст
заново, а о снимке говорит только, какую программу запускать. Ничего из прежнего при этом теряться не должно."""

from forlabs.pipeline import project as proj
from forlabs.runner.plan import ProjectPlan, validate

LIB = "using System;\n\nnamespace ClassLibrary1\n{\n    public class Calculator\n    {\n        public static int Summ(int a, int b) { return a + b; }\n    }\n}\n"
APP = ("using System;\nusing ClassLibrary1;\n\nnamespace CalculatorApp\n{\n    internal class Program\n    {\n"
       "        static void Main(string[] args)\n        {\n            Console.WriteLine(\"Сумма: \" + Calculator.Summ(2, 3));\n"
       "        }\n    }\n}\n")
TEST = APP.replace("CalculatorApp", "LibraryTestApp").replace("Сумма", "Проверка")
OLD = ProjectPlan.model_validate({
    "language": "csharp", "kind": "console", "name": "Библиотека калькулятора", "entry": "Program.cs",
    "files": [{"path": "Calculator.cs", "content": LIB}, {"path": "Program.cs", "content": APP},
              {"path": "TestProgram.cs", "content": TEST}],
    "listings": [{"section": 1, "block": 3, "file": "Calculator.cs"}, {"section": 1, "block": 5, "file": "Program.cs"},
                 {"section": 2, "block": 4, "file": "TestProgram.cs"}],
    "shots": [{"section": 1, "block": 6, "mode": "console", "input": "12\n5\n", "expect": "Сумма:"},
              {"section": 2, "block": 5, "mode": "console", "input": "2\n", "expect": "Проверка:"}]})
PROGRAMS = [{"name": "ClassLibrary1", "kind": "library"},
            {"name": "CalculatorApp", "kind": "console", "entry": "CalculatorApp/Program.cs", "uses": ["ClassLibrary1"]},
            {"name": "LibraryTestApp", "kind": "console", "entry": "LibraryTestApp/TestProgram.cs",
             "uses": ["ClassLibrary1"]}]
MOVES = [{"from": "Calculator.cs", "to": "ClassLibrary1/Calculator.cs"},
         {"from": "Program.cs", "to": "CalculatorApp/Program.cs"},
         {"from": "TestProgram.cs", "to": "LibraryTestApp/TestProgram.cs"}]
WHO = [{"section": 1, "block": 6, "program": "CalculatorApp"}, {"section": 2, "block": 5, "program": "LibraryTestApp"}]


def apply(scenarios=True, **answer):
    return proj._apply_fix(OLD, proj.FixOut.model_validate(answer), scenarios=scenarios)


def paths(plan) -> set[str]:
    return {f.path for f in plan.files}


def test_files_are_moved_without_sending_their_text_again():
    fixed = apply(programs=PROGRAMS, move=MOVES, shots=WHO)
    assert validate(fixed) == []
    assert paths(fixed) == {"ClassLibrary1/Calculator.cs", "CalculatorApp/Program.cs", "LibraryTestApp/TestProgram.cs"}
    assert fixed.file("ClassLibrary1/Calculator.cs").content == LIB  # текст тот же
    assert [(r.section, r.block, r.file) for r in fixed.listings] == [
        (1, 3, "ClassLibrary1/Calculator.cs"), (1, 5, "CalculatorApp/Program.cs"),
        (2, 4, "LibraryTestApp/TestProgram.cs")]  # листинги отчёта — по новым местам


def test_shot_that_only_names_its_program_keeps_the_scenario():
    for scenarios in (True, False):
        fixed = apply(scenarios=scenarios, programs=PROGRAMS, move=MOVES, shots=WHO)
        first, second = fixed.shots
        assert (first.mode, first.input, first.expect, first.program) == ("console", "12\n5\n", "Сумма:", "CalculatorApp")
        assert (second.mode, second.input, second.expect, second.program) == ("console", "2\n", "Проверка:",
                                                                               "LibraryTestApp")


def test_full_scenario_replaces_the_old_one_only_after_a_shot():
    new = [{"section": 1, "block": 6, "mode": "console", "input": "7\n8\n", "expect": "Сумма:", "program": "CalculatorApp"}]
    assert apply(scenarios=True, programs=PROGRAMS, move=MOVES, shots=new).shots[0].input == "7\n8\n"
    assert apply(scenarios=False, programs=PROGRAMS, move=MOVES, shots=new).shots[0].input == "12\n5\n"


def test_file_left_in_the_root_goes_to_the_program_named_as_its_namespace():
    # модель перенесла только файлы с Main, а библиотечный оставила в корне: его место видно по namespace
    fixed = apply(programs=PROGRAMS, move=MOVES[1:], shots=WHO)
    assert validate(fixed) == [] and "ClassLibrary1/Calculator.cs" in paths(fixed) and "Calculator.cs" not in paths(fixed)
    assert fixed.listings[0].file == "ClassLibrary1/Calculator.cs"


def test_listing_that_names_a_new_place_moves_the_file_there():
    listings = [{"section": 1, "block": 3, "file": "ClassLibrary1/Calculator.cs"}]
    other = LIB.replace("namespace ClassLibrary1", "namespace Tools")  # по namespace место не угадать
    old = OLD.model_copy(update={"files": [OLD.files[0].model_copy(update={"content": other}), *OLD.files[1:]]})
    fixed = proj._apply_fix(old, proj.FixOut.model_validate({"programs": PROGRAMS, "move": MOVES[1:], "shots": WHO,
                                                             "listings": listings}), scenarios=True)
    assert validate(fixed) == [] and fixed.file("ClassLibrary1/Calculator.cs").content == other


def test_moved_file_may_come_with_new_text():
    better = LIB.replace("a + b", "checked(a + b)")
    fixed = apply(programs=PROGRAMS, move=MOVES, shots=WHO,
                  files=[{"path": "ClassLibrary1/Calculator.cs", "content": better}])
    assert fixed.file("ClassLibrary1/Calculator.cs").content == better and "Calculator.cs" not in paths(fixed)


def test_move_cannot_point_outside_the_project_or_overwrite_build_files():
    fixed = apply(move=[{"from": "Calculator.cs", "to": "../Calculator.cs"},
                        {"from": "Program.cs", "to": "Program.csproj"}])
    assert paths(fixed) == paths(OLD)  # такие переносы не выполняются


def test_plain_fix_still_works():
    fixed = apply(files=[{"path": "Calculator.cs", "content": LIB + "// правка\n"}])
    assert fixed.file("Calculator.cs").content.endswith("// правка\n") and fixed.programs == []
    assert [s.input for s in fixed.shots] == ["12\n5\n", "2\n"]
