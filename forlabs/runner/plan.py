"""План проекта программы лабы (его пишет модель) и проверка плана: пути только внутри проекта, разрешённые
расширения, лимиты размера, без файлов сборки (их пишет ForLabs), известные шаги сценариев снимков."""

from __future__ import annotations

import json
import re
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Language = Literal["csharp", "cpp", "python", "java", "javascript", "web", "php", "go", "mysql"]
Kind = Literal["console", "winforms", "wpf", "web", "script", "sql", "gui"]
ShotMode = Literal["window", "console", "browser", "manual"]
SHOT_ACTIONS = ("set", "select", "click", "check", "type", "key", "wait", "scroll", "open")
NEEDS_TARGET = {"set", "select", "click", "check"}
NEEDS_VALUE = {"set", "select", "type", "key", "open"}

MAX_FILES = 60
MAX_TOTAL = 400 * 1024
MAX_FILE = 200 * 1024
MAX_PATH = 150

LANG_ALIASES = {"c#": "csharp", "cs": "csharp", "c++": "cpp", "js": "javascript", "node": "javascript",
                "nodejs": "javascript", "node.js": "javascript", "html": "web", "sql": "mysql", "golang": "go",
                "py": "python"}
KINDS: dict[str, tuple[str, ...]] = {
    "csharp": ("console", "winforms", "wpf"), "cpp": ("console",), "python": ("console", "script", "gui"),
    "java": ("console", "gui"), "javascript": ("console", "script", "web"), "web": ("web",),
    "php": ("console", "script", "web"), "go": ("console",), "mysql": ("sql",),
}
# Исходники, с которых может начинаться программа (точка входа), и прочие файлы языка
CODE_EXT: dict[str, set[str]] = {
    "csharp": {".cs"}, "cpp": {".cpp", ".cc", ".cxx", ".c"}, "python": {".py", ".pyw"}, "java": {".java"},
    "javascript": {".js", ".mjs", ".cjs"}, "web": {".html", ".htm"}, "php": {".php"}, "go": {".go"},
    "mysql": {".sql"},
}
EXTRA_EXT: dict[str, set[str]] = {
    "csharp": {".xaml", ".resx", ".config"}, "cpp": {".h", ".hpp", ".hxx", ".inl"}, "python": set(),
    "java": {".properties"}, "javascript": {".html", ".css"}, "web": {".css", ".js", ".mjs", ".svg"},
    "php": {".html", ".htm", ".css", ".js", ".sql", ".svg"}, "go": set(), "mysql": set(),
}
DATA_EXT = {".txt", ".csv", ".json", ".xml", ".md", ".dat"}  # данные лабы рядом с программой

# Файлы сборки пишет ForLabs по своим шаблонам: от модели их не берём (в них можно спрятать запуск команд)
BUILD_NAMES = {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradlew",
               "go.mod", "go.sum", "go.work", "cmakelists.txt", "makefile", "gnumakefile", "global.json",
               "nuget.config", "directory.build.props", "directory.build.targets", "directory.packages.props",
               "composer.json"}
BUILD_EXT = {".csproj", ".sln", ".slnx", ".vcxproj", ".vbproj", ".fsproj", ".props", ".targets", ".bat", ".cmd",
             ".ps1", ".psm1", ".sh", ".vbs", ".gradle", ".mk", ".cmake"}

RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", *(f"COM{i}" for i in range(10)),
            *(f"LPT{i}" for i in range(10))}
BAD_CHARS = re.compile(r'[<>"|?*\x00-\x1f]')


def norm_path(p: Any) -> str:
    """Путь от модели в общем виде: «/» вместо «\\», без «./» и двойных «/». Начальный «/» и «C:» не трогаем —
    их найдёт проверка."""
    p = str(p or "").strip().replace("\\", "/")
    lead = "//" if p.startswith("//") else "/" if p.startswith("/") else ""
    parts = [s for s in p.split("/") if s not in ("", ".")]
    trail = "/" if p.endswith("/") and parts else ""
    return lead + "/".join(parts) + trail


