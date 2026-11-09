"""Руки и глаза агента в Windows.

Запуск программы студента и поиск её главного окна; снимок — только её окна (каждое через PrintWindow, сложенные
по порядку наложения: чужие окна и плашка ForLabs в снимок не попадают); список элементов через UI Automation;
мышь и клавиатура через SendInput — и только в окна этой программы: перед каждым щелчком проверяется, чьё окно
под точкой. Окна этой же программы, открытые студентом раньше (свой документ в Word), не трогаются."""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from forlabs.agent import apps
from forlabs.agent.core import Action, AppError, Blocked, Element, Observation, Stopped  # noqa: F401
from forlabs.agent.keys import EXTENDED, EXTENDED_VK, KEYUP, UNICODE, absolute, utf16_units, vkey
from forlabs.runner import winapi


_u = ctypes.WinDLL("user32", use_last_error=True) if hasattr(ctypes, "WinDLL") else None

MOVE, LDOWN, LUP, RDOWN, RUP, WHEEL, ABSOLUTE, VIRTUALDESK = 0x1, 0x2, 0x4, 0x8, 0x10, 0x800, 0x8000, 0x4000
MENU_CLASS = "#32768"
BROWSERS = {"msedge.exe", "chrome.exe", "firefox.exe", "opera.exe", "browser.exe", "iexplore.exe", "brave.exe",
            "msedgewebview2.exe"}


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
    _u.AttachThreadInput.restype, _u.AttachThreadInput.argtypes = wintypes.BOOL, [wintypes.DWORD, wintypes.DWORD,
                                                                                  wintypes.BOOL]
    _u.BringWindowToTop.restype, _u.BringWindowToTop.argtypes = wintypes.BOOL, [wintypes.HWND]
    _u.SetForegroundWindow.restype, _u.SetForegroundWindow.argtypes = wintypes.BOOL, [wintypes.HWND]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def _elevated(pid: int | None) -> bool:
    """Процесс с правами администратора (None — сам ForLabs). Узнать не удалось — False: проверка лишь подсказка,
    дальше всё равно сработает «действия ничего не меняют»."""
    try:
        import win32api
        import win32con
        import win32security

        if pid is None:
            h = win32api.GetCurrentProcess()
        else:
            try:
                h = win32api.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            except Exception:  # noqa: BLE001 — не пускают даже посмотреть: почти наверняка администратор
                return True
        tok = win32security.OpenProcessToken(h, win32con.TOKEN_QUERY)
        return bool(win32security.GetTokenInformation(tok, win32security.TokenElevation))
    except Exception:  # noqa: BLE001
        return False


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
        if _elevated(self._pid_of(self.main)) and not _elevated(None):
            raise AppError(f"программа «{self.app.title}» запущена от имени администратора — Windows не даёт "
                           "ForLabs нажимать в ней. Закрой её и нажми «Сделать снимки в программах» снова.")
        self._fit(self.main)

    @staticmethod
    def _pid_of(hwnd: int) -> int:
        pid = wintypes.DWORD()
        _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    # --- какие окна наши ---
    def _by_name(self) -> set[int]:
        return {pid for pid, name in winapi.process_names().items() if name.lower() in self.names}

    def targets(self) -> set[int]:
        """Процессы программы: запущенный и его дочерние; для программ, которые отдают файл уже открытому окну или
        перезапускают себя (Visual Studio, Word, VS Code), — и по имени. Сам ForLabs и браузеры (ссылка «Войти» в
        программе открывает браузер) — никогда."""
        pids = winapi.process_tree({self.root}) | (self._by_name() if self.app.single else set())
        names = winapi.process_names(pids)  # только живые: process_tree возвращает и уже завершённый корень
        return {pid for pid in pids if pid in names and pid != os.getpid() and names[pid].lower() not in BROWSERS}

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
            if self.pre and time.monotonic() > end - timeout + 25 and not self._mine(owned=False):
                # Блокнот с вкладками, Notepad++: файл открылся во вкладке окна, которое студент открыл сам
                theirs = [w for w in winapi.windows_of(self._by_name()) if w.hwnd in self.pre]
                if theirs:
                    raise AppError(f"программа «{self.app.title}» открыла файл в окне, которое у тебя уже открыто, "
                                   "а в твои окна ForLabs не заходит. Закрой её и нажми «Сделать снимки в программах».")
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

    def _alive(self, grace: float = 180, relaunch: float = 20) -> None:
        """Главное окно на месте; закрылось — найти новое (окно входа сменилось главным окном Visual Studio, заставка —
        программой). Пока процесс программы жив, новое окно ждём до grace секунд."""
        if self.watch:
            self.watch.check()
        if _u.IsWindow(self.main):
            return
        end = time.monotonic() + grace
        gone_since = None  # Visual Studio после первого запуска перезапускает себя: процесса нет пару секунд
        while True:
            big = [w for w in self._mine(owned=False) if w.title and min(self._size(w.hwnd)) >= 150]
            if big:
                self.main = max(big, key=lambda w: self._size(w.hwnd)[0] * self._size(w.hwnd)[1]).hwnd
                self.last_fg = None
                return
            now = time.monotonic()
            if self.targets():
                gone_since = None
            else:
                gone_since = gone_since or now
            wait_relaunch = relaunch if self.app.single else 0  # перезапускают себя только программы вроде VS
            if now > end or (gone_since and now - gone_since >= wait_relaunch):
                raise Stopped(f"программа «{self.app.title}» закрылась" if gone_since else
                              f"программа «{self.app.title}» не показала окно за {int(grace // 60)} мин")
            if self.watch and hasattr(self.watch, "say") and int(now) % 5 == 0:
                self.watch.say(f"Жду, пока «{self.app.title}» откроется…")
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
            if hwnd and _u.IsWindow(hwnd) and self._bring(hwnd):
                time.sleep(0.3)
                self.last_fg = hwnd
                return hwnd
        raise Blocked(f"программа «{self.app.title}» не выходит на передний план — поверх открыто окно другой "
                      "программы")

    def _bring(self, hwnd: int) -> bool:
        """Вывести окно вперёд без лишних нажатий: одиночный Alt (обычный приём) в Word, Excel, Paint включает
        подсказки клавиш на ленте — следующий набор ушёл бы в команды. Сначала — без клавиш, Alt — в крайнем случае,
        и тогда подсказки сразу гасятся."""
        if _u.IsIconic(hwnd):
            _u.ShowWindow(hwnd, 9)
        _send([_mouse(MOVE, 0, 0)])  # своё «последнее действие пользователя»: Windows разрешает смену окна
        k32 = ctypes.windll.kernel32
        fg = _u.GetForegroundWindow()
        mine_tid = k32.GetCurrentThreadId()
        their_tid = _u.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = bool(their_tid and their_tid != mine_tid and _u.AttachThreadInput(mine_tid, their_tid, True))
        try:
            _u.BringWindowToTop(hwnd)
            _u.SetForegroundWindow(hwnd)
        finally:
            if attached:
                _u.AttachThreadInput(mine_tid, their_tid, False)
        for _ in range(10):
            if _u.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.05)
        if winapi.foreground(hwnd, timeout=2):  # Alt — последний способ
            time.sleep(0.2)
            if self.app.kind in ("office", "graphics") or self.key in ("paint", "access"):  # лента: погасить подсказки
                if self.watch:
                    self.watch.own_escape()
                _send([_key(0x1B), _key(0x1B, flags=KEYUP)])
            return True
        return False

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
        hwnds = sorted({h for h in hwnds if _u.IsWindow(h) and not _u.IsIconic(h)}, key=lambda h: z.get(h, 0))
        rects = {h: winapi.window_rect(h) for h in hwnds}
        # свёрнутые окна «стоят» в (-32000, -32000): в рамку снимка не берём
        rects = {h: r for h, r in rects.items()
                 if r[2] - r[0] > 2 and r[3] - r[1] > 2 and r[0] > -20000 and r[1] > -20000}
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
                if not self._visible(h, r):  # поверх чужое окно: с экрана снимать нельзя — оно попало бы в отчёт
                    continue
                piece = ImageGrab.grab(bbox=r, all_screens=True)  # меню и окна, которые не рисуют себя в PrintWindow
            img.paste(piece.resize((r[2] - r[0], r[3] - r[1])) if piece.size != (r[2] - r[0], r[3] - r[1])
                      else piece, (r[0] - left, r[1] - top))
        if winapi.blank(img):
            raise OSError("снимок окна пустой — экран заблокирован или программа не рисует окно")
        return img, (left, top)

    def _visible(self, hwnd: int, r: tuple[int, int, int, int]) -> bool:
        """Окно видно на экране целиком: в углах и середине — оно само или окна программы."""
        mine = self.targets()
        w, h = r[2] - r[0], r[3] - r[1]
        for fx, fy in ((0.1, 0.1), (0.9, 0.1), (0.5, 0.5), (0.1, 0.9), (0.9, 0.9)):
            hit = _u.WindowFromPoint(wintypes.POINT(r[0] + int(w * fx), r[1] + int(h * fy)))
            top = _u.GetAncestor(hit, 2) if hit else None
            if not top or self._pid_of(top) not in mine:
                return False
        return True

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
            raw += winuia.elements(h, timeout=8 if h == fg else 3)  # первый запрос к большой программе долгий
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

    def _still_here(self, fg: int) -> None:
        """Посреди набора или перетаскивания: студент не остановил, окно программы всё ещё впереди."""
        if self.watch:
            self.watch.check()
        now = _u.GetForegroundWindow()
        if now != fg and (not now or self._pid_of(now) not in self.targets()):
            raise Blocked("окно программы ушло с переднего плана — набор остановлен, чтобы текст не попал в "
                          "чужое окно")

    def act(self, a: Action, obs: Observation) -> str:
        from forlabs.agent.guard import keys_of

        self._alive()
        fg = self._front()
        before = (fg, self._title(fg), len([w for w in self._mine() if w.cls == MENU_CLASS]))
        held: list[int] = []
        held_mouse = False
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
                held_mouse = True
                for i in range(1, 11):
                    self._still_here(fg)
                    self._move(sx + (ex - sx) * i // 10, sy + (ey - sy) * i // 10)
                _send([_mouse(LUP)])
                held_mouse = False
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
                for i, unit in enumerate(utf16_units(a.text.replace("\r\n", "\n"))):
                    if i % 8 == 0:  # студент нажал Esc или щёлкнул в другое окно — остальное туда не уйдёт
                        self._still_here(fg)
                    if unit == 0x0A:
                        _send([_key(0x0D), _key(0x0D, flags=KEYUP)])
                    elif unit == 0x09:
                        _send([_key(0x09), _key(0x09, flags=KEYUP)])
                    else:
                        _send([_key(0, unit, UNICODE), _key(0, unit, UNICODE | KEYUP)])
                    time.sleep(0.012)
        finally:
            for vk in held:  # Ctrl не должен остаться зажатым, что бы ни случилось
                try:
                    _send([_key(vk, flags=KEYUP)])
                except Exception:  # noqa: BLE001 — отпустить остальные всё равно
                    pass
            if held_mouse:
                try:
                    _send([_mouse(LUP)])
                except Exception:  # noqa: BLE001
                    pass
        time.sleep(self.settle)
        self._alive()
        fg2 = _u.GetForegroundWindow()
        menus = len([w for w in self._mine() if w.cls == MENU_CLASS])
        if fg2 != before[0] or (fg2 and self._title(fg2) != before[1]):
            return f"!окно: «{self._title(fg2)}»" if fg2 else "!окно сменилось"
        if menus != before[2]:
            return "!открылось меню" if menus > before[2] else "меню закрылось"
        return ""
