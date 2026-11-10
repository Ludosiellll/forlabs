"""Несколько программ в одной лабе: библиотека и приложения, у каждого задания своя программа. Каждая лежит в своей
папке и собирается отдельно; студент открывает один .sln и запускает любую."""

import pytest

from forlabs.runner import languages as L
from forlabs.runner.plan import ProjectPlan, split_programs, validate

TOOLS = L.detect()


def need(key):
    return pytest.mark.skipif(key not in TOOLS, reason=f"нет инструмента {key}")


LIB = """using System;

namespace ClassLibrary1
{
    public class Calculator
    {
        public static int Summ(int a, int b)
        {
            return a + b;
        }
    }
}
"""
APP = """using System;
using ClassLibrary1;

namespace CalculatorApp
{
    internal class Program
    {
        static void Main(string[] args)
        {
            Console.WriteLine("Сумма: " + Calculator.Summ(2, 3));
        }
    }
}
"""
TEST = """using System;
using ClassLibrary1;

namespace LibraryTestApp
{
    internal class Program
    {
        static void Main(string[] args)
        {
            Console.WriteLine("Проверка: " + (Calculator.Summ(2, 2) == 4));
        }
    }
}
"""
FILES = {"ClassLibrary1/Calculator.cs": LIB, "CalculatorApp/Program.cs": APP, "LibraryTestApp/Program.cs": TEST}
PROGRAMS = [{"name": "ClassLibrary1", "kind": "library"},
            {"name": "CalculatorApp", "kind": "console", "entry": "CalculatorApp/Program.cs", "uses": ["ClassLibrary1"]},
            {"name": "LibraryTestApp", "kind": "console", "entry": "LibraryTestApp/Program.cs",
             "uses": ["ClassLibrary1"]}]


def plan(programs=PROGRAMS, files=FILES, language="csharp", shots=(), **kw):
    data = {"language": language, "kind": "console", "name": "Библиотека калькулятора",
            "entry": next(iter(files)), "files": [{"path": p, "content": c} for p, c in files.items()],
            "programs": programs, "shots": list(shots)}
    return ProjectPlan.model_validate(data | kw)


# ---------- план ----------

def test_library_and_two_apps_is_a_valid_plan():
    p = plan()
    assert validate(p) == []
    assert [x.name for x in p.programs] == ["ClassLibrary1", "CalculatorApp", "LibraryTestApp"]


def test_plan_entry_and_kind_follow_the_first_program_that_runs():
    p = plan(entry="ClassLibrary1/Calculator.cs", kind="winforms")
    assert (p.entry, p.kind) == ("CalculatorApp/Program.cs", "console")
    assert p.main_program().name == "CalculatorApp"


def test_old_plan_without_programs_is_unchanged():
    p = ProjectPlan(language="python", kind="console", name="Сумма", entry="main.py",
                    files=[{"path": "main.py", "content": "print(1)\n"}])
    assert p.programs == [] and validate(p) == [] and p.main_program() is None


def problems(**kw) -> str:
    return " | ".join(validate(plan(**kw)))


def test_program_rules():
    assert "нет программы «Нет»" in problems(programs=[*PROGRAMS[:2], PROGRAMS[2] | {"uses": ["Нет"]}])
    assert "не библиотека" in problems(programs=[PROGRAMS[0], PROGRAMS[1], PROGRAMS[2] | {"uses": ["CalculatorApp"]}])
    assert "уже есть" in problems(programs=[*PROGRAMS, PROGRAMS[1] | {"name": "calculatorapp"}])
    assert "ни одной программы" in problems(files=FILES | {"Общий.cs": "class X {}"})
    assert "должен лежать в её папке" in problems(
        programs=[PROGRAMS[0], PROGRAMS[1] | {"entry": "LibraryTestApp/Program.cs"}, PROGRAMS[2]])
    assert "нет ни одной программы, которую можно запустить" in problems(programs=[PROGRAMS[0]],
                                                                         files={"ClassLibrary1/Calculator.cs": LIB})
    assert "имя программы" in problems(programs=[PROGRAMS[0], PROGRAMS[1] | {"name": "Calc/App"}, PROGRAMS[2]])


def test_library_is_only_for_csharp():
    files = {"lib/util.py": "def f():\n    return 1\n", "app/main.py": "print(1)\n"}
    text = problems(language="python", files=files,
                    programs=[{"name": "lib", "kind": "library"}, {"name": "app", "kind": "console",
                                                                   "entry": "app/main.py"}])
    assert "library" in text


