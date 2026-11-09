"""Цикл агента на подделке программы: приветствие → редактор → «Сохранить как». Модель — записанные ответы."""

import json

import pytest
from PIL import Image, ImageDraw

from forlabs.agent import core, guard
from forlabs.agent.core import Action, Blocked, Element, Observation, Task
from forlabs.core.llm import FakeLLM, LLMError


class FakeApp:
    """Блокнот с окном приветствия. act возвращает «!…», когда открылось другое окно."""

    def __init__(self, size=(800, 600), danger=False):
        self.size, self.state, self.text, self.saved, self.focus = size, "welcome", "", "", None
        self.acts: list[Action] = []
        self.danger = danger

    def elements(self):
        if self.state == "welcome":
            return [Element(id=1, role="Button", name="Закрыть", box=(350, 400, 100, 30))]
        if self.state == "save":
            return [Element(id=4, role="Edit", name="Имя файла", value=self.saved, box=(100, 200, 400, 24)),
                    Element(id=5, role="Button", name="Сохранить", box=(520, 200, 90, 24))]
        els = [Element(id=2, role="Edit", name="Текст", value=self.text, box=(0, 40, 800, 500)),
               Element(id=3, role="MenuItem", name="Файл", box=(0, 0, 50, 20))]
        if self.danger:
            els.append(Element(id=6, role="Button", name="Удалить всё", box=(700, 0, 100, 20)))
        return els

    def observe(self):
        img = Image.new("RGB", self.size, "white")
        ImageDraw.Draw(img).text((10, 10), f"{self.state}: {self.text}", fill="black")
        return Observation(img, title={"welcome": "Добро пожаловать", "save": "Сохранить как"}.get(self.state, "Блокнот"),
                           elements=self.elements(), dialog=self.state != "editor")

    def act(self, a: Action, obs):
        self.acts.append(a)
        if a.do == "click" and a.el == 1 and self.state == "welcome":
            self.state = "editor"
            return "!окно приветствия закрылось"
        if a.do == "click" and a.el in (2, 4):
            self.focus = a.el
            return ""
        if a.do == "type":
            if self.state == "save" and self.focus == 4:
                self.saved += a.text
            elif self.state == "editor" and self.focus == 2:
                self.text += a.text
            return ""
        if a.do == "key" and a.keys == "ctrl+s":
            self.state = "save"
            return "!открылось окно «Сохранить как»"
        if a.do == "click" and a.el == 5:
            self.state = "editor"
            return "!файл сохранён"
        return ""

    def capture(self, what):
        img = Image.new("RGB", self.size, "white")
        ImageDraw.Draw(img).text((10, 10), self.text, fill="black")
        return img


def step(*actions, thought="смотрю"):
    return {"thought": thought, "actions": list(actions)}


OK = {"ok": True, "missing": ""}


def run(app, responses, **task):
    llm = FakeLLM(responses)
    res = core.run(Task(goal="Окно Блокнота с текстом «Привет»", app="Блокнот", **task), app, llm, settle=0)
    return res, llm


def test_closes_welcome_types_and_takes_verified_shot():
    app = FakeApp()
    res, llm = run(app, [step({"do": "click", "el": 1}),
                         step({"do": "click", "el": 2}, {"do": "type", "text": "Привет"}),
                         step({"do": "done", "claim": "текст набран"}), OK])
    assert res.ok and res.image is not None and app.text == "Привет"
    assert res.steps == 3 and res.log[-1].startswith("готово")
    verify_call = llm.calls[-1][0]["content"]
    assert "Привет" in verify_call[0]["text"] and verify_call[1]["image_url"]["url"].startswith("data:image/jpeg")


def test_batch_stops_when_another_window_opens():
    app = FakeApp()
    app.state = "editor"
    res, _ = run(app, [step({"do": "key", "keys": "ctrl+s"}, {"do": "type", "text": "C:\\x.txt"}),
                       step({"do": "fail", "reason": "хватит"})])
    assert [a.do for a in app.acts] == ["key"]  # набор не ушёл в окно, которое модель ещё не видела
    assert "Сохранить как" in res.log[0] and not res.ok and res.reason == "хватит"


def test_bad_shot_goes_back_to_model_then_gives_up():
    app = FakeApp()
    app.state = "editor"
    res, llm = run(app, [step({"do": "done"}), {"ok": False, "missing": "нет текста"},
                         step({"do": "done"}), {"ok": False, "missing": "всё ещё нет текста"}])
    assert not res.ok and "всё ещё нет текста" in res.reason
    second = llm.calls[2][1]["content"][0]["text"]
    assert "проверка снимка: не подходит — нет текста" in second  # модель узнала, что не так


def test_danger_button_is_not_pressed_unless_task_needs_it():
    app = FakeApp(danger=True)
    app.state = "editor"
    res, llm = run(app, [step({"do": "click", "el": 6}), step({"do": "fail", "reason": "нельзя"})])
    assert app.acts == [] and "НЕЛЬЗЯ" in res.log[0] and "Удалить всё" in res.log[0]
    assert "НЕЛЬЗЯ" in llm.calls[1][1]["content"][0]["text"]
    app2 = FakeApp(danger=True)
    app2.state = "editor"
    run(app2, [step({"do": "click", "el": 6}), step({"do": "fail", "reason": "-"})], allow=["Удалить"])
    assert [a.el for a in app2.acts] == [6]


