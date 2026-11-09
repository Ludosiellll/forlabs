"""Инструменты, шаблоны сборки, что нужно плану, запуск консольной программы с таймаутом."""

import json
import shutil
import stat
import sys
from pathlib import Path, PureWindowsPath

import pytest

from forlabs import version
from forlabs.runner import languages as L
from forlabs.runner.plan import ProjectPlan

WIN = sys.platform == "win32"
PY = sys.executable


def make(lang="python", kind="console", files=None, entry=None, name="Калькулятор"):
    files = files or {"main.py": "print('Привет')\n"}
    return ProjectPlan(language=lang, kind=kind, name=name, entry=entry or next(iter(files)),
                       files=[{"path": p, "content": c} for p, c in files.items()])


def fake_tool(path: Path, out: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho '{out}'\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


# ---------- detect ----------

def test_toolchain_fields():
    t = L.Toolchain("gpp", "/usr/bin/g++", "13.3.0")
    assert (t.name, t.path, t.version, t.args, t.setup) == ("gpp", "/usr/bin/g++", "13.3.0", [], "")


@pytest.mark.skipif(WIN, reason="набор инструментов этой машины Linux")
def test_detect_finds_tools_of_this_machine(tmp_path):
    tools = L.detect(tools_dir=tmp_path / "нет")
    expect = {"gpp": "g++", "clang": "clang++", "python": "python3", "java": "javac", "node": "node", "go": "go",
              "php": "php", "dotnet": "dotnet", "mysql": "mysql"}
    for key, exe in expect.items():
        assert (key in tools) == bool(shutil.which(exe)), key
    if "gpp" in tools:
        assert tools["gpp"].version[:1].isdigit()
    if "go" in tools:
        assert tools["go"].version.startswith("1.")
    assert "msvc" not in tools and "edge" not in tools
    assert all(isinstance(t, L.Toolchain) and t.name == k for k, t in tools.items())


@pytest.mark.skipif(WIN, reason="поддельные инструменты — sh-скрипты")
def test_detect_uses_forlabs_tools_dir_when_path_has_nothing(tmp_path):
    tdir = tmp_path / "tools"
    fake_tool(tdir / "dotnet" / "dotnet", "8.0.404 [/x/sdk]")
    fake_tool(tdir / "python" / "python", "Python 3.12.1")
    fake_tool(tdir / "node" / "node", "v20.11.0")
    fake_tool(tdir / "go" / "bin" / "go", "go version go1.22.0 linux/amd64")
    fake_tool(tdir / "jdk" / "bin" / "javac", "javac 21.0.2")
    fake_tool(tdir / "jdk" / "bin" / "java", "openjdk 21.0.2")
    fake_tool(tdir / "php" / "php", "PHP 8.3.0 (cli)")
    fake_tool(tdir / "mingw64" / "bin" / "g++", "g++ (GCC) 13.2.0")
    fake_tool(tdir / "mysql" / "bin" / "mysql", "mysql  Ver 8.0.36 for Linux")
    fake_tool(tdir / "mysql" / "bin" / "mysqld", "mysqld  Ver 8.0.36")
    tools = L.detect(env={"PATH": str(tmp_path / "пусто")}, tools_dir=tdir)
    assert set(tools) == {"dotnet", "python", "node", "go", "java", "php", "gpp", "mysql"}
    assert tools["dotnet"].path == str(tdir / "dotnet" / "dotnet") and tools["dotnet"].version == "8.0.404"
    assert tools["java"].path == str(tdir / "jdk" / "bin" / "javac") and tools["java"].version == "21.0.2"
    assert tools["go"].version == "1.22.0" and tools["gpp"].version == "13.2.0"
    assert tools["python"].version == "3.12.1" and tools["node"].version == "20.11.0"


@pytest.mark.skipif(WIN, reason="поддельные инструменты — sh-скрипты")
def test_detect_prefers_students_own_tool_over_forlabs_dir(tmp_path):
    own = fake_tool(tmp_path / "bin" / "node", "v22.1.0")
    fake_tool(tmp_path / "tools" / "node" / "node", "v20.11.0")
    tools = L.detect(env={"PATH": str(own.parent)}, tools_dir=tmp_path / "tools")
    assert tools["node"].path == str(own) and tools["node"].version == "22.1.0"


@pytest.mark.skipif(WIN, reason="поддельные инструменты — sh-скрипты")
def test_detect_skips_broken_tool_in_path(tmp_path):
    broken = tmp_path / "bin" / "go"
    broken.parent.mkdir()
    broken.write_text("#!/bin/sh\nexit 3\n")
    broken.chmod(0o755)
    fake_tool(tmp_path / "tools" / "go" / "bin" / "go", "go version go1.22.0 linux/amd64")
    tools = L.detect(env={"PATH": str(broken.parent)}, tools_dir=tmp_path / "tools")
    assert tools["go"].path == str(tmp_path / "tools" / "go" / "bin" / "go")


@pytest.mark.skipif(WIN, reason="поддельные инструменты — sh-скрипты")
def test_detect_dotnet_without_sdk_is_not_a_toolchain(tmp_path):
    fake_tool(tmp_path / "bin" / "dotnet", "")
    assert "dotnet" not in L.detect(env={"PATH": str(tmp_path / "bin")}, tools_dir=tmp_path / "нет")


@pytest.mark.skipif(WIN, reason="поддельные инструменты — sh-скрипты")
def test_detect_never_picks_forlabs_own_frozen_python(tmp_path, monkeypatch):
    own = fake_tool(tmp_path / "bin" / "python3", "Python 3.11.9")
    monkeypatch.setattr(sys, "executable", str(own))
    monkeypatch.setattr(version, "frozen", lambda: True)
    assert "python" not in L.detect(env={"PATH": str(own.parent)}, tools_dir=tmp_path / "нет")
    monkeypatch.setattr(version, "frozen", lambda: False)
    assert L.detect(env={"PATH": str(own.parent)}, tools_dir=tmp_path / "нет")["python"].path == str(own)


def test_detect_default_tools_dir_is_in_data_home(tmp_home):
    assert L.default_tools_dir() == tmp_home / "tools"


def test_windows_install_locations():
    env = {"ProgramFiles": r"C:\Program Files", "ProgramFiles(x86)": r"C:\Program Files (x86)",
           "LOCALAPPDATA": r"C:\Users\Иван\AppData\Local", "SystemDrive": "C:", "JAVA_HOME": r"D:\jdk-21"}
    c = {k: [str(PureWindowsPath(p)) for p in v] for k, v in L.windows_locations(env).items()}
    assert r"C:\xampp\php\php.exe" in c["php"]
    assert r"C:\Program Files\MySQL\MySQL Server *\bin\mysql.exe" in c["mysql"]
    assert c["java"][0] == r"D:\jdk-21\bin\javac.exe"
    assert r"C:\Program Files\Go\bin\go.exe" in c["go"] and r"C:\Program Files\nodejs\node.exe" in c["node"]
    assert r"C:\Program Files\dotnet\dotnet.exe" in c["dotnet"]
    assert r"C:\Users\Иван\AppData\Local\Programs\Python\Python3*\python.exe" in c["python"]
    assert r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" in c["edge"]
    assert any("CodeBlocks" in p for p in c["gpp"])
    assert c["vswhere"] == [r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"]


def test_forlabs_tools_layout():
    lay = {k: [p.as_posix() for p in v] for k, v in L.forlabs_locations(Path("/t"), windows=True).items()}
    assert lay["dotnet"] == ["/t/dotnet/dotnet.exe"] and lay["python"] == ["/t/python/python.exe"]
    assert lay["java"] == ["/t/jdk/bin/javac.exe"] and lay["gpp"] == ["/t/mingw64/bin/g++.exe"]
    assert lay["go"] == ["/t/go/bin/go.exe"] and lay["mysql"] == ["/t/mysql/bin/mysql.exe"]
    assert lay["node"] == ["/t/node/node.exe"] and lay["php"] == ["/t/php/php.exe"]
    assert L.forlabs_locations(Path("/t"), windows=False)["go"] == [Path("/t/go/bin/go")]


def test_vcvars_set_output_parsing():
    text = "=C:=C:\\work\r\nPath=C:\\VS\\bin;C:\\Windows\r\nINCLUDE=C:\\VS\\include\r\nUSERNAME=Иван\r\nEMPTY=\r\n"
    env = L.parse_set_output(text)
    assert env == {"Path": "C:\\VS\\bin;C:\\Windows", "INCLUDE": "C:\\VS\\include", "USERNAME": "Иван", "EMPTY": ""}


# ---------- missing ----------

def tc(*keys):
    return {k: L.Toolchain(k, f"/x/{k}", "1") for k in keys}


@pytest.mark.parametrize("lang, kind, have, need", [
    ("csharp", "console", (), ["dotnet"]),
    ("csharp", "winforms", ("dotnet",), []),
    ("cpp", "console", (), ["gpp"]),
    ("cpp", "console", ("msvc",), []),
    ("cpp", "console", ("clang",), []),
    ("python", "console", ("gpp",), ["python"]),
    ("java", "gui", (), ["java"]),
    ("javascript", "console", (), ["node"]),
    ("javascript", "web", ("node",), ["edge"]),
    ("web", "web", (), ["edge"]),
    ("php", "console", (), ["php"]),
    ("php", "web", (), ["php", "edge"]),
    ("go", "console", ("go",), []),
    ("mysql", "sql", (), ["mysql"]),
])
def test_missing(lang, kind, have, need):
    p = make(lang, kind, {"main.x": ""})
    assert L.missing(p, tc(*have)) == need


def test_missing_keys_have_titles_for_ui():
    assert set(L.TOOL_TITLES) == {"dotnet", "msvc", "gpp", "clang", "python", "java", "node", "go", "php", "mysql",
                                  "edge"}


# ---------- materialize ----------

CS = {"Program.cs": "Console.WriteLine(\"Привет\");\n", "students.json": "[]"}


def csproj(tmp_path, kind="console", tools=None, name="Калькулятор"):
    d = L.materialize(make("csharp", kind, CS, "Program.cs", name=name), tmp_path / "p", tools)
    files = list(d.glob("*.csproj"))
    assert len(files) == 1
    return files[0].name, files[0].read_text(encoding="utf-8")


def test_csproj_console_fallback_net8(tmp_path):
    name, text = csproj(tmp_path)
    assert name == "Калькулятор.csproj"
    assert "<OutputType>Exe</OutputType>" in text and "<TargetFramework>net8.0</TargetFramework>" in text
    assert "UseWindowsForms" not in text and "UseWPF" not in text
    assert "<ImplicitUsings>enable</ImplicitUsings>" in text
    assert 'CopyToOutputDirectory="PreserveNewest"' in text  # данные лабы лежат рядом с exe, как в Visual Studio


def test_csproj_takes_tfm_from_detected_sdk(tmp_path):
    _, text = csproj(tmp_path, tools={"dotnet": L.Toolchain("dotnet", "dotnet", "9.0.100")})
    assert "<TargetFramework>net9.0</TargetFramework>" in text


@pytest.mark.parametrize("kind, flag", [("winforms", "<UseWindowsForms>true</UseWindowsForms>"),
                                        ("wpf", "<UseWPF>true</UseWPF>")])
def test_csproj_gui(tmp_path, kind, flag):
    _, text = csproj(tmp_path, kind, tools={"dotnet": L.Toolchain("dotnet", "dotnet", "8.0.404")})
    assert "<OutputType>WinExe</OutputType>" in text and "<TargetFramework>net8.0-windows</TargetFramework>" in text
    assert flag in text


def test_csproj_name_and_namespace_are_safe(tmp_path):
    name, text = csproj(tmp_path, name="3 Лаба (вариант 5)")
    assert name.endswith(".csproj") and " " not in name and "(" not in name
    ns = text.split("<RootNamespace>")[1].split("<")[0]
    assert ns.isidentifier()


def test_go_mod_with_module_and_detected_version(tmp_path):
    p = make("go", files={"main.go": "package main\nfunc main() {}\n"}, name="Обратное слово")
    d = L.materialize(p, tmp_path / "a", {"go": L.Toolchain("go", "go", "1.22.5")})
    mod = (d / "go.mod").read_text(encoding="utf-8")
    assert mod.startswith("module ") and "\ngo 1.22\n" in mod
    assert mod.split()[1].isascii()
    d2 = L.materialize(p, tmp_path / "b")
    assert "\ngo 1.21\n" in (d2 / "go.mod").read_text(encoding="utf-8")


def test_package_json_for_es_modules(tmp_path):
    esm = make("javascript", files={"main.js": 'import fs from "node:fs";\nconsole.log("Привет");\n'})
    pkg = json.loads((L.materialize(esm, tmp_path / "a") / "package.json").read_text(encoding="utf-8"))
    assert pkg["type"] == "module" and "scripts" not in pkg
    cjs = make("javascript", files={"main.js": 'const fs = require("fs");\n'})
    pkg = json.loads((L.materialize(cjs, tmp_path / "b") / "package.json").read_text(encoding="utf-8"))
    assert pkg.get("type", "commonjs") == "commonjs"
    own = make("javascript", files={"main.js": "1", "package.json": '{"name": "моё", "type": "module"}'})
    assert json.loads((L.materialize(own, tmp_path / "c") / "package.json").read_text("utf-8"))["name"] == "моё"


def test_materialize_writes_files_cpp_with_bom(tmp_path):
    p = make("cpp", files={"main.cpp": "int main() {}\n", "include/util.h": "#pragma once\n", "input.txt": "1 2"})
    d = L.materialize(p, tmp_path / "p")
    assert (d / "main.cpp").read_bytes().startswith(b"\xef\xbb\xbf")  # Visual Studio без /utf-8 видит UTF-8
    assert (d / "include" / "util.h").read_bytes().startswith(b"\xef\xbb\xbf")
    assert (d / "input.txt").read_bytes() == b"1 2"
    j = L.materialize(make("java", files={"Main.java": "class Main {}"}), tmp_path / "j")
    assert not (j / "Main.java").read_bytes().startswith(b"\xef")  # javac с BOM не собирает


def test_materialize_refuses_bad_plan_and_non_empty_dir(tmp_path):
    bad = make(files={"main.py": "1", "../evil.py": "2"}, entry="main.py")
    with pytest.raises(ValueError):
        L.materialize(bad, tmp_path / "a")
    assert not (tmp_path / "evil.py").exists()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "чужое.txt").write_text("x")
    with pytest.raises(ValueError):
        L.materialize(make(), tmp_path / "b")


# ---------- run_console ----------

def spec(code, tmp_path, **kw):
    (tmp_path / "prog.py").write_text(code, encoding="utf-8")
    return L.RunSpec(cmd=[PY, "prog.py"], cwd=tmp_path, console=True, env={"PYTHONIOENCODING": "utf-8"}, **kw)


def test_run_console_feeds_input_and_returns_output(tmp_path):
    code, out = L.run_console(spec('n = input("Имя: ")\nprint("Привет,", n)\n', tmp_path), "Аня\n", 20)
    assert code == 0 and "Привет, Аня" in out


def test_run_console_kills_on_timeout(tmp_path):
    code, out = L.run_console(spec('print("Начало", flush=True)\nwhile True:\n    pass\n', tmp_path), "", 1.5)
    assert code is None and "Начало" in out and "остановлена" in out


def test_run_console_limits_output(tmp_path):
    code, out = L.run_console(spec('while True:\n    print("x" * 1000)\n', tmp_path), "", 3, limit=50_000)
    assert len(out.encode("utf-8")) < 60_000


def test_run_console_reports_crash_exit_code(tmp_path):
    code, out = L.run_console(spec('raise SystemExit(3)\n', tmp_path), "", 20)
    assert code == 3


def test_run_spec_web_is_browser_only(tmp_path):
    p = make("web", "web", {"index.html": "<h1>Привет</h1>"})
    d = L.materialize(p, tmp_path / "w")
    r = L.build(d, p, {}, 30)
    assert r.ok and r.artifact == d / "index.html"
    s = L.run_spec(p, r.artifact, d, {})
    assert s.cmd == [] and s.url.startswith("file:") and not s.console


def test_run_spec_php_site_uses_local_server(tmp_path):
    p = make("php", "web", {"index.php": "<?php echo 'Привет';"})
    s = L.run_spec(p, tmp_path / "index.php", tmp_path, tc("php"))
    assert s.cmd[:2] == ["/x/php", "-S"] and s.cmd[2].startswith("127.0.0.1:")
    assert s.url.startswith("http://127.0.0.1:") and s.url.endswith("/index.php") and not s.console


def test_run_spec_gui_on_windows_uses_windowless_launchers(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "WINDOWS", True)
    for exe in ("python.exe", "pythonw.exe", "javac.exe", "java.exe", "javaw.exe"):
        (tmp_path / exe).write_text("")
    tools = {"python": L.Toolchain("python", str(tmp_path / "python.exe"), "3.12"),
             "java": L.Toolchain("java", str(tmp_path / "javac.exe"), "21")}
    gui = L.run_spec(make("python", "gui", {"main.py": "import tkinter"}), None, tmp_path, tools)
    assert gui.cmd[0] == str(tmp_path / "pythonw.exe") and not gui.console
    con = L.run_spec(make("python", "console", {"main.py": "print(1)"}), None, tmp_path, tools)
    assert con.cmd[0] == str(tmp_path / "python.exe") and con.console
    swing = L.run_spec(make("java", "gui", {"Main.java": "class Main {}"}), tmp_path / "out", tmp_path, tools)
    assert swing.cmd[0] == str(tmp_path / "javaw.exe") and swing.cmd[-1] == "Main"


def test_build_dirs_for_copy_to_submission():
    assert set(L.BUILD_DIRS) == {"bin", "obj", "out"}


def test_build_reports_missing_tool(tmp_path):
    p = make("go", files={"main.go": "package main\nfunc main() {}\n"})
    d = L.materialize(p, tmp_path / "g")
    r = L.build(d, p, {}, 30)
    assert not r.ok and "Go" in r.log and r.artifact is None


def test_exe_named_after_project_not_program():
    from forlabs.runner.languages import _ascii

    assert _ascii("Массив", "program") == "Massiv" and _ascii("Учёт книг", "program") == "Uchet_knig"
    assert _ascii("123", "program") == "program_123"
