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


kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
user32.GetWindowDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.restype = wintypes.HGDIOBJ
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def dpi_aware() -> None:
    """Координаты и снимки — в настоящих пикселях экрана (масштаб 125–150 % не мешает)."""
    try:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass


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


def windows_of(pids: set[int]) -> list[Window]:
    """Видимые окна верхнего уровня этих процессов."""
    found: list[Window] = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            found.append(Window(int(hwnd), pid.value, buf.value, cls.value))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def wait_window(pids: set[int], timeout: float = 30.0, predicate=None) -> Window | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        wins = [w for w in windows_of(process_tree(pids)) if predicate is None or predicate(w)]
        if wins:
            return wins[0]
        time.sleep(0.2)
    return None


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
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(full))
    w, h = full.right - full.left, full.bottom - full.top
    if w <= 0 or h <= 0:
        raise OSError("у окна нет размера (свёрнуто?)")
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
        if not gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bi), 0):
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


def foreground(hwnd: int) -> None:
    user32.ShowWindow(wintypes.HWND(hwnd), 9)  # SW_RESTORE
    user32.SetForegroundWindow(wintypes.HWND(hwnd))


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
