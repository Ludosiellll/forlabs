"""Песочница AppContainer: что ей открывать на чтение и где она уже открыта (проверки, не требующие Windows)."""

from pathlib import Path

from forlabs.runner import appcontainer as ac


def test_tool_root_is_install_folder(tmp_path):
    jdk = tmp_path / "jdk-21"
    (jdk / "bin").mkdir(parents=True)
    py = tmp_path / "Python312"
    py.mkdir()
    assert ac.tool_root(jdk / "bin" / "javac.exe") == jdk.resolve()
    assert ac.tool_root(py / "python.exe") == py.resolve()


def test_program_files_and_windows_are_already_open_to_containers(tmp_path, monkeypatch):
    pf, win = tmp_path / "pf", tmp_path / "win"
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.setenv("SystemRoot", str(win))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("ProgramW6432", raising=False)
    assert ac.open_to_all_containers(pf / "nodejs")
    assert ac.open_to_all_containers(win / "System32")
    assert not ac.open_to_all_containers(tmp_path / "AppData" / "Local" / "Programs" / "Python")
    assert not ac.open_to_all_containers(tmp_path / "pfx" / "evil")  # не «pf» в начале имени


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
