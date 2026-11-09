"""Снимки в программах: план (какой рисунок в какой программе) и проход агента — с подделками программы и модели."""

from pathlib import Path

from PIL import Image
from test_project_stage import PLAN, figures, make

from forlabs.agent.core import Element, Observation
from forlabs.core.llm import LLMError
from forlabs.core.models import AppTask
from forlabs.pipeline import appshots

APP_PLAN = {"items": [{"section": 2, "block": 2, "app": "idle", "goal": "открыть main.py в IDLE",
                       "shot": "окно IDLE с кодом main.py", "open": ["Проект Сумма/main.py"]}]}


def lab(tmp_home, tmp_path, extra, agents=True):
    pipe, fake, st = make(tmp_home, tmp_path, [PLAN, *extra])
    pipe.agents = agents
    return pipe, fake, pipe.execute(st)


def idle_fig(st):
    return next(f for f in figures(st) if "IDLE" in f.topic)


def test_plan_assigns_figure_to_program_with_lab_files(tmp_home, tmp_path):
    pipe, fake, st = lab(tmp_home, tmp_path, [APP_PLAN])
    f = idle_fig(st)
    assert f.app_task == AppTask(app="idle", goal="открыть main.py в IDLE", shot="окно IDLE с кодом main.py",
                                 open=["Проект Сумма/main.py"])
    assert "Сделать снимки в программах" in f.auto_note and not f.image_path
    prompt = fake.calls[-1][1]["content"]
    assert "Проект Сумма/main.py" in prompt and "idle: IDLE (Python)" in prompt  # файлы и программы — модели


def test_plan_drops_unknown_programs_and_foreign_files(tmp_home, tmp_path):
    bad = {"items": [{"section": 2, "block": 2, "app": "photoshop", "goal": "x"},
                     {"section": 2, "block": 2, "app": "notepad", "goal": "открыть", "open": ["C:/Windows/win.ini",
                                                                                           "../секрет.txt"]}]}
    pipe, fake, st = lab(tmp_home, tmp_path, [bad])
    f = idle_fig(st)
    assert f.app_task.app == "notepad" and f.app_task.open == []


def test_plan_failure_leaves_figure_to_student(tmp_home, tmp_path):
    pipe, fake, st = lab(tmp_home, tmp_path, [LLMError("network", "нет сети")])
    assert st.status != "failed" and idle_fig(st).app_task is None


def test_no_plan_without_pending_figures_or_when_disabled(tmp_home, tmp_path):
    pipe, fake, st = lab(tmp_home, tmp_path, [], agents=False)
    assert idle_fig(st).app_task is None  # ответа на план в FakeLLM нет — запроса не было


class FakeDesk:
    def __init__(self, key, exe, files, folder, watch):
        self.files, self.folder = files, folder
        FakeDesk.made.append(self)

    def observe(self):
        return Observation(Image.new("RGB", (640, 400), "white"), "main.py - IDLE",
                           [Element(id=1, role="Документ", name="код", box=(0, 0, 640, 400))])

    def act(self, a, obs):
        return ""

    def capture(self, what):
        return Image.new("RGB", (640, 400), (250, 250, 250))


class FakeWatch:
    def __init__(self, title, stop_after=None):
        self.reason, self.said, self.stopped = None, [], False

    def say(self, text):
        self.said.append(text)

    def stop(self):
        self.stopped = True


def with_task(tmp_home, tmp_path, responses, app="idle"):
    pipe, fake, st = lab(tmp_home, tmp_path, [{"items": []}])
    f = idle_fig(st)
    f.app_task = AppTask(app=app, goal="открыть main.py", open=["Проект Сумма/main.py"])
    fake.responses += responses
    return pipe, fake, st, f


DONE = {"thought": "код виден", "actions": [{"do": "done", "claim": "код"}]}


def test_run_takes_shot_saves_it_and_report_shows_it(tmp_home, tmp_path):
    FakeDesk.made = []
    pipe, fake, st, f = with_task(tmp_home, tmp_path, [DONE, {"ok": True}])
    calls = []
    st = appshots.run(pipe, st, fake, before=lambda: calls.append("свернуть"), after=lambda: calls.append("вернуть"),
                      desk_factory=FakeDesk, watch_factory=FakeWatch, finder=lambda k, c: "idle.exe")
    assert f.image_path and (pipe.lab_dir(st) / f.image_path).is_file() and f.auto_note is None
    assert calls == ["свернуть", "вернуть"]  # окно ForLabs не мешало программе
    desk = FakeDesk.made[0]
    assert desk.files == [appshots.hand_in_dir(pipe, st) / "Проект Сумма" / "main.py"]
    assert st.docx_sha is None  # отчёт пересоберётся с картинкой


