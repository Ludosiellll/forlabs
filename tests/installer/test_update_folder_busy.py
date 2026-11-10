"""Обновление, когда папку программы ещё что-то держит.

Обновление из приложения запускает установщик из ForLabs, и тот наследует рабочую папку ForLabs, то есть папку
программы (так её задаёт ярлык). Windows не даёт переименовать папку, которая для какого-то процесса рабочая,
поэтому замена всей папки разом падает. Так же папку держат процессы окна WebView2, которые закрываются чуть позже
ForLabs. Файлы внутри такой папки переносить можно, и установщик меняет программу по файлам."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from forlabs.installer import core
from forlabs.installer.core import InstallError, install

from .conftest import make_payload

windows = pytest.mark.skipif(sys.platform != "win32", reason="поведение папок Windows")

NEW = {"ForLabs.exe": b"MZ" + b"n" * 6000, "python312.dll": b"N" * 20000,
       "forlabs/ui/templates/base.html": "<html>Новая версия</html>".encode()}


def new_payload(tmp_path: Path) -> Path:
    return make_payload(tmp_path / "payload-new.zip", NEW)


def installed_twice(tmp_path, payload, shell) -> Path:
    target = tmp_path / "ForLabs"
    install(payload, target, shell=shell, desktop=False)
    return target


def assert_new_version(target: Path, tmp_path: Path) -> None:
    assert (target / "ForLabs.exe").read_bytes() == NEW["ForLabs.exe"]
    assert (target / "forlabs/ui/templates/base.html").read_bytes() == NEW["forlabs/ui/templates/base.html"]
    assert not (target / "pymorphy3_dicts_ru").exists()  # файлы прежней версии, которых нет в новой, убраны
    assert (target / core.MARKER).is_file()
    assert not list(tmp_path.glob("ForLabs.new*"))


def hold(cwd: Path, script: str = "import time; time.sleep(60)") -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, "-c", script], cwd=str(cwd))
    time.sleep(1.0)  # процесс успел запуститься
    return p


@windows
def test_update_works_when_installer_itself_runs_from_the_program_folder(tmp_path, payload, shell, monkeypatch):
    target = installed_twice(tmp_path, payload, shell)
    monkeypatch.chdir(target)  # как установщик, запущенный из ForLabs по кнопке «Обновить»
    install(new_payload(tmp_path), target, shell=shell, desktop=False)
    assert_new_version(target, tmp_path)


@windows
def test_update_works_while_another_process_sits_in_the_program_folder(tmp_path, payload, shell):
    target = installed_twice(tmp_path, payload, shell)
    p = hold(target)  # процесс окна WebView2, который закрывается позже ForLabs
    try:
        install(new_payload(tmp_path), target, shell=shell, desktop=False)
    finally:
        p.kill()
        p.wait()
    assert_new_version(target, tmp_path)


@windows
def test_file_held_open_keeps_old_version_and_says_which_file(tmp_path, payload, shell):
    target = installed_twice(tmp_path, payload, shell)
    old = (target / "python312.dll").read_bytes()
    script = f"import time; f = open(r'{target / 'python312.dll'}', 'rb'); time.sleep(60)"
    p = hold(tmp_path, script)
    try:
        with pytest.raises(InstallError, match="python312.dll"):
            install(new_payload(tmp_path), target, shell=shell, desktop=False)
    finally:
        p.kill()
        p.wait()
    assert (target / "python312.dll").read_bytes() == old and (target / "ForLabs.exe").exists()
    assert not list(tmp_path.glob("ForLabs.new*"))


def test_folder_that_cannot_be_renamed_is_updated_file_by_file(tmp_path, payload, shell, monkeypatch):
    target = installed_twice(tmp_path, payload, shell)
    real = os.rename

    def rename(src, dst):
        if Path(src) == target:  # папку держит рабочая папка другого процесса
            raise PermissionError(32, "used by another process", str(src))
        return real(src, dst)

    monkeypatch.setattr(core.os, "rename", rename)
    monkeypatch.setattr(core, "SWAP_RETRY", 0.05)
    install(new_payload(tmp_path), target, shell=shell, desktop=False)
    assert_new_version(target, tmp_path)
    assert not list(tmp_path.glob("ForLabs.old*"))


def test_busy_file_rolls_back_every_moved_file(tmp_path, payload, shell, monkeypatch):
    target = installed_twice(tmp_path, payload, shell)
    before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    real = os.rename

    def rename(src, dst):
        if Path(src) == target or Path(src) == target / "python312.dll":
            raise PermissionError(32, "used by another process", str(src))
        return real(src, dst)

    monkeypatch.setattr(core.os, "rename", rename)
    monkeypatch.setattr(core, "SWAP_RETRY", 0.05)
    with pytest.raises(InstallError, match="python312.dll"):
        install(new_payload(tmp_path), target, shell=shell, desktop=False)
    after = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert after == before  # прежняя версия целиком на месте
    assert not list(tmp_path.glob("ForLabs.new*")) and not list(tmp_path.glob("ForLabs.old*"))


def test_installer_is_started_outside_the_program_folder(tmp_path, monkeypatch):
    from forlabs.core import update

    calls = []
    monkeypatch.setattr(update.subprocess, "Popen", lambda cmd, **kw: calls.append(kw))
    update.spawn_detached(["ForLabsSetup.exe", "--update"])
    cwd = calls[0].get("cwd")
    assert cwd and Path(cwd).is_dir() and Path(cwd).resolve() != Path.cwd().resolve()


def test_update_waits_for_the_old_process_itself(monkeypatch):
    from forlabs.installer import app as setup_app

    monkeypatch.setattr(setup_app, "forlabs_running", lambda: False)  # блокировку ForLabs уже отпустил
    monkeypatch.setattr(setup_app, "WAIT_STEP", 0.05)
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    t0 = time.monotonic()
    try:
        assert setup_app.wait_for_forlabs_exit(p.pid) is True
    finally:
        p.wait()
    assert time.monotonic() - t0 >= 1.0  # ждал, пока процесс завершится, а не только блокировку