def _text(v: Any) -> Any:
    """Число, «да/нет» и null от модели — строкой."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return v


class _Model(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)


class ProjectFile(_Model):
    path: str
    content: str = ""

    _path = field_validator("path", mode="before")(norm_path)
    _content = field_validator("content", mode="before")(_text)


class ListingRef(_Model):
    """Листинг отчёта (раздел, блок) берётся из файла проекта; code — новый текст листинга, если код пришлось менять."""
    section: int
    block: int
    file: str
    code: str = ""

    _file = field_validator("file", mode="before")(norm_path)
    _code = field_validator("code", mode="before")(_text)


class ShotStep(_Model):
    action: str
    target: str = ""
    value: str = ""

    @field_validator("action", mode="before")
    @classmethod
    def _action(cls, v):
        return str(_text(v)).strip().lower()

    _strings = field_validator("target", "value", mode="before")(_text)


class ShotPlan(_Model):
    """Сценарий одного рисунка: запустить, пройти шаги (или ввести input в консоль), проверить expect, снять."""
    section: int
    block: int
    mode: ShotMode = "window"
    steps: list[ShotStep] = Field(default_factory=list)
    input: str = ""
    url: str = ""
    expect: str = ""
    reason: str = ""  # почему снимок делает студент (mode == "manual")

    @field_validator("mode", mode="before")
    @classmethod
    def _mode(cls, v):
        return str(_text(v)).strip().lower() or "window"

    _strings = field_validator("input", "url", "expect", "reason", mode="before")(_text)


class ProjectPlan(_Model):
    language: Language
    kind: Kind
    name: str
    files: list[ProjectFile] = Field(default_factory=list)
    entry: str
    listings: list[ListingRef] = Field(default_factory=list)
    shots: list[ShotPlan] = Field(default_factory=list)

    @field_validator("language", mode="before")
    @classmethod
    def _language(cls, v):
        v = str(_text(v)).strip().lower()
        return LANG_ALIASES.get(v, v)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):
        return str(_text(v)).strip().lower()

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v):
        return str(_text(v)).strip()

    _entry = field_validator("entry", mode="before")(norm_path)

    @model_validator(mode="after")
    def _fix_case(self):
        """Точка входа и листинги, отличающиеся от файла только регистром букв, — на настоящий путь файла."""
        real = {f.path.lower(): f.path for f in self.files}
        if self.entry not in {f.path for f in self.files}:
            self.entry = real.get(self.entry.lower(), self.entry)
        code = CODE_EXT.get(self.language, set())
        if code and Path(self.entry).suffix.lower() in EXTRA_EXT.get(self.language, set()):  # «App.xaml» у WPF
            same = real.get(f"{self.entry}.cs".lower())
            cands = [f.path for f in self.files if Path(f.path).suffix.lower() in code]
            best = same or next((c for c in cands if Path(c).stem.lower().split(".")[0] in
                                 ("program", "main", "app", "index")), None) or (cands[0] if cands else None)
            if best:
                self.entry = best
        for ref in self.listings:
            ref.file = real.get(ref.file.lower(), ref.file) if ref.file not in real.values() else ref.file
        return self

    def file(self, path: str) -> ProjectFile | None:
        return next((f for f in self.files if f.path == path), None)


def _url_problem(url: str) -> str | None:
    from forlabs.runner.shots import url_problem

    return url_problem(url)


def _path_problem(path: str) -> str | None:
    if not path:
        return "пустой путь"
    if path.startswith("/") or re.match(r"[A-Za-z]:", path):
        return "абсолютный путь, а нужен путь внутри папки проекта"
    if ":" in path:
        return "двоеточие в пути (поток NTFS или диск) — нельзя"
    segs = path.split("/")
    if ".." in segs:
        return "«..» выводит за папку проекта"
    if path.endswith("/"):
        return "путь к папке, а нужен путь к файлу"
    if len(path) > MAX_PATH:
        return f"слишком длинный путь (больше {MAX_PATH} символов)"
    if BAD_CHARS.search(path):
        return 'недопустимые в Windows символы (< > " | ? * и управляющие)'
    for s in segs:
        if s[:1] in ("-", "@"):  # имя-опция: компилятор прочёл бы его как ключ или файл с ключами
            return f"«{s}»: имя не может начинаться с «-» или «@»"
        if s.split(".")[0].strip().upper() in RESERVED:
            return f"«{s}» — зарезервированное имя Windows"
        if s.endswith((".", " ")):
            return f"«{s}»: имя не может кончаться точкой или пробелом"
    return None


def _build_file_problem(f: ProjectFile) -> str | None:
    name = PurePosixPath(f.path).name.lower()
    if name in BUILD_NAMES or PurePosixPath(name).suffix in BUILD_EXT:
        return "файлы сборки и запуска пишет ForLabs, от модели — только исходники и данные"
    if name == "package.json":
        try:
            pkg = json.loads(f.content)
        except ValueError:
            return "package.json — не JSON"
        if isinstance(pkg, dict) and "scripts" in pkg:
            return "package.json со «scripts»: команды сборки и запуска пишет ForLabs"
    return None


def _name_problem(name: str) -> str | None:
    if not name or not name.strip(". "):
        return "пустое имя проекта"
    if len(name) > 60:
        return "имя проекта длиннее 60 символов"
    if re.search(r'[\\/:<>"|?*\x00-\x1f]', name):
        return f"имя проекта «{name}»: недопустимые символы (\\ / : < > \" | ? *)"
    if name.split(".")[0].strip().upper() in RESERVED or name.endswith((".", " ")):
        return f"имя проекта «{name}» не годится для папки Windows"
    return None


def validate(plan: ProjectPlan) -> list[str]:
    """Что не так с планом (по-русски, для модели и журнала). Пустой список — план можно писать на диск."""
    out: list[str] = []
    lang = plan.language
    if (p := _name_problem(plan.name)) is not None:
        out.append(p)
    if plan.kind not in KINDS[lang]:
        out.append(f"Вид программы «{plan.kind}» не подходит для языка {lang} (можно: {', '.join(KINDS[lang])})")
    if not plan.files:
        out.append("В проекте нет ни одного файла")
    if len(plan.files) > MAX_FILES:
        out.append(f"Слишком много файлов: {len(plan.files)} (не больше {MAX_FILES})")
    allowed = CODE_EXT[lang] | EXTRA_EXT[lang] | DATA_EXT
    seen: dict[str, str] = {}
    total = 0
    for f in plan.files:
        size = len(f.content.encode("utf-8"))
        total += size
        if (p := _path_problem(f.path)) is not None:
            out.append(f"«{f.path}»: {p}")
            continue
        if (p := _build_file_problem(f)) is not None:
            out.append(f"«{f.path}»: {p}")
        elif PurePosixPath(f.path).suffix.lower() not in allowed:
            out.append(f"«{f.path}»: расширение не подходит для {lang} (можно: {', '.join(sorted(allowed))})")
        if size > MAX_FILE:
            out.append(f"«{f.path}»: {size // 1024} КБ, а можно не больше {MAX_FILE // 1024} КБ")
        key = f.path.lower()
        if key in seen:
            out.append(f"«{f.path}»: такой файл уже есть — «{seen[key]}» (Windows не различает регистр букв)")
        seen.setdefault(key, f.path)
    if total > MAX_TOTAL:
        out.append(f"Все файлы вместе — {total // 1024} КБ, а можно не больше {MAX_TOTAL // 1024} КБ")
    paths = {f.path for f in plan.files}
    if plan.entry not in paths:
        out.append(f"Точка входа «{plan.entry}» — такого файла нет в проекте")
    elif PurePosixPath(plan.entry).suffix.lower() not in CODE_EXT[lang]:
        out.append(f"Точка входа «{plan.entry}» должна быть исходником {lang} "
                   f"({', '.join(sorted(CODE_EXT[lang]))})")
    for ref in plan.listings:
        if ref.file not in paths:
            out.append(f"Листинг {ref.section}.{ref.block}: файла «{ref.file}» нет в проекте")
    for shot in plan.shots:
        where = f"Снимок {shot.section}.{shot.block}"
        if shot.mode == "browser" and (why := _url_problem(shot.url)):
            out.append(f"{where}: {why}")
        for step in shot.steps:
            if step.action not in SHOT_ACTIONS:
                out.append(f"{where}: неизвестное действие «{step.action}» (можно: {', '.join(SHOT_ACTIONS)})")
            elif step.action in NEEDS_TARGET and not step.target.strip():
                out.append(f"{where}: у действия «{step.action}» не указан элемент окна (target)")
            elif step.action in NEEDS_VALUE and not step.value:
                out.append(f"{where}: у действия «{step.action}» не указано значение (value)")
    return out
