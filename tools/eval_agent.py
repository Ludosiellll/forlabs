"""Живая проверка агента «ИИ работает в программе» с настоящей моделью на настоящих программах (Linux, Xvfb).

Руки и глаза здесь — xdotool и снимок экрана X11 (без списка элементов: модель работает только по картинке, как
с программами, которые не отдают элементы, например 1С или Packet Tracer). Сам цикл, правила и проверка снимка —
те же, что в ForLabs (forlabs.agent.core).

Запуск: FORLABS_API_KEY=… FORLABS_BASE_URL=… FORLABS_MODEL=… python tools/eval_agent.py [имя сценария …]
Итоги и снимки — в папке --out (по умолчанию build/eval-agent)."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import ImageGrab  # noqa: E402

from forlabs.agent import core  # noqa: E402
from forlabs.agent.core import Action, Observation, Stopped, Task  # noqa: E402
from forlabs.core.llm import LLM  # noqa: E402
from forlabs.core.settings import Settings  # noqa: E402

XKEYS = {"enter": "Return", "esc": "Escape", "backspace": "BackSpace", "delete": "Delete", "tab": "Tab",
         "space": "space", "up": "Up", "down": "Down", "left": "Left", "right": "Right", "home": "Home", "end": "End",
         "pageup": "Prior", "pagedown": "Next", "insert": "Insert", "apps": "Menu", "ctrl": "ctrl", "shift": "shift",
         "alt": "alt", **{f"f{i}": f"F{i}" for i in range(1, 13)}}


class XDesk:
    """Экран Xvfb целиком — «окно программы» (программа развёрнута)."""

    def __init__(self, display: str, proc: subprocess.Popen):
        self.display, self.proc = display, proc
        self.env = {**os.environ, "DISPLAY": display}

    def _x(self, *args: str) -> str:
        r = subprocess.run(["xdotool", *args], env=self.env, capture_output=True, text=True, timeout=30)
        return r.stdout.strip()

    def title(self) -> str:
        return self._x("getactivewindow", "getwindowname")

    def observe(self) -> Observation:
        if self.proc.poll() is not None and not self.title():
            raise Stopped("программа закрылась")
        img = ImageGrab.grab(xdisplay=self.display)
        return Observation(img, title=self.title())

    def act(self, a: Action, obs: Observation) -> str:
        before = self.title()
        if a.do in ("click", "double_click", "right_click"):
            self._x("mousemove", "--sync", str(a.x), str(a.y))
            self._x("click", *(["--repeat", "2", "--delay", "80"] if a.do == "double_click" else []),
                    "3" if a.do == "right_click" else "1")
        elif a.do == "type":  # посимвольно кодами Unicode: «xdotool type» теряет кириллицу (как SendInput UNICODE)
            special = {" ": "space", "\n": "Return", "\t": "Tab"}
            syms = [special.get(c, f"U{ord(c):04X}") for c in a.text if c != "\r"]
            for i in range(0, len(syms), 200):
                self._x("key", "--delay", "8", *syms[i:i + 200])
        elif a.do == "key":
            from forlabs.agent.guard import keys_of

            self._x("key", "--clearmodifiers", "+".join(XKEYS.get(k, k) for k in keys_of(a.keys)))
        elif a.do == "scroll":
            if a.x is not None:
                self._x("mousemove", "--sync", str(a.x), str(a.y))
            self._x("click", "--repeat", str(min(abs(a.amount) or 3, 20)), "5" if a.amount < 0 else "4")
        elif a.do == "drag":
            self._x("mousemove", "--sync", str(a.x), str(a.y))
            self._x("mousedown", "1")
            self._x("mousemove", "--sync", str(a.x2), str(a.y2))
            self._x("mouseup", "1")
        time.sleep(0.8)
        after = self.title()
        return f"!окно: «{after}»" if after != before else ""

    def capture(self, what: str):
        return ImageGrab.grab(xdisplay=self.display)


def saved_list(folder: str) -> list[str]:
    return sorted(p.name for p in Path(folder).iterdir())


def scenarios(folder: Path) -> dict[str, dict]:
    return {
        "writer_table": {
            "cmd": ["soffice", "--writer", "--norestore"],
            "task": Task(goal="Окно LibreOffice Writer: в документе заголовок «Лабораторная работа №1», под ним таблица "
                              "из 3 столбцов и 2 строк; в первой строке — «Фамилия», «Группа», «Оценка».",
                         app="LibreOffice Writer", kind="office")},
        "calc_sum": {
            "cmd": ["soffice", "--calc", "--norestore"],
            "task": Task(goal="Окно LibreOffice Calc: в ячейках A1, A2, A3 числа 5, 7 и 9, в A4 формула суммы этих "
                              "ячеек, видно значение 21; курсор стоит на A4, в строке формул видна формула.",
                         app="LibreOffice Calc", kind="office")},
        "calc_save": {
            "cmd": ["soffice", "--calc", "--norestore"],
            "task": Task(goal=f"В LibreOffice Calc набрать в A1 «Отчёт», сохранить таблицу как файл lab.ods в папку "
                              f"{folder} (формат ODF) и снять окно.",
                         shot="Окно LibreOffice Calc, в A1 «Отчёт», в заголовке окна имя файла lab.ods.",
                         app="LibreOffice Calc", kind="office", folder=str(folder))},
        "start_center": {
            "cmd": ["soffice", "--norestore"],
            "task": Task(goal="Окно текстового редактора LibreOffice Writer с новым документом, в котором набрано "
                              "«Отчёт по лабораторной работе №2».",
                         app="LibreOffice", kind="office")},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--out", default="build/eval-agent")
    ap.add_argument("--display", default=":88")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    s = Settings()
    s.base_url = os.environ.get("FORLABS_BASE_URL", s.base_url)
    s.model = os.environ.get("FORLABS_MODEL", s.model)
    llm = LLM(s, os.environ["FORLABS_API_KEY"])
    xvfb = subprocess.Popen(["Xvfb", args.display, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    env = {**os.environ, "DISPLAY": args.display}
    wm = subprocess.Popen(["openbox"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = {}
    try:
        runs = [(f"{name}#{i + 1}" if args.repeat > 1 else name, name) for name in scenarios(Path(".")) for i in
                range(args.repeat) if not args.names or name in args.names]
        for name, base in runs:
            sc = scenarios(Path(tempfile.mkdtemp(prefix="ЛР 1 ")))[base]
            from forlabs.agent.apps import APPS

            sc["task"] = sc["task"].model_copy(update={"hints": sc["task"].hints + APPS["libreoffice"].hints})
            profile = Path(tempfile.mkdtemp(prefix="lo-profile-"))
            proc = subprocess.Popen([*sc["cmd"], f"-env:UserInstallation={profile.as_uri()}"], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(8)
            desk = XDesk(args.display, proc)
            t0, tok0 = time.time(), llm.usage.total if hasattr(llm.usage, "total") else 0
            res = core.run(sc["task"], desk, llm, on_step=lambda t: print("   ·", t[:160], flush=True))
            sec = round(time.time() - t0)
            if res.image is not None:
                res.image.save(out / f"{name}.png")
            else:
                ImageGrab.grab(xdisplay=args.display).save(out / f"{name}.last.png")
            folder = sc["task"].folder
            saved = sorted(p.name for p in Path(folder).iterdir()) if folder else []
            if folder and res.ok and not (Path(folder) / "lab.ods").exists():
                res.ok, res.reason = False, f"файла нет в папке лабы (есть: {saved_list(folder)})"
            report[name] = {"ok": res.ok, "reason": res.reason, "steps": res.steps, "sec": sec, "log": res.log,
                            "saved": saved, "usage": getattr(llm.usage, "__dict__", {})}
            print(f"[{name}] {'OK' if res.ok else 'FAIL'} шагов {res.steps}, {sec} с {res.reason}", flush=True)
            proc.kill()
            subprocess.run(["pkill", "-f", profile.as_uri()], check=False)
            time.sleep(1)
            shutil.rmtree(profile, ignore_errors=True)
    finally:
        wm.kill()
        xvfb.kill()
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0 if report and all(v["ok"] for v in report.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
