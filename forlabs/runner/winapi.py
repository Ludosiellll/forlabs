"""Windows: окна процессов, снимок окна (PrintWindow), запуск с пониженными правами в задании (job object),
ввод с клавиатуры в окно консоли. Только Windows; модуль импортируется лениво."""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

if sys.platform != "win32":  # pragma: no cover - модуль только для Windows
    raise ImportError("forlabs.runner.winapi работает только в Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

PW_RENDERFULLCONTENT = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9
GW_OWNER = 4
TH32CS_SNAPPROCESS = 0x2


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _sig(fn, restype, *argtypes):
    """Типы аргументов обязательны: 64-битные дескрипторы окон не влезают в int по умолчанию у ctypes."""
    fn.restype, fn.argtypes = restype, list(argtypes)


H, HW = wintypes.HANDLE, wintypes.HWND
_sig(kernel32.CreateToolhelp32Snapshot, H, wintypes.DWORD, wintypes.DWORD)
_sig(kernel32.Process32FirstW, wintypes.BOOL, H, ctypes.POINTER(PROCESSENTRY32W))
_sig(kernel32.Process32NextW, wintypes.BOOL, H, ctypes.POINTER(PROCESSENTRY32W))
_sig(kernel32.CloseHandle, wintypes.BOOL, H)
_sig(user32.EnumWindows, wintypes.BOOL, WNDENUMPROC, wintypes.LPARAM)
_sig(user32.IsWindowVisible, wintypes.BOOL, HW)
_sig(user32.GetWindow, HW, HW, wintypes.UINT)
_sig(user32.GetWindowThreadProcessId, wintypes.DWORD, HW, ctypes.POINTER(wintypes.DWORD))
_sig(user32.GetWindowTextLengthW, ctypes.c_int, HW)
_sig(user32.GetWindowTextW, ctypes.c_int, HW, wintypes.LPWSTR, ctypes.c_int)
_sig(user32.GetClassNameW, ctypes.c_int, HW, wintypes.LPWSTR, ctypes.c_int)
_sig(user32.GetWindowRect, wintypes.BOOL, HW, ctypes.POINTER(wintypes.RECT))
_sig(user32.GetWindowDC, wintypes.HDC, HW)
_sig(user32.ReleaseDC, ctypes.c_int, HW, wintypes.HDC)
_sig(user32.PrintWindow, wintypes.BOOL, HW, wintypes.HDC, wintypes.UINT)
_sig(user32.ShowWindow, wintypes.BOOL, HW, ctypes.c_int)
_sig(user32.SetForegroundWindow, wintypes.BOOL, HW)
_sig(user32.PostMessageW, wintypes.BOOL, HW, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_sig(gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
_sig(gdi32.CreateCompatibleBitmap, wintypes.HBITMAP, wintypes.HDC, ctypes.c_int, ctypes.c_int)
_sig(gdi32.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
_sig(gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
_sig(gdi32.DeleteDC, wintypes.BOOL, wintypes.HDC)
_sig(gdi32.GetDIBits, ctypes.c_int, wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
     ctypes.c_void_p, wintypes.UINT)
_sig(dwmapi.DwmGetWindowAttribute, ctypes.c_long, HW, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)


def dpi_aware() -> None:
    """Координаты и снимки — в настоящих пикселях экрана (масштаб 125–150 % не мешает)."""
    try:
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass


def process_names(pids: set[int] | None = None) -> dict[int, str]:
    """pid → имя exe (в нижнем регистре)."""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    out: dict[int, str] = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if pids is None or e.th32ProcessID in pids:
                out[e.th32ProcessID] = e.szExeFile.lower()
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    return out


HOSTS = {"conhost.exe", "cmd.exe", "openconsole.exe", "timeout.exe", "chcp.com"}


def program_alive(root: int) -> bool:
    """В окне консоли ещё работает сама программа (а не только cmd, который держит окно)."""
    names = process_names(process_tree({root}))
    return any(n not in HOSTS for p, n in names.items() if p != root)


def process_tree(roots: set[int]) -> set[int]:
    """Процессы и все их потомки (программа могла запустить conhost, браузер — свои процессы)."""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    parents: dict[int, int] = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            parents[e.th32ProcessID] = e.th32ParentProcessID
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    out = set(roots)
    grew = True
    while grew:
        grew = False
        for pid, parent in parents.items():
            if parent in out and pid not in out:
                out.add(pid)
                grew = True
    return out


@dataclass
class Window:
    hwnd: int
    pid: int
    title: str
    cls: str


def windows_of(pids: set[int], owned: bool = False) -> list[Window]:
    """Видимые окна верхнего уровня этих процессов; owned=True — и подчинённые (MessageBox, диалоги)."""
    found: list[Window] = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or (not owned and user32.GetWindow(hwnd, GW_OWNER)):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            found.append(Window(int(hwnd or 0), pid.value, buf.value, cls.value))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def wait_window(pids: set[int], timeout: float = 30.0, predicate=None, owned: bool = False) -> Window | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        wins = [w for w in windows_of(process_tree(pids), owned) if predicate is None or predicate(w)]
        if wins:
            return wins[0]
        time.sleep(0.2)
    return None


_sig(user32.EnumChildWindows, wintypes.BOOL, HW, WNDENUMPROC, wintypes.LPARAM)
_sig(user32.SendMessageTimeoutW, ctypes.c_size_t, HW, wintypes.UINT, wintypes.WPARAM, ctypes.c_void_p, wintypes.UINT,
     wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))


def child_texts(hwnd: int) -> list[str]:
    """Тексты окна и его элементов напрямую (WM_GETTEXT с таймаутом): быстро и без UIA — для окон сообщений."""
    out: list[str] = []

    def text_of(h) -> str:
        buf = ctypes.create_unicode_buffer(1024)
        res = ctypes.c_size_t()
        user32.SendMessageTimeoutW(h, 0x000D, 1024, ctypes.cast(buf, ctypes.c_void_p), 0x0002, 500,
                                   ctypes.byref(res))  # WM_GETTEXT, SMTO_ABORTIFHUNG
        return buf.value

    t = text_of(hwnd)
    if t:
        out.append(t)

    def cb(h, _):
        t = text_of(h)
        if t:
            out.append(t)
        return True

    user32.EnumChildWindows(wintypes.HWND(hwnd), WNDENUMPROC(cb), 0)
    return out


def size_of(hwnd: int) -> tuple[int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def describe(pids: set[int]) -> str:
    """Для причины сбоя: какие окна были у программы."""
    wins = windows_of(process_tree(pids), owned=True)
    return "; ".join(f"{w.cls} «{w.title[:40]}» {size_of(w.hwnd)[0]}×{size_of(w.hwnd)[1]}" for w in wins) or "окон нет"


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Видимая рамка окна без невидимых полей для растягивания (Windows 10/11)."""
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r),
                                    ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def capture(hwnd: int):
    """Снимок окна через PrintWindow: окно может быть закрыто другими окнами, снимок всё равно его."""
    from PIL import Image

    dpi_aware()
    full = wintypes.RECT()
    for _ in range(10):  # свёрнутое или ещё не показанное окно — развернуть и подождать
        user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(full))
        w, h = full.right - full.left, full.bottom - full.top
        if w > 40 and h > 40:
            break
        user32.ShowWindow(wintypes.HWND(hwnd), 9)  # SW_RESTORE
        time.sleep(0.3)
    if w <= 40 or h <= 40:
        raise OSError(f"у окна нет размера ({w}×{h})")
    hwnd_dc = user32.GetWindowDC(wintypes.HWND(hwnd))
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
    old = gdi32.SelectObject(mem_dc, bmp)
    try:
        if not user32.PrintWindow(wintypes.HWND(hwnd), mem_dc, PW_RENDERFULLCONTENT):
            raise OSError(f"PrintWindow не удался ({ctypes.get_last_error()})")
        bi = BITMAPINFOHEADER()
        bi.biSize, bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = ctypes.sizeof(bi), w, -h, 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mem_dc, bmp, 0, h, ctypes.cast(buf, ctypes.c_void_p), ctypes.cast(ctypes.byref(bi), ctypes.c_void_p), 0):
            raise OSError("GetDIBits не удался")
        img = Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(wintypes.HWND(hwnd), hwnd_dc)
    left, top, right, bottom = window_rect(hwnd)
    box = (left - full.left, top - full.top, right - full.left, bottom - full.top)
    if 0 <= box[0] < box[2] <= w and 0 <= box[1] < box[3] <= h:
        img = img.crop(box)
    return img


def blank(img) -> bool:
    """Чёрный или однотонный снимок — окно не отрисовалось."""
    lo, hi = img.convert("L").getextrema()
    return hi - lo < 12


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_sig(user32.SendInput, wintypes.UINT, wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_sig(user32.GetForegroundWindow, HW)
_sig(user32.keybd_event, None, wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t)
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x2, 0x4
VK = {"enter": 0x0D, "tab": 0x09, "esc": 0x1B, "space": 0x20, "backspace": 0x08, "down": 0x28, "up": 0x26,
      "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23, "f5": 0x74}


def foreground(hwnd: int, timeout: float = 3.0) -> bool:
    """Вывести окно вперёд (нужно для ввода с клавиатуры). Windows не даёт это фоновым процессам — нажатие Alt снимает
    запрет, как это делают средства автоматизации."""
    user32.ShowWindow(wintypes.HWND(hwnd), 9)  # SW_RESTORE
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        user32.keybd_event(0x12, 0, 0, 0)  # Alt
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.1)
    return False


def _send(inputs: list[INPUT]) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def type_text(hwnd: int, text: str, focus: bool = True) -> None:
    """Набрать текст в окне (Unicode — кириллица без раскладки). Окно выводится вперёд; паузы — чтобы медленные
    окна (tkinter, Swing) успевали принять каждое нажатие."""
    if focus:
        if user32.GetForegroundWindow() != hwnd:
            if not foreground(hwnd):
                raise OSError("окно не вышло на передний план")
            time.sleep(0.6)
    for ch in text:
        if ch == "\n":
            press(hwnd, "enter", focus=False)
            continue
        if ch == "\t":
            press(hwnd, "tab", focus=False)
            continue
        down = INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE, 0, 0)))
        up = INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)))
        _send([down, up])
        time.sleep(0.04)


def press(hwnd: int, key: str, focus: bool = True) -> None:
    """Нажать клавишу: enter, tab, esc, space, стрелки…"""
    if focus and user32.GetForegroundWindow() != hwnd:
        if not foreground(hwnd):
            raise OSError("окно не вышло на передний план")
        time.sleep(0.6)
    vk = VK[key.lower()]
    _send([INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(vk, 0, 0, 0, 0))),
           INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, 0)))])
    time.sleep(0.3)


def close_window(hwnd: int) -> None:
    user32.PostMessageW(wintypes.HWND(hwnd), 0x0010, 0, 0)  # WM_CLOSE


# --- запуск в задании (job) с пониженным уровнем целостности ---------------------------------------------

@dataclass
class Launched:
    pid: int
    process: object  # PyHANDLE процесса
    job: object  # PyHANDLE задания: закрытие убивает все процессы программы

    def alive(self) -> bool:
        import win32event

        return win32event.WaitForSingleObject(self.process, 0) != 0

    def wait(self, timeout: float) -> int | None:
        import win32event
        import win32process

        if win32event.WaitForSingleObject(self.process, int(timeout * 1000)) != 0:
            return None
        return win32process.GetExitCodeProcess(self.process)

    def kill(self) -> None:
        import win32job

        try:
            win32job.TerminateJobObject(self.job, 1)
        except Exception:
            pass

    def close(self) -> None:
        self.kill()
        import win32api

        for h in (self.process, self.job):
            try:
                win32api.CloseHandle(h)
            except Exception:
                pass


def low_label_dir(path: str) -> None:
    """Папка, в которую может писать программа с пониженными правами (метка Low integrity, как LocalLow)."""
    import win32security

    sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor("S:(ML;OICI;NW;;;LW)", 1)
    sacl = sd.GetSecurityDescriptorSacl()
    win32security.SetNamedSecurityInfo(path, win32security.SE_FILE_OBJECT,
                                       win32security.LABEL_SECURITY_INFORMATION, None, None, None, sacl)


def spawn(cmd: str, cwd: str, *, env: dict | None = None, low: bool = True, new_console: bool = False,
          hidden: bool = False, memory_mb: int = 1024, max_processes: int = 16) -> Launched:
    """Запуск программы: в задании (всё убивается вместе), с лимитом памяти и числа процессов; low=True —
    уровень целостности Low: программа не может менять файлы пользователя, реестр и чужие окна."""
    import win32api
    import win32con
    import win32job
    import win32process
    import win32security

    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] = (win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                                                   | win32job.JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
                                                   | win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                                                   | win32job.JOB_OBJECT_LIMIT_JOB_MEMORY)
    info["BasicLimitInformation"]["ActiveProcessLimit"] = max_processes
    info["JobMemoryLimit"] = memory_mb * 2**20
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    flags = win32con.CREATE_SUSPENDED | win32con.CREATE_UNICODE_ENVIRONMENT
    if new_console:
        flags |= win32con.CREATE_NEW_CONSOLE
    elif hidden:
        flags |= 0x08000000  # CREATE_NO_WINDOW
    si = win32process.STARTUPINFO()
    environment = {**os.environ, **(env or {})} if env is not None else None
    if low:
        tok = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
                                             win32con.TOKEN_DUPLICATE | win32con.TOKEN_QUERY
                                             | win32con.TOKEN_ADJUST_DEFAULT | win32con.TOKEN_ASSIGN_PRIMARY)
        low_tok = win32security.DuplicateTokenEx(tok, win32security.SecurityImpersonation,
                                                 win32con.MAXIMUM_ALLOWED, win32security.TokenPrimary)
        sid = win32security.ConvertStringSidToSid("S-1-16-4096")
        win32security.SetTokenInformation(low_tok, win32security.TokenIntegrityLevel,
                                          (sid, win32security.SE_GROUP_INTEGRITY))
        hp, ht, pid, _ = win32process.CreateProcessAsUser(low_tok, None, cmd, None, None, False, flags, environment,
                                                          cwd, si)
    else:
        hp, ht, pid, _ = win32process.CreateProcess(None, cmd, None, None, False, flags, environment, cwd, si)
    try:
        win32job.AssignProcessToJobObject(job, hp)
    finally:
        win32process.ResumeThread(ht)
        win32api.CloseHandle(ht)
    return Launched(pid, hp, job)