def test_libraries_cannot_use_each_other_in_a_circle():
    files = {"A/A.cs": "namespace A { public class A1 {} }", "B/B.cs": "namespace B { public class B1 {} }",
             "App/Program.cs": APP}
    text = problems(files=files, programs=[{"name": "A", "kind": "library", "uses": ["B"]},
                                           {"name": "B", "kind": "library", "uses": ["A"]},
                                           {"name": "App", "kind": "console", "entry": "App/Program.cs"}])
    assert "по кругу" in text


def test_shot_names_the_program_it_runs():
    ok = plan(shots=[{"section": 1, "block": 2, "mode": "console", "program": "LibraryTestApp"}])
    assert validate(ok) == [] and ok.program_of(ok.shots[0]).name == "LibraryTestApp"
    default = plan(shots=[{"section": 1, "block": 2, "mode": "console"}])
    assert default.program_of(default.shots[0]).name == "CalculatorApp"  # не сказано — первая запускаемая
    assert "нет программы «Другая»" in problems(shots=[{"section": 1, "block": 2, "mode": "console",
                                                         "program": "Другая"}])
    assert "библиотека" in problems(shots=[{"section": 1, "block": 2, "mode": "console",
                                            "program": "ClassLibrary1"}])


def test_programs_are_not_for_sites_and_sql():
    text = problems(language="web", files={"a/index.html": "<p>1</p>", "b/index.html": "<p>2</p>"},
                    programs=[{"name": "a", "kind": "web", "entry": "a/index.html"},
                              {"name": "b", "kind": "web", "entry": "b/index.html"}])
    assert "programs" in text


# ---------- старый план: несколько точек входа в одном проекте ----------

def test_folders_with_their_own_main_become_programs():
    p = split_programs(plan(programs=[]))
    assert validate(p) == []
    by = {x.name: x for x in p.programs}
    assert by["ClassLibrary1"].kind == "library" and by["ClassLibrary1"].uses == []
    assert by["CalculatorApp"].kind == "console" and by["CalculatorApp"].entry == "CalculatorApp/Program.cs"
    assert by["CalculatorApp"].uses == ["ClassLibrary1"] and by["LibraryTestApp"].uses == ["ClassLibrary1"]


def test_window_program_is_recognised_when_splitting():
    form = ("using System.Windows.Forms;\nnamespace CalculatorApp {\n  static class Program {\n"
            "    [STAThread] static void Main() { Application.Run(new Form()); }\n  }\n}\n")
    p = split_programs(plan(programs=[], files=FILES | {"CalculatorApp/Program.cs": form}))
    assert {x.name: x.kind for x in p.programs} == {"ClassLibrary1": "library", "CalculatorApp": "winforms",
                                                    "LibraryTestApp": "console"}


def test_single_program_is_left_alone():
    one = plan(programs=[], files={"Program.cs": APP, "Calculator.cs": LIB})
    assert split_programs(one).programs == []
    nested = plan(programs=[], files={"App/Program.cs": APP, "App/Calculator.cs": LIB})
    assert split_programs(nested).programs == []


def test_two_mains_in_one_folder_cannot_be_split_by_folders():
    p = plan(programs=[], files={"Task1.cs": APP, "Task2.cs": TEST.replace("ClassLibrary1", "System")})
    assert split_programs(p).programs == []


def test_cpp_and_go_folders_with_main_become_programs():
    cpp = plan(programs=[], language="cpp", files={
        "Задание1/main.cpp": "#include <iostream>\nint main() { std::cout << 1; return 0; }\n",
        "Задание2/main.cpp": "#include <iostream>\nint main() { std::cout << 2; return 0; }\n"})
    assert [x.name for x in split_programs(cpp).programs] == ["Задание1", "Задание2"]
    go = plan(programs=[], language="go", files={
        "first/main.go": "package main\n\nfunc main() {}\n", "second/main.go": "package main\n\nfunc main() {}\n"})
    assert [(x.name, x.entry) for x in split_programs(go).programs] == [("first", "first/main.go"),
                                                                        ("second", "second/main.go")]


# ---------- файлы сборки ----------

import re  # noqa: E402

FORM = ("using System;\nusing System.Windows.Forms;\nusing ClassLibrary1;\n\nnamespace CalculatorApp\n{\n"
        "    static class Program\n    {\n        [STAThread]\n        static void Main()\n        {\n"
        "            Application.Run(new Form { Text = \"Сумма: \" + Calculator.Summ(2, 3) });\n        }\n    }\n}\n")


