"""Программы, с которыми ForLabs умеет работать сам: как найти на компьютере, как открыть в них файлы лабы и что
предложить, если программы нет. Запускаются обычным образом (это программы самого студента), но управляет ими
только агент через окна этих программ."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

WINDOWS = sys.platform == "win32"


@dataclass
class App:
    key: str
    title: str
    kind: str  # ide, db, office, network, 1c, browser, console, graphics, editor
    exes: list[str]  # имена процессов (для программ, которые отдают файл уже открытому окну)
    where: list[str] = field(default_factory=list)  # шаблоны путей (glob, переменные окружения раскрываются)
    app_paths: list[str] = field(default_factory=list)  # имена в реестре App Paths
    open_args: Callable[[list[Path]], list[str]] = lambda files: [str(f) for f in files]
    get: str = ""  # официальная страница загрузки (если программы нет)
    note: str = ""  # что студенту знать заранее (вход в аккаунт, лицензия)
    find_hook: Callable[[], str | None] | None = None
    hints: list[str] = field(default_factory=list)  # приёмы работы в этой программе — модели в подсказки


def _vs() -> str | None:
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(pf86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        return None
    try:
        out = subprocess.run([str(vswhere), "-utf8", "-latest", "-products", "*", "-property", "productPath"],
                             capture_output=True, text=True, encoding="utf-8", timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.splitlines()[0] if out and Path(out.splitlines()[0]).is_file() else None


def _idle_args(files: list[Path]) -> list[str]:
    return ["-m", "idlelib", *[str(f) for f in files]]


def _vs_args(files: list[Path]) -> list[str]:
    sln = [f for f in files if f.suffix.lower() in (".sln", ".slnx", ".csproj", ".vcxproj")]
    rest = [str(f) for f in files if f not in sln]
    return [str(sln[0])] + (["/edit", *rest] if rest else []) if sln else ["/edit", *rest]


def _python() -> str | None:
    """pythonw студента (для IDLE): не свой Python ForLabs."""
    from forlabs.runner import languages

    t = languages.detect().get("python")
    if t is None:
        return None
    w = Path(t.path).with_name("pythonw.exe")
    return str(w) if w.is_file() else t.path


PF = ["%ProgramFiles%", "%ProgramFiles(x86)%", "%LOCALAPPDATA%\\Programs"]


def _pf(*tail: str) -> list[str]:
    return [os.path.join(root, *tail) for root in PF]


APPS: dict[str, App] = {a.key: a for a in [
    App("notepad", "Блокнот", "editor", ["notepad.exe"], ["%WINDIR%\\System32\\notepad.exe"]),
    App("notepadpp", "Notepad++", "editor", ["notepad++.exe"], _pf("Notepad++", "notepad++.exe"),
        ["notepad++.exe"], get="https://notepad-plus-plus.org/downloads/"),
    App("vscode", "Visual Studio Code", "ide", ["Code.exe"], _pf("Microsoft VS Code", "Code.exe"), ["Code.exe"],
        open_args=lambda files: ["-n", *[str(f) for f in files]], get="https://code.visualstudio.com/Download",
        hints=["открыть файл — Ctrl+P и имя файла; терминал — Ctrl+`"]),
    App("visualstudio", "Visual Studio", "ide", ["devenv.exe"], [], ["devenv.exe"], open_args=_vs_args,
        get="https://visualstudio.microsoft.com/ru/vs/community/", find_hook=_vs,
        note="при первом запуске Visual Studio может попросить войти в учётную запись Microsoft",
        hints=["Обозреватель решений — Ctrl+Alt+L; открыть файл проекта — двойной щелчок по нему там",
               "запуск без отладки — Ctrl+F5, окно консоли программы откроется отдельно",
               "окно входа в учётную запись при первом запуске — «Пропустить» / «Не сейчас»"]),
    App("idle", "IDLE (Python)", "ide", ["pythonw.exe", "python.exe"], open_args=_idle_args,
        get="https://www.python.org/downloads/windows/", find_hook=_python,
        hints=["запуск программы — F5 (Run Module); вывод появится в окне Shell"]),
    App("pycharm", "PyCharm", "ide", ["pycharm64.exe"], _pf("JetBrains", "PyCharm*", "bin", "pycharm64.exe"),
        ["pycharm64.exe"], get="https://www.jetbrains.com/pycharm/download/"),
    App("intellij", "IntelliJ IDEA", "ide", ["idea64.exe"], _pf("JetBrains", "IntelliJ IDEA*", "bin", "idea64.exe"),
        ["idea64.exe"], get="https://www.jetbrains.com/idea/download/"),
    App("codeblocks", "Code::Blocks", "ide", ["codeblocks.exe"], _pf("CodeBlocks", "codeblocks.exe"),
        get="https://www.codeblocks.org/downloads/"),
    App("devcpp", "Dev-C++", "ide", ["devcpp.exe"], _pf("Dev-Cpp", "devcpp.exe") + _pf("Embarcadero", "Dev-Cpp",
                                                                                         "devcpp.exe"),
        get="https://github.com/Embarcadero/Dev-Cpp/releases"),
    App("pascalabc", "PascalABC.NET", "ide", ["PascalABCNET.exe"], _pf("PascalABC.NET", "PascalABCNET.exe"),
        get="https://pascalabc.net/ssyilki-dlya-skachivaniya"),
    App("lazarus", "Lazarus", "ide", ["lazarus.exe"], ["C:\\lazarus\\lazarus.exe", *_pf("Lazarus", "lazarus.exe")],
        get="https://www.lazarus-ide.org/index.php?page=downloads"),
    App("ssms", "SQL Server Management Studio", "db", ["Ssms.exe"],
        _pf("Microsoft SQL Server Management Studio *", "Common7", "IDE", "Ssms.exe")
        + _pf("Microsoft SQL Server Management Studio *", "Release", "Common7", "IDE", "Ssms.exe"), ["Ssms.exe"],
        open_args=lambda files: ["-nosplash", *[str(f) for f in files]],
        get="https://learn.microsoft.com/ru-ru/ssms/install/install",
        hints=["окно подключения: сервер обычно (localdb)\\MSSQLLocalDB или .\\SQLEXPRESS, проверка подлинности "
               "Windows",
               "новый запрос — Ctrl+N, выполнить — F5, результат появится внизу"]),
    App("workbench", "MySQL Workbench", "db", ["MySQLWorkbench.exe"],
        _pf("MySQL", "MySQL Workbench *", "MySQLWorkbench.exe"), get="https://dev.mysql.com/downloads/workbench/",
        hints=["подключение — плитка Local instance на главной странице; запрос — Ctrl+Enter выполняет строку"]),
    App("packettracer", "Cisco Packet Tracer", "network", ["PacketTracer.exe"],
        _pf("Cisco Packet Tracer *", "bin", "PacketTracer.exe"),
        get="https://www.netacad.com/resources/lab-downloads",
        note="Packet Tracer при запуске просит войти в аккаунт Cisco: один раз войди сам и нажми «Повторить»",
        hints=["устройства — панель внизу слева: выбери тип, затем модель и перетащи её на рабочую область",
               "соединения — значок молнии в той же панели; настройка устройства — щелчок по нему, вкладки "
               "Config/CLI"]),
    App("1c", "1С:Предприятие", "1c", ["1cv8.exe", "1cv8c.exe"], _pf("1cv8", "*", "bin", "1cv8.exe"),
        open_args=lambda files: ["DESIGNER", "/F", str(files[0])] if files else ["DESIGNER"],
        note="нужна установленная 1С и база лабы",
        hints=["в конфигураторе: «Конфигурация → Открыть конфигурацию», дерево объектов слева",
               "запуск в режиме предприятия — F5 (отладка)"]),
    App("word", "Microsoft Word", "office", ["WINWORD.EXE"], _pf("Microsoft Office", "root", "Office16",
                                                                "WINWORD.EXE"), ["Winword.exe"],
        hints=["таблица — «Вставка → Таблица»; новый документ — «Новый документ» на начальном экране"]),
    App("excel", "Microsoft Excel", "office", ["EXCEL.EXE"], _pf("Microsoft Office", "root", "Office16", "EXCEL.EXE"),
        ["excel.exe"],
        hints=["к ячейке — через поле имени слева от строки формул (набери A4 и Enter); где курсор — видно там же",
               "пустая книга — Ctrl+N или «Пустая книга» на начальном экране"]),
    App("powerpoint", "Microsoft PowerPoint", "office", ["POWERPNT.EXE"],
        _pf("Microsoft Office", "root", "Office16", "POWERPNT.EXE"), ["powerpnt.exe"]),
    App("access", "Microsoft Access", "db", ["MSACCESS.EXE"], _pf("Microsoft Office", "root", "Office16",
                                                                 "MSACCESS.EXE"), ["MSACCESS.EXE"]),
    App("visio", "Microsoft Visio", "graphics", ["VISIO.EXE"], _pf("Microsoft Office", "root", "Office16", "VISIO.EXE"),
        ["visio.exe"]),
    App("libreoffice", "LibreOffice", "office", ["soffice.exe", "soffice.bin"], _pf("LibreOffice", "program",
                                                                                    "soffice.exe"),
        get="https://ru.libreoffice.org/download/",
        hints=["Calc: к ячейке — поле имени слева от строки формул; Writer: таблица — Ctrl+F12",
               "окно «Совет дня» при первом запуске — OK"]),
    App("drawio", "draw.io", "graphics", ["draw.io.exe"], _pf("draw.io", "draw.io.exe"),
        get="https://github.com/jgraph/drawio-desktop/releases"),
    App("paint", "Paint", "graphics", ["mspaint.exe"], ["%WINDIR%\\System32\\mspaint.exe"], ["mspaint.exe"]),
    App("cmd", "Командная строка", "console", ["cmd.exe", "conhost.exe"], ["%WINDIR%\\System32\\cmd.exe"],
        open_args=lambda files: ["/k"]),
]}


def _expand(pattern: str) -> list[str]:
    return sorted(glob.glob(os.path.expandvars(pattern)), reverse=True)  # новее версия — первая


def _app_path(name: str) -> str | None:
    if not WINDOWS:
        return None
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}") as k:
                v = winreg.QueryValue(k, None).strip('"')
                if v and Path(os.path.expandvars(v)).is_file():
                    return os.path.expandvars(v)
        except OSError:
            continue
    return None


def find(key: str, custom: dict[str, str] | None = None) -> str | None:
    """Путь к программе или None. custom — пути, которые студент указал сам («У меня она здесь»)."""
    app = APPS[key]
    if custom and custom.get(key) and Path(custom[key]).is_file():
        return custom[key]
    if app.find_hook is not None:
        return app.find_hook()
    for name in app.app_paths:
        if p := _app_path(name):
            return p
    for pat in app.where:
        for p in _expand(pat):
            if Path(p).is_file():
                return p
    for exe in app.exes:
        if p := shutil.which(exe):
            return p
    return None


def installed(custom: dict[str, str] | None = None) -> dict[str, str]:
    """Какие программы есть: ключ → путь."""
    out = {}
    for key in APPS:
        try:
            if p := find(key, custom):
                out[key] = p
        except Exception:  # noqa: BLE001 — сломанная запись реестра не должна мешать остальным
            continue
    return out


def command(key: str, exe: str, files: list[Path]) -> list[str]:
    return [exe, *APPS[key].open_args(list(files))]
