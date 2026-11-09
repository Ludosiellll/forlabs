"""Снимки окон программы лабы: запуск в песочнице, сценарий, проверка результата, снимок окна.

`WindowsShooter` — настоящий (только Windows): консоль (conhost, UTF-8, ввод с клавиатуры), оконные программы
(UI Automation; Swing/tkinter — нажатия клавиш), сайты (окно Edge с отдельным профилем). `FakeShooter` — для тестов
и других систем. Сценарий описан в плане проекта (`ShotPlan`): режим, шаги, ввод, адрес, ожидаемый текст."""

from __future__ import annotations

import os
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

EDGE_PATHS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]


@dataclass
class RunTarget:
    """Как запустить собранную программу: команда, папка, нужна ли консоль, адрес для сайта."""
    cmd: list[str]
    cwd: Path
    console: bool = False
    url: str | None = None  # сайт: file://… или http://127.0.0.1:порт/…
    server: list[str] | None = None  # сайт на PHP: команда встроенного сервера
    title: str | None = None  # заголовок консоли (путь к программе, как при обычном запуске)
    env: dict = field(default_factory=dict)


@dataclass
class ShotResult:
    ok: bool
    image: Path | None = None
    reason: str = ""  # почему снимка нет — студент увидит это в «Нужно от тебя»
    seen: str = ""  # текст, который был на экране (для проверки и отладки)


class Shooter(Protocol):
    def shoot(self, target: RunTarget, shot, out: Path) -> ShotResult: ...


def _get(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


class FakeShooter:
    """Для тестов: отдаёт картинку-заглушку или причину отказа, запоминает вызовы."""

    def __init__(self, fail: str | None = None):
        self.fail = fail
        self.calls: list[tuple[RunTarget, object]] = []

    def shoot(self, target: RunTarget, shot, out: Path) -> ShotResult:
        self.calls.append((target, shot))
        if self.fail:
            return ShotResult(False, reason=self.fail)
        from PIL import Image

        out.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 200), (240, 240, 240)).save(out)
        return ShotResult(True, image=out, seen=str(_get(shot, "expect", "") or ""))


def run_dir(base: Path | None = None) -> Path:
    """Папка запуска с меткой Low: программа с пониженными правами может писать только сюда (и в LocalLow)."""
    root = base or Path(os.environ.get("USERPROFILE", str(Path.home()))) / "AppData" / "LocalLow" / "ForLabs" / "runs"
    d = root / uuid.uuid4().hex[:10]
    d.mkdir(parents=True, exist_ok=True)
    return d


