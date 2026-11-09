"""Этап «Проект»: из листингов отчёта — настоящий проект, который собирается и запускается; снимки окон программы
делает ForLabs, студенту остаётся только то, что снять нельзя (с причиной); копия проекта — рядом с отчётом.

Порядок: модель составляет план (файлы, какие листинги из каких файлов, сценарий каждого снимка) → проверка плана →
сборка, ошибки уходят модели (до трёх кругов) → проверка кода на опасное → снимки в песочнице → листинги отчёта
сверяются с кодом → копия в папку сдачи. Сбой этапа не ломает лабу: рисунки просто остаются студенту."""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from forlabs.core.models import Figure, LabState, Listing, ProjectInfo
from forlabs.runner import languages, safety
from forlabs.runner.plan import DATA_EXT, ListingRef, ProjectFile, ProjectPlan, validate  # noqa: F401 (validate — для конвейера)

MAX_FIXES = 3
CODE_LANGS = {"python", "py", "csharp", "cs", "c#", "cpp", "c++", "c", "java", "js", "javascript", "html", "css",
              "php", "go", "sql", "mysql"}

PROJECT_SYSTEM = """Ты собираешь из отчёта по лабораторной работе настоящий проект: студент откроет его в своей среде,
соберёт и запустит, а ForLabs сам сделает снимки окон программы для отчёта. Ответ — только JSON.

Правила:
1. Все файлы исходного кода целиком, чтобы проект собирался и работал. Код из листингов отчёта уже согласован и должен
   войти в файлы дословно. Если для сборки код из листинга пришлось изменить — верни новый текст листинга в
   listings[].code (иначе code не пиши).
2. Файлы сборки (csproj, sln, pom.xml, go.mod, CMakeLists.txt, package.json со скриптами, .bat, .ps1) НЕ пиши — их
   сделает ForLabs. Файлы данных (txt, csv, json), которые читает программа, положи в проект.
3. Пиши как студент колледжа: заголовки окон, надписи и сообщения — по-русски и по теме лабы; имена — как в отчёте.
   Никаких служебных пометок, «TODO», «generated», английских заглушек, отладочного вывода и лишних окон.
4. Программа не трогает файлы вне своей папки, не запускает другие программы, не обращается к реестру и сети
   (кроме локального сервера для сайта на PHP).
5. Оконная программа: у каждого поля, списка и кнопки, которые участвуют в снимках, — понятное имя
   (WinForms — свойство Name, WPF — x:Name). В Swing и tkinter при старте курсор стоит в первом поле.
6. Сценарий каждого рисунка-скриншота (section и block — номера из квадратных скобок):
   - mode "console": input — что ввести с клавиатуры (каждая строка с \\n), expect — кусок текста, который точно
     будет на экране после ввода (например «Сумма: 17»);
   - mode "window": steps — шаги в окне: {"action":"set","target":"имя поля","value":"12"},
     {"action":"select","target":"имя списка","value":"пункт"}, {"action":"click","target":"имя или текст кнопки"},
     {"action":"check","target":"имя флажка"}, для Swing и tkinter — {"action":"type","value":"12"} и
     {"action":"key","value":"tab|enter|esc"}, {"action":"wait","value":"1"}; expect — текст, который появится
     в окне; если после нажатия открывается окно сообщения, снимок будет его;
   - mode "browser": url — путь страницы в проекте (index.html);
   - mode "manual" + reason — если снять может только студент: окно среды разработки или конструктора, проводник,
     другая программа, сервер, оборудование, сеть колледжа.
7. language — один из: csharp, cpp, python, java, javascript, web, php, go, mysql; kind — console, winforms, wpf,
   web, script, sql или gui (окно Swing/tkinter); entry — главный файл.

Формат:
{"language": "csharp", "kind": "winforms", "name": "Калькулятор", "entry": "Program.cs",
 "files": [{"path": "Program.cs", "content": "…"}, {"path": "Form1.cs", "content": "…"}],
 "listings": [{"section": 1, "block": 2, "file": "Form1.cs"}],
 "shots": [{"section": 1, "block": 3, "mode": "window", "steps": […], "expect": "17"},
           {"section": 2, "block": 2, "mode": "manual", "reason": "окно конструктора форм"}]}"""

FIX_SYSTEM = """Ты исправляешь проект лабораторной работы: сборка не прошла. Верни только JSON с изменёнными файлами
целиком: {"files": [{"path": "…", "content": "…"}], "listings": [{"section": 1, "block": 2, "file": "…",
"code": "новый текст листинга, если код из него изменился"}]}. Меняй как можно меньше, тем же стилем; правила те же:
русские надписи, без служебных пометок, без файлов сборки, без работы с файлами вне папки программы, процессами и сетью."""


