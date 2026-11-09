"""Пока агент работает в программе: плашка в углу экрана («ForLabs работает в … · Esc — остановить») и остановка,
если студент сам взялся за мышь или клавиатуру. Нажатия агента Windows помечает как программные (INJECTED) —
их хуки пропускают, настоящие — нет. Плашка не попадает в снимки (WDA_EXCLUDEFROMCAPTURE) и пропускает щелчки
сквозь себя."""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
LLKHF_INJECTED, LLMHF_INJECTED = 0x10, 0x01
WM_KEYDOWN, WM_SYSKEYDOWN = 0x100, 0x104
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_MOUSEWHEEL = 0x200, 0x201, 0x204, 0x207, 0x20A
WM_QUIT, WM_PAINT, WM_DESTROY, WM_NCHITTEST, WM_APP = 0x12, 0x0F, 0x02, 0x84, 0x8000
VK_ESCAPE = 0x1B
MOVE_LIMIT = 40  # столько пикселей настоящего движения мыши — «студент взял мышь»

LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL), ("rcPaint", wintypes.RECT),
                ("fRestore", wintypes.BOOL), ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_byte * 32)]


_FUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)  # модуль импортируется и в Linux (тесты)
HOOKPROC = _FUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
WNDPROC = _FUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


def _api():
    u, k, g = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32"), ctypes.WinDLL("gdi32")

    def sig(fn, res, *args):
        fn.restype, fn.argtypes = res, list(args)

    sig(u.SetWindowsHookExW, wintypes.HHOOK, ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
    sig(u.CallNextHookEx, LRESULT, wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    sig(u.UnhookWindowsHookEx, wintypes.BOOL, wintypes.HHOOK)
    sig(u.GetMessageW, wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
    sig(u.TranslateMessage, wintypes.BOOL, ctypes.POINTER(wintypes.MSG))
    sig(u.DispatchMessageW, LRESULT, ctypes.POINTER(wintypes.MSG))
    sig(u.PostThreadMessageW, wintypes.BOOL, wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    sig(u.DefWindowProcW, LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    sig(u.RegisterClassW, wintypes.ATOM, ctypes.POINTER(WNDCLASSW))
    sig(u.CreateWindowExW, wintypes.HWND, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE,
        wintypes.LPVOID)
    sig(u.DestroyWindow, wintypes.BOOL, wintypes.HWND)
    sig(u.ShowWindow, wintypes.BOOL, wintypes.HWND, ctypes.c_int)
    sig(u.SetLayeredWindowAttributes, wintypes.BOOL, wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD)
    sig(u.SetWindowDisplayAffinity, wintypes.BOOL, wintypes.HWND, wintypes.DWORD)
    sig(u.InvalidateRect, wintypes.BOOL, wintypes.HWND, ctypes.c_void_p, wintypes.BOOL)
    sig(u.BeginPaint, wintypes.HDC, wintypes.HWND, ctypes.POINTER(PAINTSTRUCT))
    sig(u.EndPaint, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(PAINTSTRUCT))
    sig(u.GetClientRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    sig(u.FillRect, ctypes.c_int, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH)
    sig(u.DrawTextW, ctypes.c_int, wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT),
        wintypes.UINT)
    sig(u.SystemParametersInfoW, wintypes.BOOL, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT)
    sig(u.GetAsyncKeyState, ctypes.c_short, ctypes.c_int)
    sig(u.PostMessageW, wintypes.BOOL, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    sig(k.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)
    sig(k.GetCurrentThreadId, wintypes.DWORD)
    sig(g.CreateSolidBrush, wintypes.HBRUSH, wintypes.COLORREF)
    sig(g.CreateFontW, wintypes.HFONT, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR)
    sig(g.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
    sig(g.SetTextColor, wintypes.COLORREF, wintypes.HDC, wintypes.COLORREF)
    sig(g.SetBkMode, ctypes.c_int, wintypes.HDC, ctypes.c_int)
    sig(g.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
    return u, k, g


class Watch:
    """watch.start(); … watch.check() перед каждым действием; watch.say("Открываю меню «Файл»"); watch.stop()."""

    def __init__(self, title: str, physical_only: bool = True):
        self.title = title
        self.line = "Готовлюсь…"
        self.reason: str | None = None
        self.hooked = False
        self.physical_only = physical_only  # в проверке на CI нажатия «студента» — программные
        self._ready = threading.Event()
        self._tid = 0
        self._hwnd = None
        self._moved = 0.0
        self._last_pt = None
        self._own_esc_until = 0.0
        self._keep = []
        self._thread = threading.Thread(target=self._loop, daemon=True, name="forlabs-agent-watch")

    # --- снаружи ---
    def start(self, timeout: float = 5.0) -> None:
        self._thread.start()
        self._ready.wait(timeout)

    def say(self, text: str) -> None:
        self.line = text.strip().replace("\n", " ")[:140]
        if self._hwnd:
            self._u.InvalidateRect(self._hwnd, None, True)

    def own_escape(self) -> None:
        """Агент сам жмёт Esc (закрыть окно-приветствие): не считать это остановкой."""
        self._own_esc_until = time.monotonic() + 1.0

    def check(self) -> None:
        from forlabs.agent.core import Stopped

        if not self.hooked and self.reason is None:  # хуков нет (запрещены политикой) — хотя бы Esc
            if self._u.GetAsyncKeyState(VK_ESCAPE) & 0x8000 and time.monotonic() > self._own_esc_until:
                self.reason = "остановлено (Esc)"
        if self.reason:
            raise Stopped(self.reason)

    def stop(self) -> None:
        if self._tid:
            self._u.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        self._thread.join(3)

    # --- решения по событиям (без Windows: проверяются тестами) ---
    def key_event(self, vk: int, injected: bool) -> None:
        if vk == VK_ESCAPE:
            if not injected or (not self.physical_only and time.monotonic() >= self._own_esc_until):
                self.reason = self.reason or "остановлено (Esc)"
        elif not injected:
            self.reason = self.reason or "ты нажал клавишу — остановил, чтобы не мешать"

    def mouse_event(self, msg: int, pt: tuple[int, int], injected: bool) -> None:
        if injected:
            self._last_pt = None  # программное движение сбрасывает отсчёт
            return
        if msg in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_MOUSEWHEEL):
            self.reason = self.reason or "ты щёлкнул мышью — остановил, чтобы не мешать"
        elif msg == WM_MOUSEMOVE:
            if self._last_pt is not None:
                self._moved += abs(pt[0] - self._last_pt[0]) + abs(pt[1] - self._last_pt[1])
                if self._moved > MOVE_LIMIT:
                    self.reason = self.reason or "ты взял мышь — остановил, чтобы не мешать"
            self._last_pt = pt

    # --- поток с очередью сообщений: хуки и плашка ---
    def _loop(self) -> None:
        u, k, g = self._u, self._k, self._g = _api()
        self._tid = k.GetCurrentThreadId()
        hinst = k.GetModuleHandleW(None)

        def on_key(code, wparam, lparam):
            if code >= 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                info = KBDLLHOOKSTRUCT.from_address(lparam)
                self.key_event(info.vkCode, bool(info.flags & LLKHF_INJECTED))
            return u.CallNextHookEx(None, code, wparam, lparam)

        def on_mouse(code, wparam, lparam):
            if code >= 0:
                info = MSLLHOOKSTRUCT.from_address(lparam)
                self.mouse_event(wparam, (info.pt.x, info.pt.y), bool(info.flags & LLMHF_INJECTED))
            return u.CallNextHookEx(None, code, wparam, lparam)

        kproc, mproc = HOOKPROC(on_key), HOOKPROC(on_mouse)
        self._keep += [kproc, mproc]
        hk = u.SetWindowsHookExW(WH_KEYBOARD_LL, kproc, hinst, 0)
        hm = u.SetWindowsHookExW(WH_MOUSE_LL, mproc, hinst, 0)
        self.hooked = bool(hk and hm)
        try:
            self._hwnd = self._overlay(hinst)
        except Exception:  # noqa: BLE001 — без плашки работать можно
            self._hwnd = None
        self._ready.set()
        msg = wintypes.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
        for h in (hk, hm):
            if h:
                u.UnhookWindowsHookEx(h)
        if self._hwnd:
            u.DestroyWindow(self._hwnd)
            self._hwnd = None

    def _overlay(self, hinst):
        u, g = self._u, self._g
        bg, fg = g.CreateSolidBrush(0x00382A1E), 0x00FFFFFF  # тёмно-синий фон, белый текст (BGR)
        font = g.CreateFontW(-15, 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")
        small = g.CreateFontW(-13, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")

        def proc(hwnd, msg, wparam, lparam):
            if msg == WM_NCHITTEST:
                return -1  # HTTRANSPARENT: щелчки — сквозь плашку
            if msg == WM_PAINT:
                ps = PAINTSTRUCT()
                hdc = u.BeginPaint(hwnd, ctypes.byref(ps))
                rc = wintypes.RECT()
                u.GetClientRect(hwnd, ctypes.byref(rc))
                u.FillRect(hdc, ctypes.byref(rc), bg)
                g.SetBkMode(hdc, 1)
                g.SetTextColor(hdc, fg)
                top = wintypes.RECT(14, 8, rc.right - 12, 30)
                g.SelectObject(hdc, font)
                u.DrawTextW(hdc, f"ForLabs работает в {self.title} · Esc — остановить", -1, ctypes.byref(top),
                            0x20 | 0x8000)  # SINGLELINE | END_ELLIPSIS
                g.SelectObject(hdc, small)
                bottom = wintypes.RECT(14, 32, rc.right - 12, rc.bottom - 6)
                u.DrawTextW(hdc, self.line, -1, ctypes.byref(bottom), 0x20 | 0x8000)
                u.EndPaint(hwnd, ctypes.byref(ps))
                return 0
            return u.DefWindowProcW(hwnd, msg, wparam, lparam)

        wndproc = WNDPROC(proc)
        self._keep.append(wndproc)
        wc = WNDCLASSW(0, wndproc, 0, 0, hinst, None, None, None, None, "ForLabsAgentOverlay")
        u.RegisterClassW(ctypes.byref(wc))
        work = wintypes.RECT()
        u.SystemParametersInfoW(0x30, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA
        w, h = 520, 58
        ex = 0x8 | 0x80 | 0x80000 | 0x20 | 0x08000000  # TOPMOST | TOOLWINDOW | LAYERED | TRANSPARENT | NOACTIVATE
        hwnd = u.CreateWindowExW(ex, "ForLabsAgentOverlay", "ForLabs", 0x80000000, work.right - w - 16,
                                 work.bottom - h - 16, w, h, None, None, hinst, None)  # WS_POPUP
        if not hwnd:
            return None
        u.SetLayeredWindowAttributes(hwnd, 0, 235, 0x2)  # LWA_ALPHA
        u.SetWindowDisplayAffinity(hwnd, 0x11)  # WDA_EXCLUDEFROMCAPTURE (Windows 10 2004+), иначе — без этого
        u.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
        return hwnd
