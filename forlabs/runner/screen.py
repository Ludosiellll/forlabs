"""Расчёты для снимков окон, не зависящие от Windows (их можно проверять тестами на любой системе)."""

from __future__ import annotations


def render_geometry(full: tuple[int, int, int, int], frame: tuple[int, int, int, int],
                    scale: float) -> tuple[tuple[int, int], tuple[int, int, int, int]]:
    """Размер картинки, которую окно рисует в PrintWindow, и видимая рамка окна в ней. full — GetWindowRect, frame —
    видимая рамка без невидимых полей, обе в пикселях экрана; scale — во сколько раз окно растянуто на экране:
    программа без поддержки масштаба рисует окно в исходном размере, а Windows растягивает его (125 % — в 1,25 раза)."""
    fl, ft, fr, fb = full
    s = scale if scale > 0 else 1.0
    size = (max(1, round((fr - fl) / s)), max(1, round((fb - ft) / s)))
    left, top, right, bottom = frame
    box = (round((left - fl) / s), round((top - ft) / s), round((right - fl) / s), round((bottom - ft) / s))
    return size, box
