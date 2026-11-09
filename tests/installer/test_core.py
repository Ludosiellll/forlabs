"""Ядро установщика: распаковка с прогрессом, замена старой версии, ярлыки, строка в «Приложениях», удаление."""

import os
import shutil
import zipfile
from pathlib import Path

import pytest

from forlabs.installer import core
from forlabs.installer.core import InstallError, install, uninstall

from .conftest import make_payload


def _install(payload, target, shell, **kw):
    events = []
    kw.setdefault("desktop", True)
    res = install(payload, target, shell=shell, progress=lambda p: events.append(p), **kw)
    return res, events


def test_components_in_install_order(payload):
    comps = core.components(payload)
    assert [c.name for c in comps] == ["Программа ForLabs", "Шаблоны отчётов и правила", "Словари русского языка",
                                       "Проверка и ярлыки"]
    assert comps[0].size == 25002 and comps[1].size > 0 and comps[2].size == 9000 and comps[3].size == 0


def test_install_extracts_files_shortcuts_and_registry(tmp_path, payload, shell):
    target = tmp_path / "Мои программы" / "ForLabs"
    res, events = _install(payload, target, shell)
    assert (target / "ForLabs.exe").read_bytes().startswith(b"MZ")
    assert (target / "forlabs" / "ui" / "templates" / "base.html").read_text("utf-8") == "<html>Шаблон</html>"
    assert res.exe == target / "ForLabs.exe" and res.files == 5
    assert shell.shortcuts[shell.programs / "ForLabs.lnk"]["target"] == target / "ForLabs.exe"
    assert shell.shortcuts[shell.desktop / "ForLabs.lnk"]["workdir"] == target
    entry = shell.entry
    assert entry["DisplayName"] == "ForLabs" and entry["InstallLocation"] == str(target)
    assert entry["UninstallString"] == f'"{target / "ForLabs.exe"}" --uninstall'
    assert entry["QuietUninstallString"] == f'"{target / "ForLabs.exe"}" --uninstall --silent'
    assert entry["NoModify"] == 1 and entry["EstimatedSize"] >= 1
    assert not (tmp_path / "Мои программы" / "ForLabs.new").exists()
    done = [e.done for e in events]
    assert done == sorted(done) and events[-1].done == events[-1].total and events[-1].component == 3
    assert [e.component for e in events] == sorted(e.component for e in events)


def test_no_desktop_shortcut_when_switched_off(tmp_path, payload, shell):
    _install(payload, tmp_path / "ForLabs", shell, desktop=False)
    assert shell.desktop / "ForLabs.lnk" not in shell.shortcuts


def test_reinstall_replaces_old_version_and_drops_stale_files(tmp_path, payload, shell):
    target = tmp_path / "ForLabs"
    _install(payload, target, shell)
    (target / "old_module.pyd").write_bytes(b"old")
    _install(payload, target, shell)
    assert not (target / "old_module.pyd").exists()
    assert (target / "ForLabs.exe").exists()
    assert not (tmp_path / "ForLabs.old").exists()


def test_cancel_keeps_old_version_and_cleans_staging(tmp_path, payload, shell):
    target = tmp_path / "ForLabs"
    _install(payload, target, shell)
    (target / "marker").write_text("старая версия")
    with pytest.raises(InstallError, match="отменена"):
        install(payload, target, shell=shell, desktop=True, progress=lambda p: None, cancelled=lambda: True)
    assert (target / "marker").read_text() == "старая версия"
    assert not (tmp_path / "ForLabs.new").exists()


def test_running_program_blocks_update_early(tmp_path, payload, shell, monkeypatch):
    target = tmp_path / "ForLabs"
    _install(payload, target, shell)
    monkeypatch.setattr(core, "forlabs_running", lambda: True)
    with pytest.raises(InstallError, match="ForLabs сейчас открыт"):
        _install(payload, target, shell)
    assert (target / "ForLabs.exe").exists() and not list(tmp_path.glob("ForLabs.new*"))


def test_locked_folder_during_swap_keeps_old_version(tmp_path, payload, shell, monkeypatch):
    target = tmp_path / "ForLabs"
    _install(payload, target, shell)
    real_rename = os.rename

    def locked(src, dst):
        if Path(src) == target:
            raise PermissionError(13, "used by another process", str(src))
        return real_rename(src, dst)

    monkeypatch.setattr(core.os, "rename", locked)
    with pytest.raises(InstallError, match="ForLabs сейчас открыт"):
        _install(payload, target, shell)
    assert (target / "ForLabs.exe").exists() and not list(tmp_path.glob("ForLabs.new*"))


