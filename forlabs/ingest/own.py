"""«Своя программа»: папка или ZIP студента → копия в папке лабы (own/), язык, код для модели.

Берётся код языков ForLabs и данные рядом с ним; картинки и прочие файлы — ресурсами (их ForLabs кладёт в папку
проекта при сборке). Результаты сборки, служебные папки сред и мусор архиваторов не берутся. Оригинал не трогается.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path, PurePosixPath

from forlabs.core.models import OwnProgram
from forlabs.ingest.archive import JUNK, unpack
from forlabs.runner.plan import BUILD_EXT, BUILD_NAMES, CODE_EXT, DATA_EXT, EXTRA_EXT, MAX_FILE

# результаты сборки и служебные папки сред — в любом проекте
SKIP_DIRS = {"bin", "obj", "out", ".vs", ".vscode", ".git", ".svn", ".idea", "node_modules", "__pycache__", "venv",
             ".venv", "packages", "debug", "release", "x64", "x86", "target", "dist", ".gradle", "__macosx",
             ".pytest_cache", ".mypy_cache"}
# папки Unity — только в корне Unity-проекта (рядом с Assets и ProjectSettings): Library — гигабайты кэша
UNITY_DIRS = {"library", "temp", "logs", "usersettings", "projectsettings", "packages", "build", "builds", "obj"}
SKIP_EXT = {".meta", ".user", ".suo", ".pdb", ".exe", ".dll", ".class", ".pyc", ".o", ".obj"}
MAX_WALK = 20_000  # файлов при обходе (папка не та — например, весь диск)
MAX_CODE_FILES = 300
MAX_ASSET = 20 * 1024 * 1024
MAX_ASSETS = 200 * 1024 * 1024
TEXT_MAX = 60_000  # знаков кода для модели: она перечитывает его в каждом задании
DATA_PREVIEW = 3_000  # знаков от файла данных
MAIN_NAMES = ("program", "main", "app", "index", "form1", "mainwindow", "mainform", "game")
GENERATED = re.compile(r"\.(?:designer|g|g\.i)\.cs$|\.resx$|\.xaml$|assemblyinfo\.cs$", re.I)
LANG_TITLES = {"csharp": "C#", "cpp": "C++", "python": "Python", "java": "Java", "javascript": "JavaScript",
               "web": "HTML, CSS, JS", "php": "PHP", "go": "Go", "mysql": "SQL"}
LANG_ORDER = ("csharp", "java", "cpp", "python", "php", "go", "javascript", "mysql")
ROOT_NS = re.compile(r"<RootNamespace>\s*([^<\s]+)\s*</RootNamespace>", re.I)


def _text(raw: bytes) -> str | None:
    """Текст файла как бы его ни сохранил редактор (UTF-8, UTF-16, Windows-1251); None — не текст. Правило то же,
    что у сборки проекта (project.decode_text): иначе файл, взятый здесь, молча пропал бы из проекта."""
    try:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16")
        else:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("cp1251")
    except (UnicodeDecodeError, ValueError):
        return None
    if "\x00" in text or any(ch < " " and ch not in "\n\r\t\f" for ch in text):
        return None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _walk(root: Path) -> tuple[list[str], bool]:
    """→ (файлы от root, Unity ли это). Папки сборки не обходятся; папки Unity — только в корне Unity-проекта."""
    files: list[str] = []
    unity = False
    for here, dirs, names in os.walk(root, followlinks=False):
        low = {d.lower() for d in dirs}
        unity_root = {"assets", "projectsettings"} <= low
        unity = unity or unity_root
        dirs[:] = sorted(d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")
                         and not (unity_root and d.lower() in UNITY_DIRS) and not (Path(here) / d).is_symlink())
        for n in sorted(names):
            p = Path(here) / n
            if n.lower() in JUNK or n.startswith("._") or p.is_symlink():
                continue
            files.append(p.relative_to(root).as_posix())
            if len(files) > MAX_WALK:
                raise ValueError(f"в папке больше {MAX_WALK} файлов — выбери папку самой программы")
    return files, unity or any(PurePosixPath(r).suffix.lower() == ".meta" for r in files)


def _top(rels: list[str], unity: bool) -> tuple[str, str | None]:
    """Корень программы внутри присланного: → (префикс «папка/» или "", имя проекта C#).
    Проект Visual Studio (один .csproj, весь код в его папке) — корень там, где .csproj: тогда имена ресурсов
    («Calc.Properties.Resources») совпадут с теми, что в коде студента. Иначе снимается общая верхняя папка."""
    projects = [r for r in rels if r.lower().endswith(".csproj")]
    code_ext = set().union(*CODE_EXT.values())
    if len(projects) == 1:
        folder = PurePosixPath(projects[0]).parent.as_posix()
        prefix = "" if folder == "." else folder + "/"
        code = [r for r in rels if PurePosixPath(r).suffix.lower() in code_ext]
        if code and all(r.startswith(prefix) for r in code):
            return prefix, PurePosixPath(projects[0]).stem
    tops = {PurePosixPath(r).parts[0] for r in rels}
    if len(tops) == 1 and all(len(PurePosixPath(r).parts) > 1 for r in rels):
        top = next(iter(tops))
        if not (unity and top.lower() == "assets"):  # выбрана папка Unity-проекта: Assets — уже его часть
            return top + "/", None
    return "", None


def _language(code: list[str]) -> str | None:
    """Язык по большинству файлов кода. HTML-страницы — сайт, только если другого кода нет (Flask, справка .html у
    программы на C# — это Python и C#)."""
    exts = [PurePosixPath(r).suffix.lower() for r in code]
    count = {lang: sum(1 for e in exts if e in CODE_EXT[lang]) for lang in LANG_ORDER}
    if count["php"]:
        return "php"
    best = max(LANG_ORDER, key=lambda lang: count[lang])  # при равенстве — раньше в LANG_ORDER
    if count[best] and not (best == "javascript" and any(e in CODE_EXT["web"] for e in exts)):
        return best
    return "web" if any(e in CODE_EXT["web"] for e in exts) else None


def _main_first(rel: str) -> tuple:
    """Порядок файлов для модели: главные, затем остальной код, затем сгенерированное средой и данные."""
    stem = PurePosixPath(rel).name.lower().split(".")[0]
    data = PurePosixPath(rel).suffix.lower() in DATA_EXT
    rank = MAIN_NAMES.index(stem) if stem in MAIN_NAMES and not GENERATED.search(rel) else len(MAIN_NAMES)
    return (data, bool(GENERATED.search(rel)), rank, rel.count("/"), rel.lower())


def program_text(name: str, language: str | None, files: dict[str, str], assets: list[str]) -> str:
    """Код для модели: главные файлы первыми, файлы данных — началом; что не влезло в TEXT_MAX — по имени."""
    head = f"Программа студента «{name}»" + (f" (язык: {language})" if language else "") + \
        f", файлов кода и данных: {len(files)}"
    parts, left, used = [], [], 0
    for rel in sorted(files, key=_main_first):
        body = files[rel]
        if PurePosixPath(rel).suffix.lower() in DATA_EXT and len(body) > DATA_PREVIEW:
            body = body[:DATA_PREVIEW] + f"\n… (дальше ещё {len(files[rel]) - DATA_PREVIEW} знаков)"
        part = f"\n\nФайл {rel}:\n<<<\n{body.rstrip()}\n>>>"
        if used + len(part) > TEXT_MAX:
            left.append(rel)
            continue
        parts.append(part)
        used += len(part)
    tail = ""
    if left:
        tail += "\n\nФайлы программы, которые здесь не показаны (большие): " + ", ".join(left)
    if assets:
        tail += "\n\nДругие файлы программы (картинки, ресурсы): " + ", ".join(assets[:60]) + \
            (f" и ещё {len(assets) - 60}" if len(assets) > 60 else "")
    return head + "".join(parts) + tail


def _collect(root: Path, dest: Path, name: str) -> OwnProgram:
    rels, unity = _walk(root)
    prefix, project = _top(rels, unity)
    if project:  # имя проекта C# — как в .csproj студента (RootNamespace, если задано)
        try:
            found = ROOT_NS.search(_text((root / f"{prefix}{project}.csproj").read_bytes()) or "")
        except OSError:
            found = None
        project = found.group(1) if found else project
    code_ext = set().union(*CODE_EXT.values(), *EXTRA_EXT.values(), DATA_EXT)
    code: dict[str, str] = {}
    assets: list[str] = []
    asset_bytes = 0
    for rel in rels:
        if not rel.startswith(prefix):
            continue  # рядом с проектом Visual Studio: .sln, README — не часть программы
        src, inner = root / rel, rel[len(prefix):]
        p = PurePosixPath(inner)
        ext = p.suffix.lower()
        if p.name.lower() in BUILD_NAMES or ext in BUILD_EXT or ext in SKIP_EXT:
            continue  # сборку пишет ForLabs; исполняемые и служебные файлы среды не нужны
        if unity and p.parts[0].lower() != "assets":
            continue  # у Unity программа — только Assets
        size = src.stat().st_size
        if ext in code_ext and size <= MAX_FILE:
            text = _text(src.read_bytes())
            if text is not None:
                if len(code) >= MAX_CODE_FILES:
                    raise ValueError(f"в программе больше {MAX_CODE_FILES} файлов кода — выбери папку самой "
                                     "программы, без библиотек")
                code[inner] = text
                continue
            if ext not in DATA_EXT:
                continue  # «код», который не читается как текст (бинарный мусор с тем же расширением)
        if size <= MAX_ASSET and asset_bytes + size <= MAX_ASSETS and not unity:
            assets.append(inner)
            asset_bytes += size
    if not any(PurePosixPath(r).suffix.lower() not in DATA_EXT for r in code):
        raise ValueError("в ней нет кода, который ForLabs умеет читать (C#, Python, Java, C++, JavaScript, PHP, Go, "
                         "SQL, HTML)")
    # копия — во временную папку, затем подмена: сбой на середине не оставляет полупустую прежнюю программу
    staging = dest.with_name(dest.name + "_new")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        for inner in [*code, *assets]:
            dst = staging.joinpath(*PurePosixPath(inner).parts)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / f"{prefix}{inner}", dst)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(dest, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    staging.rename(dest)
    sha = hashlib.sha256()
    for rel in sorted(code):
        sha.update(rel.encode("utf-8") + b"\0" + code[rel].encode("utf-8") + b"\0")
    for rel in sorted(assets):
        sha.update(rel.encode("utf-8") + b"\0" + str((root / f"{prefix}{rel}").stat().st_size).encode() + b"\0")
    language = _language(list(code))
    return OwnProgram(name=name, language=language, engine="Unity" if unity else None, files=sorted(code),
                      assets=sorted(assets), text=program_text(name, language, code, sorted(assets)),
                      project_name=project, sha=sha.hexdigest()[:16])


def read_own(src: Path, dest: Path, name: str | None = None) -> OwnProgram:
    """Папка или ZIP → копия в dest (прежняя копия заменяется только при удаче). ValueError — причина по-русски."""
    src, dest = Path(src), Path(dest)
    name = name or src.name
    if src.is_dir():
        if src.resolve() == Path.home().resolve() or src.resolve().parent == src.resolve():
            raise ValueError("это домашняя папка или целый диск — выбери папку самой программы")
        return _collect(src, dest, name)
    if src.is_file() and src.suffix.lower() == ".zip":
        tmp = dest.parent / "own_tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            unpack(src, tmp)
            return _collect(tmp, dest, name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    raise ValueError("нужна папка с программой или ZIP-архив с ней")


def summary(own: OwnProgram) -> str:
    """Одной строкой для страницы лабы: «Calc · 5 файлов кода · C#»."""
    n = len(own.files)
    word = "файл" if n % 10 == 1 and n % 100 != 11 else \
        "файла" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "файлов"
    parts = [own.name, f"{n} {word} кода"]
    if own.language:
        parts.append(LANG_TITLES.get(own.language, own.language))
    return " · ".join(parts)
