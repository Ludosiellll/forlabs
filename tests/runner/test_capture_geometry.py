"""Снимок окна при масштабе экрана: программа без поддержки масштаба рисует окно в исходном размере, Windows
растягивает его на экране (125 % — в 1,25 раза). Снимок — то, что окно нарисовало, без чёрных полей."""

from forlabs.runner.screen import render_geometry


def test_plain_window_at_100_percent_keeps_its_size():
    size, box = render_geometry((100, 100, 516, 439), (107, 100, 509, 432), 1.0)
    assert size == (416, 339) and box == (7, 0, 409, 332)


def test_stretched_window_is_taken_at_its_own_size():
    # окно 322×292 на экране 125 %: рамка на экране 402×365 (+ невидимые поля), рисует себя окно в 322×292
    size, box = render_geometry((100, 100, 520, 474), (109, 100, 511, 465), 1.25)
    assert size == (336, 299)
    assert box == (7, 0, 329, 292) and box[2] - box[0] == 322 and box[3] - box[1] == 292


def test_window_drawn_bigger_than_on_screen_is_not_cut():
    size, box = render_geometry((0, 0, 400, 300), (0, 0, 400, 300), 0.8)  # 150 % программы на экране 120 %
    assert size == (500, 375) and box == (0, 0, 500, 375)