def test_running_check_uses_instance_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("FORLABS_HOME", str(tmp_path / "data"))
    assert core.forlabs_running() is False
    from forlabs.core.instance import InstanceLock

    lock = InstanceLock()
    assert lock.acquire()
    try:
        assert core.forlabs_running() is True
    finally:
        lock.release()


def test_not_enough_disk_space(tmp_path, payload, shell, monkeypatch):
    monkeypatch.setattr(core.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10**9, 10**9 - 100, 100))
    with pytest.raises(InstallError, match="Не хватает места"):
        _install(payload, tmp_path / "ForLabs", shell)


def test_payload_with_unsafe_paths_is_rejected(tmp_path, shell):
    bad = make_payload(tmp_path / "bad.zip", {"ForLabs.exe": b"MZ", "../evil.txt": b"x"})
    with pytest.raises(InstallError, match="повреждён"):
        _install(bad, tmp_path / "ForLabs", shell)
    assert not (tmp_path / "evil.txt").exists()


def test_broken_payload_is_reported(tmp_path, shell):
    (tmp_path / "broken.zip").write_bytes(b"not a zip")
    with pytest.raises(InstallError, match="повреждён"):
        _install(tmp_path / "broken.zip", tmp_path / "ForLabs", shell)


def test_payload_without_program_is_rejected(tmp_path, shell):
    p = make_payload(tmp_path / "p.zip", {"readme.txt": b"x"})
    with pytest.raises(InstallError, match="повреждён"):
        _install(p, tmp_path / "ForLabs", shell)


def test_files_missing_after_extract_are_reported(tmp_path, payload, shell, monkeypatch):
    real = core._extract_one

    def antivirus(z, info, dest, *a):
        real(z, info, dest, *a)
        if info.filename == "python312.dll":
            (dest / info.filename).unlink()

    monkeypatch.setattr(core, "_extract_one", antivirus)
    with pytest.raises(InstallError, match="антивирус"):
        _install(payload, tmp_path / "ForLabs", shell)


def test_uninstall_removes_program_shortcuts_and_entry_but_keeps_data(tmp_path, payload, shell):
    target = tmp_path / "ForLabs"
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text("{}")
    _install(payload, target, shell)
    res = uninstall(target, shell=shell, data_dir=data, remove_data=False)
    assert not target.exists() and shell.shortcuts == {} and shell.entry is None and res.delete_later is None
    assert (data / "settings.json").exists()


def test_uninstall_with_data(tmp_path, payload, shell):
    target, data = tmp_path / "ForLabs", tmp_path / "data"
    data.mkdir()
    _install(payload, target, shell)
    uninstall(target, shell=shell, data_dir=data, remove_data=True)
    assert not data.exists()


def test_uninstall_from_inside_leaves_folder_for_delayed_delete(tmp_path, payload, shell):
    target = tmp_path / "ForLabs"
    _install(payload, target, shell)
    res = uninstall(target, shell=shell, data_dir=tmp_path / "d", remove_data=False, running_from=target / "ForLabs.exe")
    assert target.exists() and res.delete_later == target  # себя удалить нельзя — удалит команда после выхода
    assert shell.entry is None and shell.shortcuts == {}


# ревью I4: команда удаления ждёт выхода ForLabs и не ломается на кавычках, %, $ и пробелах в пути
def test_delayed_delete_command_is_safe_for_odd_paths():
    import base64

    odd = Path("C:/Users/Ива'н 100%/$Программы/ForLabs")
    cmd = core.delayed_delete_command(odd, pid=4321)
    assert cmd[0].lower().startswith("powershell") and "-EncodedCommand" in cmd
    script = base64.b64decode(cmd[cmd.index("-EncodedCommand") + 1]).decode("utf-16-le")
    assert "Wait-Process -Id 4321" in script
    assert "$p = '" + str(odd).replace("'", "''") + "'" in script and "-LiteralPath $p" in script
    assert "Stop-Process" in script and "ForLabsUninstall.log" in script


def test_uninstall_keeps_entry_of_other_location(tmp_path, payload, shell):
    _install(payload, tmp_path / "A" / "ForLabs", shell)
    uninstall(tmp_path / "B" / "ForLabs", shell=shell, data_dir=tmp_path / "d", remove_data=False)
    assert shell.entry is not None