class FixOut(BaseModel):
    files: list[ProjectFile] = Field(default_factory=list)
    listings: list[ListingRef] = Field(default_factory=list)


def _blocks(st: LabState):
    for sec in st.report.sections if st.report else []:
        for bi, b in enumerate(sec.blocks, start=1):
            yield sec.n, bi, b


def needs_project(st: LabState) -> bool:
    """Есть ли в отчёте программа: листинг на поддерживаемом языке."""
    return any(isinstance(b, Listing) and (b.language or "").strip().lower() in CODE_LANGS for _, _, b in _blocks(st))


def lab_text(st: LabState) -> str:
    spec = st.spec
    tasks = "\n".join(f"{t.n}. {t.text}" for t in spec.tasks) if spec else ""
    parts = [f"Тема: {spec.topic if spec else ''}", f"Задания:\n{tasks}"]
    if spec and spec.variant_text:
        parts.append(f"Вариант: {spec.variant_text}")
    listings, figures = [], []
    for n, bi, b in _blocks(st):
        if isinstance(b, Listing):
            listings.append(f"[{n}.{bi}] {b.topic} ({b.language or 'код'}):\n<<<\n{b.code}\n>>>")
        elif isinstance(b, Figure) and not b.image_path:
            figures.append(f"[{n}.{bi}] {b.topic}. Что в кадре: {b.slot or '—'}")
    parts.append("Листинги отчёта:\n" + ("\n\n".join(listings) or "нет"))
    parts.append("Рисунки-скриншоты, которые нужно снять:\n" + ("\n".join(figures) or "нет"))
    if st.materials:
        parts.append("Файлы от преподавателя (ForLabs сам положит их в папку программы, читай их по имени): "
                     + ", ".join(m.name for m in st.materials))
    return "\n\n".join(parts)


def plan_project(st: LabState, llm, memory: str = "") -> ProjectPlan:
    user = lab_text(st) + (f"\n\nПамять предмета и требования:\n{memory[:6000]}" if memory.strip() else "")
    return llm.json([{"role": "system", "content": PROJECT_SYSTEM}, {"role": "user", "content": user}], ProjectPlan,
                    max_tokens=8000)


def fix_project(plan: ProjectPlan, log: str, llm) -> ProjectPlan:
    files = "\n\n".join(f"Файл {f.path}:\n<<<\n{f.content}\n>>>" for f in plan.files
                        if Path(f.path).suffix.lower() not in DATA_EXT)
    user = (f"Язык: {plan.language}, вид: {plan.kind}, главный файл: {plan.entry}.\n\nОшибки сборки:\n{log[-6000:]}"
            f"\n\nФайлы проекта:\n{files}")
    out = llm.json([{"role": "system", "content": FIX_SYSTEM}, {"role": "user", "content": user}], FixOut,
                   max_tokens=8000)
    by_path = {f.path: f for f in plan.files}
    for f in out.files:
        by_path[f.path] = f
    refs = {(r.section, r.block): r for r in plan.listings}
    for r in out.listings:
        refs[(r.section, r.block)] = r
    return plan.model_copy(update={"files": list(by_path.values()), "listings": list(refs.values())})


def _with_materials(plan: ProjectPlan, st: LabState) -> ProjectPlan:
    """Файлы данных от преподавателя — рядом с программой (модель их не переписывает)."""
    have = {f.path.lower() for f in plan.files}
    extra = [ProjectFile(path=m.name, content=m.text) for m in st.materials
             if Path(m.name).suffix.lower() in DATA_EXT and m.name.lower() not in have and "/" not in m.name]
    return plan.model_copy(update={"files": [*plan.files, *extra]}) if extra else plan


def _tool_title(key: str) -> str:
    return getattr(languages, "TOOL_TITLES", {}).get(key, key)


