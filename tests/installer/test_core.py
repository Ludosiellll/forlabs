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
    real_rename = os.rename

    def locked(src, dst):
        if Path(src) == target:
            raise PermissionError(13, "used by another process", str(src))
        return real_rename(src, dst)

    monkeypatch.setattr(core.os, "rename", locked)
    with pytest.raises(InstallError, match="ForLabs сейчас открыт"):
        _install(payload, target, shell)
    assert (target / "ForLabs.exe").exists() and not (tmp_path / "ForLabs.new").exists()


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
    uninstall(target, shell=shell, data_dir=data, remove_data=False)
    assert not target.exists() and shell.shortcuts == {} and shell.entry is None
    assert (data / "settings.json").exists()


def test_uninstall_with_data(tmp_path, payload, shell):
    target, data = tmp_path / "ForLabs", tmp_path / "data"
    data.mkdir()
    _install(payload, target, shell)
    uninstall(target, shell=shell, data_dir=data, remove_data=True)
    assert not data.exists()


def test_uninstall_from_inside_schedules_folder_removal(tmp_path, payload, shell):
    target = tmp_path / "ForLabs"
    _install(payload, target, shell)
    spawned = []
    uninstall(target, shell=shell, data_dir=tmp_path / "d", remove_data=False, running_from=target / "ForLabs.exe",
              spawn=spawned.append)
    assert target.exists()  # себя удалить нельзя — удалит отложенная команда после выхода
    assert len(spawned) == 1 and str(target) in " ".join(spawned[0])
    assert shell.entry is None


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


# Review Focus: выбранная чужая папка (например, «Документы») не должна быть заменена программой
def test_refuses_non_empty_foreign_folder(tmp_path, payload, shell):
    docs = tmp_path / "Документы"
    docs.mkdir()
    (docs / "курсовая.docx").write_text("важное")
    with pytest.raises(InstallError, match="не пуста"):
        _install(payload, docs, shell)
    assert (docs / "курсовая.docx").read_text() == "важное"
    assert not (tmp_path / "Документы.new").exists()


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
