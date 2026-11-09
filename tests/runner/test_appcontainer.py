"""Песочница AppContainer: что ей открывать на чтение и где она уже открыта (проверки, не требующие Windows)."""

from pathlib import Path

from forlabs.runner import appcontainer as ac


def test_tool_root_is_install_folder():
    assert ac.tool_root("/opt/jdk-21/bin/javac") == Path("/opt/jdk-21")
    assert ac.tool_root("/home/u/Python312/python.exe") == Path("/home/u/Python312")


def test_program_files_and_windows_are_already_open_to_containers(monkeypatch):
    monkeypatch.setenv("ProgramFiles", "/pf")
    monkeypatch.setenv("SystemRoot", "/win")
    assert ac.open_to_all_containers("/pf/nodejs")
    assert ac.open_to_all_containers("/win/System32")
    assert not ac.open_to_all_containers("/home/u/AppData/Local/Programs/Python")
    assert not ac.open_to_all_containers("/pfx/evil")  # не «/pf» в начале имени


def test_tool_dirs_skip_project_copy_and_add_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("FORLABS_HOME", str(tmp_path / "home"))
    from forlabs.runner import languages

    monkeypatch.setattr(languages, "detect", lambda *a, **k: {})
    work = tmp_path / "run" / "Проект"
    work.mkdir(parents=True)
    exe = work / "out" / "lab.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    py = tmp_path / "Python312" / "python.exe"
    py.parent.mkdir()
    py.write_bytes(b"MZ")
    dirs = ac.tool_dirs([str(py), str(work / "main.py")], work)
    assert py.parent in dirs and all(work not in [d, *d.parents] for d in dirs)
    assert ac.tool_dirs([str(exe)], work) == [tmp_path / "home" / "tools"]


def test_read_access_goes_to_all_packages_not_own_container(tmp_path, monkeypatch):
    """Своя запись на папке Python ломала его обычный запуск (Windows не находил DLL) — только «все песочницы»."""
    monkeypatch.setenv("FORLABS_HOME", str(tmp_path / "home"))
    calls = []
    monkeypatch.setattr(ac, "grant", lambda path, sid, access=ac.READ_EXECUTE: calls.append((str(path), sid)) or True)
    d = tmp_path / "Python312"
    d.mkdir()
    assert ac.ensure_read([d], "S-1-15-2-123") == []
    assert calls == [(str(d), ac.ALL_PACKAGES)]
    ac.ensure_read([d])
    assert len(calls) == 1  # второй раз не трогаем — помнится