def build_and_shoot(pipe, st: LabState, plan: ProjectPlan, llm=None, emit=None) -> LabState:
    """Сборка (с исправлениями, если есть модель) → снимки → листинги → копия в папку сдачи. Меняет st."""
    from forlabs.runner.shots import RunTarget

    emit = emit or (lambda text, kind="info": None)
    lab = pipe.lab_dir(st)
    pdir = lab / "project"
    pdir.mkdir(parents=True, exist_ok=True)
    plan = _with_materials(plan, st)
    info = ProjectInfo(name=plan.name, language=plan.language, kind=plan.kind)
    tools = languages.detect()
    info.missing = languages.missing(plan, tools)
    work = pdir / "src"
    result = None
    shutil.rmtree(work, ignore_errors=True)
    languages.materialize(plan, work, tools)
    if not info.missing:
        emit("Собираю программу")
        result = languages.build(work, plan, tools)
        while not result.ok and llm is not None and info.fixes < MAX_FIXES:
            info.fixes += 1
            emit(f"Исправляю ошибки сборки ({info.fixes} из {MAX_FIXES})")
            plan = _with_materials(fix_project(plan, result.log, llm), st)
            if validate(plan):
                break
            shutil.rmtree(work, ignore_errors=True)
            languages.materialize(plan, work, tools)
            result = languages.build(work, plan, tools)
        info.built = bool(result.ok)
        if not result.ok:
            info.note = "Программу не собрал: ошибки остались. Проект в папке сдачи, снимки сделай сам."
    (pdir / "plan.json").write_text(plan.model_dump_json(indent=1), encoding="utf-8")
    info.blocked = safety.scan(plan)

    # листинги отчёта — как в коде проекта
    refs = {(r.section, r.block): r for r in plan.listings}
    for n, bi, b in _blocks(st):
        r = refs.get((n, bi))
        if isinstance(b, Listing) and r is not None and r.code.strip():
            b.code = r.code.rstrip()

    # снимки
    shots = {(s.section, s.block): s for s in plan.shots}
    shooter = pipe.shooter
    if shooter is None and sys.platform == "win32":
        from forlabs.runner.shots import WindowsShooter

        shooter = WindowsShooter()
    spec = None
    for n, bi, b in list(_blocks(st)):
        if not isinstance(b, Figure) or b.image_path:
            continue
        b.need_tool = None
        shot = shots.get((n, bi))
        if shot is None or shot.mode == "manual":
            b.auto_note = f"Этот снимок делаешь ты: {shot.reason}." if shot and shot.reason else None
            continue
        if info.missing:
            b.need_tool = info.missing[0]
            b.auto_note = f"Для снимка нужна программа {_tool_title(info.missing[0])}: установи её и нажми «Повторить»."
            continue
        if not info.built:
            b.auto_note = "Программа не собралась, снимок сделай сам."
            continue
        if info.blocked:
            b.auto_note = f"Сам не запускал: {info.blocked[0]}"
            continue
        if shooter is None:
            b.auto_note = "Снимки окон ForLabs делает только в Windows."
            continue
        if spec is None:
            spec = languages.run_spec(plan, result.artifact if result else None, work, tools)
        target = RunTarget(cmd=[] if spec.url and plan.language == "php" else list(spec.cmd), cwd=Path(spec.cwd),
                           console=spec.console, url=spec.url or None, env=dict(spec.env or {}),
                           server=list(spec.cmd) if spec.url and plan.language == "php" else None)
        emit(f"Снимаю рисунок: {b.topic}")
        rel = f"assets/shot_{n}_{bi}_{uuid.uuid4().hex[:6]}.png"
        res = shooter.shoot(target, shot, lab / rel)
        if res.ok:
            b.image_path, b.auto_note = rel, None
            pipe._sync(st, [rel])
        else:
            b.auto_note = f"Сам не снял: {res.reason}."
    try:
        pipe._sync(st, ["project/plan.json"])
    except Exception:
        pass

    # копия проекта в папку сдачи
    try:
        info.folder = str(_copy_to_hand_in(pipe, st, plan, work))
    except OSError as e:
        emit(f"Проект не скопировался в папку сдачи ({e})", "warn")
    st.project = info
    st.docx_sha = None
    return st


def _copy_to_hand_in(pipe, st: LabState, plan: ProjectPlan, work: Path) -> Path:
    from forlabs.core.store import safe_name
    from forlabs.core.subjects import get_subject
    from forlabs.pipeline.run import output_root

    profile = pipe._profile(st)
    short = safe_name(get_subject(st.subject_id).short_name)
    number = safe_name(st.spec.number if st.spec else st.number)
    out = output_root(profile) / short / f"ЛР{number}" / f"Проект {safe_name(plan.name) or 'программа'}"
    skip = set(getattr(languages, "BUILD_DIRS", ())) | {"obj", "bin", "out", "__pycache__", ".vs"}
    tmp = out.with_name(out.name + ".new")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(work, tmp, ignore=shutil.ignore_patterns(*skip))
    shutil.rmtree(out, ignore_errors=True)
    tmp.replace(out)
    return out


def load_plan(pipe, st: LabState) -> ProjectPlan | None:
    f = pipe.lab_dir(st) / "project" / "plan.json"
    if not f.exists():
        return None
    try:
        return ProjectPlan.model_validate(json.loads(f.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
