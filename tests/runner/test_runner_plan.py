import json
import re

import pytest

from forlabs.runner.plan import SHOT_ACTIONS, ListingRef, ProjectFile, ProjectPlan, ShotPlan, ShotStep, validate

PY = 'name = input("Как тебя зовут? ")\nprint("Привет,", name)\n'


def make(**kw) -> ProjectPlan:
    base = dict(language="python", kind="console", name="Привет", entry="main.py",
                files=[{"path": "main.py", "content": PY}])
    base.update(kw)
    return ProjectPlan.model_validate(base)


def problems_for(**kw) -> list[str]:
    return validate(make(**kw))


def test_good_plan_has_no_problems():
    assert validate(make()) == []


def test_models_are_importable_by_design_names():
    plan = ProjectPlan(language="cpp", kind="console", name="Сумма", entry="main.cpp",
                       files=[ProjectFile(path="main.cpp", content="int main() { return 0; }\n")],
                       listings=[ListingRef(section=1, block=2, file="main.cpp")],
                       shots=[ShotPlan(section=1, block=3, mode="console", input="2 3\n",
                                       steps=[ShotStep(action="wait", value="1")], expect="Сумма")])
    assert validate(plan) == []
    assert set(SHOT_ACTIONS) == {"set", "select", "click", "check", "uncheck", "type", "key", "wait", "scroll",
                                 "open", "hover"}


def test_paths_are_normalized():
    p = make(files=[{"path": ".\\src\\main.py", "content": PY}, {"path": "./data//students.json", "content": "[]"}],
             entry="./src/main.py", listings=[{"section": 1, "block": 1, "file": "src\\main.py"}])
    assert [f.path for f in p.files] == ["src/main.py", "data/students.json"]
    assert p.entry == "src/main.py" and p.listings[0].file == "src/main.py"
    assert validate(p) == []


def test_numbers_given_as_strings_are_coerced():
    p = make(listings=[{"section": "2", "block": "3", "file": "main.py"}],
             shots=[{"section": "2", "block": "4", "mode": "Console", "input": 12,
                     "steps": [{"action": "Wait", "value": 1.5, "target": None}]}])
    assert (p.listings[0].section, p.listings[0].block) == (2, 3)
    assert p.shots[0].input == "12" and p.shots[0].mode == "console"
    assert p.shots[0].steps[0].action == "wait" and p.shots[0].steps[0].value == "1.5"
    assert p.shots[0].steps[0].target == ""


@pytest.mark.parametrize("raw, lang", [("C#", "csharp"), ("c++", "cpp"), ("Python", "python"), ("JS", "javascript"),
                                       ("node", "javascript"), ("html", "web"), ("SQL", "mysql"), ("golang", "go")])
def test_language_aliases(raw, lang):
    assert make(language=raw, kind="console").language == lang


def test_entry_case_is_fixed_to_real_file():
    p = make(entry="Main.py")
    assert p.entry == "main.py" and validate(p) == []


@pytest.mark.parametrize("path", [
    "../evil.py", "src/../../x.py", "src/../main2.py", "/etc/passwd.py", "C:/Windows/x.py", "C:x.py",
    "\\\\server\\share\\x.py", "main.py:stream", "CON.py", "src/nul.txt", "Com1.py", "lpt9.txt", "src/aux/x.py",
    "src/", "bad|name.py", "what?.py", "end.py.", "dir /x.py",
])
def test_bad_paths_are_rejected(path):
    probs = problems_for(files=[{"path": "main.py", "content": PY}, {"path": path, "content": "x = 1\n"}])
    assert probs, path
    assert all(re.search("[а-яё]", p, re.I) for p in probs)


def test_absolute_path_message_names_the_file():
    probs = problems_for(files=[{"path": "main.py", "content": PY}, {"path": "C:/Windows/x.py", "content": ""}])
    assert any("C:/Windows/x.py" in p for p in probs)