def test_each_program_gets_its_own_csproj_and_there_is_one_solution():
    t = L.templates(plan(), TOOLS)
    assert set(t) == {"ClassLibrary1/ClassLibrary1.csproj", "CalculatorApp/CalculatorApp.csproj",
                      "LibraryTestApp/LibraryTestApp.csproj", "Библиотека_калькулятора.sln"}
    lib, app = t["ClassLibrary1/ClassLibrary1.csproj"], t["CalculatorApp/CalculatorApp.csproj"]
    assert "<OutputType>Library</OutputType>" in lib and "ProjectReference" not in lib
    assert "<OutputType>Exe</OutputType>" in app
    assert '<ProjectReference Include="..\\ClassLibrary1\\ClassLibrary1.csproj" />' in app
    listed = re.findall(r'^Project\("\{[^}]+\}"\) = "([^"]+)", "([^"]+)", "\{[0-9A-F-]{36}\}"', t["Библиотека_калькулятора.sln"],
                        re.M)
    # первой идёт программа, которая запускается: Visual Studio делает первый проект запускаемым
    assert listed == [("CalculatorApp", "CalculatorApp\\CalculatorApp.csproj"),
                      ("ClassLibrary1", "ClassLibrary1\\ClassLibrary1.csproj"),
                      ("LibraryTestApp", "LibraryTestApp\\LibraryTestApp.csproj")]
    assert t["Библиотека_калькулятора.sln"].count(".Build.0 = Debug|Any CPU") == 3


def test_window_program_targets_windows_and_plain_library_does_not():
    progs = [PROGRAMS[0], PROGRAMS[1] | {"kind": "winforms"}, PROGRAMS[2]]
    t = L.templates(plan(programs=progs, files=FILES | {"CalculatorApp/Program.cs": FORM}), TOOLS)
    app, lib = t["CalculatorApp/CalculatorApp.csproj"], t["ClassLibrary1/ClassLibrary1.csproj"]
    assert "<OutputType>WinExe</OutputType>" in app and "<UseWindowsForms>true</UseWindowsForms>" in app
    assert "-windows</TargetFramework>" in app and "-windows" not in lib and "UseWindowsForms" not in lib


def test_library_with_window_code_and_everything_that_uses_it_target_windows():
    box = ("using System.Windows.Forms;\nnamespace ClassLibrary1 {\n  public class Messages {\n"
           "    public static void Show(string t) { MessageBox.Show(t); }\n  }\n}\n")
    t = L.templates(plan(files=FILES | {"ClassLibrary1/Messages.cs": box}), TOOLS)
    assert "<UseWindowsForms>true</UseWindowsForms>" in t["ClassLibrary1/ClassLibrary1.csproj"]
    assert "-windows</TargetFramework>" in t["LibraryTestApp/LibraryTestApp.csproj"]  # иначе ссылка не соберётся
    assert "<OutputType>Exe</OutputType>" in t["LibraryTestApp/LibraryTestApp.csproj"]


def test_single_project_template_is_as_before():
    one = ProjectPlan(language="csharp", kind="console", name="Сумма", entry="Program.cs",
                      files=[{"path": "Program.cs", "content": APP}])
    t = L.templates(one, TOOLS)
    assert set(t) == {"Сумма.csproj"} and "ProjectReference" not in t["Сумма.csproj"]
    assert "<OutputType>Exe</OutputType>" in t["Сумма.csproj"]


# ---------- сборка и запуск ----------

def run(p, r, d, program=""):
    code, out = L.run_console(L.run_spec_for(p, r, d, TOOLS, program), "", 60)
    assert code == 0, out
    return out


@need("dotnet")
def test_library_and_apps_build_and_each_app_runs(tmp_path):
    p = plan()
    d = L.materialize(p, tmp_path / "Проект", TOOLS)
    r = L.build(d, p, TOOLS, 600)
    assert r.ok, r.log
    assert set(r.artifacts) == {"ClassLibrary1", "CalculatorApp", "LibraryTestApp"}
    assert r.artifacts["ClassLibrary1"].name == "ClassLibrary1.dll"
    # русские слова в выводе через перенаправление Windows отдаёт знаками «?» — сверяем то, что от кодировки не зависит
    assert ": 5" in run(p, r, d, "CalculatorApp") and ": True" in run(p, r, d, "LibraryTestApp")
    assert ": 5" in run(p, r, d)  # программа не названа — первая запускаемая
    assert (d / "Библиотека_калькулятора.sln").is_file() and not list(d.glob("*.csproj"))


