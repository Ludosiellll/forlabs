"""Проверка «рук и глаз» агента на настоящей Windows (CI). Вместо модели — сценарий с жёсткими правилами: здесь
проверяются запуск программ, снимок только их окон, ввод, ограничения и остановка, а не решения модели (их проверяет
tools/eval_agent.py с настоящей моделью).

Итог — build/probe/agent/report.json и снимки рядом."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forlabs.agent import apps, core  # noqa: E402
from forlabs.agent.core import Action, Blocked, Stopped, Task, Verdict  # noqa: E402

OUT = Path("build/probe/agent")
WORK = Path(os.environ.get("RUNNER_TEMP", "build")) / "agent-probe"
REPORT: dict = {"cases": {}}
OUT.mkdir(parents=True, exist_ok=True)  # проверки идут сразу при объявлении (@case) — папки нужны заранее
WORK.mkdir(parents=True, exist_ok=True)


class ScriptBrain:
    """Вместо модели: щёлкнуть в редактор, набрать, готово. Проверка снимка всегда «да»."""

    def __init__(self, text: str | list):
        self.text, self.n = text, 0

    def json(self, messages, schema, **kw):
        if schema is Verdict:
            return Verdict(ok=True)
        self.n += 1
        body = messages[-1]["content"][0]["text"]
        if self.n == 1:
            ids = [ln for ln in body.splitlines() if ln.startswith("[") and ("Документ" in ln or "Поле" in ln)]
            el = int(ids[0][1:ids[0].index("]")]) if ids else None
            acts = ([{"do": "click", "el": el}] if el else [{"do": "click", "x": 200, "y": 200}])
            return core.Step(actions=[*acts, {"do": "type", "text": self.text}])
        return core.Step(actions=[{"do": "done", "claim": "текст набран"}])


def case(fn):
    name = fn.__name__
    t0 = time.time()
    res: dict = {}
    try:
        fn(res)
        res.setdefault("ok", True)
    except Exception as e:  # noqa: BLE001
        res.update(ok=False, error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-2000:])
    res["sec"] = round(time.time() - t0, 1)
    REPORT["cases"][name] = res
    print(f"[{name}] {'OK' if res.get('ok') else 'FAIL'} {res.get('error') or res.get('note') or ''}", flush=True)
    return fn


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)


def elements_dump(obs, n=40):
    return [e.line() for e in obs.elements[:n]]


def editor_of(obs):
    eds = [e for e in obs.elements if e.role in ("Документ", "Поле") and e.box[2] * e.box[3] > 20000]
    return max(eds, key=lambda e: e.box[2] * e.box[3]) if eds else None


def notepad(folder: Path, name: str = "заметки.txt"):
    from forlabs.agent.windesk import WinDesk

    f = folder / name
    f.write_text("", encoding="utf-8")
    return WinDesk("notepad", apps.find("notepad"), [f], folder), f


@case
def apps_found(res):
    res["installed"] = apps.installed()
    res["missing_packettracer"] = apps.find("packettracer")
    assert "notepad" in res["installed"]


@case
def notepad_type_save_and_save_as(res):
    folder = WORK / "ЛР 1 Иванов"
    folder.mkdir(parents=True, exist_ok=True)
    desk, f = notepad(folder)
    try:
        obs = desk.observe()
        obs.image.save(OUT / "notepad_first.png")
        res["title"], res["elements"] = obs.title, elements_dump(obs)
        ed = editor_of(obs)
        assert ed is not None, "нет поля редактора в элементах"
        desk.act(Action(do="click", el=ed.id), obs)
        desk.act(Action(do="type", text="Привет из ForLabs 😀\nЛабораторная №1\tтаб"), obs)
        note = desk.act(Action(do="key", keys="ctrl+s"), obs)
        time.sleep(1.5)
        res["saved_text"] = f.read_text(encoding="utf-8", errors="replace")
        assert "Привет из ForLabs" in res["saved_text"] and "Лабораторная №1" in res["saved_text"], res["saved_text"]
        res["emoji_kept"] = "😀" in res["saved_text"]
        note = desk.act(Action(do="key", keys="ctrl+shift+s"), desk.observe())
        res["save_as_note"] = note
        time.sleep(1.5)
        dlg = desk.observe()
        dlg.image.save(OUT / "notepad_save_as.png")
        res["dialog"], res["dialog_title"] = dlg.dialog, dlg.title
        res["dialog_elements"] = elements_dump(dlg, 60)
        field = next((e for e in dlg.elements if e.role in ("Поле", "Список") and
                      ("имя файла" in e.name.lower() or "file name" in e.name.lower())), None)
        assert field is not None, "в «Сохранить как» не нашёлся элемент «Имя файла»"
        desk.act(Action(do="click", el=field.id), dlg)
        desk.act(Action(do="key", keys="ctrl+a"), dlg)
        target = folder / "копия отчёта.txt"
        desk.act(Action(do="type", text=str(target)), dlg)
        desk.act(Action(do="key", keys="enter"), dlg)
        time.sleep(2)
        res["save_as_exists"] = target.exists()
        assert target.exists(), "файл «Сохранить как» не появился"
        final = desk.capture("window")
        final.save(OUT / "notepad_final.png")
    finally:
        kill_tree(desk.root)


@case
def foreign_window_on_top_is_not_in_shot_and_not_clickable(res):
    from forlabs.runner import winapi

    folder = WORK / "чужое окно"
    folder.mkdir(parents=True, exist_ok=True)
    desk, _ = notepad(folder)
    tk = None
    try:
        left, top, right, bottom = winapi.window_rect(desk.main)
        cx, cy = (left + right) // 2, (top + bottom) // 2
        code = textwrap.dedent(f"""
            import tkinter as tk
            r = tk.Tk(); r.title("Чужое окно"); r.configure(bg="#ff00ff")
            r.geometry("300x200+{cx - 150}+{cy - 100}"); r.attributes("-topmost", True)
            r.after(60000, r.destroy); r.mainloop()
        """)
        tk = subprocess.Popen([sys.executable, "-c", code])
        time.sleep(3)
        obs = desk.observe()
        obs.image.save(OUT / "foreign_observed.png")
        magenta = sum(1 for px in obs.image.convert("RGB").getdata() if px[0] > 240 and px[1] < 20 and px[2] > 240)
        res["magenta_pixels_in_shot"] = magenta
        assert magenta < 50, "чужое окно попало в снимок"
        try:
            desk.act(Action(do="click", x=cx - desk.origin[0], y=cy - desk.origin[1]), obs)
            raise AssertionError("щелчок по чужому окну прошёл")
        except Blocked as e:
            res["blocked"] = str(e)
    finally:
        if tk:
            tk.kill()
        kill_tree(desk.root)


@case
def minimized_window_is_restored(res):
    from forlabs.agent import windesk

    folder = WORK / "свёрнутое"
    folder.mkdir(parents=True, exist_ok=True)
    desk, _ = notepad(folder)
    try:
        windesk._u.ShowWindow(desk.main, 6)  # SW_MINIMIZE
        time.sleep(1)
        obs = desk.observe()
        res["iconic_after"] = bool(windesk._u.IsIconic(desk.main))
        assert not res["iconic_after"] and obs.image.width > 200
    finally:
        kill_tree(desk.root)


@case
def program_closed_mid_run(res):
    folder = WORK / "закрыли"
    folder.mkdir(parents=True, exist_ok=True)
    desk, _ = notepad(folder)
    obs = desk.observe()
    kill_tree(desk.root)
    time.sleep(1.5)
    try:
        desk.act(Action(do="key", keys="ctrl+s"), obs)
        raise AssertionError("действие в закрытой программе прошло")
    except Stopped as e:
        res["stopped"] = str(e)
    r = core.run(Task(goal="x"), desk, ScriptBrain([]), settle=0)
    res["run_reason"] = r.reason
    assert not r.ok and "закрылась" in r.reason


@case
def student_escape_and_overlay(res):
    from forlabs.agent import watch, windesk

    w = watch.Watch("Блокнот", physical_only=False)
    w.start()
    res["hooked"], res["overlay"] = w.hooked, bool(w._hwnd)
    w.say("Открываю меню «Файл»")
    time.sleep(0.5)
    w.own_escape()
    windesk._send([windesk._key(0x1B), windesk._key(0x1B, flags=windesk.KEYUP)])  # Esc самого агента
    time.sleep(0.3)
    res["after_own_esc"] = w.reason
    w._own_esc_until = 0
    windesk._send([windesk._key(0x1B), windesk._key(0x1B, flags=windesk.KEYUP)])  # «студент» жмёт Esc
    time.sleep(0.3)
    try:
        w.check()
        raise AssertionError("Esc не остановил")
    except Stopped as e:
        res["stopped"] = str(e)
    finally:
        w.stop()
    assert res["hooked"] and res["after_own_esc"] is None


@case
def app_missing_or_broken(res):
    from forlabs.agent.core import AppError
    from forlabs.agent.windesk import WinDesk

    try:
        WinDesk("notepad", str(WORK / "нет такой программы.exe"), [], WORK)
        raise AssertionError("несуществующая программа «запустилась»")
    except AppError as e:
        res["missing_exe"] = str(e)


@case
def visual_studio_project(res):
    from forlabs.agent.windesk import WinDesk

    exe = apps.find("visualstudio")
    if not exe:
        res.update(ok=True, note="Visual Studio нет на этой машине")
        return
    proj = WORK / "VsLab"
    subprocess.run(["dotnet", "new", "console", "-o", str(proj), "-n", "Kalkulyator", "--force"], capture_output=True)
    subprocess.run(["dotnet", "new", "sln", "-o", str(proj), "-n", "Kalkulyator", "--force"], capture_output=True)
    sln = next(iter(proj.glob("*.sln*")), None)
    if sln:
        subprocess.run(["dotnet", "sln", str(sln), "add", str(proj / "Kalkulyator.csproj")], capture_output=True)
    t0 = time.time()
    desk = WinDesk("visualstudio", exe, [p for p in [sln, proj / "Program.cs"] if p], proj, launch_timeout=300)
    res["main_window_sec"] = round(time.time() - t0)
    try:
        import re

        seen = []
        first_run = re.compile(r"Skip|Пропустить|Not now|Не сейчас|Start Visual Studio|Запуск Visual Studio|"
                               r"Continue without code|Продолжить без кода")
        for i in range(20):
            time.sleep(6)
            t1 = time.time()
            obs = desk.observe()
            step = {"title": obs.title, "dialog": obs.dialog, "elements": len(obs.elements),
                    "observe_sec": round(time.time() - t1, 1)}
            obs.image.save(OUT / f"vs_{i:02d}.png")
            skip = next((e for e in obs.elements if first_run.search(e.name) and e.role in ("Кнопка", "Ссылка")), None)
            if skip:
                step["clicked"] = skip.name
                seen.append(step)
                desk.act(Action(do="click", el=skip.id), obs)
                continue
            seen.append(step)
            if "Kalkulyator" in obs.title or "Program.cs" in obs.title:
                res["project_open"] = True
                break
        res["seen"] = seen
        res["elements_sample"] = elements_dump(obs, 60)
        desk.capture("window").save(OUT / "vs_final.png")
        assert res.get("project_open"), "проект в Visual Studio так и не открылся"
    finally:
        kill_tree(desk.root)


@case
def idle_with_code(res):
    from forlabs.agent.windesk import WinDesk

    exe = apps.find("idle")
    if not exe:
        res.update(ok=True, note="Python нет")
        return
    folder = WORK / "idle"
    folder.mkdir(parents=True, exist_ok=True)
    main = folder / "main.py"
    main.write_text('name = input("Как тебя зовут? ")\nprint("Привет,", name)\n', encoding="utf-8")
    desk = WinDesk("idle", exe, [main], folder, launch_timeout=60)
    try:
        time.sleep(2)
        obs = desk.observe()
        obs.image.save(OUT / "idle.png")
        res["title"], res["elements"] = obs.title, len(obs.elements)
        assert "main.py" in obs.title
    finally:
        kill_tree(desk.root)


@case
def core_run_end_to_end_in_notepad(res):
    folder = WORK / "цикл"
    folder.mkdir(parents=True, exist_ok=True)
    desk, _ = notepad(folder)
    try:
        r = core.run(Task(goal="Блокнот с текстом", app="Блокнот"), desk, ScriptBrain("Отчёт по лабораторной №3"))
        res.update(result=r.ok, reason=r.reason, log=r.log)
        if r.image is not None:
            r.image.save(OUT / "core_run.png")
        assert r.ok
    finally:
        kill_tree(desk.root)


@case
def students_own_window_is_left_alone(res):
    from forlabs.agent.windesk import WinDesk
    from forlabs.runner import winapi

    folder = WORK / "своё окно"
    folder.mkdir(parents=True, exist_ok=True)
    mine = folder / "мой файл.txt"
    mine.write_text("личное", encoding="utf-8")
    own = subprocess.Popen([apps.find("notepad"), str(mine)])
    time.sleep(3)
    own_wins = [w.hwnd for w in winapi.windows_of(winapi.process_tree({own.pid}))]
    lab = folder / "лаба.txt"
    lab.write_text("", encoding="utf-8")
    desk = WinDesk("notepad", apps.find("notepad"), [lab], folder)
    try:
        res["own_windows"], res["agent_main"] = own_wins, desk.main
        assert desk.main not in own_wins, "агент взялся за окно студента"
        obs = desk.observe()
        assert "лаба" in obs.title, obs.title
        if own_wins:
            left, top, right, bottom = winapi.window_rect(own_wins[0])
            winapi.foreground(own_wins[0])
            time.sleep(0.5)
            obs = desk.observe()  # агент снова выводит своё окно вперёд
            res["front_after"] = obs.title
            assert "лаба" in obs.title
    finally:
        kill_tree(desk.root)
        own.kill()


@case
def paint_drag_draws_a_line(res):
    from forlabs.agent.windesk import WinDesk

    exe = apps.find("paint")
    if not exe:
        res.update(ok=True, note="Paint нет")
        return
    folder = WORK / "paint"
    folder.mkdir(parents=True, exist_ok=True)
    desk = WinDesk("paint", exe, [], folder, launch_timeout=60)
    try:
        time.sleep(2)
        obs = desk.observe()
        obs.image.save(OUT / "paint_before.png")
        w, h = obs.image.size
        x1, y1, x2, y2 = w // 3, h // 2, 2 * w // 3, h // 2 + 60
        before = obs.image.crop((x1, y1 - 5, x2, y2 + 5)).convert("L").getextrema()
        desk.act(Action(do="drag", x=x1, y=y1, x2=x2, y2=y2), obs)
        after_img = desk.observe().image
        after_img.save(OUT / "paint_after.png")
        after = after_img.crop((x1, y1 - 5, x2, y2 + 5)).convert("L").getextrema()
        res["before_extrema"], res["after_extrema"] = before, after
        assert after[0] < before[0] - 50, "линия не нарисовалась — перетаскивание не дошло"
    finally:
        kill_tree(desk.root)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    bad = [k for k, v in REPORT["cases"].items() if not v.get("ok")]
    print("НЕ ПРОШЛИ:", bad, flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