@pytest.mark.parametrize("lang, kind, files, entry", [
    ("cpp", "console", ["main.cpp", "student.h", "student.cpp", "input.txt"], "main.cpp"),
    ("csharp", "winforms", ["Program.cs", "Form1.cs", "Form1.Designer.cs", "Form1.resx", "students.json"], "Program.cs"),
    ("csharp", "wpf", ["App.xaml", "App.xaml.cs", "MainWindow.xaml", "MainWindow.xaml.cs"], "App.xaml.cs"),
    ("java", "console", ["src/ru/lab/Main.java", "src/ru/lab/Student.java", "data.csv"], "src/ru/lab/Main.java"),
    ("javascript", "console", ["main.js", "lib/utils.mjs", "package.json"], "main.js"),
    ("web", "web", ["index.html", "css/style.css", "js/script.js", "img/logo.svg"], "index.html"),
    ("php", "web", ["index.php", "header.php", "style.css", "schema.sql"], "index.php"),
    ("go", "console", ["main.go", "student.go"], "main.go"),
    ("mysql", "sql", ["library.sql"], "library.sql"),
])
def test_allowed_extensions_per_language(lang, kind, files, entry):
    content = {"package.json": '{"type": "module"}'}
    plan = make(language=lang, kind=kind, entry=entry,
                files=[{"path": f, "content": content.get(f, "x\n")} for f in files])
    assert validate(plan) == []


@pytest.mark.parametrize("lang, path", [("python", "main.cpp"), ("cpp", "helper.py"), ("go", "main.js"),
                                        ("mysql", "main.php"), ("web", "app.php"), ("python", "tool.exe"),
                                        ("csharp", "lib.dll"), ("java", "Main.class")])
def test_foreign_extensions_rejected(lang, path):
    entry = {"python": "main.py", "cpp": "main.cpp", "go": "main.go", "mysql": "db.sql", "web": "index.html",
             "csharp": "Program.cs", "java": "Main.java"}[lang]
    kind = {"mysql": "sql", "web": "web"}.get(lang, "console")
    probs = problems_for(language=lang, kind=kind, entry=entry,
                         files=[{"path": entry, "content": "x\n"}, {"path": path, "content": "x\n"}])
    assert any(path in p for p in probs)


@pytest.mark.parametrize("path", ["Lab.csproj", "Lab.sln", "pom.xml", "build.gradle", "build.gradle.kts", "go.mod",
                                  "go.sum", "CMakeLists.txt", "Makefile", "run.bat", "build.sh", "setup.ps1",
                                  "start.cmd", "Directory.Build.props", "Directory.Build.targets", "global.json",
                                  "NuGet.config", "sub/pom.xml", "lab.vcxproj"])
def test_build_files_from_model_rejected(path):
    probs = problems_for(files=[{"path": "main.py", "content": PY}, {"path": path, "content": "x\n"}])
    assert any(path in p and "сборк" in p for p in probs), probs


def test_package_json_with_scripts_rejected_without_scripts_allowed():
    def plan(pkg):
        return make(language="javascript", entry="main.js",
                    files=[{"path": "main.js", "content": "console.log('Привет');\n"},
                           {"path": "package.json", "content": json.dumps(pkg)}])
    assert validate(plan({"type": "module", "name": "lab"})) == []
    assert any("package.json" in p for p in validate(plan({"scripts": {"postinstall": "calc"}})))
    bad = make(language="javascript", entry="main.js",
               files=[{"path": "main.js", "content": "1"}, {"path": "package.json", "content": "{oops"}])
    assert any("package.json" in p for p in validate(bad))


def test_limits_on_count_and_size():
    many = [{"path": f"m{i}.py", "content": "x = 1\n"} for i in range(60)]
    assert problems_for(files=[{"path": "main.py", "content": PY}, *many])  # 61 файл
    assert validate(make(files=[{"path": "main.py", "content": PY}, *many[:59]])) == []  # ровно 60
    assert problems_for(files=[{"path": "main.py", "content": "#" * (200 * 1024 + 1)}])
    assert validate(make(files=[{"path": "main.py", "content": "#" * (200 * 1024)}])) == []
    big = [{"path": f"m{i}.py", "content": "#" * (150 * 1024)} for i in range(3)]
    assert any("400" in p for p in problems_for(files=[{"path": "main.py", "content": PY}, *big]))


