"""Windows (CI): снимки окон при масштабе экрана 125–150 %, как на большинстве ноутбуков.

Программа без поддержки масштаба (WinForms, tkinter) рисует окно в исходном размере, а Windows растягивает его на
экране. Снимок должен быть окном целиком, без чёрных полей справа и снизу. Масштаб ставится тем же способом, что и в
«Параметры → Дисплей» (DisplayConfigSetDeviceInfo), и возвращается после проверки.
Отчёт: build/probe/dpi.json, снимки: build/probe/shots/dpi_*.png."""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
import traceback
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

OUT = Path("build/probe").resolve()
SHOTS = OUT / "shots"
SCALES = [100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class HEADER(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int32), ("size", ctypes.c_uint32), ("adapterId", LUID), ("id", ctypes.c_uint32)]


class DPI_GET(ctypes.Structure):
    _fields_ = [("header", HEADER), ("minScaleRel", ctypes.c_int32), ("curScaleRel", ctypes.c_int32),
                ("maxScaleRel", ctypes.c_int32)]


class DPI_SET(ctypes.Structure):
    _fields_ = [("header", HEADER), ("scaleRel", ctypes.c_int32)]


def _source() -> tuple[LUID, int]:
    user32 = ctypes.windll.user32
    n_paths, n_modes = ctypes.c_uint32(), ctypes.c_uint32()
    QDC_ONLY_ACTIVE_PATHS = 2
    if user32.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), ctypes.byref(n_modes)):
        raise OSError("GetDisplayConfigBufferSizes")
    paths = (ctypes.c_ubyte * (72 * n_paths.value))()
    modes = (ctypes.c_ubyte * (64 * n_modes.value))()
    if user32.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), paths, ctypes.byref(n_modes), modes,
                                 None):
        raise OSError("QueryDisplayConfig")
    raw = bytes(paths[:72])
    luid = LUID.from_buffer_copy(raw[:8])
    return luid, int.from_bytes(raw[8:12], "little")


def get_scale() -> dict:
    luid, sid = _source()
    g = DPI_GET()
    g.header.type, g.header.size, g.header.adapterId, g.header.id = -3, ctypes.sizeof(g), luid, sid
    if ctypes.windll.user32.DisplayConfigGetDeviceInfo(ctypes.byref(g)):
        raise OSError("DisplayConfigGetDeviceInfo")
    rec = abs(g.minScaleRel)
    cur = rec + g.curScaleRel
    return {"recommended": SCALES[rec], "current": SCALES[cur], "max": SCALES[rec + g.maxScaleRel],
            "min": SCALES[rec + g.minScaleRel]}


def set_scale(percent: int) -> None:
    luid, sid = _source()
    g = DPI_GET()
    g.header.type, g.header.size, g.header.adapterId, g.header.id = -3, ctypes.sizeof(g), luid, sid
    ctypes.windll.user32.DisplayConfigGetDeviceInfo(ctypes.byref(g))
    rel = SCALES.index(percent) - abs(g.minScaleRel)
    s = DPI_SET()
    s.header.type, s.header.size, s.header.adapterId, s.header.id = -4, ctypes.sizeof(s), luid, sid
    s.scaleRel = max(g.minScaleRel, min(g.maxScaleRel, rel))
    if ctypes.windll.user32.DisplayConfigSetDeviceInfo(ctypes.byref(s)):
        raise OSError("DisplayConfigSetDeviceInfo")
    time.sleep(2.0)


def monitor_dpi() -> int:
    from forlabs.runner import winapi

    winapi.dpi_aware()
    mon = ctypes.windll.user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)
    x, y = ctypes.c_uint(), ctypes.c_uint()
    ctypes.windll.shcore.GetDpiForMonitor(mon, 0, ctypes.byref(x), ctypes.byref(y))
    return x.value


def black_edges(img) -> tuple[float, float]:
    """Доля совсем чёрных точек в правых и нижних 12 % снимка."""
    g = img.convert("L")
    w, h = g.size
    px = g.load()

    def share(xs, ys):
        pts = [(x, y) for x in xs for y in ys]
        return sum(1 for x, y in pts if px[x, y] < 8) / max(1, len(pts))

    right = share(range(int(w * 0.88), w, 2), range(0, h, 3))
    bottom = share(range(0, w, 3), range(int(h * 0.88), h, 2))
    return round(right, 3), round(bottom, 3)


