"""Запуск программы лабы в AppContainer — песочнице Windows, в которой работают приложения из Microsoft Store.

Программа в контейнере не видит файлы пользователя (Документы, Рабочий стол, Загрузки — их права не пускают
контейнер), не выходит в сеть (у контейнера нет разрешений internetClient и т.п.) и пишет только туда, куда ей
разрешили: папка запуска с копией проекта. Читать она может Windows и Program Files (они открыты всем контейнерам)
и папки инструментов, которым ForLabs выдал чтение (Python, JDK, Node, MinGW из профиля пользователя).
Прав администратора не нужно: профиль контейнера создаётся для текущего пользователя.

Сайт на PHP и MySQL здесь не запускаются: контейнеру закрыт localhost, а открыть его можно только от администратора."""

from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
from ctypes import wintypes
from pathlib import Path

NAME = "ForLabs.LabProgram"
WINDOWS = sys.platform == "win32"
_lock = threading.Lock()
_sid_cache: dict[str, str] = {}

PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_SUSPENDED, CREATE_UNICODE_ENVIRONMENT, CREATE_NEW_CONSOLE, CREATE_NO_WINDOW = 0x4, 0x400, 0x10, 0x08000000
HRESULT_ALREADY_EXISTS = 0x800700B7 - (1 << 32)  # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS) как знаковое


class ContainerError(OSError):
    pass


class SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [("AppContainerSid", ctypes.c_void_p), ("Capabilities", ctypes.c_void_p),
                ("CapabilityCount", wintypes.DWORD), ("Reserved", wintypes.DWORD)]


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


INTERNET_CLIENT = "S-1-15-3-1"  # исходящие соединения в интернет; локальная сеть и localhost — нет
SE_GROUP_ENABLED = 0x4


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD),
                ("dwThreadId", wintypes.DWORD)]