def test_missing_program_names_it_and_offers_install(tmp_home, tmp_path):
    pipe, fake, st, f = with_task(tmp_home, tmp_path, [])
    st = appshots.run(pipe, st, fake, desk_factory=FakeDesk, watch_factory=FakeWatch, finder=lambda k, c: None)
    assert f.need_tool == "app:idle" and "IDLE" in f.auto_note and not f.image_path


def test_program_that_does_not_start_is_explained(tmp_home, tmp_path):
    from forlabs.agent.core import AppError

    def broken(*a):
        raise AppError("программа «IDLE (Python)» закрылась сразу после запуска (код 1)")

    pipe, fake, st, f = with_task(tmp_home, tmp_path, [])
    st = appshots.run(pipe, st, fake, desk_factory=broken, watch_factory=FakeWatch, finder=lambda k, c: "idle.exe")
    assert f.auto_note == "Сам не снял: программа «IDLE (Python)» закрылась сразу после запуска (код 1)."


def test_student_stop_halts_and_leaves_rest_for_later(tmp_home, tmp_path):
    pipe, fake, st, f = with_task(tmp_home, tmp_path, [])
    other = next(x for x in figures(st) if x is not f)
    other.image_path = None
    other.app_task = AppTask(app="notepad", goal="открыть")
    events = []

    class StoppingWatch(FakeWatch):
        def __init__(self, title):
            super().__init__(title)
            self.reason = "ты взял мышь — остановил, чтобы не мешать"

    st = appshots.run(pipe, st, fake, emit=lambda t, k="info": events.append(t), desk_factory=FakeDesk,
                      watch_factory=StoppingWatch, finder=lambda k, c: "x.exe")
    first, second = (other, f) if figures(st).index(other) < figures(st).index(f) else (f, other)
    assert not first.image_path and first.auto_note.startswith("Остановлено: ты взял мышь")
    assert second.app_task and not second.image_path and "Остановлено" not in (second.auto_note or "")  # не дошло
    assert any("Остальные снимки" in e for e in events)


def test_not_on_windows_says_so(tmp_home, tmp_path, monkeypatch):
    pipe, fake, st, f = with_task(tmp_home, tmp_path, [])
    monkeypatch.setattr(appshots.sys, "platform", "linux")
    st = appshots.run(pipe, st, fake)
    assert "только в Windows" in f.auto_note


def test_pipeline_button_runs_and_rerenders(tmp_home, tmp_path, monkeypatch):
    pipe, fake, st, f = with_task(tmp_home, tmp_path, [DONE, {"ok": True}])
    pipe._save(st)
    real = appshots.run
    monkeypatch.setattr(appshots, "run", lambda *a, **k: real(*a, desk_factory=FakeDesk, watch_factory=FakeWatch,
                                                              finder=lambda k_, c: "idle.exe", **k))
    st = pipe.app_shots(st)
    assert idle_fig(st).image_path and st.docx_path and Path(st.docx_path).exists()


def test_one_figure_crash_does_not_lose_other_shots(tmp_home, tmp_path, monkeypatch):
    pipe, fake, st, f = with_task(tmp_home, tmp_path, [DONE, {"ok": True}])
    other = next(x for x in figures(st) if x is not f)
    other.image_path = None
    other.app_task = AppTask(app="idle", goal="второй")
    from forlabs.agent import core as agent_core

    calls = {"n": 0}
    real = agent_core.run

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("UI Automation упал")
        return real(*a, **k)

    monkeypatch.setattr(agent_core, "run", flaky)
    st = appshots.run(pipe, st, fake, desk_factory=FakeDesk, watch_factory=FakeWatch, finder=lambda k, c: "x.exe")
    first, second = (other, f) if figures(st).index(other) < figures(st).index(f) else (f, other)
    assert "RuntimeError" in first.auto_note and second.image_path


def test_allow_list_drops_empty_words():
    item = appshots.AppShotItem(section=1, block=1, allow=["", "  ", "Удалить", None])
    assert item.allow == ["Удалить"]