TK = '''import tkinter as tk

root = tk.Tk()
root.title("Площадь прямоугольника")
root.geometry("360x200")
tk.Label(root, text="Длина:").grid(row=0, column=0, padx=10, pady=8, sticky="w")
tk.Entry(root).grid(row=0, column=1, padx=10)
tk.Label(root, text="Ширина:").grid(row=1, column=0, padx=10, pady=8, sticky="w")
tk.Entry(root).grid(row=1, column=1, padx=10)
tk.Label(root, text="Площадь: 24").grid(row=2, column=0, columnspan=2, pady=12)
root.mainloop()
'''


def main() -> int:
    from ci_window_probe import DESIGNER_GOOD, SUM_STEPS, build, plan

    from forlabs.runner import languages
    from forlabs.runner.shots import WindowsShooter

    OUT.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)
    report: dict = {"cases": {}}
    try:
        report["before"] = get_scale()
    except Exception as e:  # noqa: BLE001
        report["before"] = f"{type(e).__name__}: {e}"
    tools = languages.detect()
    winforms = build("dpi_winforms", plan(DESIGNER_GOOD, []), tools)
    tk = build("dpi_tkinter", {"language": "python", "kind": "gui", "name": "Площадь", "entry": "main.py",
                               "files": [{"path": "main.py", "content": TK}], "listings": [], "shots": []}, tools)
    from ci_lab_programs import CASES

    wpf = build("dpi_wpf", CASES["csharp_wpf"], tools)
    con = build("dpi_console", CASES["csharp_console"], tools)
    cases = [("winforms", winforms, {"mode": "window", "steps": SUM_STEPS, "expect": "20"}, True),
             ("tkinter", tk, {"mode": "window", "steps": []}, True),
             ("wpf", wpf, CASES["csharp_wpf"]["shots"][0], True),  # WPF сам поддерживает масштаб
             ("console", con, CASES["csharp_console"]["shots"][0], False)]  # у консоли фон чёрный сам по себе
    try:
        for percent in (125, 150):
            try:
                set_scale(percent)
                report[f"scale_{percent}"] = {**get_scale(), "monitor_dpi": monitor_dpi()}
            except Exception as e:  # noqa: BLE001
                report[f"scale_{percent}"] = f"{type(e).__name__}: {e}"
                continue
            if report[f"scale_{percent}"]["current"] != percent:
                continue  # такой масштаб на этом экране недоступен
            shooter = WindowsShooter()
            for name, target, shot, edges in cases:
                key, t0, res = f"{name}_{percent}", time.time(), {}
                try:
                    out = SHOTS / f"dpi_{key}.png"
                    r = shooter.shoot(target, shot, out)
                    res.update(shot_ok=r.ok, reason=r.reason, sandbox=r.sandbox)
                    if r.ok:
                        from PIL import Image

                        img = Image.open(out)
                        right, bottom = black_edges(img)
                        res.update(size=list(img.size), black_right=right, black_bottom=bottom)
                        res["ok"] = (right < 0.2 and bottom < 0.2) if edges else img.size[0] > 200
                    else:
                        res["ok"] = False
                except Exception as e:  # noqa: BLE001
                    res.update(ok=False, error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-1500:])
                res["sec"] = round(time.time() - t0, 1)
                report["cases"][key] = res
                print(f"[{key}] {'OK' if res.get('ok') else 'FAIL'} {res}", flush=True)
    finally:
        try:
            set_scale(100)
            report["after"] = get_scale()
        except Exception as e:  # noqa: BLE001
            report["after"] = f"{type(e).__name__}: {e}"
    (OUT / "dpi.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False), flush=True)
    bad = [k for k, v in report["cases"].items() if not v.get("ok")]
    print("НЕ ПРОШЛИ:", bad, "| проверено случаев:", len(report["cases"]), flush=True)
    return 1 if bad or not report["cases"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
