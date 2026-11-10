"""Языки программ лаб: где взять инструмент (сначала то, что поставил студент, потом папка инструментов ForLabs),
файлы сборки по шаблонам ForLabs, сборка с короткими понятными ошибками для модели, чем запускать программу.

Саму программу здесь не запускают — кроме run_console (тесты и запасной путь, когда нет песочницы Windows)."""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping
from urllib.parse import quote

from forlabs import version
from forlabs.core import paths
from forlabs.runner.plan import CODE_EXT, ProjectPlan, validate

WINDOWS = sys.platform == "win32"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
NEW_GROUP = 0x00000200  # CREATE_NEW_PROCESS_GROUP

TOOL_TITLES = {"dotnet": ".NET SDK", "msvc": "Visual Studio (C++)", "gpp": "MinGW (g++)", "clang": "Clang",
               "python": "Python", "java": "JDK (Java)", "node": "Node.js", "go": "Go", "php": "PHP",
               "mysql": "MySQL", "edge": "Microsoft Edge"}
NEEDS = {"csharp": ["dotnet"], "python": ["python"], "java": ["java"], "javascript": ["node"], "go": ["go"],
         "php": ["php"], "mysql": ["mysql"], "web": ["edge"]}
CPP_COMPILERS = ("msvc", "gpp", "clang")  # C++ собирает любой; ForLabs умеет поставить MinGW
BUILD_DIRS = ("bin", "obj", "out")  # результаты сборки: в копию проекта для сдачи не попадают

# Имена в PATH и проверка «инструмент работает»: аргументы и шаблон версии в выводе
PATH_NAMES = {"dotnet": ["dotnet"], "gpp": ["g++"], "clang": ["clang++"], "python": ["python3", "python"],
              "java": ["javac"], "node": ["node", "nodejs"], "go": ["go"], "php": ["php"], "mysql": ["mysql"]}
PROBES = {"dotnet": (["--list-sdks"], r"^(\d+\.\d+\.\d+\S*)"), "gpp": (["--version"], r"(\d+\.\d+(?:\.\d+)?)"),
          "clang": (["--version"], r"version\s+(\d+\.\d+(?:\.\d+)?)"),
          "python": (["--version"], r"^Python\s+(3\.\d+(?:\.\d+)?)"), "java": (["-version"], r"javac\s+(\d[\w.]*)"),
          "node": (["--version"], r"^v(\d+\.\d+\.\d+)"), "go": (["version"], r"\bgo(\d+\.\d+(?:\.\d+)?)"),
          "php": (["-v"], r"^PHP\s+(\d+\.\d+\.\d+)"), "mysql": (["--version"], r"Ver\s+(\d+\.\d+(?:\.\d+)?)")}

DOTNET_ENV = {"DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
              "DOTNET_CLI_UI_LANGUAGE": "en", "MSBUILDDISABLENODEREUSE": "1", "DOTNET_CLI_USE_MSBUILD_SERVER": "0"}
JAVAC_FLAGS = ["-J-Duser.language=en", "-J-Duser.country=US", "-J-Dstdout.encoding=UTF-8",
               "-J-Dstderr.encoding=UTF-8", "-J-Dsun.stdout.encoding=UTF-8", "-J-Dsun.stderr.encoding=UTF-8"]
JAVA_RUN = ["-Dfile.encoding=UTF-8", "-Dstdout.encoding=UTF-8", "-Dstderr.encoding=UTF-8",
            "-Dsun.stdout.encoding=UTF-8", "-Dsun.stderr.encoding=UTF-8"]
