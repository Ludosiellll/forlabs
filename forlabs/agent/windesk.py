"""Руки и глаза агента в Windows.

Запуск программы студента и поиск её главного окна; снимок — только её окна (каждое через PrintWindow, сложенные
по порядку наложения: чужие окна и плашка ForLabs в снимок не попадают); список элементов через UI Automation;
мышь и клавиатура через SendInput — и только в окна этой программы: перед каждым щелчком проверяется, чьё окно
под точкой. Окна этой же программы, открытые студентом раньше (свой документ в Word), не трогаются."""

from __future__ import annotations

import ctypes
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from forlabs.agent import apps
from forlabs.agent.core import Action, Blocked, Element, Observation, Stopped
from forlabs.agent.keys import EXTENDED, EXTENDED_VK, KEYUP, UNICODE, absolute, utf16_units, vkey
from forlabs.runner import winapi


class AppError(Exception):
    """Программа не запустилась или ею нельзя управлять — причина для студента."""


_u = ctypes.WinDLL("user32", use_last_error=True) if hasattr(ctypes, "WinDLL") else None

MOVE, LDOWN, LUP, RDOWN, RUP, WHEEL, ABSOLUTE, VIRTUALDESK = 0x1, 0x2, 0x4, 0x8, 0x10, 0x800, 0x8000, 0x4000
MENU_CLASS = "#32768"


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", winapi.KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