def test_default_target_is_per_user(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert core.default_target() == tmp_path / "Programs" / "ForLabs"


def test_installed_version_reads_entry(tmp_path, payload, shell):
    assert core.installed(shell) is None
    _install(payload, tmp_path / "ForLabs", shell)
    info = core.installed(shell)
    assert info.version == core.__version__ and info.location == tmp_path / "ForLabs"


def test_payload_zip_entries_are_utf8_names(tmp_path, shell):
    p = make_payload(tmp_path / "p.zip", {"ForLabs.exe": b"MZ", "forlabs/rules/Правила.md": "т".encode()})
    with zipfile.ZipFile(p) as z:
        assert z.namelist()[1] == "forlabs/rules/Правила.md"
    _install(p, tmp_path / "ForLabs", shell)
    assert (tmp_path / "ForLabs" / "forlabs" / "rules" / "Правила.md").exists()


# Review Focus: выбранная чужая папка (например, «Документы») — ставим в подпапку ForLabs, её файлы не трогаем
def test_foreign_folder_gets_forlabs_subfolder(tmp_path, payload, shell):
    docs = tmp_path / "Документы"
    docs.mkdir()
    (docs / "курсовая.docx").write_text("важное")
    res, _ = _install(payload, docs, shell)
    assert res.target == docs / "ForLabs" and (docs / "ForLabs" / "ForLabs.exe").exists()
    assert (docs / "курсовая.docx").read_text() == "важное"
    uninstall(res.target, shell=shell, data_dir=tmp_path / "d", remove_data=False)
    assert (docs / "курсовая.docx").read_text() == "важное"


def test_typed_path_without_forlabs_leaf_gets_one(tmp_path):
    assert core.check_target(tmp_path / "Programs") == tmp_path / "Programs" / "ForLabs"
    assert core.check_target(tmp_path / "forlabs") == tmp_path / "forlabs"


# ревью C1: папка данных ForLabs (%LOCALAPPDATA%\ForLabs) не может стать папкой программы
def test_refuses_data_folder_as_target(tmp_path, payload, shell, monkeypatch):
    data = tmp_path / "Local" / "ForLabs"
    monkeypatch.setenv("FORLABS_HOME", str(data))
    for bad in (data, data / "ForLabs", tmp_path / "Local"):  # «Local» → Local\ForLabs = папка данных
        with pytest.raises(InstallError, match="данн"):
            core.check_target(bad)
    with pytest.raises(InstallError, match="данн"):
        _install(payload, data, shell)


def test_refuses_existing_forlabs_folder_without_install_marker(tmp_path, payload, shell):
    t = tmp_path / "ForLabs"
    t.mkdir()
    (t / "settings.json").write_text("{}")  # например, папка данных старой версии
    with pytest.raises(InstallError, match="не пуста"):
        _install(payload, t, shell)
    assert (t / "settings.json").exists()


# ревью I5: чужие ForLabs.old / ForLabs.new рядом не удаляются
def test_foreign_sibling_folders_survive_install(tmp_path, payload, shell):
    for name in ("ForLabs.old", "ForLabs.new"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "backup.txt").write_text("моё")
    target = tmp_path / "ForLabs"
    _install(payload, target, shell)
    _install(payload, target, shell)
    uninstall(target, shell=shell, data_dir=tmp_path / "d", remove_data=False)
    assert (tmp_path / "ForLabs.old" / "backup.txt").read_text() == "моё"
    assert (tmp_path / "ForLabs.new" / "backup.txt").read_text() == "моё"


def test_empty_existing_folder_is_fine(tmp_path, payload, shell):
    (tmp_path / "ForLabs").mkdir()
    _install(payload, tmp_path / "ForLabs", shell)
    assert (tmp_path / "ForLabs" / "ForLabs.exe").exists()


@pytest.mark.parametrize("bad", ["", "ForLabs", "C:"])
def test_refuses_relative_or_root_targets(tmp_path, payload, shell, bad):
    with pytest.raises(InstallError, match="папк"):
        _install(payload, Path(bad), shell)


def test_refuses_filesystem_root(payload, shell):
    with pytest.raises(InstallError, match="папк"):
        _install(payload, Path(Path.cwd().anchor), shell)


def test_uninstall_refuses_folder_without_program(tmp_path, shell):
    docs = tmp_path / "Документы"
    docs.mkdir()
    (docs / "курсовая.docx").write_text("важное")
    with pytest.raises(InstallError, match="нет ForLabs"):
        uninstall(docs, shell=shell, data_dir=tmp_path / "d", remove_data=False)
    assert (docs / "курсовая.docx").exists()