PY_ENV = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
BOM_EXT = {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx", ".inl"}  # Visual Studio без BOM читает как 1251
# Синтаксис Python без __pycache__ в папке студента (py_compile пишет .pyc)
PY_CHECK = ("import sys, traceback\nbad = 0\nfor f in sys.argv[1:]:\n    try:\n"
            "        compile(open(f, 'rb').read(), f, 'exec')\n    except (SyntaxError, ValueError) as e:\n"
            "        bad = 1\n        print(''.join(traceback.format_exception_only(type(e), e)).rstrip())\n"
            "sys.exit(bad)\n")
LIMIT = 1_000_000  # байт вывода, больше не держим в памяти

CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <OutputType>{output}</OutputType>
    <TargetFramework>{tfm}</TargetFramework>{gui}
    <RootNamespace>{ns}</RootNamespace>
    <AssemblyName>{asm}</AssemblyName>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>disable</Nullable>
    <LangVersion>latest</LangVersion>
    <SatelliteResourceLanguages>ru</SatelliteResourceLanguages>
  </PropertyGroup>

  <ItemGroup>
    <None Update="**\\*.txt;**\\*.csv;**\\*.json;**\\*.xml;**\\*.dat" CopyToOutputDirectory="PreserveNewest" />
  </ItemGroup>

</Project>
"""
NOISE = re.compile(r"\s*(?:Picked up (?:JAVA_TOOL_OPTIONS|_JAVA_OPTIONS|JDK_JAVA_OPTIONS)|No syntax errors detected|"
                   r"Errors parsing |Note: |Determining projects to restore|All projects are up-to-date|Restored |"
                   r"Build FAILED|Time Elapsed|\d+ (?:Warning|Error)\(s\)|MSBuild version|Node\.js v\d|"
                   r"at (?:[\w.<>]+ \()?node:)")


@dataclass
class Toolchain:
    name: str           # dotnet, msvc, gpp, clang, python, java, node, go, php, mysql, edge
    path: str           # исполняемый файл (java — javac, msvc — cl.exe)
    version: str = ""
    args: list[str] = field(default_factory=list)  # py -3 → ["-3"]
    setup: str = ""     # msvc: vcvars64.bat, из него окружение для cl


@dataclass
class BuildResult:
    ok: bool
    log: str = ""                # ошибки сборки — коротко, пути внутри проекта; уходят модели на исправление
    artifact: Path | None = None  # exe/dll, папка классов Java или главный файл (Python, PHP, сайт, SQL)


@dataclass
class RunSpec:
    cmd: list[str]       # пусто — открыть url в браузере
    cwd: Path
    console: bool        # программа работает в окне консоли (иначе — своё окно или страница)
    env: dict[str, str] = field(default_factory=dict)  # добавки к окружению
    url: str = ""        # что показать в браузере (сайт)


# ---------- поиск инструментов ----------

def default_tools_dir() -> Path:
    """Папка переносных инструментов ForLabs (их ставит сама программа)."""
    return paths.data_home() / "tools"


def forlabs_locations(tdir: Path, windows: bool = WINDOWS) -> dict[str, list[Path]]:
    x = ".exe" if windows else ""
    t = Path(tdir)
    return {"dotnet": [t / "dotnet" / f"dotnet{x}"], "python": [t / "python" / f"python{x}"],
            "node": [t / "node" / f"node{x}"], "go": [t / "go" / "bin" / f"go{x}"],
            "java": [t / "jdk" / "bin" / f"javac{x}"], "php": [t / "php" / f"php{x}"],
            "gpp": [t / "mingw64" / "bin" / f"g++{x}"], "mysql": [t / "mysql" / "bin" / f"mysql{x}"]}


def windows_locations(env: Mapping[str, str]) -> dict[str, list[str]]:
    """Куда эти инструменты ставятся в Windows по умолчанию (шаблоны с * — для glob)."""
    W = PureWindowsPath
    pf = W(env.get("ProgramFiles") or r"C:\Program Files")
    pf86 = W(env.get("ProgramFiles(x86)") or r"C:\Program Files (x86)")
    drive = W((env.get("SystemDrive") or "C:") + "\\")
    local = env.get("LOCALAPPDATA")
    jdks = ("Java", "Eclipse Adoptium", "Microsoft", "Zulu", "BellSoft", "Amazon Corretto")
    loc = {
        "dotnet": [pf / "dotnet" / "dotnet.exe"],
        "gpp": [drive / "msys64" / "ucrt64" / "bin" / "g++.exe", drive / "msys64" / "mingw64" / "bin" / "g++.exe",
                drive / "mingw64" / "bin" / "g++.exe", drive / "MinGW" / "bin" / "g++.exe",
                pf / "CodeBlocks" / "MinGW" / "bin" / "g++.exe", pf86 / "CodeBlocks" / "MinGW" / "bin" / "g++.exe",
                pf86 / "Dev-Cpp" / "MinGW64" / "bin" / "g++.exe", drive / "TDM-GCC-64" / "bin" / "g++.exe"],
        "clang": [pf / "LLVM" / "bin" / "clang++.exe"],
        "python": ([W(local) / "Programs" / "Python" / "Python3*" / "python.exe"] if local else [])
        + [pf / "Python3*" / "python.exe", drive / "Python3*" / "python.exe"],
        "java": ([W(env["JAVA_HOME"]) / "bin" / "javac.exe"] if env.get("JAVA_HOME") else [])
        + [pf / j / "*" / "bin" / "javac.exe" for j in jdks],
        "node": [pf / "nodejs" / "node.exe"],
        "go": ([W(env["GOROOT"]) / "bin" / "go.exe"] if env.get("GOROOT") else [])
        + [pf / "Go" / "bin" / "go.exe", drive / "Go" / "bin" / "go.exe"],
        "php": [drive / "xampp" / "php" / "php.exe", drive / "php" / "php.exe"],
        "mysql": [pf / "MySQL" / "MySQL Server *" / "bin" / "mysql.exe", drive / "xampp" / "mysql" / "bin" / "mysql.exe"],
        "edge": [pf86 / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                 pf / "Microsoft" / "Edge" / "Application" / "msedge.exe"],
        "vswhere": [pf86 / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"],
    }
    return {k: [str(p) for p in v] for k, v in loc.items()}


def _natural(s: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _candidates(key: str, env: Mapping[str, str], tdir: Path) -> list[tuple[str, list[str]]]:
    """Где искать, по порядку: у студента (JAVA_HOME, PATH, обычные папки установки), потом папка ForLabs."""
    out: list[tuple[str, list[str]]] = []
    if key == "java" and env.get("JAVA_HOME"):
        out.append((os.path.join(env["JAVA_HOME"], "bin", "javac.exe" if WINDOWS else "javac"), []))
    for name in PATH_NAMES.get(key, []):
        if p := shutil.which(name, path=env.get("PATH", "")):
            out.append((p, []))
    if WINDOWS:
        if key == "python" and (py := shutil.which("py", path=env.get("PATH", ""))):
            out.append((py, ["-3"]))
        for pat in windows_locations(env).get(key, []):
            out += [(p, []) for p in sorted(glob.glob(pat), key=_natural, reverse=True)]
    out += [(str(p), []) for p in forlabs_locations(tdir).get(key, [])]
    own = sys.executable if version.frozen() else None
    seen: set[str] = set()
    good = []
    for p, args in out:
        k = os.path.normcase(p) + " ".join(args)
        if k in seen or not os.path.isfile(p) or "\\windowsapps\\" in p.lower() or (own and _same(p, own)):
            continue  # WindowsApps — заглушка «поставьте из Microsoft Store»; свой Python ForLabs не берём
        seen.add(k)
        good.append((p, args))
    return good


def _probe(cmd: list[str], env: Mapping[str, str], timeout: float = 30) -> str | None:
    """Вывод команды, если она отработала с кодом 0, иначе None."""
    full = {**os.environ, **env, **DOTNET_ENV}
    try:
        r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout, env=full,
                           creationflags=NO_WINDOW if WINDOWS else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    return (r.stdout + r.stderr).decode("utf-8", "replace") if r.returncode == 0 else None


def _version(key: str, path: str, args: list[str], env: Mapping[str, str]) -> str | None:
    """Версия работающего инструмента; None — не работает (или .NET без SDK, или Python 2)."""
    flags, pat = PROBES[key]
    out = _probe([path, *args, *flags], env)
    if out is None:
        return None
    if key == "dotnet":
        found = re.findall(pat, out, re.M)
        return max(found, key=_natural) if found else None
    m = re.search(pat, out, re.M)
    if key == "python" and not m:
        return None
    return m[1] if m else ""


def _find(key: str, env: Mapping[str, str], tdir: Path) -> Toolchain | None:
    for path, args in _candidates(key, env, tdir):
        if (ver := _version(key, path, args, env)) is not None:
            return Toolchain(key, path, ver, args)
    return None


def _msvc(env: Mapping[str, str]) -> Toolchain | None:
    """Visual Studio с C++: vswhere → папка установки → vcvars64.bat и самый новый cl.exe."""
    vswhere = windows_locations(env)["vswhere"][0]
    if not os.path.isfile(vswhere):
        return None
    out = _probe([vswhere, "-utf8", "-latest", "-products", "*", "-requires",
                  "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"], env) or ""
    inst = out.strip().splitlines()[0].strip() if out.strip() else ""
    if not inst:
        return None
    vcvars = os.path.join(inst, "VC", "Auxiliary", "Build", "vcvars64.bat")
    cls = sorted(glob.glob(os.path.join(inst, "VC", "Tools", "MSVC", "*", "bin", "Hostx64", "x64", "cl.exe")),
                 key=_natural, reverse=True)
    if not cls or not os.path.isfile(vcvars):
        return None
    return Toolchain("msvc", cls[0], Path(cls[0]).parents[3].name, setup=vcvars)


def detect(env: Mapping[str, str] | None = None, tools_dir: Path | str | None = None) -> dict[str, Toolchain]:
    """Какие инструменты есть: {ключ: Toolchain}. Ключи: dotnet, msvc, gpp, clang, python, java, node, go, php,
    mysql, edge. Свой Python ForLabs (собранная программа) не берётся никогда."""
    env = dict(os.environ if env is None else env)
    tdir = Path(tools_dir) if tools_dir is not None else default_tools_dir()
    with ThreadPoolExecutor(max_workers=len(PROBES)) as pool:
        found = dict(zip(PROBES, pool.map(lambda k: _find(k, env, tdir), PROBES), strict=True))
    if WINDOWS:
        found["msvc"] = _msvc(env)
        edge = next((p for p in windows_locations(env)["edge"] if os.path.isfile(p)), None)
        found["edge"] = Toolchain("edge", edge) if edge else None
    return {k: v for k, v in found.items() if v is not None}


def missing(plan: ProjectPlan, tools: Mapping[str, Toolchain]) -> list[str]:
    """Каких инструментов не хватает плану (ключи для кнопок «Установить»). C++ — любой компилятор; если нет ни
    одного, просим MinGW (gpp): его ForLabs ставит сам."""
    if plan.language == "cpp":
        return [] if any(k in tools for k in CPP_COMPILERS) else ["gpp"]
    need = list(NEEDS[plan.language])
    if plan.kind == "web" and plan.language in ("php", "javascript"):
        need.append("edge")
    return [k for k in need if k not in tools]


# ---------- файлы проекта ----------

_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                      "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def _translit(name: str) -> str:
    """«Калькулятор» → «Kalkulyator»: имя exe как дал бы студент, а не безликое program.exe."""
    out = []
    for ch in name:
        t = _TRANSLIT.get(ch.lower())
        out.append(ch if t is None else (t.capitalize() if ch.isupper() else t))
    return "".join(out)


def _ascii(name: str, default: str) -> str:
    """Имя для go.mod и exe: только латиница, цифры и «_» (русское название — транслитом)."""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", _translit(name)).strip("_")
    return s if s[:1].isalpha() else f"{default}_{s}".strip("_")


def project_name(plan: ProjectPlan) -> str:
    """Имя .csproj и сборки C#: буквы (и русские), цифры, «_», «-», «.»."""
    return re.sub(r"[^\w.-]+", "_", plan.name).strip("._") or "Lab"


def _namespace(plan: ProjectPlan) -> str:
    s = re.sub(r"\W+", "_", plan.name).strip("_")
    return s if s[:1].isalpha() else f"Lab_{s}".strip("_")


def _tfm(tools: Mapping[str, Toolchain]) -> str:
    m = re.match(r"(\d+)\.", tools["dotnet"].version) if "dotnet" in tools else None
    major = int(m[1]) if m else 8
    return f"net{major}.0" if major >= 5 else "netcoreapp3.1"


def _go_version(tools: Mapping[str, Toolchain]) -> str:
    m = re.match(r"(1\.\d+)", tools["go"].version) if "go" in tools else None
    return m[1] if m else "1.21"


def _is_esm(plan: ProjectPlan) -> bool:
    return any(re.search(r"^\s*(?:import\s*[\w{*\"']|export\s)", f.content, re.M)
               for f in plan.files if f.path.lower().endswith(".js"))


def templates(plan: ProjectPlan, tools: Mapping[str, Toolchain] | None = None) -> dict[str, str]:
    """Файлы сборки ForLabs: .csproj (C#), go.mod (Go), package.json (Node, если модель не дала свой)."""
    tools = tools or {}
    if plan.language == "csharp":
        gui = {"winforms": "\n    <UseWindowsForms>true</UseWindowsForms>",
               "wpf": "\n    <UseWPF>true</UseWPF>"}.get(plan.kind, "")
        tfm = _tfm(tools) + ("-windows" if gui else "")
        if gui:
            gui += "\n    <EnableWindowsTargeting>true</EnableWindowsTargeting>"
        text = CSPROJ.format(output="WinExe" if gui else "Exe", tfm=tfm, gui=gui, ns=_namespace(plan),
                             asm=project_name(plan))
        return {f"{project_name(plan)}.csproj": text}
    if plan.language == "go":
        mod = _ascii(plan.name, "lab").lower()
        if mod in ("go", "std", "cmd", "main", "builtin", "unsafe", "c") or "." in mod:
            mod = f"{mod}_lab"  # имена, которые Go занимает сам («module go» роняет сборку)
        return {"go.mod": f"module {mod}\n\ngo {_go_version(tools)}\n"}
    if plan.language == "javascript" and plan.file("package.json") is None:
        pkg = {"name": _ascii(plan.name, "lab").lower().replace("_", "-"), "version": "1.0.0", "private": True,
               "main": plan.entry}
        if _is_esm(plan):
            pkg["type"] = "module"
        return {"package.json": json.dumps(pkg, ensure_ascii=False, indent=2) + "\n"}
    return {}


def materialize(plan: ProjectPlan, project_dir: Path | str, tools: Mapping[str, Toolchain] | None = None) -> Path:
    """Пишет файлы проекта и файлы сборки ForLabs в пустую (или новую) папку. План сначала проверяется."""
    if problems := validate(plan):
        raise ValueError("План проекта не прошёл проверку: " + "; ".join(problems))
    d = Path(project_dir)
    if d.exists() and any(d.iterdir()):
        raise ValueError(f"Папка проекта не пуста: {d}")
    d.mkdir(parents=True, exist_ok=True)
    files = {f.path: f.content for f in plan.files} | templates(plan, tools)
    for rel, text in files.items():
        p = d.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8")
        if PurePosixPath(rel).suffix.lower() in BOM_EXT and not text.startswith("\ufeff"):
            data = b"\xef\xbb\xbf" + data
        p.write_bytes(data)
    return d


# ---------- запуск процессов ----------

def _kill_tree(p: subprocess.Popen) -> None:
    if WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, creationflags=NO_WINDOW)
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        p.kill()
    except OSError:
        pass


