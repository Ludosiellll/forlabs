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
LIBRARY = "library"  # вид программы-библиотеки (только C#): её не запускают, её подключают другие программы плана
PROGRAM_LANGS = ("csharp", "cpp", "python", "java", "javascript", "php", "go")  # где в лабе бывает несколько программ
NOT_PROGRAM_DIRS = {"bin", "obj", "out", "__pycache__", ".vs"}  # папки сборки: в копию для сдачи не попадают
# Точка входа в коде: по ней видно, что в одном проекте лежит несколько программ
ENTRY_POINT = {
    "csharp": r"\bstatic\s+(?:async\s+)?(?:void|int|(?:System\.Threading\.Tasks\.)?Task(?:\s*<\s*int\s*>)?)\s+Main\s*\(",
    "cpp": r"^\s*(?:int|void)\s+(?:w?main|_tmain)\s*\(", "go": r"^\s*func\s+main\s*\(\s*\)",
}  # Java здесь нет: пакеты со своими main собираются вместе и видят друг друга — это обычный единый проект
PROGRAM_NAME = re.compile(r"[^\W_][\w.\-]*")  # имя программы — и папка, и имя проекта в .csproj и .sln
WINDOW_CODE = {"csharp": r"System\.Windows\.Forms|Application\.Run\s*\(|:\s*Form\b",
               "java": r"javax\.swing|java\.awt|javafx\."}
ShotMode = Literal["window", "console", "browser", "manual", "app"]  # app — агент в программе студента
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
    program: str = ""  # какую программу плана запускать (когда их несколько); пусто — первую запускаемую
    app: str = ""  # mode == "app": ключ программы (forlabs.agent.apps.APPS)
    goal: str = ""  # mode == "app": что сделать в программе
    open: list[str] = Field(default_factory=list)  # mode == "app": файлы проекта, которые открыть

    @field_validator("mode", mode="before")
    @classmethod
    def _mode(cls, v):
        return str(_text(v)).strip().lower() or "window"

    _strings = field_validator("input", "url", "expect", "reason", "app", "goal", "program", mode="before")(_text)

    @field_validator("open", mode="before")
    @classmethod
    def _open(cls, v):
        items = v if isinstance(v, list) else ([v] if v else [])
        return [str(x).replace("\\", "/").removeprefix("./") for x in items]


class ProgramPlan(_Model):
    """Одна из нескольких программ лабы: лежит в папке со своим именем, собирается и запускается отдельно."""
    name: str  # имя программы = имя её папки и проекта (ClassLibrary1, CalculatorApp)
    kind: str = "console"  # как у плана, плюс library (C#)
    entry: str = ""  # главный файл, путь от корня проекта (CalculatorApp/Program.cs); у библиотеки пусто
    uses: list[str] = Field(default_factory=list)  # библиотеки этого же плана, которые программа подключает

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v):
        return str(_text(v)).strip().replace("\\", "/").strip("/")

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):
        v = str(_text(v)).strip().lower()
        return {"lib": LIBRARY, "dll": LIBRARY, "classlib": LIBRARY, "classlibrary": LIBRARY}.get(v, v) or "console"

    _entry = field_validator("entry", mode="before")(norm_path)

    @field_validator("uses", mode="before")
    @classmethod
    def _uses(cls, v):
        items = v if isinstance(v, list) else ([v] if v else [])
        return [str(_text(x)).strip() for x in items if str(_text(x)).strip()]

    @property
    def runs(self) -> bool:
        return self.kind != LIBRARY


