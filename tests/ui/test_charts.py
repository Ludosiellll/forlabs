"""Графики админ-панели: встроенный SVG без скриптов и сети."""

import re

import pytest

from forlabs.ui.charts import SERIES_COLORS, bars, fmt_tokens, nice_ticks, stacked_bars

DAYS = ["06.11", "07.11", "08.11"]


@pytest.mark.parametrize("n,text", [(0, "0"), (950, "950"), (9500, "9 500"), (27000, "27 тыс."),
                                    (1250000, "1,2 млн"), (92011485, "92 млн")])
def test_fmt_tokens(n, text):
    assert fmt_tokens(n) == text


def test_nice_ticks():
    assert nice_ticks(27000) == [0, 10000, 20000, 30000]
    assert nice_ticks(4) == [0, 1, 2, 3, 4]
    assert nice_ticks(0) == [0, 1]


def test_stacked_bars_marks_legend_and_tips():
    svg = str(stacked_bars(DAYS, [("Анна", [27000, 0, 3000], SERIES_COLORS[0]),
                                  ("Пётр", [0, 1500, 500], SERIES_COLORS[1])]))
    assert svg.count('class="seg"') == 4  # нулевые отрезки не рисуются
    assert "<script" not in svg and "http" not in svg.replace("http://www.w3.org/2000/svg", "")
    assert "Анна" in svg and "Пётр" in svg  # легенда
    tips = re.findall(r'data-tip="([^"]+)"', svg)
    assert len(tips) == 3 and "Анна: 27 тыс." in tips[0] and "Всего: 3 500" in tips[2]
    widths = [float(w) for w in re.findall(r'class="seg"[^>]*width="([\d.]+)"', svg)]
    assert widths and max(widths) <= 24


def test_single_series_has_no_legend():
    svg = str(bars(["Неделя 1", "Неделя 2"], [3, 5]))
    assert 'class="legend"' not in svg and svg.count('class="seg"') == 2
    assert re.findall(r'data-tip="([^"]+)"', svg) == ["Неделя 1: 3", "Неделя 2: 5"]


def test_empty_chart_says_so():
    assert "Пока нет данных" in str(stacked_bars(DAYS, [("Анна", [0, 0, 0], SERIES_COLORS[0])]))
    assert "Пока нет данных" in str(bars([], []))