def _execute(cmd: list[str], cwd: Path, env: Mapping[str, str], stdin: bytes = b"", timeout: float = 60,
             limit: int = LIMIT) -> tuple[int | None, bytes]:
    """Запуск с вводом, общим выводом (stdout+stderr, не больше limit байт) и таймаутом. По таймауту убивается
    всё дерево процессов; код None."""
    kw = {"creationflags": NO_WINDOW | NEW_GROUP} if WINDOWS else {"start_new_session": True}
    p = subprocess.Popen(cmd, cwd=cwd, env=dict(env), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, **kw)
    buf = bytearray()

    def read() -> None:
        while chunk := p.stdout.read1(65536):
            if len(buf) < limit:
                buf.extend(chunk[: limit - len(buf)])

    def write() -> None:
        try:
            if stdin:
                p.stdin.write(stdin)
            p.stdin.close()
        except OSError:
            pass

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    threading.Thread(target=write, daemon=True).start()
    try:
        code = p.wait(timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        code = None
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            pass
    reader.join(5)
    return code, bytes(buf)


def _run(cmd: list[str], cwd: Path, extra: Mapping[str, str] | None = None, timeout: float = 300,
         base: Mapping[str, str] | None = None) -> tuple[int | None, str]:
    env = {**(os.environ if base is None else base), **(extra or {})}
    try:
        code, out = _execute(cmd, cwd, env, b"", timeout)
    except OSError as e:
        return -1, f"Не удалось запустить {Path(cmd[0]).name}: {e}"
    text = out.decode("utf-8", "replace")
    if code is None:
        text += f"\nСборка не уложилась в {timeout:g} с и остановлена"
    return code, text


def run_console(spec: RunSpec, stdin_text: str = "", timeout: float = 10, limit: int = LIMIT) -> tuple[int | None, str]:
    """Запустить консольную программу без окна: ввод из stdin_text, общий вывод. По таймауту программа и всё,
    что она запустила, убиваются; код возврата тогда None. Для тестов и как запасной путь без песочницы Windows."""
    try:
        code, out = _execute(spec.cmd, spec.cwd, {**os.environ, **spec.env}, stdin_text.encode("utf-8"), timeout, limit)
    except OSError as e:
        return -1, f"Не удалось запустить программу: {e}"
    text = out.decode("utf-8", "replace")
    if code is None:
        text += f"\n[ForLabs: программа не завершилась за {timeout:g} с и остановлена]"
    return code, text


# ---------- сборка ----------

def trim_log(text: str, project_dir: Path, drop: set[str] = frozenset(), max_lines: int = 60,
             max_chars: int = 6000) -> str:
    """Лог сборки для модели: пути внутри проекта, без шума инструментов и повторов, не длиннее нужного."""
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text).replace("\r\n", "\n").replace("\r", "\n")
    for base in sorted({str(project_dir), str(Path(project_dir).resolve())}, key=len, reverse=True):
        for sep in ("\\", "/"):
            text = text.replace(base + sep, "")
        text = text.replace(base, ".")
    lines: list[str] = []
    seen: set[str] = set()
    for line in text.split("\n"):
        line = re.sub(r"\s*\[[^\]\n]*\.csproj\]\s*$", "", line.rstrip())
        if NOISE.match(line) or line.strip() in drop:
            continue
        if re.search(r"\b(?:error|warning)\b", line, re.I):
            if line in seen:
                continue  # MSBuild печатает каждую ошибку дважды
            seen.add(line)
        lines.append(line)
    lines = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n").split("\n")
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… ещё {len(lines) - max_lines} строк"]
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars] + "\n… (обрезано)"