class ProjectPlan(_Model):
    language: Language
    kind: Kind
    name: str
    files: list[ProjectFile] = Field(default_factory=list)
    entry: str
    listings: list[ListingRef] = Field(default_factory=list)
    shots: list[ShotPlan] = Field(default_factory=list)
    programs: list[ProgramPlan] = Field(default_factory=list)  # несколько программ в лабе; пусто — одна, как раньше

    @field_validator("language", mode="before")
    @classmethod
    def _language(cls, v):
        v = str(_text(v)).strip().lower()
        return LANG_ALIASES.get(v, v)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):
        v = str(_text(v)).strip().lower()
        return "console" if v == LIBRARY else v  # вид плана из нескольких программ — вид главной из них (ниже)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v):
        return str(_text(v)).strip()

    _entry = field_validator("entry", mode="before")(norm_path)

    @field_validator("programs", mode="before")
    @classmethod
    def _programs(cls, v):
        return v if isinstance(v, list) else []

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
        for prog in self.programs:  # главный файл программы: «Program.cs» → «CalculatorApp/Program.cs»
            if prog.runs and prog.entry not in real.values():
                inside = f"{prog.name}/{prog.entry}".lower()
                prog.entry = real.get(prog.entry.lower()) or real.get(inside) or prog.entry
        main = self.main_program()
        if main is not None:  # вид и точка входа плана — от программы, которая запускается первой
            self.entry = main.entry or self.entry
            if main.kind in Kind.__args__:
                self.kind = main.kind
        return self

    def file(self, path: str) -> ProjectFile | None:
        return next((f for f in self.files if f.path == path), None)

    def main_program(self) -> ProgramPlan | None:
        """Программа, которая запускается, если не сказано иное: первая не библиотека."""
        return next((p for p in self.programs if p.runs), None)

    def program(self, name: str) -> ProgramPlan | None:
        return next((p for p in self.programs if p.name.lower() == (name or "").strip().lower()), None)

    def program_of(self, shot: ShotPlan) -> ProgramPlan | None:
        """Какую программу запускает снимок. None — программ в плане нет (один проект)."""
        found = self.program(shot.program) if shot.program.strip() else None
        return found if found is not None and found.runs else self.main_program()

    def files_of(self, prog: ProgramPlan) -> list[ProjectFile]:
        return [f for f in self.files if f.path.lower().startswith(prog.name.lower() + "/")]


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


def _program_problems(plan: ProjectPlan) -> list[str]:
    """Несколько программ в плане: у каждой своя папка, главный файл в ней, библиотеки — только C#."""
    out: list[str] = []
    lang = plan.language
    if lang not in PROGRAM_LANGS:
        return [f"programs не подходят для языка {lang}: у сайта и SQL один проект"]
    kinds = KINDS[lang] + ((LIBRARY,) if lang == "csharp" else ())
    paths = {f.path for f in plan.files}
    seen: dict[str, str] = {}
    for p in plan.programs:
        title = f"Программа «{p.name}»"
        why = _path_problem(p.name) if p.name else "пустое имя"
        if why or not PROGRAM_NAME.fullmatch(p.name) or p.name.lower() in NOT_PROGRAM_DIRS:
            out.append(f"{title}: имя программы — это имя её папки: одно слово из букв, цифр, «_», «-» и «.», "
                       "не bin, obj, out"
                       + (f" ({why})" if why else ""))
            continue
        if p.name.lower() in seen:
            out.append(f"{title}: такая программа уже есть — «{seen[p.name.lower()]}»")
            continue
        seen[p.name.lower()] = p.name
        if p.kind not in kinds:
            extra = " (library — только в C#)" if p.kind == LIBRARY else ""
            out.append(f"{title}: вид «{p.kind}» не подходит для языка {lang} (можно: {', '.join(kinds)}){extra}")
        if not plan.files_of(p):
            out.append(f"{title}: в папке «{p.name}/» нет ни одного файла")
        if p.runs:
            if p.entry not in paths:
                out.append(f"{title}: главного файла «{p.entry}» нет в проекте")
            elif not p.entry.lower().startswith(p.name.lower() + "/"):
                out.append(f"{title}: главный файл «{p.entry}» должен лежать в её папке «{p.name}/»")
            elif PurePosixPath(p.entry).suffix.lower() not in CODE_EXT[lang]:
                out.append(f"{title}: главный файл «{p.entry}» должен быть исходником {lang}")
    if not any(p.runs for p in plan.programs):
        out.append("В programs нет ни одной программы, которую можно запустить (одни библиотеки)")
    by = {p.name.lower(): p for p in plan.programs}
    for p in plan.programs:
        for u in p.uses:
            lib = by.get(u.lower())
            if lib is None:
                out.append(f"Программа «{p.name}» подключает «{u}», а в programs нет программы «{u}»")
            elif lib.runs:
                out.append(f"Программа «{p.name}» подключает «{u}», а это не библиотека (kind не library)")

    def loops(name: str, trail: tuple[str, ...]) -> bool:
        if name in trail:
            return True
        node = by.get(name)
        return node is not None and any(loops(u.lower(), (*trail, name)) for u in node.uses)

    if any(loops(p.name.lower(), ()) for p in plan.programs):
        out.append("Библиотеки подключают друг друга по кругу (uses)")
    folders = [p.name.lower() + "/" for p in plan.programs]
    for f in plan.files:
        if not any(f.path.lower().startswith(d) for d in folders):
            out.append(f"«{f.path}»: файл не лежит в папке ни одной программы из programs")
    for shot in plan.shots:
        if not shot.program.strip():
            continue
        prog = plan.program(shot.program)
        where = f"Снимок {shot.section}.{shot.block}"
        if prog is None:
            out.append(f"{where}: в programs нет программы «{shot.program}»")
        elif not prog.runs:
            out.append(f"{where}: «{shot.program}» — библиотека, её нельзя запустить; укажи программу, которая "
                       "её использует")
    return out


