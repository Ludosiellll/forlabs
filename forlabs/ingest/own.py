"""«Своя программа»: папка или ZIP студента → копия в папке лабы (own/), язык, код для модели.

Берётся код языков ForLabs и данные рядом с ним; картинки и прочие файлы — ресурсами (их ForLabs кладёт в папку
проекта при сборке). Результаты сборки, служебные папки сред и мусор архиваторов не берутся. Оригинал не трогается.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path, PurePosixPath

from forlabs.core.models import OwnProgram
from forlabs.ingest.archive import JUNK, unpack
from forlabs.runner.plan import BUILD_EXT, BUILD_NAMES, CODE_EXT, DATA_EXT, EXTRA_EXT, MAX_FILE

SKIP_DIRS = {"bin", "obj", "out", ".vs", ".vscode", ".git", ".svn", ".idea", "node_modules", "__pycache__", "venv",
             ".venv", "env", "library", "temp", "logs", "usersettings", "projectsettings", "packages", "debug",
             "release", "x64", "x86", "build", "target", "dist", ".gradle", "__macosx", ".pytest_cache", ".mypy_cache"}
MAX_WALK = 20_000  # файлов при обходе (папка не та — например, весь диск)
MAX_CODE_FILES = 300
MAX_ASSET = 20 * 1024 * 1024
MAX_ASSETS = 200 * 1024 * 1024
TEXT_MAX = 60_000  # знаков кода для модели: она перечитывает его в каждом задании
DATA_PREVIEW = 3_000  # знаков от файла данных
MAIN_NAMES = ("program", "main", "app", "index", "form1", "mainwindow", "mainform", "game")
LANG_ORDER = ("csharp", "java", "cpp", "python", "php", "go", "javascript", "web", "mysql")


def _text(raw: bytes) -> str | None:
    """Текст файла как бы его ни сохранил редактор (UTF-8, UTF-16, Windows-1251); None — не текст."""
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
    if "\x00" in text:
        return None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _walk(root: Path) -> tuple[list[str], set[str]]:
    """→ (файлы от root, имена всех встреченных папок в нижнем регистре — для узнавания Unity)."""
    files: list[str] = []
    seen: set[str] = set()
    for here, dirs, names in os.walk(root, followlinks=False):
        seen.update(d.lower() for d in dirs)
        dirs[:] = sorted(d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")
                         and not (Path(here) / d).is_symlink())
        for n in sorted(names):
            p = Path(here) / n
            if n.lower() in JUNK or n.startswith("._") or p.is_symlink():
                continue
            files.append(p.relative_to(root).as_posix())
            if len(files) > MAX_WALK:
                raise ValueError(f"в папке больше {MAX_WALK} файлов — выбери папку самой программы")
    return files, seen


def _strip_top(rels: list[str]) -> str:
    """Общая верхняя папка всех файлов («Calc/…») — её имя, иначе ""."""
    tops = {PurePosixPath(r).parts[0] for r in rels}
    if len(tops) == 1 and all(len(PurePosixPath(r).parts) > 1 for r in rels):
        return next(iter(tops))
    return ""


def _language(code: list[str]) -> str | None:
    exts = [PurePosixPath(r).suffix.lower() for r in code]
    count = {lang: sum(1 for e in exts if e in CODE_EXT[lang]) for lang in LANG_ORDER}
    if count["php"]:
        return "php"
    if count["web"] and count["web"] >= count["javascript"]:
        return "web"
    best = max(LANG_ORDER, key=lambda lang: count[lang])  # при равенстве — раньше в LANG_ORDER
    return best if count[best] else None


def _main_first(rel: str) -> tuple:
    stem = PurePosixPath(rel).name.lower().split(".")[0]
    data = PurePosixPath(rel).suffix.lower() in DATA_EXT
    rank = MAIN_NAMES.index(stem) if stem in MAIN_NAMES else len(MAIN_NAMES)
    return (data, rank, rel.count("/"), rel.lower())


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
    rels, seen = _walk(root)
    code_ext = set().union(*CODE_EXT.values(), *EXTRA_EXT.values(), DATA_EXT)
    unity = any(PurePosixPath(r).suffix.lower() == ".meta" for r in rels) or {"assets", "projectsettings"} <= seen
    top = _strip_top(rels)
    if unity and top.lower() == "assets":  # выбрана папка Unity-проекта: Assets — уже его часть
        top = ""
    code: dict[str, str] = {}
    assets: list[str] = []
    asset_bytes = 0
    for rel in rels:
        src = root / rel
        inner = rel[len(top) + 1:] if top else rel
        p = PurePosixPath(inner)
        ext = p.suffix.lower()
        if p.name.lower() in BUILD_NAMES or ext in BUILD_EXT or ext in {".meta", ".user", ".suo", ".pdb", ".exe",
                                                                        ".dll", ".class", ".pyc", ".o", ".obj"}:
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
        if size <= MAX_ASSET and asset_bytes + size <= MAX_ASSETS and not unity:
            assets.append(inner)
            asset_bytes += size
    if not any(PurePosixPath(r).suffix.lower() not in DATA_EXT for r in code):
        raise ValueError("в ней нет кода, который ForLabs умеет читать (C#, Python, Java, C++, JavaScript, PHP, Go, "
                         "SQL, HTML)")
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    for inner in [*code, *assets]:
        src = root / (f"{top}/{inner}" if top else inner)
        dst = dest.joinpath(*PurePosixPath(inner).parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    language = _language(list(code))
    return OwnProgram(name=name, language=language, engine="Unity" if unity else None, files=sorted(code),
                      assets=sorted(assets), text=program_text(name, language, code, sorted(assets)))


def read_own(src: Path, dest: Path, name: str | None = None) -> OwnProgram:
    """Папка или ZIP → копия в dest (прежняя копия заменяется). ValueError — причина по-русски."""
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