def _sources(plan: ProjectPlan, exts: set[str]) -> list[str]:
    """Исходники как пути «./…»: имя файла никогда не станет ключом компилятора или node."""
    return sorted("./" + f.path for f in plan.files if PurePosixPath(f.path).suffix.lower() in exts)


def _exe(d: Path, plan: ProjectPlan) -> Path:
    out = d / "out"
    out.mkdir(exist_ok=True)
    return out / (_ascii(plan.name, "program") + (".exe" if WINDOWS else ""))


_VCVARS: dict[str, dict[str, str]] = {}


def parse_set_output(text: str) -> dict[str, str]:
    """Вывод «set» из cmd → окружение. Строки «=C:=C:\\…» — служебные переменные cmd, их не передать дальше."""
    return dict(ln.split("=", 1) for ln in text.splitlines() if "=" in ln and not ln.startswith("="))


def _vcvars_env(vcvars: str) -> dict[str, str] | None:
    """Окружение Visual Studio: vcvars64.bat, затем «set» (cmd /u — в UTF-16, чтобы кириллица в путях не ломалась)."""
    if vcvars not in _VCVARS:
        try:
            r = subprocess.run(f'cmd.exe /d /u /s /c ""{vcvars}" >nul 2>&1 && set"', capture_output=True,
                               timeout=180, creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
        _VCVARS[vcvars] = parse_set_output(r.stdout.decode("utf-16-le", "replace"))
    return dict(_VCVARS[vcvars])


def _failed(code: int | None, log: str, d: Path, drop: set[str] = frozenset()) -> BuildResult:
    return BuildResult(False, trim_log(log, d, drop) or f"Сборка завершилась с кодом {code}", None)


def _build_csharp(d: Path, plan: ProjectPlan, tools, timeout) -> BuildResult:
    asm = project_name(plan)
    code, log = _run([tools["dotnet"].path, "build", f"{asm}.csproj", "-c", "Debug", "-nologo", "-v", "q",
                      "-clp:NoSummary;ErrorsOnly", "-nodeReuse:false", "-p:UseSharedCompilation=false"],
                     d, DOTNET_ENV, timeout)
    if code != 0:
        return _failed(code, log, d)
    for ext in (".exe", ".dll"):
        if hits := sorted(d.glob(f"bin/Debug/*/{asm}{ext}")):
            return BuildResult(True, trim_log(log, d), hits[0])
    return BuildResult(False, f"Сборка прошла, но {asm}.exe не найден в bin/Debug", None)


def _build_cpp(d: Path, plan: ProjectPlan, tools, timeout) -> BuildResult:
    srcs = _sources(plan, CODE_EXT["cpp"])
    exe = _exe(d, plan)
    rel = str(exe.relative_to(d))
    if "msvc" in tools:
        env = _vcvars_env(tools["msvc"].setup)
        if env is None:
            return BuildResult(False, "Не удалось подготовить окружение Visual Studio (vcvars64.bat)", None)
        cmd = [tools["msvc"].path, "/nologo", "/EHsc", "/utf-8", "/std:c++17", "/W3", f"/Fe{rel}", "/Foout\\", *srcs]
        code, log = _run(cmd, d, {"VSLANG": "1033"}, timeout, base=env)
    else:
        t = tools["gpp"] if "gpp" in tools else tools["clang"]
        cmd = [t.path, "-std=c++17", "-finput-charset=UTF-8", "-fexec-charset=UTF-8", "-fdiagnostics-color=never",
               "-I.", "-o", rel, *srcs] + (["-static"] if WINDOWS and t.name == "gpp" else [])
        code, log = _run(cmd, d, {"LC_ALL": "C", "LANG": "C"}, timeout)
    if code != 0 or not exe.is_file():
        return _failed(code, log, d, {PurePosixPath(s).name for s in srcs})
    return BuildResult(True, trim_log(log, d, {PurePosixPath(s).name for s in srcs}), exe)


def _build_java(d: Path, plan: ProjectPlan, tools, timeout) -> BuildResult:
    cmd = [tools["java"].path, "-encoding", "UTF-8", "-d", "out", "-nowarn", "-Xmaxerrs", "30", *JAVAC_FLAGS,
           *_sources(plan, {".java"})]
    code, log = _run(cmd, d, None, timeout)
    return _failed(code, log, d) if code != 0 else BuildResult(True, trim_log(log, d), d / "out")


def _build_go(d: Path, plan: ProjectPlan, tools, timeout) -> BuildResult:
    exe = _exe(d, plan)
    env = {"GOTOOLCHAIN": "local", "GOPROXY": "off", "GOFLAGS": "-mod=mod", "CGO_ENABLED": "0", "GO111MODULE": "on"}
    if not any(k in os.environ for k in ("GOCACHE", "LOCALAPPDATA", "XDG_CACHE_HOME", "HOME")):
        env["GOCACHE"] = str(paths.data_home() / "cache" / "go")
    code, log = _run([tools["go"].path, "build", "-o", str(exe.relative_to(d)), "."], d, env, timeout)
    return _failed(code, log, d) if code != 0 else BuildResult(True, trim_log(log, d), exe)


def _check_each(d: Path, cmd: list[str], files: list[str], timeout: float) -> tuple[bool, str]:
    """Проверка синтаксиса по файлу (node --check, php -l): все ли прошли и что сказали про упавшие."""
    logs = []
    for f in files:
        code, log = _run([*cmd, f], d, None, timeout)
        if code != 0:
            logs.append(log)
    return not logs, "\n".join(logs)


def _build_script(d: Path, plan: ProjectPlan, tools, timeout) -> BuildResult:
    lang = plan.language
    if lang == "python":
        t = tools["python"]
        code, log = _run([t.path, *t.args, "-I", "-B", "-c", PY_CHECK, *_sources(plan, {".py", ".pyw"})], d, PY_ENV,
                         timeout)
        ok = code == 0
    elif lang == "javascript":
        ok, log = _check_each(d, [tools["node"].path, "--check"], _sources(plan, CODE_EXT["javascript"]), timeout)
    else:
        php = [tools["php"].path, "-d", "display_errors=1", "-d", "log_errors=0", "-d", "error_reporting=-1", "-l"]
        ok, log = _check_each(d, php, _sources(plan, {".php"}), timeout)
    return BuildResult(True, "", d / plan.entry) if ok else _failed(None, log, d)


BUILDERS = {"csharp": _build_csharp, "cpp": _build_cpp, "java": _build_java, "go": _build_go,
            "python": _build_script, "javascript": _build_script, "php": _build_script}


def build(project_dir: Path | str, plan: ProjectPlan, tools: Mapping[str, Toolchain], timeout: float = 300) -> BuildResult:
    """Собрать проект (C#, C++, Java, Go) или проверить синтаксис (Python, Node, PHP). Сайт и SQL не собираются.
    Ошибки — в log, коротко и с путями внутри проекта: их получает модель на исправление."""
    d = Path(project_dir)
    if plan.language in ("web", "mysql"):
        return BuildResult(True, "", d / plan.entry)
    if lack := [k for k in missing(plan, tools) if k != "edge"]:
        return BuildResult(False, "Нет инструмента для сборки: " + ", ".join(TOOL_TITLES[k] for k in lack), None)
    return BUILDERS[plan.language](d, plan, tools, timeout)


# ---------- запуск ----------

def _windowless(path: str, console: bool) -> str:
    """Оконная программа в Windows — через pythonw/javaw: без лишнего окна консоли рядом с окном программы."""
    p = Path(path)
    w = p.with_name(p.stem + "w" + p.suffix)
    return str(w) if WINDOWS and not console and p.stem.lower() in ("python", "java") and w.is_file() else path


def _java_exe(t: Toolchain) -> str:
    """java рядом с javac (тот же JDK), иначе из PATH."""
    sib = Path(t.path).with_name("java" + Path(t.path).suffix)
    return str(sib) if sib.is_file() else shutil.which("java") or "java"


def _java_main(plan: ProjectPlan) -> str:
    f = plan.file(plan.entry)
    m = re.search(r"^\s*package\s+([\w.]+)\s*;", f.content if f else "", re.M)
    cls = PurePosixPath(plan.entry).stem
    return f"{m[1]}.{cls}" if m else cls


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_spec(plan: ProjectPlan, artifact: Path | str | None, project_dir: Path | str,
             tools: Mapping[str, Toolchain]) -> RunSpec:
    """Чем и где запускать собранную программу. Данные лабы лежат в рабочей папке (у C# — рядом с exe, туда их
    копирует сборка, как в Visual Studio)."""
    d = Path(project_dir)
    a = Path(artifact) if artifact else d / plan.entry
    console = plan.kind in ("console", "script", "sql")
    lang = plan.language
    if lang == "csharp":
        cmd = [str(a)] if a.suffix.lower() == ".exe" else [tools["dotnet"].path, str(a)]
        return RunSpec(cmd, a.parent, console, dict(DOTNET_ENV))
    if lang in ("cpp", "go"):
        return RunSpec([str(a)], d, console)
    if lang == "python":
        t = tools["python"]
        return RunSpec([_windowless(t.path, console), *t.args, "-B", plan.entry], d, console, dict(PY_ENV))
    if lang == "java":
        java = _windowless(_java_exe(tools["java"]), console)
        # путь классов — от папки проекта: кириллица в аргументах java при кодовой странице 1252 становится «?»
        cp = os.path.relpath(a, d) if Path(a).is_relative_to(d) else str(a)
        return RunSpec([java, *JAVA_RUN, "-cp", cp, _java_main(plan)], d, console)
    if lang == "javascript":
        return RunSpec([tools["node"].path, plan.entry], d, console)
    if lang == "php" and plan.kind == "web":
        port = _free_port()
        return RunSpec([tools["php"].path, "-S", f"127.0.0.1:{port}", "-t", str(d)], d, False,
                       url=f"http://127.0.0.1:{port}/{quote(plan.entry)}")
    if lang == "php":
        return RunSpec([tools["php"].path, plan.entry], d, console)
    if lang == "mysql":  # для снимков конвейер заменит команду на клиент своего сервера (pipeline/project.start_mysql)
        return RunSpec([tools["mysql"].path, "--default-character-set=utf8mb4", "--user=root"], d, True)
    return RunSpec([], d, False, url=a.resolve().as_uri())