def split_programs(plan: ProjectPlan) -> ProjectPlan:
    """План без programs, где у нескольких папок своя точка входа (библиотека и приложения, сложенные в один
    проект: такая сборка падает с «несколько точек входа»), — папки становятся программами. Делится только то, что
    делится однозначно: весь код лежит по папкам верхнего уровня и в каждой не больше одной точки входа."""
    lang = plan.language
    if plan.programs or lang not in ENTRY_POINT:
        return plan
    pat = re.compile(ENTRY_POINT[lang], re.M)
    code = CODE_EXT[lang]
    tops: dict[str, list[ProjectFile]] = {}
    loose: list[ProjectFile] = []
    for f in plan.files:
        top, _, rest = f.path.partition("/")
        if rest:
            tops.setdefault(top, []).append(f)
        elif PurePosixPath(f.path).suffix.lower() in DATA_EXT:
            loose.append(f)  # данные из корня получит каждая программа
        else:
            return plan
    entries = {t: [f for f in fs if PurePosixPath(f.path).suffix.lower() in code and pat.search(f.content)]
               for t, fs in tops.items()}
    if sum(1 for t in tops if entries[t]) < 2 or any(len(e) > 1 for e in entries.values()):
        return plan
    libs = [t for t in tops if not entries[t]]
    if libs and lang != "csharp":
        return plan  # общий код без точки входа в других языках к программам не подключить
    if any(not any(PurePosixPath(f.path).suffix.lower() in code for f in tops[t]) for t in libs):
        return plan  # папка без кода (одни данные) — не библиотека: чья она, по файлам не понять
    programs = []
    for t, fs in tops.items():
        if not entries[t]:
            programs.append(ProgramPlan(name=t, kind=LIBRARY))
            continue
        text = "\n".join(f.content for f in fs)
        kind = "console"
        if lang == "csharp" and any(f.path.lower().endswith(".xaml") for f in fs):
            kind = "wpf"
        elif lang in WINDOW_CODE and re.search(WINDOW_CODE[lang], text):
            kind = "winforms" if lang == "csharp" else "gui"
        programs.append(ProgramPlan(name=t, kind=kind, entry=entries[t][0].path, uses=list(libs)))
    first = plan.entry.partition("/")[0]
    programs.sort(key=lambda p: not (p.runs and p.name == first))  # прежняя точка входа остаётся главной
    files = [f for f in plan.files if "/" in f.path]
    have = {f.path.lower() for f in files}
    for f in loose:
        files += [ProjectFile(path=f"{p.name}/{f.path}", content=f.content) for p in programs
                  if p.runs and f"{p.name}/{f.path}".lower() not in have]
    # данные из корня теперь лежат у каждой программы: листинги и открываемые файлы — на копию у главной
    home = next(p.name for p in programs if p.runs)
    gone = {f.path.lower(): f"{home}/{f.path}" for f in loose}
    data = plan.model_dump() | {"programs": [p.model_dump() for p in programs],
                                "files": [f.model_dump() for f in files]}
    for ref in data["listings"]:
        ref["file"] = gone.get(ref["file"].lower(), ref["file"])
    for shot in data["shots"]:
        shot["open"] = [gone.get(x.lower(), x) for x in shot["open"]]
    out = ProjectPlan.model_validate(data)
    return out if set(validate(out)) <= set(validate(plan)) else plan  # деление не должно добавить замечаний


def validate(plan: ProjectPlan) -> list[str]:
    """Что не так с планом (по-русски, для модели и журнала). Пустой список — план можно писать на диск."""
    out: list[str] = []
    lang = plan.language
    if plan.programs:
        out += _program_problems(plan)
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
        if shot.mode == "app":
            from forlabs.agent.apps import APPS

            if shot.app not in APPS:
                out.append(f"{where}: неизвестная программа «{shot.app}» (можно: {', '.join(APPS)})")
            if not shot.goal.strip():
                out.append(f"{where}: не сказано, что сделать в программе (goal)")
            for f in shot.open:
                if plan.file(f) is None:
                    out.append(f"{where}: файла «{f}» нет в проекте")
        for step in shot.steps:
            if step.action not in SHOT_ACTIONS:
                out.append(f"{where}: неизвестное действие «{step.action}» (можно: {', '.join(SHOT_ACTIONS)})")
            elif step.action in NEEDS_TARGET and not step.target.strip():
                out.append(f"{where}: у действия «{step.action}» не указан элемент окна (target)")
            elif step.action in NEEDS_VALUE and not step.value:
                out.append(f"{where}: у действия «{step.action}» не указано значение (value)")
    return out