def _api():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ue = ctypes.WinDLL("userenv")
    adv = ctypes.WinDLL("advapi32", use_last_error=True)

    def sig(fn, res, *args):
        fn.restype, fn.argtypes = res, list(args)

    sig(ue.CreateAppContainerProfile, ctypes.c_long, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))
    sig(ue.DeriveAppContainerSidFromAppContainerName, ctypes.c_long, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p))
    sig(adv.ConvertSidToStringSidW, wintypes.BOOL, ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    sig(adv.ConvertStringSidToSidW, wintypes.BOOL, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p))
    sig(adv.FreeSid, ctypes.c_void_p, ctypes.c_void_p)
    sig(k32.LocalFree, ctypes.c_void_p, ctypes.c_void_p)
    sig(k32.InitializeProcThreadAttributeList, wintypes.BOOL, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t))
    sig(k32.UpdateProcThreadAttribute, wintypes.BOOL, ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p)
    sig(k32.DeleteProcThreadAttributeList, None, ctypes.c_void_p)
    sig(k32.CreateProcessW, wintypes.BOOL, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOEXW),
        ctypes.POINTER(PROCESS_INFORMATION))
    sig(k32.ResumeThread, wintypes.DWORD, wintypes.HANDLE)
    sig(k32.CloseHandle, wintypes.BOOL, wintypes.HANDLE)
    sig(k32.AssignProcessToJobObject, wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE)
    sig(k32.TerminateProcess, wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
    return k32, ue, adv


def sid(name: str = NAME) -> str:
    """SID контейнера строкой (S-1-15-2-…): профиль создаётся при первом обращении."""
    with _lock:
        if name in _sid_cache:
            return _sid_cache[name]
        k32, ue, adv = _api()
        psid = ctypes.c_void_p()
        hr = ue.CreateAppContainerProfile(name, "ForLabs — программа лабы", "Песочница для программ лабораторных",
                                          None, 0, ctypes.byref(psid))
        if hr == HRESULT_ALREADY_EXISTS or (hr & 0xFFFFFFFF) == 0x800700B7:
            hr = ue.DeriveAppContainerSidFromAppContainerName(name, ctypes.byref(psid))
        if hr != 0 or not psid:
            raise ContainerError(f"профиль песочницы не создан (HRESULT {hr & 0xFFFFFFFF:#x})")
        text = wintypes.LPWSTR()
        try:
            if not adv.ConvertSidToStringSidW(psid, ctypes.byref(text)):
                raise ContainerError("SID песочницы не читается")
            value = text.value
        finally:
            if text:
                k32.LocalFree(text)
            adv.FreeSid(psid)
        _sid_cache[name] = value
        return value


READ_EXECUTE = 0x001200A9  # FILE_GENERIC_READ | FILE_GENERIC_EXECUTE
FULL = 0x001F01FF  # FILE_ALL_ACCESS


def grant(path: str | Path, sid_text: str, access: int = READ_EXECUTE) -> bool:
    """Разрешить контейнеру доступ к папке и всему внутри (наследуемая запись). Делает это icacls — стандартная
    программа Windows: запись встаёт в правильном порядке, остальные права (пользователя, администраторов) не
    меняются. Папки пользователя меняет сам пользователь: прав администратора не нужно. False — не вышло."""
    import subprocess

    right = "F" if access == FULL else "RX"
    system = os.environ.get("SystemRoot", r"C:\Windows")
    icacls = os.path.join(system, "System32", "icacls.exe")
    try:
        r = subprocess.run([icacls, str(path), "/grant", f"*{sid_text}:(OI)(CI)({right})", "/C", "/Q"],
                           capture_output=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def rights(path: str | Path) -> str:
    """Права на папку текстом (для разбора на CI)."""
    import subprocess

    system = os.environ.get("SystemRoot", r"C:\Windows")
    r = subprocess.run([os.path.join(system, "System32", "icacls.exe"), str(path)], capture_output=True, text=True,
                       timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout


def open_to_all_containers(path: str | Path) -> bool:
    """Windows и Program Files уже читаются любым контейнером (ALL APPLICATION PACKAGES)."""
    p = os.path.normcase(os.path.abspath(str(path)))
    roots = [os.environ.get(v, "") for v in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
    return any(r and (p == os.path.normcase(r) or p.startswith(os.path.normcase(r) + os.sep)) for r in roots)


def tool_root(exe: str | Path) -> Path:
    """Папка установки инструмента по его exe: …\\bin\\javac.exe → папка JDK, python.exe → папка Python."""
    p = Path(exe).resolve().parent
    return p.parent if p.name.lower() == "bin" else p


def tool_dirs(cmd: list[str], work: Path) -> list[Path]:
    """Папки инструментов, которые программе нужно читать в песочнице: exe из команды (python, java, node) и всё,
    что нашёл languages.detect() (DLL MinGW, JDK), плюс программы, поставленные ForLabs."""
    from forlabs.core.paths import data_home

    out: list[Path] = [data_home() / "tools"]
    w = Path(work).resolve()
    for arg in cmd:
        p = Path(arg)
        if p.is_absolute() and p.is_file():
            try:
                p.resolve().relative_to(w)
                continue  # своё — в папке запуска
            except ValueError:
                out.append(tool_root(p))
    try:
        from forlabs.runner import languages

        out += [tool_root(t.path) for t in languages.detect().values() if t.path and Path(t.path).is_absolute()]
    except Exception:  # noqa: BLE001 — без поиска инструментов: хватит exe из команды
        pass
    return out


_GRANTED_FILE = "container-grants.json"


def ensure_read(paths: list[str | Path], sid_text: str) -> list[str]:
    """Чтение для контейнера на папки инструментов (один раз: помнится в data_home). Возвращает то, что не вышло."""
    from forlabs.core.paths import data_home

    memo = data_home() / _GRANTED_FILE
    try:
        done = set(json.loads(memo.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        done = set()
    failed = []
    for path in {str(Path(p)) for p in paths if p}:
        if path in done or open_to_all_containers(path) or not Path(path).exists():
            continue
        if grant(path, sid_text):
            done.add(path)
        else:
            failed.append(path)
    try:
        memo.parent.mkdir(parents=True, exist_ok=True)
        memo.write_text(json.dumps(sorted(done), ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return failed


def _env_block(env: dict[str, str]):
    text = "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0"
    return ctypes.create_unicode_buffer(text, len(text))


def spawn(cmd: str, cwd: str, sid_text: str, *, env: dict | None = None, new_console: bool = False,
          hidden: bool = False, memory_mb: int = 1024, max_processes: int = 16, internet: bool = False):
    """Как winapi.spawn, но в контейнере. Возвращает winapi.Launched (задание с лимитами, всё убивается вместе).
    internet=True — программа ходит в интернет (лаба про запросы к сайту, API); файлы пользователя ей всё так же
    закрыты, поэтому отправить ей нечего."""
    import pywintypes
    import win32job

    from forlabs.runner import winapi

    k32, _, adv = _api()
    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] = (win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                                                   | win32job.JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
                                                   | win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                                                   | win32job.JOB_OBJECT_LIMIT_JOB_MEMORY)
    info["BasicLimitInformation"]["ActiveProcessLimit"] = max_processes
    info["JobMemoryLimit"] = memory_mb * 2**20
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)

    psid = ctypes.c_void_p()
    if not adv.ConvertStringSidToSidW(sid_text, ctypes.byref(psid)):
        raise ContainerError("SID песочницы не разобран")
    size = ctypes.c_size_t()
    k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    attrs = ctypes.create_string_buffer(size.value)
    cap_sid = ctypes.c_void_p()
    cap_list = None
    if internet:
        if not adv.ConvertStringSidToSidW(INTERNET_CLIENT, ctypes.byref(cap_sid)):
            raise ContainerError("разрешение на интернет не разобрано")
        cap_list = (SID_AND_ATTRIBUTES * 1)(SID_AND_ATTRIBUTES(cap_sid, SE_GROUP_ENABLED))
    # без разрешений: ни сети, ни библиотек пользователя; с internet — только интернет
    caps = SECURITY_CAPABILITIES(psid, ctypes.cast(cap_list, ctypes.c_void_p) if cap_list else None,
                                 1 if cap_list else 0, 0)
    try:
        if not k32.InitializeProcThreadAttributeList(attrs, 1, 0, ctypes.byref(size)):
            raise ContainerError(f"InitializeProcThreadAttributeList: {ctypes.get_last_error()}")
        try:
            if not k32.UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                                                 ctypes.byref(caps), ctypes.sizeof(caps), None, None):
                raise ContainerError(f"UpdateProcThreadAttribute: {ctypes.get_last_error()}")
            si = STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
            si.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)
            pi = PROCESS_INFORMATION()
            flags = CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT
            flags |= CREATE_NEW_CONSOLE if new_console else (CREATE_NO_WINDOW if hidden else 0)
            block = _env_block({**os.environ, **(env or {})})
            line = ctypes.create_unicode_buffer(cmd)
            if not k32.CreateProcessW(None, line, None, None, False, flags, ctypes.cast(block, ctypes.c_void_p),
                                      cwd, ctypes.byref(si), ctypes.byref(pi)):
                raise ContainerError(f"программа не запустилась в песочнице (код {ctypes.get_last_error()})")
        finally:
            k32.DeleteProcThreadAttributeList(attrs)
    finally:
        adv.FreeSid(psid)
        if cap_sid:
            k32.LocalFree(cap_sid)
    try:
        if not k32.AssignProcessToJobObject(int(job), pi.hProcess):
            k32.TerminateProcess(pi.hProcess, 1)
            raise ContainerError(f"задание для программы не назначено ({ctypes.get_last_error()})")
    finally:
        k32.ResumeThread(pi.hThread)
        k32.CloseHandle(pi.hThread)
    return winapi.Launched(pi.dwProcessId, pywintypes.HANDLE(pi.hProcess), job)