@need("dotnet")
def test_build_error_names_the_file_with_its_program_folder(tmp_path):
    p = plan(files=FILES | {"LibraryTestApp/Program.cs": TEST.replace("Summ(2, 2)", "Нет(2, 2)")})
    d = L.materialize(p, tmp_path / "Проект", TOOLS)
    r = L.build(d, p, TOOLS, 600)
    assert not r.ok and "LibraryTestApp/Program.cs(" in r.log and "CS0117" in r.log
    assert str(d) not in r.log


def two(language, files, kind="console"):
    names = list(dict.fromkeys(f.split("/")[0] for f in files))
    progs = [{"name": n, "kind": kind, "entry": next(f for f in files if f.startswith(n + "/"))} for n in names]
    p = plan(programs=progs, files=files, language=language)
    assert validate(p) == []
    return p, names


def build_two(p, tmp_path):
    d = L.materialize(p, tmp_path / "Проект", TOOLS)
    r = L.build(d, p, TOOLS, 600)
    assert r.ok, r.log
    return d, r


@need("python")
def test_python_programs_run_from_their_own_folders(tmp_path):
    p, (a, b) = two("python", {"Задание1/main.py": "print(open('data.txt', encoding='utf-8').read().strip())\n",
                               "Задание1/data.txt": "первая\n", "Задание2/main.py": "print('вторая')\n"})
    d, r = build_two(p, tmp_path)
    assert run(p, r, d, a).strip() == "первая" and run(p, r, d, b).strip() == "вторая"


@need("gpp")
def test_cpp_programs_are_separate_executables(tmp_path):
    main = '#include <iostream>\nint main() {{ std::cout << "{0}" << std::endl; return 0; }}\n'
    p, (a, b) = two("cpp", {"first/main.cpp": main.format("one"), "second/main.cpp": main.format("two")})
    d, r = build_two(p, tmp_path)
    assert r.artifacts[a] != r.artifacts[b]
    assert run(p, r, d, a).strip() == "one" and run(p, r, d, b).strip() == "two"


@need("go")
def test_go_programs_are_separate_modules(tmp_path):
    main = 'package main\n\nimport "fmt"\n\nfunc main() {{\n\tfmt.Println("{0}")\n}}\n'
    p, (a, b) = two("go", {"first/main.go": main.format("one"), "second/main.go": main.format("two")})
    d, r = build_two(p, tmp_path)
    assert (d / "first" / "go.mod").is_file() and (d / "second" / "go.mod").is_file()
    assert run(p, r, d, a).strip() == "one" and run(p, r, d, b).strip() == "two"


@need("java")
def test_java_programs_with_the_same_class_name_do_not_clash(tmp_path):
    main = ('public class Main {{\n    public static void main(String[] args) {{\n'
            '        System.out.println("{0}");\n    }}\n}}\n')
    p, (a, b) = two("java", {"first/Main.java": main.format("one"), "second/Main.java": main.format("two")})
    d, r = build_two(p, tmp_path)
    # последняя строка: в этой среде java сначала печатает «Picked up JAVA_TOOL_OPTIONS»
    assert run(p, r, d, a).split()[-1] == "one" and run(p, r, d, b).split()[-1] == "two"


@need("gpp")
def test_error_in_one_of_the_programs_is_reported_with_its_name(tmp_path):
    good = '#include <iostream>\nint main() { std::cout << 1; return 0; }\n'
    p, _ = two("cpp", {"first/main.cpp": good, "second/main.cpp": "int main() { return x; }\n"})
    d = L.materialize(p, tmp_path / "Проект", TOOLS)
    r = L.build(d, p, TOOLS, 600)
    assert not r.ok and "second" in r.log and "main.cpp" in r.log and "first" not in r.log


def test_build_log_names_files_the_way_the_project_does(tmp_path):
    # Windows пишет пути с обратной чертой, а в проекте они с прямой: модель и чат ищут файл по пути из ошибки
    msbuild = (f"{tmp_path}\\LibraryTestApp\\Program.cs(10,58): error CS0117: 'Calculator' does not contain a "
               f"definition for 'Нет' [{tmp_path}\\LibraryTestApp\\LibraryTestApp.csproj]")
    assert L.trim_log(msbuild, tmp_path).startswith("LibraryTestApp/Program.cs(10,58): error CS0117")
    assert L.trim_log("second\\main.cpp:1:21: error: 'x' was not declared", tmp_path).startswith("second/main.cpp:1:21")
    kept = L.trim_log("a\\b.cs(1,1): error CS1010: Newline in constant '\\n'", tmp_path)
    assert kept.startswith("a/b.cs(1,1)") and "'\\n'" in kept  # текст сообщения не трогаем
    assert L.trim_log("main.py:3: SyntaxError", tmp_path) == "main.py:3: SyntaxError"
