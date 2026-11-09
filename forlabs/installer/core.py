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
MARKER = ".forlabs-install"  # метка папки, которую создал установщик: только такую он заменяет и удаляет

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
class UninstallResult:
    warnings: list[str] = field(default_factory=list)
    delete_later: Path | None = None  # папка работающей программы: удалит команда после выхода ForLabs


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


def _norm(p: Path) -> str:
    return os.path.normcase(os.path.abspath(p))


def _inside(child: Path, parent: Path) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def _ours(p: Path) -> bool:
    return (p / MARKER).is_file()


def check_target(target: Path | str) -> Path:
    """Папка установки: полный путь; последняя папка — ForLabs (иначе добавляется); не папка данных и не чужая
    непустая папка — заменять и удалять можно только папку с меткой установщика."""
    raw = str(target).strip().strip('"').strip()
    t = Path(raw) if raw else Path()
    if not raw or not t.is_absolute() or t.parent == t:
        raise InstallError("Укажи полный путь к папке установки, например " + str(default_target()) + ".")
    if t.name.lower() != APP_NAME.lower():
        t = t / APP_NAME
    data = data_dir()
    if _inside(t, data) or _inside(data, t):
        raise InstallError(f"В {t} лежат данные ForLabs (лабы, настройки) — программу туда ставить нельзя. "
                           f"Выбери другую папку, например {default_target()}.")
    if _inside(Path.home(), t):
        raise InstallError(f"Папка {t} содержит папку пользователя — выбери другую.")
    if t.exists() and not t.is_dir():
        raise InstallError(f"По пути {t} уже лежит файл — выбери другую папку.")
    if t.is_dir() and not _ours(t) and any(t.iterdir()):
        raise InstallError(f"Папка {t} не пуста, и это не установленный ForLabs. Выбери пустую или новую папку.")
    return t


def forlabs_running() -> bool:
    """ForLabs держит блокировку файла в папке данных, пока работает (forlabs.core.instance.InstanceLock)."""
    lock = data_dir() / "forlabs.lock"
    if not lock.exists():
        return False
    try:
        fh = open(lock, "a+b")  # noqa: SIM115
    except OSError:
        return True
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        fh.close()


def _sibling(target: Path, kind: str) -> Path:
    """Временная папка рядом с целевой; занятое чужое имя не трогаем — берём другое."""
    base = target.with_name(f"{target.name}.{kind}")
    if not base.exists() or _ours(base):
        return base
    for i in range(2, 100):
        cand = target.with_name(f"{target.name}.{kind}{i}")
        if not cand.exists() or _ours(cand):
            return cand
    raise InstallError(f"Рядом с {target} слишком много папок {target.name}.{kind}* — удали лишние.")


def _clean_leftovers(target: Path) -> None:
    for kind in ("new", "old"):
        for p in target.parent.glob(f"{target.name}.{kind}*"):
            if p.is_dir() and _ours(p):
                _rmtree(p)


def _swap(staging: Path, target: Path) -> None:
    old = _sibling(target, "old")
    _rmtree(old) if old.exists() else None
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
        if forlabs_running():
            raise InstallError("ForLabs сейчас открыт — закрой его (и окно в браузере, если открыто) и нажми "
                               "«Повторить».")
        _clean_leftovers(target)
        staging = _sibling(target, "new")
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
            (staging / MARKER).write_text(f"ForLabs {__version__}\n", encoding="utf-8")
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


def delayed_delete_command(target: Path, pid: int) -> list[str]:
    """PowerShell ждёт выхода ForLabs (pid) и удаляет папку программы. Скрипт в -EncodedCommand: кавычки, %, $
    и пробелы в пути не ломают команду."""
    import base64

    path = str(target)
    for q in ("'", "\u2018", "\u2019", "\u201a", "\u201b"):  # PowerShell считает кавычкой и «умные» апострофы
        path = path.replace(q, q + q)
    script = (
        f"$p = '{path}'; $log = Join-Path $env:TEMP 'ForLabsUninstall.log'\n"
        f"\"start $(Get-Date -Format o) wait={int(pid)}\" | Out-File $log -Encoding utf8\n"
        f"Wait-Process -Id {int(pid)} -ErrorAction SilentlyContinue\n"
        # помощники ForLabs из этой папки (драйвер браузера для схем и т. п.) держат файлы — закрыть их
        "Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($p + '\\', [StringComparison]::OrdinalIgnoreCase) } |"
        " Stop-Process -Force -ErrorAction SilentlyContinue\n"
        "Start-Sleep -Milliseconds 700\n"
        "for ($i = 0; $i -lt 15; $i++) { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue;"
        " if (-not (Test-Path -LiteralPath $p)) { break }; Start-Sleep -Seconds 1 }\n"
        "\"done $(Get-Date -Format o) left=$(Test-Path -LiteralPath $p)\" | Out-File $log -Append -Encoding utf8\n"
        "if (Test-Path -LiteralPath $p) { $Error | Out-String | Out-File $log -Append -Encoding utf8 }\n")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded]


CREATE_NO_WINDOW, CREATE_NEW_PROCESS_GROUP = 0x08000000, 0x00000200


def spawn_detached(cmd: list[str]) -> None:
    """Команда, которая переживёт выход ForLabs. Скрытая консоль (CREATE_NO_WINDOW), а не DETACHED_PROCESS:
    консольный PowerShell без консоли молча завершается, ничего не сделав."""
    flags = 0
    if sys.platform == "win32":
        flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(cmd, cwd=tempfile.gettempdir(), creationflags=flags, close_fds=True,  # noqa: S603
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def uninstall(target: Path, *, shell: Shell, data_dir: Path, remove_data: bool,
              running_from: Path | None = None) -> UninstallResult:
    """Убрать ярлыки, строку в «Приложениях», файлы программы (только папку с меткой установщика) и данные —
    только по согласию. Папку работающей программы возвращает в delete_later: её удаляют после выхода."""
    target = Path(target)
    if target.is_dir() and not _ours(target) and any(target.iterdir()):
        raise InstallError(f"В папке {target} нет ForLabs — удалять там нечего.")
    res = UninstallResult()
    for lnk in (shell.programs_dir() / f"{APP_NAME}.lnk", shell.desktop_dir() / f"{APP_NAME}.lnk"):
        try:
            shell.remove_shortcut(lnk)
        except Exception as e:
            res.warnings.append(f"Ярлык {lnk}: {e}")
    info = installed(shell)
    if info is None or _same(info.location, target):
        shell.remove_uninstall_entry()
    if remove_data and not _rmtree(Path(data_dir)):
        res.warnings.append(f"Часть данных не удалилась: {data_dir}")
    _clean_leftovers(target)
    if running_from is not None and _same(Path(running_from).parent, target):
        res.delete_later = target
    elif target.exists() and not _rmtree(target):
        res.warnings.append(f"Не все файлы удалились — удали папку вручную: {target}")
    return res
