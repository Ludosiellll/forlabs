"""Графики админ-панели: встроенный SVG (без скриптов, библиотек и сети).

Правила оформления — навык dataviz: столбики не толще 24 px, скруглённый верх 4 px, ровное основание,
2 px просвета между отрезками стопки, тонкая сетка, легенда для двух и более рядов, подсказка на
каждый столбик (data-tip, её показывает app.js; <title> — запасной вариант), таблица чисел под графиком.
Цвета рядов — первые пять цветов проверенной палитры (validate_palette.js: все жёсткие проверки пройдены;
у трёх светлых цветов контраст ниже 3:1, поэтому имена рядов всегда подписаны, а числа есть в таблице).
"""

from __future__ import annotations

import math
from html import escape

from markupsafe import Markup

SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
ACCENT = "#1F5F4A"
GRID = "#E9EBED"
AXIS_TEXT = "#59626B"
SURFACE = "#FFFFFF"

W = 720
LEFT, RIGHT, TOP, BOTTOM = 52, 8, 10, 26
MAX_BAR = 24
GAP = 2
RADIUS = 4


def fmt_tokens(n: int | float | None) -> str:
    if n is None:
        return "—"
    n = int(n)
    if abs(n) < 10_000:
        return f"{n:,}".replace(",", " ")
    if abs(n) < 1_000_000:
        return f"{round(n / 1000)} тыс."
    m = n / 1_000_000
    text = f"{m:.1f}".replace(".", ",") if m < 10 else str(round(m))
    return f"{text.removesuffix(',0')} млн"


def nice_ticks(maximum: float) -> list[int]:
    """Круглые деления оси от нуля: шаг 1, 2 или 5 × 10^k, не больше пяти делений."""
    if maximum <= 0:
        return [0, 1]
    raw = maximum / 4
    power = 10 ** math.floor(math.log10(raw)) if raw >= 1 else 1
    step = next(m * power for m in (1, 2, 5, 10) if m * power >= raw)
    step = max(1, int(step))
    top = math.ceil(maximum / step) * step
    return list(range(0, int(top) + 1, step))


def _rounded_top(x: float, y: float, w: float, h: float) -> str:
    r = min(RADIUS, w / 2, h)
    return (f'M{x:.1f},{y + h:.1f} L{x:.1f},{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} '
            f'L{x + w - r:.1f},{y:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} L{x + w:.1f},{y + h:.1f} Z')


def _x_label_indices(n: int) -> set[int]:
    if n <= 8:
        return set(range(n))
    step = math.ceil(n / 7)
    return set(range(0, n, step)) | {n - 1}


def _empty(height: int) -> Markup:
    return Markup(f'<figure class="chart"><div class="chart-empty" style="height:{int(height * 0.6)}px">'
                  f'Пока нет данных</div></figure>')


def _frame(height: int, ticks: list[int], fmt, width: int) -> tuple[list[str], float]:
    plot_h = height - TOP - BOTTOM
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="min-width:{int(width * 0.8)}px" role="img" '
             f'xmlns="http://www.w3.org/2000/svg">']
    for t in ticks:
        y = TOP + plot_h - plot_h * t / ticks[-1]
        parts.append(f'<line x1="{LEFT}" x2="{width - RIGHT}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{LEFT - 8}" y="{y + 4:.1f}" text-anchor="end" class="ax">{escape(fmt(t))}</text>')
    return parts, plot_h


def _table(labels: list[str], series: list[tuple[str, list[int], str]], fmt) -> str:
    head = "".join(f'<th class="num">{escape(name)}</th>' for name, _, _ in series)
    total_col = len(series) > 1
    rows = []
    for i, label in enumerate(labels):
        vals = [s[1][i] for s in series]
        cells = "".join(f'<td class="num">{escape(fmt(v))}</td>' for v in vals)
        if total_col:
            cells += f'<td class="num">{escape(fmt(sum(vals)))}</td>'
        rows.append(f"<tr><td>{escape(label)}</td>{cells}</tr>")
    total_head = '<th class="num">Всего</th>' if total_col else ""
    return (f'<details class="chart-table"><summary>Числа</summary><div class="table-wrap"><table class="t">'
            f'<thead><tr><th></th>{head}{total_head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div></details>')


def _stack_tip(label: str, drawn: list[tuple[str, int, str]], total: int, fmt) -> str:
    lines = [label] + [f"{name}: {fmt(v)}" for name, v, _ in drawn]
    if len(drawn) > 1:
        lines.append(f"Всего: {fmt(total)}")
    return "\n".join(lines)


def stacked_bars(labels: list[str], series: list[tuple[str, list[int], str]], height: int = 220,
                 fmt=fmt_tokens, tip=_stack_tip, width: int = W) -> Markup:
    """Столбики стопкой: ряд = (имя, значения по меткам, цвет). Первый ряд — внизу."""
    totals = [sum(s[1][i] for s in series) for i in range(len(labels))]
    if not labels or not series or max(totals, default=0) <= 0:
        return _empty(height)
    ticks = nice_ticks(max(totals))
    parts, plot_h = _frame(height, ticks, fmt, width)
    band = (width - LEFT - RIGHT) / len(labels)
    bw = min(MAX_BAR, band * 0.62)
    show = _x_label_indices(len(labels))
    for i, label in enumerate(labels):
        x = LEFT + band * i + (band - bw) / 2
        base = TOP + plot_h
        drawn = [(name, s[i], color) for name, s, color in series if s[i] > 0]
        for k, (name, v, color) in enumerate(drawn):
            h = plot_h * v / ticks[-1]
            top = base - h
            bottom_gap = GAP if k > 0 else 0
            seg_h = max(1.0, h - bottom_gap)
            title = f"<title>{escape(label)}: {escape(name)} — {escape(fmt(v))}</title>"
            if k == len(drawn) - 1:
                parts.append(f'<path class="seg" d="{_rounded_top(x, top, bw, seg_h)}" fill="{color}">{title}</path>')
            else:
                parts.append(f'<rect class="seg" x="{x:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{seg_h:.1f}" '
                             f'fill="{color}">{title}</rect>')
            base = top
        parts.append(f'<rect class="hit" x="{LEFT + band * i:.1f}" y="{TOP}" width="{band:.1f}" height="{plot_h:.1f}" '
                     f'fill="transparent" data-tip="{escape(tip(label, drawn, totals[i], fmt))}"/>')
        if i in show:
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{height - 8}" text-anchor="middle" class="ax">{escape(label)}</text>')
    parts.append("</svg>")
    legend = ""
    if len(series) > 1:
        legend = '<div class="legend">' + "".join(
            f'<span><i style="background:{color}"></i>{escape(name)}</span>' for name, _, color in series) + "</div>"
    return Markup(f'<figure class="chart">{legend}<div class="chart-scroll">{"".join(parts)}</div>'
                  f'{_table(labels, series, fmt)}</figure>')


def bars(labels: list[str], values: list[int], color: str = ACCENT, height: int = 180, fmt=fmt_tokens,
         name: str = "", width: int = W) -> Markup:
    """Один ряд столбиков; легенды нет — что показано, говорит заголовок."""
    if not labels or max(values, default=0) <= 0:
        return _empty(height)
    return stacked_bars(labels, [(name or "Значение", values, color)], height=height, fmt=fmt, width=width,
                        tip=lambda label, drawn, total, f: f"{label}: {f(total)}")
