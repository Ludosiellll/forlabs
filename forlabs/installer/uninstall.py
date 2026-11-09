"""ForLabs.exe --uninstall [--silent] [--remove-data] [--target папка] — удаление из «Параметры → Приложения»."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import os

from forlabs.installer.core import InstallError, data_dir, default_target, delayed_delete_command, spawn_detached, uninstall

TITLE = "Удаление ForLabs"


class Dialogs(Protocol):
    def ask(self, text: str, default_no: bool = False) -> bool: ...
    def tell(self, text: str, warning: bool = False) -> None: ...


class WindowsDialogs:
    def ask(self, text: str, default_no: bool = False) -> bool:
        from forlabs.installer import winshell as w

        flags = w.MB_YESNO | w.MB_ICONQUESTION | (w.MB_DEFBUTTON2 if default_no else 0)
        return w.message_box(text, TITLE, flags) == w.IDYES

    def tell(self, text: str, warning: bool = False) -> None:
        from forlabs.installer import winshell as w

        w.message_box(text, TITLE, w.MB_OK | (w.MB_ICONWARNING if warning else w.MB_ICONINFO))


class SilentDialogs:
    def ask(self, text: str, default_no: bool = False) -> bool:
        return True

    def tell(self, text: str, warning: bool = False) -> None:
        pass


def target_from(argv: list[str], exe: Path | None) -> Path:
    if "--target" in argv and argv.index("--target") + 1 < len(argv):
        return Path(argv[argv.index("--target") + 1])
    return Path(exe).parent if exe else default_target()


def program_exe() -> Path:
    """Путь к работающему ForLabs.exe (у Nuitka это sys.executable, на всякий случай — argv[0])."""
    for cand in (sys.executable, sys.argv[0] if sys.argv else ""):
        if cand and Path(cand).name.lower() == "forlabs.exe":
            return Path(cand).resolve()
    return Path(sys.executable).resolve()


def _forlabs_running() -> bool:
    from forlabs.core.instance import InstanceLock

    lock = InstanceLock()
    if not lock.acquire():
        return True
    lock.release()
    return False


def _forget_saved_keys(_: bool = True) -> None:
    """Ключ API и вход в облако лежат в «Диспетчере учётных данных» Windows, а не в папке данных."""
    try:
        import keyring

        from forlabs.cloud.auth import KEYRING_SESSION
        from forlabs.core.settings import use_windows_vault

        use_windows_vault()
        from forlabs.core.settings import KEYRING_SERVICE, KEYRING_USER

        for user in (KEYRING_USER, KEYRING_SESSION):
            try:
                keyring.delete_password(KEYRING_SERVICE, user)
            except Exception:
                pass
    except Exception:
        pass


def run(argv: list[str], *, target: Path, shell, dialogs: Dialogs, data: Path, is_running: Callable[[], bool],
        exe: Path | None, forget_keys: Callable[[bool], None] = _forget_saved_keys,
        spawn: Callable[[list[str]], None] = spawn_detached, pid: int | None = None) -> int:
    silent = "--silent" in argv
    if silent:  # тихий режим (CI, сценарии) ничего не показывает: итог — код выхода
        dialogs = SilentDialogs()
    if not silent and not dialogs.ask("Удалить ForLabs с этого компьютера?"):
        return 0
    if is_running():
        dialogs.tell("ForLabs сейчас открыт — закрой его (и вкладку в браузере, если открыта) и повтори удаление.",
                     warning=True)
        return 2
    if silent:
        remove_data = "--remove-data" in argv
    else:
        remove_data = dialogs.ask(
            f"Удалить и данные ForLabs на этом компьютере?\n\nЭто настройки, сохранённый вход и лабы, которые лежат "
            f"только здесь ({data}).\nЛабы в облачном аккаунте и готовые отчёты в папке сдачи останутся.",
            default_no=True)
    try:
        res = uninstall(target, shell=shell, data_dir=data, remove_data=remove_data, running_from=exe)
    except InstallError as e:
        dialogs.tell(str(e), warning=True)
        return 1
    if remove_data:
        forget_keys(True)
    tail = ("\n\n" + "\n".join(res.warnings)) if res.warnings else ""
    dialogs.tell("ForLabs удалён." + tail, warning=bool(res.warnings))
    if res.delete_later is not None:  # последним шагом: команда ждёт выхода этого процесса и удаляет папку
        spawn(delayed_delete_command(res.delete_later, pid if pid is not None else os.getpid()))
    return 0


def main(argv: list[str] | None = None) -> int:
    from forlabs.installer.winshell import WindowsShell
    from forlabs.version import frozen

    argv = sys.argv[1:] if argv is None else argv
    exe = program_exe() if frozen() else None
    dialogs = SilentDialogs() if "--silent" in argv else WindowsDialogs()
    return run(argv, target=target_from(argv, exe), shell=WindowsShell(), dialogs=dialogs, data=data_dir(),
               is_running=_forlabs_running, exe=exe)
