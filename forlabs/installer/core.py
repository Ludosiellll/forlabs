"""Установка и удаление ForLabs для одного пользователя Windows (без прав администратора).

Распаковка идёт во временную папку рядом с целевой и только потом заменяет старую версию: оборванная или
отменённая установка не ломает уже установленный ForLabs, повторный запуск начинает заново.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Protocol

from forlabs.version import __version__

APP_NAME = "ForLabs"
EXE_NAME = "ForLabs.exe"
PUBLISHER = "ForLabs"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ForLabs"
CHUNK = 1 << 20

# (название в окне установщика, с чего начинаются пути файлов) — в этом порядке и ставятся
COMPONENTS: list[tuple[str, tuple[str, ...]]] = [
    ("Программа ForLabs", ()),
    ("Шаблоны отчётов и правила", ("forlabs/",)),
    ("Словари русского языка", ("pymorphy3_dicts_ru/",)),
    ("Проверка и ярлыки", ()),
]
FINAL = len(COMPONENTS) - 1


class InstallError(Exception):
    """Понятная студенту причина, почему установка не удалась."""


@dataclass
class Component:
    name: str
    size: int = 0
    files: list[zipfile.ZipInfo] = field(default_factory=list)


@dataclass
class Progress:
    done: int  # байт распаковано
    total: int
    component: int  # индекс в COMPONENTS
    component_done: int
    component_total: int


@dataclass
class InstallResult:
    target: Path
    exe: Path
    files: int
    size: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class Installed:
    version: str
    location: Path


class Shell(Protocol):
    def programs_dir(self) -> Path: ...
    def desktop_dir(self) -> Path: ...
    def create_shortcut(self, lnk: Path, target: Path, workdir: Path, description: str) -> None: ...
    def remove_shortcut(self, lnk: Path) -> None: ...
    def write_uninstall_entry(self, values: dict) -> None: ...
    def read_uninstall_entry(self) -> dict | None: ...
    def remove_uninstall_entry(self) -> None: ...


def default_target() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / APP_NAME


def data_dir() -> Path:
    """Папка данных ForLabs (как forlabs.core.paths.data_home, без импорта всего ForLabs)."""
    env = os.environ.get("FORLABS_HOME")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME


def _component_of(name: str) -> int:
    for i, (_, prefixes) in enumerate(COMPONENTS):
        if prefixes and name.startswith(prefixes):
            return i
    return 0


def _broken(detail: str) -> InstallError:
    return InstallError(f"Установщик повреждён ({detail}). Скачай ForLabsSetup.exe заново.")


def _open(payload: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(payload)
    except (OSError, zipfile.BadZipFile) as e:
        raise _broken(f"архив не читается: {type(e).__name__}") from e


def _plan(z: zipfile.ZipFile) -> list[Component]:
    comps = [Component(name) for name, _ in COMPONENTS]
    names = set()
    for info in z.infolist():
        if info.is_dir():
            continue
        p = PurePosixPath(info.filename)
        if p.is_absolute() or ".." in p.parts or "\\" in info.filename or ":" in info.filename:
            raise _broken(f"недопустимый путь {info.filename!r}")
        names.add(info.filename)
        c = comps[_component_of(info.filename)]
        c.files.append(info)
        c.size += info.file_size
    if EXE_NAME not in names:
        raise _broken(f"нет {EXE_NAME}")
    for c in comps:
        c.files.sort(key=lambda i: i.filename)
    return comps


def components(payload: Path) -> list[Component]:
    with _open(payload) as z:
        return _plan(z)


def _extract_one(z: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path, step: Callable[[int], None],
                 cancelled: Callable[[], bool]) -> None:
    out = dest / info.filename
    out.parent.mkdir(parents=True, exist_ok=True)
    with z.open(info) as src, open(out, "wb") as dst:
        while chunk := src.read(CHUNK):
            if cancelled():
                raise InstallError("Установка отменена — установленный ForLabs не изменён.")
            dst.write(chunk)
            step(len(chunk))


def _rmtree(p: Path) -> bool:
    if not p.exists():
        return True
    for attempt in range(3):  # антивирус на миг держит только что распакованный файл
        shutil.rmtree(p, ignore_errors=True)
        if not p.exists():
            return True
        time.sleep(0.3 * (attempt + 1))
    return not p.exists()


def _os_error(e: OSError, target: Path) -> InstallError:
    import errno

    if e.errno == errno.ENOSPC:
        return InstallError("На диске закончилось место — освободи место и нажми «Повторить».")
    if isinstance(e, PermissionError):
        return InstallError(f"Нет доступа к папке {target}. Выбери другую папку (например, предложенную по "
                            "умолчанию) или закрой программы, которые используют файлы ForLabs.")
    return InstallError(f"Не удалось записать файлы ForLabs: {e.strerror or e}")


def check_target(target: Path | str) -> Path:
    """Папка установки: полный путь, не корень диска и не чужая непустая папка (её пришлось бы заменить)."""
    t = Path(str(target).strip().strip('"'))
    if not str(t).strip(".") or not t.is_absolute() or t.parent == t:
        raise InstallError("Укажи полный путь к папке установки, например " + str(default_target()) + ".")
    if t.exists() and not t.is_dir():
        raise InstallError(f"По пути {t} уже лежит файл — выбери другую папку.")
    if t.is_dir() and not (t / EXE_NAME).exists() and any(t.iterdir()):
        raise InstallError(f"Папка {t} не пуста, и в ней нет ForLabs. Выбери пустую или новую папку, например "
                           f"{t / APP_NAME}.")
    return t


def _check_not_running(target: Path) -> None:
    """Папку с работающим ForLabs.exe Windows не даёт переименовать — проверяем до долгой распаковки."""
    if not target.exists():
        return
    probe = target.with_name(target.name + ".check")
    try:
        os.rename(target, probe)
    except PermissionError as e:
        raise InstallError("ForLabs сейчас открыт — закрой его (и окно в браузере, если открыто) и нажми "
                           "«Повторить».") from e
    except OSError as e:
        raise _os_error(e, target) from e
    os.rename(probe, target)


def _swap(staging: Path, target: Path) -> None:
    old = target.with_name(target.name + ".old")
    _rmtree(old)
    had_old = target.exists()
    try:
        if had_old:
            os.rename(target, old)
    except PermissionError as e:
        raise InstallError("ForLabs сейчас открыт — закрой его и нажми «Повторить».") from e
    try:
        os.rename(staging, target)
    except OSError as e:
        if had_old:
            os.rename(old, target)  # вернуть старую версию на место
        raise _os_error(e, target) from e
    _rmtree(old)  # не удалось (антивирус) — уберётся при следующей установке


def install(payload: Path, target: Path, *, shell: Shell, desktop: bool = True,
            progress: Callable[[Progress], None] = lambda p: None,
            cancelled: Callable[[], bool] = lambda: False) -> InstallResult:
    target = check_target(target)
    with _open(payload) as z:
        comps = _plan(z)
        total = sum(c.size for c in comps)
        target.parent.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(target.parent).free
        need = total + 50 * 2**20
        if free < need:
            raise InstallError(f"Не хватает места на диске: нужно {need // 2**20} МБ, свободно {free // 2**20} МБ.")
        _check_not_running(target)
        staging = target.with_name(target.name + ".new")
        _rmtree(staging)
        done = 0
        try:
            for ci, comp in enumerate(comps):
                cdone = 0

                def step(n: int, ci=ci, comp=comp) -> None:
                    nonlocal done, cdone
                    done += n
                    cdone += n
                    progress(Progress(done, total, ci, cdone, comp.size))

                progress(Progress(done, total, ci, 0, comp.size))
                for info in comp.files:
                    if cancelled():
                        raise InstallError("Установка отменена — установленный ForLabs не изменён.")
                    _extract_one(z, info, staging, step, cancelled)
            missing = [i.filename for c in comps for i in c.files
                       if not (staging / i.filename).is_file() or (staging / i.filename).stat().st_size != i.file_size]
            if missing:
                raise InstallError("Часть файлов пропала сразу после распаковки — похоже, их удалил антивирус ("
                                   + ", ".join(missing[:3]) + "). Добавь папку ForLabs в исключения и повтори.")
            _swap(staging, target)
        except InstallError:
            _rmtree(staging)
            raise
        except OSError as e:
            _rmtree(staging)
            raise _os_error(e, target) from e
    exe = target / EXE_NAME
    warnings = []
    lnks = [shell.programs_dir() / f"{APP_NAME}.lnk"] + ([shell.desktop_dir() / f"{APP_NAME}.lnk"] if desktop else [])
    for lnk in lnks:
        try:
            lnk.parent.mkdir(parents=True, exist_ok=True)
            shell.create_shortcut(lnk, exe, target, "Лабораторные работы и отчёты по оформлению колледжа")
        except Exception as e:  # без ярлыка программа работает — сообщить, не падать
            warnings.append(f"Не удалось создать ярлык {lnk.name} в {lnk.parent}: {e}")
    if not desktop:
        shell.remove_shortcut(shell.desktop_dir() / f"{APP_NAME}.lnk")
    shell.write_uninstall_entry({
        "DisplayName": APP_NAME, "DisplayVersion": __version__, "Publisher": PUBLISHER,
        "InstallLocation": str(target), "DisplayIcon": f'"{exe}",0',
        "UninstallString": f'"{exe}" --uninstall', "QuietUninstallString": f'"{exe}" --uninstall --silent',
        "EstimatedSize": max(1, total // 1024), "NoModify": 1, "NoRepair": 1,
        "InstallDate": date.today().strftime("%Y%m%d"),
    })
    progress(Progress(total, total, FINAL, 1, 1))
    return InstallResult(target, exe, sum(len(c.files) for c in comps), total, warnings)


def installed(shell: Shell) -> Installed | None:
    entry = shell.read_uninstall_entry()
    if not entry or not entry.get("InstallLocation"):
        return None
    return Installed(str(entry.get("DisplayVersion") or ""), Path(entry["InstallLocation"]))


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _spawn_detached(cmd: list[str]) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(cmd, cwd=tempfile.gettempdir(), creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def uninstall(target: Path, *, shell: Shell, data_dir: Path, remove_data: bool,
              running_from: Path | None = None, spawn: Callable[[list[str]], None] = _spawn_detached) -> list[str]:
    """Убрать ярлыки, строку в «Приложениях», файлы программы (и данные — только по согласию)."""
    target = Path(target)
    if target.is_dir() and not (target / EXE_NAME).exists() and any(target.iterdir()):
        raise InstallError(f"В папке {target} нет ForLabs — удалять там нечего.")
    warnings = []
    for lnk in (shell.programs_dir() / f"{APP_NAME}.lnk", shell.desktop_dir() / f"{APP_NAME}.lnk"):
        try:
            shell.remove_shortcut(lnk)
        except Exception as e:
            warnings.append(f"Ярлык {lnk}: {e}")
    info = installed(shell)
    if info is None or _same(info.location, target):
        shell.remove_uninstall_entry()
    if remove_data and not _rmtree(Path(data_dir)):
        warnings.append(f"Часть данных не удалилась: {data_dir}")
    for leftover in (target.with_name(target.name + ".old"), target.with_name(target.name + ".new")):
        _rmtree(leftover)
    if running_from is not None and _same(Path(running_from).parent, target):
        # работающий exe удалить нельзя: команда подождёт выхода этого процесса и удалит папку
        spawn(["cmd.exe", "/d", "/c", f'ping -n 4 127.0.0.1 >nul & rmdir /s /q "{target}"'])
    elif not _rmtree(target):
        warnings.append(f"Не все файлы удалились — удали папку вручную: {target}")
    return warnings
