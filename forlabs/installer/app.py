"""Окно установщика (pywebview + WebView2) в стиле Windows 11, тихий режим и запасной путь без окна."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from forlabs.installer.core import (
    APP_NAME,
    COMPONENTS,
    FINAL,
    InstallError,
    Progress,
    check_target,
    components,
    default_target,
    install,
    installed,
)
from forlabs.version import __version__

log = logging.getLogger("forlabs.setup")
WEB = Path(__file__).parent / "web"
WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _mb(n: int) -> str:
    return f"{max(1, round(n / 2**20))} МБ" if n else ""


def launch(exe: Path) -> None:
    """Запустить ForLabs отдельно от установщика: окно установщика закроется, а ForLabs останется."""
    if sys.platform != "win32":
        subprocess.Popen([str(exe)], cwd=str(exe.parent), start_new_session=True)  # noqa: S603
        return
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:  # вне задания (job) установщика, иначе Windows закроет ForLabs вместе с ним
        subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True,  # noqa: S603
                         creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        subprocess.Popen(["explorer.exe", str(exe)], close_fds=True, creationflags=flags)  # noqa: S603,S607


class SetupApi:
    """Методы, которые вызывает страница установщика (pywebview.api.*). Каждый — короткий; установка — в потоке."""

    def __init__(self, payload: Path, shell, *, launcher: Callable[[Path], None] = launch,
                 folder_picker: Callable[[str], str | None] | None = None):
        self._payload, self._shell = Path(payload), shell
        self._launcher, self._picker = launcher, folder_picker
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict = {"state": "idle"}
        self._result = None
        self._window = None

    def attach(self, window) -> None:
        self._window = window

    def defaults(self) -> dict:
        try:
            comps = components(self._payload)
        except InstallError as e:
            return {"error": str(e)}
        inst = installed(self._shell)
        target = inst.location if inst else default_target()
        desktop = (self._shell.desktop_dir() / f"{APP_NAME}.lnk").exists() if inst else True
        if inst and not desktop:
            desktop = False
        total = sum(c.size for c in comps)
        return {"version": __version__, "installed": inst.version if inst else None, "target": str(target),
                "desktop": desktop, "size_mb": max(1, round(total / 2**20)),
                "components": [{"name": c.name, "size": _mb(c.size)} for c in comps]}

    def browse(self, current: str) -> str | None:
        chosen = self._picker(current) if self._picker else None
        if not chosen:
            return None
        p = Path(chosen)
        return str(p if p.name.lower() == APP_NAME.lower() else p / APP_NAME)

    def start(self, target: str, desktop: bool) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"ok": False, "error": "Установка уже идёт."}
            try:
                t = check_target(target)
            except InstallError as e:
                return {"ok": False, "error": str(e)}
            self._cancel.clear()
            self._state = {"state": "running", "prep": True, "percent": 0, "component": 0,
                           "fractions": [0] * len(COMPONENTS)}
            self._thread = threading.Thread(target=self._work, args=(t, bool(desktop)), daemon=True)
            self._thread.start()
            return {"ok": True}

    def _on_progress(self, p: Progress) -> None:
        fr = [1.0 if i < p.component else 0.0 for i in range(len(COMPONENTS))]
        fr[p.component] = (p.component_done / p.component_total) if p.component_total else 0.0
        pct = 100 * p.done / p.total if p.total else 0
        if not (p.component == FINAL and fr[FINAL] >= 1):
            pct = min(pct, 99)
        self._state = {"state": "running", "prep": False, "percent": round(pct, 1), "component": p.component,
                       "fractions": [round(f, 3) for f in fr]}

    def _work(self, target: Path, desktop: bool) -> None:
        try:
            self._result = install(self._payload, target, shell=self._shell, desktop=desktop,
                                   progress=self._on_progress, cancelled=self._cancel.is_set)
            self._state = {"state": "done", "percent": 100, "component": FINAL, "fractions": [1] * len(COMPONENTS),
                           "warnings": self._result.warnings, "target": str(target)}
            log.info("Установлено в %s (%s файлов)", target, self._result.files)
        except InstallError as e:
            log.warning("Установка не удалась: %s", e)
            self._state = {"state": "error", "error": str(e)}
        except Exception as e:  # непредвиденное — в журнал и понятной строкой в окно
            log.exception("Сбой установки")
            self._state = {"state": "error", "error": f"Непредвиденная ошибка: {e}. Журнал: {_log_path()}"}

    def poll(self) -> dict:
        return dict(self._state)

    def cancel(self) -> None:
        self._cancel.set()

    def finish(self, launch_app: bool) -> dict:
        if launch_app and self._result is not None:
            try:
                self._launcher(self._result.exe)
            except OSError as e:
                return {"ok": False, "error": f"Не удалось запустить ForLabs: {e}. Открой его из меню «Пуск»."}
        self.close()
        return {"ok": True}

    def minimize(self) -> None:
        if self._window is not None:
            self._window.minimize()

    def close(self) -> None:
        if self._window is not None:
            self._window.destroy()


def _log_path() -> Path:
    import tempfile

    return Path(tempfile.gettempdir()) / "ForLabsSetup.log"


def _arg(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def silent(payload: Path, argv: list[str], *, shell=None) -> int:
    """ForLabsSetup.exe --silent [--target папка] [--no-desktop] [--launch] [--log файл] — без окна (CI, сценарии)."""
    if shell is None:
        from forlabs.installer.winshell import WindowsShell

        shell = WindowsShell()
    out = Path(_arg(argv, "--log") or _log_path())
    try:
        inst = installed(shell)
        target = Path(_arg(argv, "--target") or (inst.location if inst else default_target()))
        res = install(payload, target, shell=shell, desktop="--no-desktop" not in argv)
        out.write_text(f"ForLabs {__version__} установлен в {res.target} ({res.files} файлов)\n"
                       + "".join(w + "\n" for w in res.warnings), encoding="utf-8")
        if "--launch" in argv:
            launch(res.exe)
        return 0
    except InstallError as e:
        out.write_text(f"ОШИБКА: {e}\n", encoding="utf-8")
        return 1
    except Exception as e:  # непредвиденное — тоже в журнал и кодом выхода, а не молчаливым падением
        log.exception("Сбой тихой установки")
        out.write_text(f"ОШИБКА: {type(e).__name__}: {e}\n", encoding="utf-8")
        return 1


def webview2_ready() -> bool:
    import winreg

    for root, sub in ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
                      (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
                      (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}")):
        try:
            with winreg.OpenKey(root, sub) as k:
                pv = str(winreg.QueryValueEx(k, "pv")[0] or "")
                if pv and pv != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def _offer_webview2(bundle: Path) -> bool:
    from forlabs.installer import winshell as w

    setup = bundle / "MicrosoftEdgeWebview2Setup.exe"
    if not setup.exists():
        return False
    text = ("Для окна установщика и самого ForLabs нужен компонент Microsoft Edge WebView2 (бесплатный, от Microsoft; "
            "в Windows 11 он уже есть).\n\nУстановить его сейчас? Нужен интернет, это займёт около минуты.")
    if w.message_box(text, "Установка ForLabs", w.MB_YESNO | w.MB_ICONQUESTION) != w.IDYES:
        return False
    try:
        subprocess.run([str(setup), "/silent", "/install"], timeout=600, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        log.exception("WebView2 не установился")
    return webview2_ready()


def _plain_install(payload: Path, shell) -> int:
    """Без окна (нет WebView2): вопрос, установка с настройками по умолчанию, итог."""
    from forlabs.installer import winshell as w

    inst = installed(shell)
    target = inst.location if inst else default_target()
    if w.message_box(f"Установить ForLabs {__version__} в папку\n{target}?\n\nЯрлыки появятся в меню «Пуск» и на "
                     "рабочем столе.", "Установка ForLabs", w.MB_YESNO | w.MB_ICONQUESTION) != w.IDYES:
        return 0
    try:
        res = install(payload, target, shell=shell, desktop=True)
    except InstallError as e:
        w.message_box(str(e), "Установка ForLabs", w.MB_OK | w.MB_ICONWARNING)
        return 1
    w.message_box("ForLabs установлен. Сейчас он откроется (в браузере, пока нет WebView2).", "Установка ForLabs",
                  w.MB_OK | w.MB_ICONINFO)
    launch(res.exe)
    return 0


def _round_corners(window) -> None:
    """Windows 11: скруглённые углы и тонкая рамка у окна без системного заголовка."""
    try:
        import ctypes

        hwnd = window.native.Handle.ToInt64()
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        border = ctypes.c_uint(0x002F2F2F)  # COLORREF 0x00BBGGRR — #2f2f2f
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), 4)
    except Exception:  # Windows 10 или другой вид окна — без скругления
        log.debug("Скругление углов недоступно", exc_info=True)


def run(payload: Path, bundle: Path, argv: list[str] | None = None) -> int:
    logging.basicConfig(filename=str(_log_path()), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    argv = sys.argv[1:] if argv is None else argv
    if "--silent" in argv:
        return silent(payload, argv)
    from forlabs.installer.winshell import WindowsShell

    shell = WindowsShell()
    try:
        import webview
    except ImportError:
        return _plain_install(payload, shell)
    if sys.platform == "win32" and not webview2_ready() and not _offer_webview2(bundle):
        return _plain_install(payload, shell)

    def pick(initial: str) -> str | None:
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or webview.FOLDER_DIALOG
        start_dir = initial if initial and os.path.isdir(initial) else str(Path(initial or "").parent)
        res = window.create_file_dialog(kind, directory=start_dir if os.path.isdir(start_dir) else "")
        return res[0] if res else None

    api = SetupApi(payload, shell, folder_picker=pick)
    window = webview.create_window("Установка ForLabs", html=(WEB / "setup.html").read_text("utf-8"), js_api=api,
                                   width=760, height=480, resizable=False, frameless=True, easy_drag=False,
                                   background_color="#202020")
    api.attach(window)
    window.events.shown += lambda: _round_corners(window)
    try:
        webview.start(gui="edgechromium" if sys.platform == "win32" else None, private_mode=True)
    except Exception:
        log.exception("Окно установщика не открылось")
        return _plain_install(payload, shell)
    return 0
