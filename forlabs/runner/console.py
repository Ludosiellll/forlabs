"""Окно консоли для снимка: запуск в настоящем conhost в UTF-8, ввод с клавиатуры, заголовок, чтение текста окна.

Ввод и чтение делает отдельный короткий процесс-помощник: он подключается к консоли программы (AttachConsole),
пишет нажатия клавиш (WriteConsoleInput) и читает буфер экрана. Сам ForLabs к чужой консоли не подключается —
у окна приложения консоли нет, а подключение сломало бы его вывод."""

from __future__ import annotations

import json
import subprocess
import sys

CONHOST = r"C:\Windows\System32\conhost.exe"


def quote(arg: str) -> str:
    """Аргумент для командной строки cmd.exe (пробелы, кавычки)."""
    if arg and not any(c in arg for c in ' \t"&|<>^()%!'):
        return arg
    return '"' + arg.replace('"', '""') + '"'


def console_command(cmd: list[str]) -> str:
    """conhost (а не Windows Terminal: окно должно принадлежать процессу программы) → cmd с UTF-8 → программа;
    после выхода программы окно держит `timeout /nobreak`: ничего не печатает и не съедает нажатия клавиш
    (`pause` съел бы набранный ввод, если программа завершилась раньше, и окно закрылось бы)."""
    line = " ".join(quote(a) for a in cmd)
    return f'"{CONHOST}" cmd.exe /d /s /c "chcp 65001>nul & {line} & timeout /t 86400 /nobreak >nul"'


def helper_cmd() -> list[str]:
    """Как запустить помощника: в установленной программе — сам ForLabs.exe, иначе — этот модуль."""
    from forlabs.version import frozen

    if frozen():
        return [sys.executable, "--console-helper"]
    return [sys.executable, "-m", "forlabs.runner.console"]


def act(pid: int | list[int], *, text: str | None = None, title: str | None = None, read: bool = False,
        timeout: float = 20.0) -> dict:
    """Ввод, заголовок и/или чтение экрана консоли. pid — процесс этой консоли или список: подключение к первому
    живому (сама программа могла уже завершиться — окно держит cmd). Возвращает {"ok", "screen", "error"}."""
    import tempfile
    from pathlib import Path

    # задание и ответ — файлами: у собранного ForLabs.exe (оконная программа) нет надёжных stdin/stdout
    with tempfile.TemporaryDirectory(prefix="forlabs-con-") as tmp:
        job_file = Path(tmp) / "job.json"
        pids = [pid] if isinstance(pid, int) else list(pid)
        job_file.write_text(json.dumps({"pids": pids, "text": text, "title": title, "read": read},
                                       ensure_ascii=False), encoding="utf-8")
        try:
            subprocess.run([*helper_cmd(), str(job_file)], timeout=timeout, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "помощник консоли не ответил"}
        out = job_file.with_suffix(".out")
        try:
            return json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"ok": False, "error": "помощник консоли не дал ответа"}


def _helper(job: dict) -> dict:
    import time

    import win32con
    import win32console
    import win32file

    win32console.FreeConsole()
    pids = job.get("pids") or [job["pid"]]
    last = None
    for p in pids:  # первый живой процесс этой консоли
        try:
            win32console.AttachConsole(int(p))
            break
        except Exception as e:  # noqa: BLE001 - процесс уже завершился или без консоли
            last = e
    else:
        raise RuntimeError(f"не подключиться к консоли ({last})")
    try:
        if job.get("title"):
            win32console.SetConsoleTitle(job["title"])
        if job.get("text"):
            h = win32file.CreateFile("CONIN$", win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                                     win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE, None,
                                     win32con.OPEN_EXISTING, 0, None)
            con = win32console.PyConsoleScreenBufferType(h)
            records = []
            for ch in job["text"]:
                for down in (True, False):
                    r = win32console.PyINPUT_RECORDType(win32console.KEY_EVENT)
                    r.KeyDown, r.RepeatCount = down, 1
                    if ch == "\n":
                        r.Char, r.VirtualKeyCode = "\r", 13
                    else:
                        r.Char = ch
                    records.append(r)
            con.WriteConsoleInput(records)
            time.sleep(0.2)
        screen = ""
        if job.get("read"):
            h = win32file.CreateFile("CONOUT$", win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                                     win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE, None,
                                     win32con.OPEN_EXISTING, 0, None)
            out = win32console.PyConsoleScreenBufferType(h)
            info = out.GetConsoleScreenBufferInfo()
            width = info["Size"].X
            rows = info["CursorPosition"].Y + 1
            text = out.ReadConsoleOutputCharacter(width * rows, win32console.PyCOORDType(0, 0))
            screen = "\n".join(text[i:i + width].rstrip() for i in range(0, len(text), width)).rstrip()
        return {"ok": True, "screen": screen}
    finally:
        win32console.FreeConsole()


def main(argv: list[str] | None = None) -> int:
    """ForLabs.exe --console-helper <job.json> (или python -m forlabs.runner.console <job.json>)."""
    from pathlib import Path

    argv = sys.argv[1:] if argv is None else argv
    job_file = Path([a for a in argv if not a.startswith("--")][-1])
    try:
        res = _helper(json.loads(job_file.read_text(encoding="utf-8")))
    except Exception as e:  # noqa: BLE001 - любой сбой — ответом, а не трассировкой
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    job_file.with_suffix(".out").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