def test_model_coordinates_map_back_to_real_pixels():
    app = FakeApp(size=(2560, 1600))
    app.state = "editor"
    run(app, [step({"do": "click", "x": 100, "y": 50}), step({"do": "fail", "reason": "-"})])
    a = app.acts[0]
    assert (a.x, a.y) == (200, 100)  # модель видела картинку вдвое меньше


def test_elements_are_shown_in_model_pixels():
    app = FakeApp(size=(2560, 1600))
    app.state = "editor"
    _, llm = run(app, [step({"do": "fail", "reason": "-"})])
    text = llm.calls[0][1]["content"][0]["text"]
    assert "[2] Edit «Текст»" in text and "@(200,145)" in text and "1280×800" in text


def test_request_fits_proxy_limit_for_big_noisy_screen():
    import os

    img = Image.frombytes("RGB", (2560, 1600), os.urandom(2560 * 1600 * 3))
    app = FakeApp()
    app.observe = lambda: Observation(img, "Шум", [Element(id=i, role="Button", name=f"Кнопка {i}",
                                                           box=(i, i, 10, 10)) for i in range(300)])
    _, llm = run(app, [step({"do": "fail", "reason": "-"})])
    assert len(json.dumps(llm.calls[0], ensure_ascii=False)) < 600_000


def test_out_of_budget_and_model_errors_are_reasons_not_crashes():
    app = FakeApp()
    app.state = "editor"
    res, _ = run(app, [step({"do": "wait", "seconds": 0})] * 3, steps=3)
    assert not res.ok and "3 шагов" in res.reason
    err = LLMError("quota", "429")
    err.message_ru = "Дневной лимит на сегодня исчерпан"
    res, _ = run(FakeApp(), [err])
    assert not res.ok and "лимит" in res.reason


def test_student_stop_ends_run():
    app = FakeApp()
    res = core.run(Task(goal="x"), app, FakeLLM([]), should_stop=lambda: True)
    assert not res.ok and res.reason == "остановлено"


def test_action_aliases_and_one_action_without_list():
    s = core.Step.model_validate({"actions": {"do": "Press", "keys": "Enter"}})
    assert s.actions[0].do == "key"
    assert Action.model_validate({"do": "doubleclick", "el": 3}).do == "double_click"


@pytest.mark.parametrize("combo, ok", [("ctrl+s", True), ("Ctrl + Shift + S", True), ("enter", True), ("F5", True),
                                       ("alt+f4", False), ("win+r", False), ("alt+tab", False), ("ctrl+esc", False),
                                       ("ctrl+shift+esc", False), ("ctrl", False), ("ctrl+bogus", False)])
def test_keys(combo, ok):
    obs = Observation(Image.new("RGB", (10, 10)))
    try:
        guard.check(Action(do="key", keys=combo), obs, Task(goal="x"))
        assert ok
    except Blocked:
        assert not ok


@pytest.mark.parametrize("text, kind, ok", [
    ("C:\\Users\\Иван\\Desktop\\a.txt", "", False), ("C:\\Labs\\ЛР 1\\отчёт.docx", "", True),
    ("c:/labs/лр 1/sub/b.png", "", True), ("\\\\server\\share\\x", "", False), ("Просто текст 1:2", "", True),
    ("SELECT * FROM students;", "db", True), ("DROP DATABASE college;", "db", False),
    ("CREATE DATABASE lab3; DROP DATABASE lab3;", "db", True), ("GRANT ALL ON *.* TO x;", "db", False),
    ("ipconfig /all", "console", True), ("ping 8.8.8.8 -n 4", "console", True), ("del /q *.*", "console", False),
    ("ping ya.ru && del x", "console", False), ("net user bob 123 /add", "console", False),
    ("net user", "console", True), ("format c:", "console", False),
])
def test_typed_text(text, kind, ok):
    obs = Observation(Image.new("RGB", (10, 10)))
    task = Task(goal="x", folder="C:\\Labs\\ЛР 1", kind=kind)
    try:
        guard.check(Action(do="type", text=text), obs, task)
        assert ok
    except Blocked:
        assert not ok


def test_click_needs_target_inside_screen_and_known_element():
    obs = Observation(Image.new("RGB", (100, 100)), elements=[Element(id=1, role="Button", name="OK", box=(0, 0, 5, 5))])
    for a in [Action(do="click"), Action(do="click", el=9), Action(do="click", x=150, y=5), Action(do="drag", el=1)]:
        with pytest.raises(Blocked):
            guard.check(a, obs, Task(goal="x"))
    guard.check(Action(do="click", el=1), obs, Task(goal="x"))


def test_click_that_changes_nothing_is_reported_and_endless_retries_stop():
    app = FakeApp()
    app.state = "editor"
    click = step({"do": "click", "x": 700, "y": 500})
    res, llm = run(app, [click] * 6)
    assert "окно не изменилось" in llm.calls[1][1]["content"][0]["text"]  # модель узнаёт, что щелчок впустую
    assert not res.ok and "ничего не меняют" in res.reason and len(llm.calls) == 3