class WindowsShooter:
    """Настоящие снимки. Каждый запуск — своя копия программы в папке с меткой Low, процесс с уровнем Low в задании
    (лимит памяти, процессов; закрытие убивает всё)."""

    def __init__(self, timeout: float = 45.0, low: bool = True):
        if sys.platform != "win32":
            raise OSError("снимки окон программ делаются только в Windows")
        self.timeout, self.low = timeout, low

    def shoot(self, target: RunTarget, shot, out: Path) -> ShotResult:
        from forlabs.runner import winapi

        mode = _get(shot, "mode", "window")
        try:
            if mode == "browser":
                return self._browser(target, shot, out)
            work = self._stage(target)
            if mode == "console" or target.console:
                return self._console(target, work, shot, out)
            return self._window(target, work, shot, out)
        except winapi.SandboxError as e:  # type: ignore[attr-defined]
            return ShotResult(False, reason=str(e))
        except Exception as e:  # любой сбой — причиной для студента, а не падением этапа
            return ShotResult(False, reason=f"не получилось снять окно программы ({type(e).__name__}: {e})")

    # --- подготовка ---
    def _stage(self, target: RunTarget) -> Path:
        """Копия программы в папку с меткой Low: пишет она только туда, файлы студента недоступны для записи."""
        from forlabs.runner import winapi

        if not self.low:
            return target.cwd
        work = run_dir()
        dst = work / target.cwd.name
        shutil.copytree(target.cwd, dst, ignore=shutil.ignore_patterns("obj", ".git", ".vs", "node_modules"))
        winapi.low_label_dir(str(work))
        return dst

    def _remap(self, target: RunTarget, work: Path) -> list[str]:
        """Пути в команде — на копию в папке запуска."""
        src = str(target.cwd)
        return [a.replace(src, str(work)) if src in a else a for a in target.cmd]

    @staticmethod
    def _line(cmd: list[str]) -> str:
        from forlabs.runner.console import quote

        return " ".join(quote(a) for a in cmd)

    # --- консоль ---
    def _console(self, target: RunTarget, work: Path, shot, out: Path) -> ShotResult:
        from forlabs.runner import console, winapi

        cmd = self._remap(target, work)
        app = winapi.spawn(console.console_command(cmd), str(work), low=self.low, env=target.env or None)
        try:
            w = winapi.wait_window({app.pid}, self.timeout, lambda x: x.cls == "ConsoleWindowClass")
            if not w:
                return ShotResult(False, reason="окно консоли не появилось")
            time.sleep(1.2)
            pid = next((p for p in sorted(winapi.process_tree({app.pid})) if p != app.pid), app.pid)
            title = target.title or (cmd[0] if cmd else None)
            text = _get(shot, "input") or ""
            for step in _get(shot, "steps", None) or []:  # ввод по шагам: type/key/wait
                action, value = _get(step, "action"), _get(step, "value") or ""
                if action == "type":
                    text += value
                elif action == "key" and value.lower() == "enter":
                    text += "\n"
                elif action == "wait":
                    if text:
                        console.act(pid, text=text, title=title)
                        text = ""
                    time.sleep(min(float(value or 1), 10))
            res = console.act(pid, text=text or None, title=title)
            if not res.get("ok"):
                return ShotResult(False, reason=f"не получилось ввести данные в консоль ({res.get('error')})")
            time.sleep(1.5)
            seen = console.act(pid, read=True).get("screen", "")
            expect = _get(shot, "expect") or ""
            if expect and expect not in seen:
                return ShotResult(False, reason=f"программа вывела не то, что ожидалось («{expect}»)", seen=seen)
            img = winapi.capture(w.hwnd)
            if winapi.blank(img):
                return ShotResult(False, reason="снимок окна консоли получился пустым", seen=seen)
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out)
            return ShotResult(True, image=out, seen=seen)
        finally:
            app.close()

    # --- оконная программа ---
    def _window(self, target: RunTarget, work: Path, shot, out: Path) -> ShotResult:
        from forlabs.runner import uia, winapi

        cmd = self._remap(target, work)
        app = winapi.spawn(self._line(cmd), str(work), low=self.low, env=target.env or None)
        try:
            w = winapi.wait_window({app.pid}, self.timeout)
            if not w:
                code = app.wait(0)
                return ShotResult(False, reason="окно программы не появилось" +
                                  (f" (программа завершилась с кодом {code})" if code is not None else ""))
            time.sleep(1.0)
            before = {x.hwnd for x in winapi.windows_of(winapi.process_tree({app.pid}), owned=True)}
            ui_steps = []
            for step in _get(shot, "steps", None) or []:
                action = _get(step, "action")
                target_name, value = _get(step, "target") or "", _get(step, "value") or ""
                if action in ("set", "select", "click", "check", "uncheck", "wait"):
                    ui_steps.append({"action": action, "target": target_name, "value": value})
                    continue
                if ui_steps:
                    uia.perform(w.hwnd, ui_steps)
                    ui_steps = []
                if action == "type":
                    winapi.type_text(w.hwnd, value)
                elif action == "key":
                    winapi.press(w.hwnd, value)
            if ui_steps:
                uia.perform(w.hwnd, ui_steps)
            time.sleep(0.8)
            wins = winapi.windows_of(winapi.process_tree({app.pid}), owned=True)
            fresh = [x for x in wins if x.hwnd not in before]  # MessageBox или новое окно после нажатия
            capture = _get(shot, "capture") or ("last" if fresh else "main")
            hwnd = fresh[-1].hwnd if capture == "last" and fresh else w.hwnd
            seen = ""
            try:
                seen = " | ".join(uia.texts(uia.root(hwnd)))
            except Exception:
                pass
            expect = _get(shot, "expect") or ""
            if expect and seen and expect not in seen:
                return ShotResult(False, reason=f"в окне нет ожидаемого «{expect}»", seen=seen)
            img = winapi.capture(hwnd)
            if winapi.blank(img):
                return ShotResult(False, reason="снимок окна получился пустым", seen=seen)
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out)
            return ShotResult(True, image=out, seen=seen)
        finally:
            app.close()

    # --- сайт ---
    def _browser(self, target: RunTarget, shot, out: Path) -> ShotResult:
        import subprocess

        from forlabs.runner import winapi

        edge = next((p for p in EDGE_PATHS if Path(p).exists()), None)
        if not edge:
            return ShotResult(False, reason="не найден браузер Microsoft Edge")
        server = None
        work = self._stage(target) if target.server else target.cwd
        if target.server:  # PHP: встроенный сервер — с пониженными правами (это код лабы)
            server = winapi.spawn(self._line(self._remap(target, work)[:0] + [a.replace(str(target.cwd), str(work))
                                                                        for a in target.server]),
                                  str(work), low=self.low, hidden=True)
            time.sleep(1.5)
        url = _get(shot, "url") or target.url or ""
        if url and not url.startswith(("http://127.0.0.1", "http://localhost", "file:")):
            rel = url.lstrip("/")
            url = (target.url.rstrip("/") + "/" + rel) if target.url and target.url.startswith("http") \
                else (work / rel).as_uri()
        profile = run_dir() / "edge"
        cmd = (f'"{edge}" --user-data-dir="{profile}" --no-first-run --no-default-browser-check '
               f'--disable-features=msEdgeFirstRunExperience,msImplicitSignin --window-size=1200,800 --new-window "{url}"')
        app = winapi.spawn(cmd, str(work), low=False, max_processes=64, memory_mb=4096)
        try:
            w = winapi.wait_window({app.pid}, self.timeout, lambda x: "Edge" in x.title)
            if not w:
                return ShotResult(False, reason="окно браузера не появилось")
            time.sleep(2.5)
            img = winapi.capture(w.hwnd)
            if winapi.blank(img):
                return ShotResult(False, reason="снимок окна браузера получился пустым")
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out)
            return ShotResult(True, image=out, seen=w.title)
        finally:
            app.close()
            if server is not None:
                server.close()
            subprocess.run(["taskkill", "/F", "/T", "/FI", f"WINDOWTITLE eq {profile}"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
