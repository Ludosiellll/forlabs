"""Снимки окон программы лабы: запуск в песочнице, сценарий, проверка результата, снимок окна.

`WindowsShooter` — настоящий (только Windows): консоль (conhost, UTF-8, ввод с клавиатуры), оконные программы
(UI Automation; Swing/tkinter — нажатия клавиш), сайты (окно Edge с отдельным профилем). `FakeShooter` — для тестов
и других систем. Сценарий описан в плане проекта (`ShotPlan`): режим, шаги, ввод, адрес, ожидаемый текст."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from forlabs.core.fs import remove_tree

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
    network: bool = False  # программе нужен интернет: только в песочнице (файлы пользователя ей закрыты)


@dataclass
class ShotResult:
    ok: bool
    image: Path | None = None
    reason: str = ""  # почему снимка нет — студент увидит это в «Нужно от тебя»
    seen: str = ""  # текст, который был на экране (для проверки и отладки)
    sandbox: str = ""  # как работала программа: container (AppContainer), low (уровень Low), "" — без песочницы


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


# мышь и клавиатура одни на компьютер: снимки окон (этап «Проект») и агент в программах — по очереди, даже из
# разных лаб
DESKTOP = threading.Lock()

_URL_SAFE = "/?=&#.-_~%+,;:@"


def _encode(part: str) -> str:
    """Кавычки, пробелы и прочее — %XX: адрес остаётся одним словом командной строки браузера."""
    from urllib.parse import quote

    return quote(part, safe=_URL_SAFE)


def url_problem(page: str) -> str | None:
    """Почему адрес страницы из плана нельзя открывать: только страницы проекта или сервер на 127.0.0.1."""
    import re

    p = (page or "").strip()
    if not p:
        return None
    if any(c in p for c in '"<>\\') or re.search(r"\s", p) or p.startswith("-"):
        return "в адресе страницы кавычки, пробелы или «-» в начале"
    if re.match(r"[a-z][a-z0-9+.-]*:", p, re.I):
        if not re.match(r"https?://(?:127\.0\.0\.1|localhost)(?::\d+)?(?:/|$)", p, re.I):
            return "страница вне проекта (нужен путь файла проекта или сервер 127.0.0.1)"
        return None
    if re.match(r"[A-Za-z]:", p) or ".." in p.replace("\\", "/").split("/"):
        return "путь страницы выходит за папку проекта"
    return None


def browser_url(page: str, base: str, work: Path) -> str:
    """Адрес страницы: «order.php» у сайта на PHP — от корня сервера (а не «index.php/order.php»); у сайта из файлов —
    рядом с главной страницей. Всё вне проекта — ValueError; опасные символы закодированы."""
    from urllib.parse import urljoin

    page = (page or "").strip()
    why = url_problem(page)
    if why:
        raise ValueError(why)
    if page.lower().startswith(("http://", "https://")):
        return _encode(page)
    if not page:
        return base or (work / "index.html").as_uri()
    rel = _encode(page.lstrip("/"))
    if base.startswith("http://"):
        return urljoin(base, "/" + rel)
    if base.startswith("file:"):
        return urljoin(base, rel)
    return (work / page.lstrip("/")).as_uri()


def edge_command(edge: str, profile: Path, url: str) -> str:
    """Командная строка Edge: аргументы по правилам Windows, после «--» — только адрес (ключом он не станет)."""
    import subprocess

    return subprocess.list2cmdline([edge, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                                    "--disable-features=msEdgeFirstRunExperience,msImplicitSignin",
                                    "--window-size=1200,800", "--new-window", "--", url])


def typed_text(shot) -> str:
    """Что ForLabs сам ввёл в программу: ввод консоли и значения шагов (набор, поля формы, выбор в списке)."""
    parts = [_get(shot, "input") or ""]
    parts += [_get(st, "value") or "" for st in _get(shot, "steps", None) or []
              if _get(st, "action") in ("type", "set", "select")]
    return "\n".join(parts)


def shown(expect: str, seen: str, typed: str = "") -> bool:
    """Ожидаемое видно как результат, а не только как набранный ForLabs текст (эхо ввода в консоли, поле формы):
    на экране его больше, чем во вводе. Пробелы и переносы не считаются — длинная строка в консоли переносится."""
    def squash(text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    e = squash(expect)
    return not e or squash(seen).count(e) > squash(typed).count(e)


def runs_root() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "AppData" / "LocalLow" / "ForLabs" / "runs"


def run_dir(base: Path | None = None) -> Path:
    """Папка запуска с меткой Low: программа с пониженными правами может писать только сюда (и в LocalLow).
    Заодно убираются папки прошлых запусков старше суток (если что-то не удалилось сразу)."""
    root = base or runs_root()
    try:
        cutoff = time.time() - 86400
        for old in root.iterdir() if root.is_dir() else []:
            if old.is_dir() and old.stat().st_mtime < cutoff:
                remove_tree(old)
    except OSError:
        pass
    d = root / uuid.uuid4().hex[:10]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _drop_run(path: Path | None) -> None:
    """Папка запуска больше не нужна (программа закрыта): копия проекта и профиль браузера — удалить."""
    if path is None:
        return
    root = runs_root()
    for p in [path, *path.parents]:
        if p.parent == root:
            remove_tree(p, tries=5, pause=0.4)  # процессы закрываются не мгновенно — файлы могут быть ещё заняты
            return


def _texts_with_timeout(hwnd: int, timeout: float = 8.0) -> str:
    """Текст окна через UI Automation, но не дольше timeout: пока открыт модальный диалог, UIA может ждать."""
    import threading

    from forlabs.runner import uia

    out: list[str] = []

    def go():
        try:
            out.extend(uia.texts(uia.root(hwnd)))
        except Exception:
            pass

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(timeout)
    return " | ".join(out)


LAUNCH_PROBLEMS = ("окно программы не появилось", "окно консоли не появилось", "программа завершилась сразу",
                   "песочница:", "не получилось снять окно программы", "не получилось ввести данные в консоль")


def _launch_problem(reason: str) -> bool:
    """Программа не запустилась или сразу упала — в песочнице ей могло не хватить доступа: повторить без неё."""
    return any(reason.startswith(p) or p in reason for p in LAUNCH_PROBLEMS)


def _tool_dirs(cmd: list[str], work: Path) -> list[Path]:
    """Папки инструментов, которые программе нужно читать в песочнице: exe из команды (python, java, node) и всё,
    что нашёл languages.detect() (DLL MinGW, JDK), плюс программы, поставленные ForLabs."""
    from forlabs.runner.appcontainer import tool_dirs

    return tool_dirs(cmd, work)


class WindowsShooter:
    """Настоящие снимки. Каждый запуск — своя копия программы в папке с меткой Low, процесс с уровнем Low в задании
    (лимит памяти, процессов; закрытие убивает всё)."""

    def __init__(self, timeout: float = 45.0, low: bool = True, container: bool = True):
        if sys.platform != "win32":
            raise OSError("снимки окон программ делаются только в Windows")
        self.timeout, self.low = timeout, low
        self.container = container and low  # AppContainer: ни файлов пользователя, ни сети
        self._in_container = False
        self._network = False

    def shoot(self, target: RunTarget, shot, out: Path) -> ShotResult:
        if not DESKTOP.acquire(timeout=900):  # агент другой лабы ещё работает в программе
            return ShotResult(False, reason="мышь и клавиатура заняты: ForLabs снимает окна в другой лабе")
        try:
            return self._shoot(target, shot, out)
        finally:
            DESKTOP.release()

    def _shoot(self, target: RunTarget, shot, out: Path) -> ShotResult:
        mode = _get(shot, "mode", "window")
        try:
            if mode == "browser":
                return self._browser(target, shot, out)
            run = self._console if (mode == "console" or target.console) else self._window
            self._network = bool(target.network)
            if self.container:
                self._in_container = True
                try:
                    res = run(target, self._stage(target), shot, out)
                except Exception as e:  # noqa: BLE001 — песочница не создалась: как раньше, уровень Low
                    res = ShotResult(False, reason=f"песочница: {type(e).__name__}: {e}")
                finally:
                    self._in_container = False
                if res.ok or not _launch_problem(res.reason):
                    res.sandbox = "container"
                    return res
                if target.network:  # без песочницы сетевая программа могла бы отправить файлы пользователя
                    return ShotResult(False, reason="программе нужен интернет, а запустить её в песочнице не вышло "
                                                    f"({res.reason}); без песочницы ForLabs такие программы не запускает",
                                      sandbox="container")
                logging.getLogger("forlabs").info("в песочнице не вышло (%s) — снимаю с уровнем Low", res.reason)
            if target.network and self.low:
                return ShotResult(False, reason="программе нужен интернет: ForLabs запускает такие программы только "
                                                "в песочнице, а она здесь недоступна")
            res = run(target, self._stage(target), shot, out)
            res.sandbox = "low" if self.low else ""
            return res
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

    def _spawn(self, line: str, work: Path, cmd: list[str], env: dict | None):
        """Запуск программы: в AppContainer (сейчас идёт попытка в песочнице) или с уровнем Low."""
        from forlabs.runner import winapi

        if not self._in_container:
            return winapi.spawn(line, str(work), low=self.low, env=env or None)
        from forlabs.runner import appcontainer

        sid = appcontainer.sid()
        root = work.parent  # папка запуска: копия проекта — пишет программа только сюда
        if not appcontainer.grant(root, sid, appcontainer.FULL):
            raise appcontainer.ContainerError("папка запуска не открылась для песочницы")
        tmp = root / ".tmp"
        tmp.mkdir(exist_ok=True)
        appcontainer.ensure_read(_tool_dirs(cmd, work))
        extra = {"TEMP": str(tmp), "TMP": str(tmp), "PYTHONNOUSERSITE": "1"}
        return appcontainer.spawn(line, str(work), sid, env={**(env or {}), **extra}, internet=self._network)

    def _console_host(self, cmd: list[str], work: Path, target: RunTarget):
        """Консоль принадлежит помощнику ForLabs (ввод для снимка проходит), программа — в песочнице в этой консоли."""
        from forlabs.runner import appcontainer, console, winapi

        sid = appcontainer.sid()
        root = work.parent
        if not appcontainer.grant(root, sid, appcontainer.FULL):
            raise appcontainer.ContainerError("папка запуска не открылась для песочницы")
        tmp = root / ".tmp"
        tmp.mkdir(exist_ok=True)
        appcontainer.ensure_read(_tool_dirs(cmd, work))
        # без cmd.exe: в песочнице он отвечает «Access is denied»; кодировку ставит и окно держит сам помощник
        job = {"cmd": self._line(cmd), "cwd": str(work), "sid": sid,
               "env": {**(target.env or {}), "TEMP": str(tmp), "TMP": str(tmp), "PYTHONNOUSERSITE": "1"},
               "internet": self._network, "title": target.title or (cmd[0] if cmd else "")}
        job_file = root / "console-host.json"
        job_file.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        # conhost явно: иначе Windows 11 откроет консоль во вкладке Windows Terminal, и окно не найти
        return winapi.spawn(f'"{console.CONHOST}" ' + self._line(console.host_cmd() + [str(job_file)]), str(work),
                            low=False, new_console=True)

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
        hosted = self._in_container  # консоль у ForLabs, в ней — программа в песочнице
        if hosted:
            app = self._console_host(cmd, work, target)
        else:
            app = self._spawn(console.console_command(cmd, work), work, cmd, target.env)
        try:
            w = winapi.wait_window({app.pid}, self.timeout,
                                   lambda x: x.cls == "ConsoleWindowClass" and min(winapi.size_of(x.hwnd)) > 40)
            if not w:
                err = work.parent / "console-host.err"
                return ShotResult(False, reason="окно консоли не появилось" + (
                    f" (песочница: {err.read_text(encoding='utf-8')[:400]})" if hosted and err.exists() else ""))
            time.sleep(1.2)
            if hosted and (err := (work.parent / "console-host.err")).exists():
                return ShotResult(False, reason="песочница: " + err.read_text(encoding="utf-8")[:300])
            others = [p for p in sorted(winapi.process_tree({app.pid})) if p != app.pid]
            pid = ([app.pid] + others) if hosted else (others or [app.pid])  # хозяин консоли — первым
            title = target.title or (cmd[0] if cmd else None)
            alive = (any(n not in winapi.HOSTS for p, n in winapi.process_names(set(others)).items())
                     if hosted else winapi.program_alive(app.pid))
            if not alive:  # программа уже завершилась (ошибка запуска, нет подключения…)
                early = console.act(pid, read=True).get("screen", "").strip()
                if _get(shot, "input") or any(_get(s, "action") in ("type", "key") for s in _get(shot, "steps", []) or []):
                    lines = [ln for ln in early.splitlines() if ln.strip()]
                    head = " ".join(lines[:3])
                    last = (head if len(lines) <= 3 else f"{head} … {lines[-1]}")[:400] or "окно пустое"
                    return ShotResult(False, reason=f"программа завершилась сразу после запуска: {last}", seen=early)
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
            pid = [p for p in sorted(winapi.process_tree({app.pid})) if p != app.pid] or [app.pid]
            read = console.act(pid, read=True)
            seen = read.get("screen", "")
            try:
                img = winapi.capture(w.hwnd)
            except OSError as e:
                return ShotResult(False, reason=f"окно консоли не снялось ({e}; окна: {winapi.describe({app.pid})})",
                                  seen=seen)
            expect = _get(shot, "expect") or ""
            if expect and not shown(expect, seen, typed_text(shot)):
                img.save(out.with_name(out.stem + ".fail.png"))  # для разбора: что было на экране
                why = read.get("error") or (seen.strip().splitlines() or ["пусто"])[-1][:120]
                return ShotResult(False, reason=f"программа вывела не то, что ожидалось («{expect}»; на экране: {why})",
                                  seen=seen)
            if winapi.blank(img):
                return ShotResult(False, reason="снимок окна консоли получился пустым", seen=seen)
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out)
            return ShotResult(True, image=out, seen=seen)
        finally:
            app.close()
            if self.low:
                _drop_run(work)

    # --- оконная программа ---
    def _window(self, target: RunTarget, work: Path, shot, out: Path) -> ShotResult:
        from forlabs.runner import uia, winapi

        cmd = self._remap(target, work)
        app = self._spawn(self._line(cmd), work, cmd, target.env)
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
            seen = " | ".join(winapi.child_texts(hwnd)) if fresh and hwnd != w.hwnd else _texts_with_timeout(hwnd)
            expect = _get(shot, "expect") or ""
            if expect and seen and not shown(expect, seen, typed_text(shot)):
                return ShotResult(False, reason=f"в окне нет ожидаемого «{expect}»", seen=seen)
            img = winapi.capture(hwnd)
            if winapi.blank(img):
                return ShotResult(False, reason="снимок окна получился пустым", seen=seen)
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out)
            return ShotResult(True, image=out, seen=seen)
        finally:
            app.close()
            if self.low:
                _drop_run(work)

    # --- сайт ---
    def _browser(self, target: RunTarget, shot, out: Path) -> ShotResult:

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
        try:
            url = browser_url(_get(shot, "url") or "", target.url or "", work)
        except ValueError as e:
            if server is not None:
                server.close()
            return ShotResult(False, reason=str(e))
        profile_dir = run_dir()
        profile = profile_dir / "edge"
        app = winapi.spawn(edge_command(edge, profile, url), str(work), low=False, max_processes=64, memory_mb=4096)
        try:
            w = winapi.wait_window({app.pid}, self.timeout, lambda x: "Edge" in x.title)
            if not w:
                return ShotResult(False, reason="окно браузера не появилось")
            time.sleep(2.5)
            steps = _get(shot, "steps", None) or []
            if steps:  # форма на странице: поля по id из HTML, кнопки по тексту; клавиши — в окно браузера
                from forlabs.runner import uia

                ui = []
                for step in steps:
                    action, name, value = _get(step, "action"), _get(step, "target") or "", _get(step, "value") or ""
                    if action in ("set", "select", "click", "check", "uncheck", "wait"):
                        ui.append({"action": action, "target": name, "value": value})
                        continue
                    if ui:
                        uia.perform(w.hwnd, ui, timeout=8)
                        ui = []
                    if action == "type":
                        winapi.type_text(w.hwnd, value)
                    elif action == "key":
                        winapi.press(w.hwnd, value)
                if ui:
                    uia.perform(w.hwnd, ui, timeout=8)
                time.sleep(2.0)  # страница после отправки формы
            seen = _texts_with_timeout(w.hwnd) if (steps or _get(shot, "expect")) else w.title
            expect = _get(shot, "expect") or ""
            if expect and seen and not shown(expect, seen, typed_text(shot)):
                return ShotResult(False, reason=f"на странице нет ожидаемого «{expect}»", seen=seen)
            img = winapi.capture(w.hwnd)
            if winapi.blank(img):
                return ShotResult(False, reason="снимок окна браузера получился пустым")
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out)
            return ShotResult(True, image=out, seen=seen)
        finally:
            app.close()
            if server is not None:
                server.close()
            _drop_run(profile_dir)
            if target.server and self.low:
                _drop_run(work)