def test_size_is_counted_in_utf8_bytes():
    # 110 000 кириллических символов = 220 000 байт > 200 КБ
    assert problems_for(files=[{"path": "main.py", "content": "# " + "я" * 110_000}])


def test_entry_must_exist_and_be_code():
    assert any("main2.py" in p for p in problems_for(entry="main2.py"))
    probs = problems_for(entry="data.txt", files=[{"path": "main.py", "content": PY},
                                                  {"path": "data.txt", "content": "1 2"}])
    assert any("data.txt" in p for p in probs)


def test_no_files_is_a_problem():
    assert problems_for(files=[], entry="main.py")


def test_duplicate_paths_case_insensitive():
    probs = problems_for(files=[{"path": "main.py", "content": PY}, {"path": "Main.py", "content": PY}])
    assert any("Main.py" in p for p in probs)
    assert problems_for(files=[{"path": "main.py", "content": PY}, {"path": "Data/a.txt", "content": ""},
                               {"path": "data/A.TXT", "content": ""}])


def test_listing_must_reference_project_file():
    assert any("other.py" in p for p in problems_for(listings=[{"section": 1, "block": 0, "file": "other.py"}]))


@pytest.mark.parametrize("step, ok", [
    ({"action": "set", "target": "inputA", "value": "12"}, True),
    ({"action": "select", "target": "op", "value": "Сумма"}, True),
    ({"action": "click", "target": "calc"}, True),
    ({"action": "check", "target": "agree"}, True),
    ({"action": "type", "value": "12\n"}, True),
    ({"action": "key", "value": "Enter"}, True),
    ({"action": "wait", "value": "1"}, True),
    ({"action": "scroll", "value": "300"}, True),
    ({"action": "open", "value": "about.html"}, True),
    ({"action": "doubleclick", "target": "x"}, False),
    ({"action": "click"}, False),
    ({"action": "set", "target": "inputA"}, False),
    ({"action": "type"}, False),
])
def test_shot_steps(step, ok):
    probs = problems_for(shots=[{"section": 1, "block": 2, "mode": "window", "steps": [step]}])
    assert (probs == []) is ok, probs


def test_unknown_shot_mode_rejected():
    with pytest.raises(ValueError):
        make(shots=[{"section": 1, "block": 2, "mode": "video"}])


@pytest.mark.parametrize("lang, kind, ok", [
    ("csharp", "winforms", True), ("csharp", "wpf", True), ("python", "gui", True), ("java", "gui", True),
    ("php", "web", True), ("javascript", "web", True), ("mysql", "sql", True), ("python", "script", True),
    ("python", "winforms", False), ("go", "wpf", False), ("mysql", "console", False), ("cpp", "web", False),
])
def test_kind_must_fit_language(lang, kind, ok):
    ext = {"csharp": "cs", "python": "py", "java": "java", "php": "php", "javascript": "js", "mysql": "sql",
           "go": "go", "cpp": "cpp"}[lang]
    probs = problems_for(language=lang, kind=kind, entry=f"main.{ext}", files=[{"path": f"main.{ext}", "content": "x"}])
    assert (probs == []) is ok, probs


@pytest.mark.parametrize("name", ["", "   ", "Лаба/1", "a\\b", "CON", "x:y", "Имя?", "." * 3])
def test_bad_project_name(name):
    assert problems_for(name=name)


def test_good_project_names():
    for name in ["Калькулятор", "Lab 3 Matrix", "ЛР_5-сортировка"]:
        assert validate(make(name=name)) == []



def test_wpf_entry_xaml_points_to_code_file():
    from forlabs.runner.plan import ProjectPlan, validate

    p = ProjectPlan.model_validate({"language": "csharp", "kind": "wpf", "name": "Книги", "entry": "App.xaml",
                                    "files": [{"path": "App.xaml", "content": "<Application/>"},
                                              {"path": "App.xaml.cs", "content": "class App {}"},
                                              {"path": "MainWindow.xaml.cs", "content": "class W {}"}]})
    assert p.entry == "App.xaml.cs" and validate(p) == []
