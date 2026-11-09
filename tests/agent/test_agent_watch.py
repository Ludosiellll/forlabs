"""Остановка агента студентом: настоящие нажатия и движения мыши — стоп, программные (самого агента) — нет."""

from forlabs.agent import watch as W


def make(**kw):
    return W.Watch("Visual Studio", **kw)


def test_escape_from_student_stops_but_agent_escape_does_not():
    w = make()
    w.key_event(W.VK_ESCAPE, injected=True)  # агент закрыл окно-приветствие
    assert w.reason is None
    w.key_event(W.VK_ESCAPE, injected=False)
    assert w.reason == "остановлено (Esc)"


def test_any_real_key_stops_agent_keys_do_not():
    w = make()
    w.key_event(0x41, injected=True)
    assert w.reason is None
    w.key_event(0x41, injected=False)
    assert "клавишу" in w.reason


def test_small_mouse_jitter_is_ignored_real_movement_stops():
    w = make()
    w.mouse_event(W.WM_MOUSEMOVE, (100, 100), injected=False)
    w.mouse_event(W.WM_MOUSEMOVE, (105, 103), injected=False)  # стол качнули
    assert w.reason is None
    w.mouse_event(W.WM_MOUSEMOVE, (500, 300), injected=True)  # агент ведёт мышь
    w.mouse_event(W.WM_MOUSEMOVE, (520, 330), injected=False)
    w.mouse_event(W.WM_MOUSEMOVE, (560, 360), injected=False)
    assert "мышь" in w.reason


def test_real_click_stops():
    w = make()
    w.mouse_event(W.WM_LBUTTONDOWN, (1, 1), injected=True)
    assert w.reason is None
    w.mouse_event(W.WM_LBUTTONDOWN, (1, 1), injected=False)
    assert "щёлкнул" in w.reason


def test_ci_mode_treats_injected_escape_as_student_unless_agent_announced_it():
    w = make(physical_only=False)
    w.own_escape()
    w.key_event(W.VK_ESCAPE, injected=True)
    assert w.reason is None
    w._own_esc_until = 0
    w.key_event(W.VK_ESCAPE, injected=True)
    assert w.reason == "остановлено (Esc)"


def test_first_reason_wins():
    w = make()
    w.key_event(W.VK_ESCAPE, injected=False)
    w.mouse_event(W.WM_LBUTTONDOWN, (1, 1), injected=False)
    assert w.reason == "остановлено (Esc)"
