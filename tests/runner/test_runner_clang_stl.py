"""Clang в Windows берёт библиотеку C++ у Visual Studio, а она проверяет версию компилятора: новая Visual Studio
со старым LLVM отказывается собирать даже «Привет, мир» (error STL1000)."""

from pathlib import Path

from forlabs.runner import languages as L
from forlabs.runner.plan import ProjectPlan

STL1000 = ("In file included from ./main.cpp:1:\nC:\\VS\\include\\yvals_core.h:923:1: error: static assertion failed: "
           "error STL1000: Unexpected compiler version, expected Clang 22 or newer.\n1 error generated.\n")
MISMATCH = "-D_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH"


def project(tmp_path):
    p = ProjectPlan(language="cpp", kind="console", name="Привет", entry="main.cpp",
                    files=[{"path": "main.cpp", "content": "#include <iostream>\nint main() { std::cout << 1; }\n"}])
    tools = {"clang": L.Toolchain("clang", "clang++", "20.1.8")}
    return p, tools, L.materialize(p, tmp_path / "Проект", tools)


def fake_run(monkeypatch, answers):
    """Подменить запуск компилятора: каждый вызов отдаёт следующий ответ; (0, …) ещё и кладёт exe."""
    seen = []

    def run(cmd, cwd, *a, **k):
        seen.append(cmd)
        code, log = answers[len(seen) - 1]
        if code == 0:
            out = Path(cwd) / cmd[cmd.index("-o") + 1]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"MZ")
        return code, log

    monkeypatch.setattr(L, "_run", run)
    return seen


def test_clang_older_than_visual_studio_stl_is_retried_without_the_version_check(tmp_path, monkeypatch):
    p, tools, d = project(tmp_path)
    seen = fake_run(monkeypatch, [(1, STL1000), (0, "")])
    r = L.build(d, p, tools)
    assert r.ok and r.artifact.is_file()
    assert len(seen) == 2 and MISMATCH not in seen[0] and MISMATCH in seen[1]
    assert [a for a in seen[1] if a != MISMATCH] == seen[0]  # та же команда, только без проверки версии


def test_clang_too_old_for_the_stl_says_so_instead_of_errors_from_inside_the_library(tmp_path, monkeypatch):
    p, tools, d = project(tmp_path)
    fake_run(monkeypatch, [(1, STL1000), (1, "C:\\VS\\include\\xutility:400: error: unknown builtin\n")])
    r = L.build(d, p, tools)
    assert not r.ok and r.artifact is None
    assert "STL1000" in r.log and "unknown builtin" not in r.log
    assert "Clang 20.1.8" in r.log and "MinGW" in r.log  # что не так и что с этим делать — по-русски


def test_ordinary_clang_error_is_not_retried(tmp_path, monkeypatch):
    p, tools, d = project(tmp_path)
    seen = fake_run(monkeypatch, [(1, "main.cpp:2:5: error: use of undeclared identifier 'x'\n")])
    r = L.build(d, p, tools)
    assert not r.ok and len(seen) == 1 and "undeclared identifier" in r.log
