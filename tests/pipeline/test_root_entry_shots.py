"""Несколько программ одной папкой (классы Java с main в корне): снимок берёт программу по листингу над рисунком."""

from forlabs.pipeline.project import _assign_programs, _quiet_console
from forlabs.runner.plan import ProjectPlan


def java(cls, body):
    return (f"public class {cls} {{\n    public static void main(String[] args) {{\n        {body}\n    }}\n}}\n")


FILES = [("EvenOddChecker.java", java("EvenOddChecker",
                                      "int n = new java.util.Scanner(System.in).nextInt();")),
         ("MultiplesOfFive.java", java("MultiplesOfFive", 'System.out.println("5 10 15");')),
         ("LeapYearCentury.java", java("LeapYearCentury",
                                       "int y = new java.util.Scanner(System.in).nextInt();"))]


def plan(shots, listings=None):
    return ProjectPlan.model_validate({
        "language": "java", "kind": "console", "name": "Lab3", "entry": "EvenOddChecker.java",
        "files": [{"path": p, "content": c} for p, c in FILES],
        "listings": listings if listings is not None else [
            {"section": 2, "block": 1, "file": "EvenOddChecker.java"},
            {"section": 2, "block": 3, "file": "MultiplesOfFive.java"},
            {"section": 3, "block": 1, "file": "LeapYearCentury.java"}],
        "shots": shots})


def shot(section, block, program="", **kw):
    return {"section": section, "block": block, "mode": "console", "program": program, **kw}


def test_shot_runs_the_class_whose_listing_stands_above_the_figure():
    out = _assign_programs(plan([shot(2, 2), shot(2, 4), shot(3, 2)]))
    assert [s.program for s in out.shots] == ["EvenOddChecker.java", "MultiplesOfFive.java", "LeapYearCentury.java"]


def test_named_class_is_kept_and_written_as_its_file():
    out = _assign_programs(plan([shot(2, 2, "LeapYearCentury")]))
    assert out.shots[0].program == "LeapYearCentury.java"


def test_without_listings_nearby_the_main_file_runs():
    out = _assign_programs(plan([shot(5, 1)], listings=[]))
    assert out.shots[0].program == ""


def test_typed_input_is_kept_only_for_the_class_that_reads_the_keyboard():
    # раньше смотрели на весь проект: «кто-то читает клавиатуру» — и ввод оставался у программы без ввода
    p = _assign_programs(plan([shot(2, 2, input="7\n"), shot(2, 4, input="7\n")]))
    out = _quiet_console(p)
    assert out.shots[0].input == "7\n" and out.shots[1].input == ""