if _u is not None:
    _u.SendInput.restype, _u.SendInput.argtypes = wintypes.UINT, [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    _u.GetSystemMetrics.restype, _u.GetSystemMetrics.argtypes = ctypes.c_int, [ctypes.c_int]
    _u.WindowFromPoint.restype, _u.WindowFromPoint.argtypes = wintypes.HWND, [wintypes.POINT]
    _u.GetAncestor.restype, _u.GetAncestor.argtypes = wintypes.HWND, [wintypes.HWND, wintypes.UINT]
    _u.GetForegroundWindow.restype, _u.GetForegroundWindow.argtypes = wintypes.HWND, []
    _u.IsWindow.restype, _u.IsWindow.argtypes = wintypes.BOOL, [wintypes.HWND]
    _u.IsIconic.restype, _u.IsIconic.argtypes = wintypes.BOOL, [wintypes.HWND]
    _u.GetTopWindow.restype, _u.GetTopWindow.argtypes = wintypes.HWND, [wintypes.HWND]
    _u.GetWindow.restype, _u.GetWindow.argtypes = wintypes.HWND, [wintypes.HWND, wintypes.UINT]
    _u.ShowWindow.restype, _u.ShowWindow.argtypes = wintypes.BOOL, [wintypes.HWND, ctypes.c_int]
    _u.GetWindowThreadProcessId.restype = wintypes.DWORD
    _u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u.MonitorFromWindow.restype, _u.MonitorFromWindow.argtypes = wintypes.HANDLE, [wintypes.HWND, wintypes.DWORD]
    _u.GetMonitorInfoW.restype, _u.GetMonitorInfoW.argtypes = wintypes.BOOL, [wintypes.HANDLE, ctypes.c_void_p]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def _send(items: list[INPUT]) -> None:
    arr = (INPUT * len(items))(*items)
    if _u.SendInput(len(items), arr, ctypes.sizeof(INPUT)) != len(items):
        raise Blocked("Windows не пропустил нажатие (программа запущена от имени администратора?)")


def _mouse(flags: int, x: int = 0, y: int = 0, data: int = 0) -> INPUT:
    return INPUT(type=0, u=_UNION(mi=MOUSEINPUT(x, y, data & 0xFFFFFFFF, flags, 0, 0)))


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    if vk in EXTENDED_VK:
        flags |= EXTENDED
    return INPUT(type=1, u=_UNION(ki=winapi.KEYBDINPUT(vk, scan, flags, 0, 0)))


class WinDesk:
    def __init__(self, key: str, exe: str, files: list[Path], cwd: str | Path, watch=None,
                 launch_timeout: float = 120, settle: float = 0.35):
        winapi.dpi_aware()
        self.key, self.app, self.watch, self.settle = key, apps.APPS[key], watch, settle
        self.names = {n.lower() for n in self.app.exes}
        self.pre = {w.hwnd for w in winapi.windows_of(self._by_name(), owned=True)}  # уже открытые студентом
        self.origin = (0, 0)
        self.last_fg = None
        try:
            self.proc = subprocess.Popen(apps.command(key, exe, files), cwd=str(cwd))
        except OSError as e:
            if getattr(e, "winerror", None) == 740:
                raise AppError(f"программа «{self.app.title}» запускается только от имени администратора — ForLabs "
                               "не может ею управлять. Сделай этот снимок сам.") from e
            raise AppError(f"программа «{self.app.title}» не запустилась: {e}") from e
        self.root = self.proc.pid
        self.main = self._wait_main(launch_timeout)
        self._fit(self.main)

    # --- какие окна наши ---
    def _by_name(self) -> set[int]:
        return {pid for pid, name in winapi.process_names().items() if name.lower() in self.names}

    def targets(self) -> set[int]:
        return winapi.process_tree({self.root}) | self._by_name()

    def _mine(self, owned: bool = True) -> list:
        return [w for w in winapi.windows_of(self.targets(), owned) if w.hwnd not in self.pre]

    @staticmethod
    def _size(hwnd: int) -> tuple[int, int]:
        left, top, right, bottom = winapi.window_rect(hwnd)
        return right - left, bottom - top

    def _wait_main(self, timeout: float) -> int:
        end, exited_at = time.monotonic() + timeout, None
        while time.monotonic() < end:
            if self.watch:
                self.watch.check()
            big = [w for w in self._mine(owned=False) if w.title and min(self._size(w.hwnd)) >= 150]
            if big:
                return max(big, key=lambda w: self._size(w.hwnd)[0] * self._size(w.hwnd)[1]).hwnd
            code = self.proc.poll()
            if code is not None:
                exited_at = exited_at or time.monotonic()
                # запускатель отдал файл уже открытой программе (VS Code, Word) — окно появится в другом процессе;
                # упал — окна не будет
                if code != 0 and time.monotonic() - exited_at > 10:
                    raise AppError(f"программа «{self.app.title}» закрылась сразу после запуска (код {code})")
            time.sleep(0.5)
        raise AppError(f"окно программы «{self.app.title}» не появилось за {int(timeout)} с"
                       + (f" ({self.app.note})" if self.app.note else ""))

    def _fit(self, hwnd: int) -> None:
        """Окно, вылезающее за экран, — развернуть: щёлкать можно только по видимому."""
        if _u.IsIconic(hwnd):
            _u.ShowWindow(hwnd, 9)
        left, top, right, bottom = winapi.window_rect(hwnd)
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        mon = _u.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if mon and _u.GetMonitorInfoW(mon, ctypes.byref(info)):
            w = info.rcWork
            if left < w.left or top < w.top or right > w.right or bottom > w.bottom:
                _u.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
                time.sleep(0.8)

    def _alive(self, grace: float = 45) -> None:
        """Главное окно на месте; закрылось — найти новое (окно входа сменилось главным окном Visual Studio, заставка —
        программой). Пока процесс программы жив, новое окно ждём до grace секунд."""
        if self.watch:
            self.watch.check()
        if _u.IsWindow(self.main):
            return
        end = time.monotonic() + grace
        while True:
            big = [w for w in self._mine(owned=False) if w.title and min(self._size(w.hwnd)) >= 150]
            if big:
                self.main = max(big, key=lambda w: self._size(w.hwnd)[0] * self._size(w.hwnd)[1]).hwnd
                self.last_fg = None
                return
            if not self.targets() or time.monotonic() > end:
                raise Stopped(f"программа «{self.app.title}» закрылась")
            if self.watch:
                self.watch.check()
            time.sleep(0.5)

    def _foreground(self) -> int | None:
        fg = _u.GetForegroundWindow()
        return fg if fg and fg in {w.hwnd for w in self._mine()} else None

    def _front(self) -> int:
        fg = self._foreground()
        if fg:
            self.last_fg = fg
            return fg
        for hwnd in (self.last_fg, self.main):
            if hwnd and _u.IsWindow(hwnd) and winapi.foreground(hwnd, timeout=2):
                time.sleep(0.3)
                self.last_fg = hwnd
                return hwnd
        raise Blocked(f"программа «{self.app.title}» не выходит на передний план — поверх открыто окно другой "
                      "программы")

    def _z(self) -> dict[int, int]:
        """Порядок наложения: больше — выше."""
        order, h, i = {}, _u.GetTopWindow(None), 0
        while h and i < 5000:
            order[int(h)] = i
            h, i = _u.GetWindow(h, 2), i + 1  # GW_HWNDNEXT
        return {k: len(order) - v for k, v in order.items()}

    # --- глаза ---
    def _picture(self, hwnds: list[int]):
        from PIL import Image, ImageGrab

        z = self._z()
        hwnds = sorted({h for h in hwnds if _u.IsWindow(h)}, key=lambda h: z.get(h, 0))
        rects = {h: winapi.window_rect(h) for h in hwnds}
        rects = {h: r for h, r in rects.items() if r[2] - r[0] > 2 and r[3] - r[1] > 2}
        if not rects:
            raise OSError("у окон программы нет размера")
        left, top = min(r[0] for r in rects.values()), min(r[1] for r in rects.values())
        right, bottom = max(r[2] for r in rects.values()), max(r[3] for r in rects.values())
        img = Image.new("RGB", (right - left, bottom - top), (240, 240, 240))
        for h in hwnds:
            if h not in rects:
                continue
            r = rects[h]
            try:
                piece = winapi.capture(h)
                if winapi.blank(piece):
                    raise OSError("пусто")
            except OSError:
                piece = ImageGrab.grab(bbox=r, all_screens=True)  # меню и окна, которые не рисуют себя в PrintWindow
            img.paste(piece.resize((r[2] - r[0], r[3] - r[1])) if piece.size != (r[2] - r[0], r[3] - r[1])
                      else piece, (r[0] - left, r[1] - top))
        if winapi.blank(img):
            raise OSError("снимок окна пустой — экран заблокирован или программа не рисует окно")
        return img, (left, top)

    def observe(self) -> Observation:
        from forlabs.agent import winuia

        self._alive()
        note = ""
        try:
            fg = self._front()
        except Blocked as e:
            fg, note = self.main, str(e)
        wins = self._mine()
        img, self.origin = self._picture([w.hwnd for w in wins])
        sources = [fg] + [w.hwnd for w in wins if w.cls == MENU_CLASS]
        raw = []
        for h in sources:
            raw += winuia.elements(h, timeout=4 if h == fg else 2)
        ox, oy = self.origin
        els, seen = [], set()
        for role, name, value, (x, y, w, h), enabled in raw:
            box = (x - ox, y - oy, w, h)
            outside = box[0] + w < 0 or box[1] + h < 0 or box[0] > img.width or box[1] > img.height
            if outside or (role, name, box) in seen:
                continue
            seen.add((role, name, box))
            els.append(Element(id=len(els) + 1, role=role, name=name, value=value, box=box, enabled=enabled))
        menus = sum(1 for w in wins if w.cls == MENU_CLASS)
        if menus:
            note = (note + " " if note else "") + "Открыто меню."
        return Observation(img, title=self._title(fg), elements=els, note=note, dialog=fg != self.main)

    @staticmethod
    def _title(hwnd: int) -> str:
        n = winapi.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        winapi.user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def capture(self, what: str):
        self._alive()
        fg = self._foreground() or self.main
        if what == "dialog" and fg != self.main:
            return self._picture([fg] + [w.hwnd for w in self._mine() if w.cls == MENU_CLASS])[0]
        others = [w.hwnd for w in self._mine() if w.cls == MENU_CLASS]
        return self._picture([self.main, *others])[0]

    # --- руки ---
    def _screen(self) -> tuple[int, int, int, int]:
        m = _u.GetSystemMetrics
        return m(76), m(77), m(78), m(79)

    def _point(self, a: Action, obs: Observation, second: bool = False) -> tuple[int, int]:
        eid = a.to_el if second else a.el
        if eid is not None:
            el = obs.element(eid)
            if el is None:
                raise Blocked(f"элемента [{eid}] нет")
            px, py = el.center
        else:
            px, py = (a.x2, a.y2) if second else (a.x, a.y)
        sx, sy = self.origin[0] + px, self.origin[1] + py
        vx, vy, vw, vh = self._screen()
        if not (vx <= sx < vx + vw and vy <= sy < vy + vh):
            raise Blocked("точка за краем экрана")
        hit = _u.WindowFromPoint(wintypes.POINT(sx, sy))
        top = _u.GetAncestor(hit, 2) if hit else None  # GA_ROOT
        pid = wintypes.DWORD()
        if top:
            _u.GetWindowThreadProcessId(top, ctypes.byref(pid))
        if not top or pid.value not in self.targets() or int(top) in self.pre:
            raise Blocked("в этой точке окно другой программы")
        return sx, sy

    def _move(self, sx: int, sy: int) -> None:
        ax, ay = absolute(sx, sy, self._screen())
        _send([_mouse(MOVE | ABSOLUTE | VIRTUALDESK, ax, ay)])
        time.sleep(0.05)

    def act(self, a: Action, obs: Observation) -> str:
        from forlabs.agent.guard import keys_of

        self._alive()
        fg = self._front()
        before = (fg, self._title(fg), len([w for w in self._mine() if w.cls == MENU_CLASS]))
        held: list[int] = []
        try:
            if a.do in ("click", "double_click", "right_click"):
                sx, sy = self._point(a, obs)
                self._move(sx, sy)
                down, up = (RDOWN, RUP) if a.do == "right_click" else (LDOWN, LUP)
                for _ in range(2 if a.do == "double_click" else 1):
                    _send([_mouse(down), _mouse(up)])
                    time.sleep(0.06)
            elif a.do == "drag":
                sx, sy = self._point(a, obs)
                ex, ey = self._point(a, obs, second=True)
                self._move(sx, sy)
                _send([_mouse(LDOWN)])
                for i in range(1, 11):
                    self._move(sx + (ex - sx) * i // 10, sy + (ey - sy) * i // 10)
                _send([_mouse(LUP)])
            elif a.do == "scroll":
                if a.el is not None or a.x is not None:
                    self._move(*self._point(a, obs))
                _send([_mouse(WHEEL, data=120 * (a.amount or -3))])
            elif a.do == "key":
                keys = [vkey(k) for k in keys_of(a.keys)]
                if a.keys.strip().lower() in ("esc", "escape") and self.watch:
                    self.watch.own_escape()
                for vk in keys:
                    _send([_key(vk)])
                    held.append(vk)
                for vk in reversed(keys):
                    _send([_key(vk, flags=KEYUP)])
                    held.remove(vk)
            elif a.do == "type":
                for unit in utf16_units(a.text.replace("\r\n", "\n")):
                    if unit == 0x0A:
                        _send([_key(0x0D), _key(0x0D, flags=KEYUP)])
                    elif unit == 0x09:
                        _send([_key(0x09), _key(0x09, flags=KEYUP)])
                    else:
                        _send([_key(0, unit, UNICODE), _key(0, unit, UNICODE | KEYUP)])
                    time.sleep(0.012)
        finally:
            for vk in held:  # Ctrl не должен остаться зажатым, что бы ни случилось
                _send([_key(vk, flags=KEYUP)])
        time.sleep(self.settle)
        self._alive()
        fg2 = _u.GetForegroundWindow()
        menus = len([w for w in self._mine() if w.cls == MENU_CLASS])
        if fg2 != before[0] or (fg2 and self._title(fg2) != before[1]):
            return f"!окно: «{self._title(fg2)}»" if fg2 else "!окно сменилось"
        if menus != before[2]:
            return "!открылось меню" if menus > before[2] else "меню закрылось"
        return ""