def test_time_limit():
    t = iter([0, 10, 400, 400])
    app = FakeApp()
    app.state = "editor"
    res = core.run(Task(goal="x", seconds=300), app, FakeLLM([step({"do": "click", "el": 2})]), settle=0,
                   clock=lambda: next(t))
    assert not res.ok and "5 мин" in res.reason


def test_program_closed_or_window_gone_mid_run():
    class Closing(FakeApp):
        def act(self, a, obs):
            raise core.Stopped("программа закрылась")

    app = Closing()
    app.state = "editor"
    res, _ = run(app, [step({"do": "click", "el": 2})])
    assert not res.ok and res.reason == "программа закрылась"

    class Gone(FakeApp):
        def observe(self):
            raise OSError("окно не найдено")

    res, _ = run(Gone(), [])
    assert not res.ok and "окно не найдено" in res.reason


def test_flaky_internet_is_retried_and_run_continues(monkeypatch):
    """Связь пропадает на запросе — клиент повторяет, агент продолжает с того же места."""
    import httpx

    from forlabs.core.llm import LLM
    from forlabs.core.settings import Settings

    answers = [json.dumps(step({"do": "click", "el": 2}, {"do": "type", "text": "Привет"})),
               json.dumps(step({"do": "done"})), json.dumps(OK)]
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] in (1, 3, 4):  # обрывы и таймаут
            raise httpx.ConnectTimeout("сеть пропала") if state["n"] != 3 else httpx.ReadError("обрыв")
        body = answers.pop(0)
        return httpx.Response(200, json={"choices": [{"message": {"content": body}, "finish_reason": "stop"}]})

    llm = LLM(Settings(), "k", http_client=httpx.Client(transport=httpx.MockTransport(handler)),
              retry_delays=(0, 0, 0))
    app = FakeApp()
    app.state = "editor"
    res = core.run(Task(goal="Привет в блокноте"), app, llm, settle=0)
    assert res.ok and app.text == "Привет" and state["n"] == 6


def test_internet_gone_for_good_is_a_clear_reason():
    import httpx

    from forlabs.core.llm import LLM
    from forlabs.core.settings import Settings

    def handler(request):
        raise httpx.ConnectError("нет сети")

    llm = LLM(Settings(), "k", http_client=httpx.Client(transport=httpx.MockTransport(handler)), retry_delays=(0, 0))
    app = FakeApp()
    res = core.run(Task(goal="x"), app, llm, settle=0)
    assert not res.ok and res.reason and app.acts == []


def test_model_answers_garbage_then_fixes_it():
    import httpx

    from forlabs.core.llm import LLM
    from forlabs.core.settings import Settings

    answers = ["Сейчас нажму кнопку!", json.dumps(step({"do": "fail", "reason": "нет такой кнопки"}))]

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": answers.pop(0)},
                                                      "finish_reason": "stop"}]})

    llm = LLM(Settings(), "k", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    res = core.run(Task(goal="x"), FakeApp(), llm, settle=0)
    assert not res.ok and res.reason == "нет такой кнопки"


def test_zoom_sends_enlarged_part_with_rulers_next_time():
    app = FakeApp()
    app.state = "editor"
    res, llm = run(app, [step({"do": "zoom", "x": 400, "y": 300}, {"do": "click", "el": 2}),
                         step({"do": "fail", "reason": "-"})])
    assert app.acts == []  # после приближения действия того же ответа не выполняются: модель сначала посмотрит
    first, second = llm.calls[0][1]["content"], llm.calls[1][1]["content"]
    assert len(first) == 2 and len(second) == 4
    assert "увеличенный в 3 раза" in second[2]["text"] and "(240,200)" in second[2]["text"]
    assert "приблизил" in res.log[0] and "окно не изменилось" not in res.log[0]


def test_zoom_image_keeps_full_image_coordinates():
    from forlabs.agent.core import zoomed

    img = Image.new("RGB", (1280, 800), "white")
    big, corner = zoomed(img, 5, 795)  # у края — участок прижимается к краю
    assert corner == (0, 600) and big.size == (960, 600)


def test_different_futile_tries_get_more_room_than_one_repeated():
    app = FakeApp()
    app.state = "editor"
    tries = [step({"do": "click", "x": 700 + i, "y": 500}) for i in range(8)]
    res, llm = run(app, tries)
    assert not res.ok and len(llm.calls) == 6  # шесть разных попыток, потом стоп
    text = llm.calls[1][1]["content"][0]["text"]
    assert "приблизь участок (zoom)" in text and "Enter" in text


def test_typing_that_lands_nowhere_says_where_to_click():
    app = FakeApp()
    app.state = "editor"  # фокус не в поле: набор ничего не меняет
    _, llm = run(app, [step({"do": "type", "text": "Привет"}), step({"do": "fail", "reason": "-"})])
    assert "текст никуда не попал" in llm.calls[1][1]["content"][0]["text"]
