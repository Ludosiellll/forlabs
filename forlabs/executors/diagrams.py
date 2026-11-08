"""Схемы: DOT → PNG через viz.js (Graphviz в браузере). Строгий стиль: белый фон, чёрные линии, Times New Roman."""

from __future__ import annotations

import base64
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

VIZ_JS = Path(__file__).parent / "assets" / "viz-standalone.js"
STYLE = ('graph [fontname="Times New Roman", fontsize=14, bgcolor="white", pad=0.2, nodesep=0.35, ranksep=0.45];\n'
         'node [fontname="Times New Roman", fontsize=14, shape=box, color="black", fontcolor="black", '
         'style="solid", penwidth=1.0, margin="0.15,0.08"];\n'
         'edge [fontname="Times New Roman", fontsize=12, color="black", arrowsize=0.8, penwidth=1.0];\n')


def styled(dot: str) -> str:
    """Вставляет нейтральный стиль сразу после первой «{»; явные атрибуты автора схемы идут позже и важнее."""
    dot = dot.strip()
    i = dot.find("{")
    if i < 0:
        dot = "digraph {\n" + dot + "\n}"
        i = dot.find("{")
    # цветные заливки и радуга — ИИ-штамп; оставляем только оттенки серого
    dot = re.sub(r'(fill)?color\s*=\s*"?(#[0-9a-fA-F]{6}|[a-z]+[0-9]*)"?',
                 lambda m: m.group(0) if m.group(2).lower() in ("black", "white", "gray", "grey", "lightgray",
                                                                  "lightgrey", "gray90", "gray95", "#000000",
                                                                  "#ffffff") else (m.group(1) or "") + 'color="black"'
                 if not m.group(1) else 'fillcolor="white"', dot)
    return dot[:i + 1] + "\n" + STYLE + dot[i + 1:]


FALLBACK_EXECUTABLES = ["/opt/pw-browsers/chromium", "/usr/bin/chromium", "/usr/bin/chromium-browser",
                        "/usr/bin/google-chrome"]


def _launch(p):
    """Edge на Windows (есть всегда), иначе браузер Playwright, иначе любой найденный Chromium."""
    import os

    from forlabs.core.settings import load_settings

    channel = load_settings().browser_channel or ("msedge" if sys.platform == "win32" else None)
    attempts = []
    if channel:
        attempts.append(lambda: p.chromium.launch(channel=channel))
    attempts.append(lambda: p.chromium.launch())
    for exe in [os.environ.get("FORLABS_BROWSER", "")] + FALLBACK_EXECUTABLES:
        if exe and Path(exe).exists():
            attempts.append(lambda exe=exe: p.chromium.launch(executable_path=exe))
    last = None
    for attempt in attempts:
        try:
            return attempt()
        except Exception as e:
            last = e
    raise last or RuntimeError("Браузер не найден")


@lru_cache(maxsize=1)
def browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            b = _launch(p)
            b.close()
        return True
    except Exception:
        return False


def render_dot(dot: str, out_png: Path, scale: float = 2.0) -> Path | None:
    """Возвращает путь к PNG или None, если браузер недоступен или схема некорректна."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    html = ("<!doctype html><html><head><meta charset='utf-8'><style>body{margin:0;background:#fff}"
            "#out{display:inline-block;padding:4px}</style></head><body><div id='out'></div></body></html>")
    try:
        with sync_playwright() as p:
            browser = _launch(p)
            page = browser.new_page(device_scale_factor=scale)
            page.set_content(html)
            page.add_script_tag(content=VIZ_JS.read_text(encoding="utf-8"))
            ok = page.evaluate("""async (src) => {
                const viz = await Viz.instance();
                const svg = viz.renderSVGElement(src);
                document.getElementById('out').appendChild(svg);
                return true;
            }""", styled(dot))
            if not ok:
                return None
            out_png = Path(out_png)
            out_png.parent.mkdir(parents=True, exist_ok=True)
            page.locator("#out").screenshot(path=str(out_png))
            browser.close()
        return out_png
    except Exception:
        return None
