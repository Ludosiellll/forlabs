"""Windows: папки «Пуск» и рабочего стола, ярлыки .lnk, строка в «Параметры → Приложения» (HKCU)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from forlabs.installer.core import UNINSTALL_KEY

CSIDL_PROGRAMS = 0x0002
CSIDL_DESKTOPDIRECTORY = 0x0010


def _shell_folder(csidl: int) -> Path | None:
    """Настоящий путь (рабочий стол бывает перенесён в OneDrive)."""
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return None


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


class WindowsShell:
    def programs_dir(self) -> Path:
        return _shell_folder(CSIDL_PROGRAMS) or Path(os.environ.get("APPDATA", str(Path.home()))) / \
            "Microsoft" / "Windows" / "Start Menu" / "Programs"

    def desktop_dir(self) -> Path:
        return _shell_folder(CSIDL_DESKTOPDIRECTORY) or Path.home() / "Desktop"

    def create_shortcut(self, lnk: Path, target: Path, workdir: Path, description: str) -> None:
        """Ярлык через COM (pywin32), при любой ошибке — через PowerShell; не вышло обоими — обе причины."""
        com_error = ""
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()  # установка идёт в отдельном потоке
            try:
                s = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(lnk))
                s.TargetPath, s.WorkingDirectory = str(target), str(workdir)
                s.IconLocation, s.Description = f"{target},0", description
                s.Save()
            finally:
                pythoncom.CoUninitialize()
            if Path(lnk).exists():
                return
            com_error = "COM не сохранил ярлык"
        except Exception as e:
            com_error = f"{type(e).__name__}: {e}"
        script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(lnk))});"
                  f"$s.TargetPath={_ps_quote(str(target))};$s.WorkingDirectory={_ps_quote(str(workdir))};"
                  f"$s.IconLocation={_ps_quote(str(target) + ',0')};$s.Description={_ps_quote(description)};$s.Save()")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],  # noqa: S603,S607
                           capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode != 0 or not Path(lnk).exists():
            err = (r.stderr or b"").decode("utf-8", errors="replace").strip()[:300]
            raise OSError(f"COM: {com_error or 'нет'}; PowerShell: код {r.returncode} {err}")

    def remove_shortcut(self, lnk: Path) -> None:
        Path(lnk).unlink(missing_ok=True)

    def write_uninstall_entry(self, values: dict) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_WRITE) as k:
            for name, v in values.items():
                if isinstance(v, int):
                    winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, v)
                else:
                    winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(v))

    def read_uninstall_entry(self) -> dict | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
                out, i = {}, 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(k, i)
                    except OSError:
                        return out
                    out[name] = value
                    i += 1
        except FileNotFoundError:
            return None

    def remove_uninstall_entry(self) -> None:
        import winreg

        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        except FileNotFoundError:
            pass


def message_box(text: str, title: str, flags: int) -> int:
    import ctypes

    return ctypes.windll.user32.MessageBoxW(None, text, title, flags | 0x10000)  # MB_SETFOREGROUND


MB_OK, MB_YESNO = 0x0, 0x4
MB_ICONWARNING, MB_ICONQUESTION, MB_ICONINFO = 0x30, 0x20, 0x40
MB_DEFBUTTON2 = 0x100
IDYES = 6
