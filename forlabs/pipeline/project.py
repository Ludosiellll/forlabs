"""Этап «Проект»: из листингов отчёта — настоящий проект, который собирается и запускается; снимки окон программы
делает ForLabs, студенту остаётся только то, что снять нельзя (с причиной); копия проекта — рядом с отчётом.

Порядок: модель составляет план (файлы, какие листинги из каких файлов, сценарий каждого снимка) → проверка плана →
сборка, ошибки уходят модели (до трёх кругов) → проверка кода на опасное → листинги отчёта сверяются с кодом →
копия в папку сдачи → снимки в песочнице (пути на них — из папки сдачи). Сбой этапа не ломает лабу: рисунки
просто остаются студенту."""

from __future__ import annotations

import dataclasses
import difflib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from forlabs.core.llm import LLMError
from forlabs.core.models import Figure, LabState, Listing, ProjectInfo
from forlabs.runner import languages, safety
from forlabs.runner import plan as plan_rules
from forlabs.runner.plan import DATA_EXT, ListingRef, ProjectFile, ProjectPlan, validate  # noqa: F401 (validate — для конвейера)

MAX_FIXES = 3
CODE_LANGS = {"python", "py", "csharp", "cs", "c#", "cpp", "c++", "c", "java", "js", "javascript", "html", "css",
              "php", "go", "sql", "mysql"}

PROJECT_SYSTEM = """Ты собираешь из отчёта по лабораторной работе настоящий проект: студент откроет его в своей среде,
соберёт и запустит, а ForLabs сам сделает снимки окон программы для отчёта. Ответ — только JSON.

Код и файлы:
1. Все файлы исходного кода целиком, чтобы проект собирался и работал (со всеми using, import, #include). Код из
   листингов отчёта уже согласован: он входит в файлы дословно, ничего в нём не меняй — ни ради снимков (фокус,
   имена элементов, клавиши), ни «для надёжности». Чего не хватает (другие классы, файл Designer формы, App.xaml,
   разметка, главный файл), допиши сам в том же стиле.
2. listings — по записи на КАЖДЫЙ листинг отчёта (все номера [N.M], и фрагменты, и файлы данных): из какого файла
   он взят. Если код листинга в итоговом файле всё же другой (задание продолжает прежнее и меняет его код,
   например в main добавлены строки), верни в code текст этого листинга так, как он стоит в файле; иначе code не
   пиши.
3. Файлы сборки (csproj, sln, pom.xml, go.mod, CMakeLists.txt, package.json со скриптами, .bat, .ps1) НЕ пиши — их
   сделает ForLabs. Файлы данных (txt, csv, json), которые читает программа, положи в проект; файлы от
   преподавателя ForLabs положит сам.
4. Пиши как студент колледжа: заголовки окон, надписи и сообщения — по-русски и по теме лабы; имена — как в отчёте.
   Никаких служебных пометок, «TODO», «generated», английских надписей, отладочного вывода и лишних окон.
5. Программа не трогает файлы вне своей папки, не запускает другие программы, не обращается к реестру и сети
   (кроме локального сервера для сайта на PHP).
6. name — короткое название по-русски, 1–4 слова (это имя папки проекта): без двоеточий, кавычек, слэшей и
   названия языка. language — один из: csharp, cpp, python, java, javascript, web, php, go, mysql; kind — console,
   winforms, wpf, web, script, sql или gui (окно Swing/tkinter); entry — главный файл (у сайта на PHP — файл .php).

Снимки — по одному на каждый рисунок-скриншот (section и block — номера из квадратных скобок). ForLabs запускает
программу в Windows студента, проходит сценарий и проверяет, что на экране есть expect:
- mode "console": input — что набрать на клавиатуре, каждая строка с \\n, ровно столько строк, сколько программа
  прочитает (лишний Enter закроет окно консоли; программа ничего не читает — input пустой), в том формате, как
  читает код: «12 5\\n» — если два числа читаются из одной строки, «12\\n5\\n» — если по одному. expect — короткий кусок строки, которую печатает сама программа:
  подпись результата («Общая стоимость:»). Сам ничего не считай: число, которое вычисляет программа, добавляй к
  подписи, только если оно уже написано в тексте отчёта. Набранный ввод в expect не включай. C# и Java в русской
  Windows читают и печатают дробные числа с запятой (2,5): в input для них бери целые числа, в expect дробное — с
  запятой.
- mode "window", WinForms и WPF: steps — {"action":"set","target":"имя поля","value":"12"},
  {"action":"select","target":"имя списка","value":"пункт"}, {"action":"click","target":"имя кнопки"},
  {"action":"check","target":"имя флажка"}, {"action":"wait","value":"1"}; target — Name элемента (WinForms) или
  x:Name (WPF), это имя должно быть задано в коде. expect — текст, который появится в окне; если после нажатия
  открывается окно сообщения, снимок будет его.
- mode "window", Swing и tkinter: только клавиатура — {"action":"type","value":"12"},
  {"action":"key","value":"tab|enter|space|esc"}, {"action":"wait","value":"1"}. Tab переходит по полям и кнопкам в
  порядке их создания, надписи пропускает. Swing: при запуске курсор уже в первом поле. tkinter: при запуске курсора
  нет нигде, первый шаг — key tab (если код не вызывает focus_set). Кнопку нажимает key space, когда до неё дошли
  клавишей Tab; key enter — только если в коде Enter привязан (bind("<Return>", …), setDefaultButton). Сначала
  запиши в снимке "order" — по каким полям и кнопкам ходит Tab («Длина → Ширина → Вычислить»), и составь шаги по нему:
  Swing — type 6, key tab, type 4, key tab, key space; tkinter — то же, но первым шагом key tab. Лишний Tab уводит с
  кнопки обратно в первое поле.
- mode "browser": url — путь страницы в проекте (index.html, form.php); сайт на PHP открывается через встроенный
  сервер. Если на снимке результат после формы — steps: {"action":"set","target":"id:имя","value":"…"} (id поля
  из HTML), {"action":"select","target":"id:список","value":"пункт"}, {"action":"click","target":"текст кнопки"};
  expect — текст, который появится на странице. У полей формы, участвующих в снимке, обязательно атрибут id.
- MySQL: ForLabs запускает консольный клиент mysql в папке проекта, сервер чистый: баз лабы на нём нет. Результат
  запросов — mode "console", input — source каждого скрипта проекта по порядку: создание и заполнение, затем запрос
  («source shop.sql\\nsource select.sql\\n»), или сами запросы; expect — значение из результата. Скрипты должны
  выполняться повторно без ошибок: перед CREATE DATABASE — DROP DATABASE IF EXISTS той же базы, перед
  CREATE USER — DROP USER IF EXISTS того же пользователя.
- mode "manual" + reason — только если снять может лишь студент: окно среды разработки, конструктора или
  обозревателя решений, phpMyAdmin, Блокнот и другие программы, страница после ввода данных или отправки формы.

Формат:
{"language": "csharp", "kind": "winforms", "name": "Калькулятор", "entry": "Program.cs",
 "files": [{"path": "Program.cs", "content": "…"}, {"path": "Form1.cs", "content": "…"}],
 "listings": [{"section": 1, "block": 2, "file": "Form1.cs"}],
 "shots": [{"section": 1, "block": 3, "mode": "window", "steps": […], "expect": "Результат:"},
           {"section": 2, "block": 2, "mode": "manual", "reason": "окно конструктора форм"}]}"""

PARTS_NOTE = """Прошлый ответ не поместился целиком: проект большой. Сейчас верни план без текста файлов — у каждого
файла в files только path (content не пиши); listings и shots — полностью, как обычно. Тексты файлов я попрошу
следом, по одному."""

FILE_SYSTEM = """Ты пишешь один файл проекта лабораторной работы по готовому плану. Ответ — только JSON:
{"path": "…", "content": "полный текст файла"}. Код из листингов отчёта, которые берутся из этого файла, входит в него
дословно; с уже написанными файлами проекта файл должен собираться вместе (те же имена классов, полей, функций).
Правила те же: русские надписи, без служебных пометок и отладочного вывода, без работы с файлами вне папки
программы, процессами и сетью."""

FIX_SYSTEM = """Ты исправляешь проект лабораторной работы: сборка не прошла. Верни только JSON с изменёнными файлами
целиком: {"files": [{"path": "…", "content": "…"}], "listings": [{"section": 1, "block": 2, "file": "…",
"code": "новый текст листинга"}]}. Исправляй причину ошибки, меняй как можно меньше, тем же стилем. Если изменился
код, который входит в листинг отчёта (список ниже), верни этот листинг в listings — так, как он теперь стоит в файле.
Файлы сборки (csproj, go.mod, pom.xml, package.json, CMakeLists.txt) делает ForLabs — не пиши и не меняй их. Правила
те же: русские надписи, без служебных пометок, без работы с файлами вне папки программы, процессами и сетью."""

PLAN_MAX_TOKENS = 8000
FILE_MAX_TOKENS = 8000  # один файл большого проекта (план по частям)
MAX_PART_FILES = 12  # большой проект по частям: не больше стольких файлов (по запросу на файл)
DATA_PREVIEW_LINES = 12  # начало файла данных от преподавателя в запросе: столбцы, разделитель, формат чисел
HEADER = re.compile(r"\s*(?:import\s|using\s+[\w.=\s]+;|#\s*include\b|package\s|from\s+[\w.]+\s+import\s|"
                    r"require(?:_once)?\b|include(?:_once)?\b)")
KEYBOARD = {  # чем программа читает клавиатуру (нет ни одного — сценарий ничего не набирает)
    "python": r"\binput\s*\(|\bstdin\b", "cpp": r"\bcin\b|\bstdin\b|\b(?:scanf|getline|getchar|_?getch|gets)\b",
    "csharp": r"Console\s*\.\s*Read", "java": r"System\s*\.\s*in\b|\b(?:Scanner|Console|BufferedReader)\b",
    "javascript": r"\breadline\b|\bstdin\b|\bprompt\s*\(", "php": r"\bSTDIN\b|php://stdin|\b(?:readline|fscanf)\s*\(",
    "go": r"\b(?:Scan\w*|Sscan\w*|Fscan\w*)\s*\(|\bStdin\b"}
NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")
NAME_BAD = re.compile(r'[\\/:<>"|?*«»\x00-\x1f]+')


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
        for m in st.materials:
            if Path(m.name).suffix.lower() in DATA_EXT and m.text.strip():
                lines = m.text.strip("\n").split("\n")
                head = "\n".join(lines[:DATA_PREVIEW_LINES])[:1500]
                more = f"\n… (всего строк: {len(lines)})" if len(lines) > DATA_PREVIEW_LINES else ""
                parts.append(f"Начало файла {m.name}:\n<<<\n{head}{more}\n>>>")
    return "\n\n".join(parts)


def _tidy(plan: ProjectPlan) -> ProjectPlan:
    """Мелочи, из-за которых не стоит терять весь этап: двоеточие или кавычки в имени проекта (это имя папки) и
    файлы сборки, которые модель всё же написала (их всё равно пишет ForLabs по своим шаблонам)."""
    name = " ".join(NAME_BAD.sub(" ", plan.name).split()).strip(". ")[:60].strip(". ") or plan.name
    files = [f for f in plan.files if plan_rules._build_file_problem(f) is None]
    return plan.model_copy(update={"name": name, "files": files})


def _files_text(files) -> str:
    return "\n\n".join(f"Файл {f.path}:\n<<<\n{f.content}\n>>>" for f in files
                        if Path(f.path).suffix.lower() not in DATA_EXT or f.content.strip())


def _plan_in_parts(user: str, llm) -> ProjectPlan:
    """Большой проект, который не влез в один ответ: сначала план без текстов файлов, затем каждый файл отдельно."""
    head = _tidy(llm.json([{"role": "system", "content": PROJECT_SYSTEM},
                           {"role": "user", "content": f"{user}\n\n{PARTS_NOTE}"}], ProjectPlan,
                          max_tokens=PLAN_MAX_TOKENS))
    todo = [f for f in head.files if not f.content.strip()]
    if len(todo) > MAX_PART_FILES:
        raise LLMError("bad_response", f"{len(todo)} файлов", "Проект программы слишком большой.")
    refs = "; ".join(f"[{r.section}.{r.block}] → {r.file}" for r in head.listings) or "нет"
    manifest = (f"План проекта: язык {head.language}, вид {head.kind}, главный файл {head.entry}.\n"
                f"Файлы: {', '.join(f.path for f in head.files)}.\nЛистинги отчёта: {refs}.")
    done: dict[str, ProjectFile] = {f.path: f for f in head.files if f.content.strip()}
    for f in todo:
        mine = ", ".join(f"[{r.section}.{r.block}]" for r in head.listings if r.file == f.path) or "нет"
        written = _files_text(done.values())
        ask = (f"{user}\n\n{manifest}\n\n" + (f"Уже написаны:\n{written}\n\n" if written else "")
               + f"Напиши файл {f.path} целиком. Листинги отчёта из этого файла: {mine}.")
        out = llm.json([{"role": "system", "content": FILE_SYSTEM}, {"role": "user", "content": ask}], ProjectFile,
                       max_tokens=FILE_MAX_TOKENS)
        done[f.path] = ProjectFile(path=f.path, content=out.content)
    return head.model_copy(update={"files": [done[f.path] for f in head.files]})


def _report_numbers(st: LabState) -> set[str]:
    """Числа, которые есть в отчёте и данных лабы (в expect их можно не перепроверять)."""
    texts = [st.spec.topic, *(t.text for t in st.spec.tasks), st.spec.variant_text or ""] if st.spec else []
    for _, _, b in _blocks(st):
        texts += [getattr(b, a, None) or "" for a in ("text", "code", "slot", "topic")]
        texts += [str(x) for x in getattr(b, "items", None) or []]
    texts += [m.text for m in st.materials]
    return {n.replace(",", ".").lstrip("-") for t in texts for n in NUMBER.findall(t)}


def _trusted_expects(plan: ProjectPlan, st: LabState) -> ProjectPlan:
    """Число в expect, которого нет ни в отчёте, ни во вводе, модель посчитала сама — и может ошибиться в
    арифметике. Тогда проверяется только подпись перед ним («Общая стоимость:»): снимок не теряется из-за счёта."""
    known = _report_numbers(st)
    shots = []
    for sh in plan.shots:
        typed = sh.input + " " + " ".join(step.value for step in sh.steps)
        mine = known | {n.replace(",", ".").lstrip("-") for n in NUMBER.findall(typed)}
        bad = next((m for m in NUMBER.finditer(sh.expect) if m[0].replace(",", ".").lstrip("-") not in mine), None)
        shots.append(sh.model_copy(update={"expect": sh.expect[:bad.start()].rstrip(" -—=")}) if bad else sh)
    return plan.model_copy(update={"shots": shots})


def _quiet_console(plan: ProjectPlan) -> ProjectPlan:
    """Программа не читает клавиатуру, а сценарий что-то набирает: в Windows этот Enter закрыл бы «pause» — и окно
    консоли исчезло бы до снимка. Такой ввод убирается."""
    reads = KEYBOARD.get(plan.language)
    code = "\n".join(f.content for f in plan.files if Path(f.path).suffix.lower() in plan_rules.CODE_EXT[plan.language])
    if reads is None or re.search(reads, code):
        return plan
    shots = [sh.model_copy(update={"input": "", "steps": [x for x in sh.steps if x.action not in ("type", "key")]})
             if sh.mode == "console" else sh for sh in plan.shots]
    return plan.model_copy(update={"shots": shots})


def plan_project(st: LabState, llm, memory: str = "") -> ProjectPlan:
    user = lab_text(st) + (f"\n\nПамять предмета и требования:\n{memory[:6000]}" if memory.strip() else "")
    try:
        plan = llm.json([{"role": "system", "content": PROJECT_SYSTEM}, {"role": "user", "content": user}],
                        ProjectPlan, max_tokens=PLAN_MAX_TOKENS)
    except LLMError as e:
        if e.kind != "bad_response":  # сеть, ключ, лимит — части не помогут
            raise
        plan = _plan_in_parts(user, llm)  # ответ обрезан по длине или JSON не собрался
    return _quiet_console(_trusted_expects(_tidy(plan), st))


def fix_project(plan: ProjectPlan, log: str, llm) -> ProjectPlan:
    files = "\n\n".join(f"Файл {f.path}:\n<<<\n{f.content}\n>>>" for f in plan.files
                        if Path(f.path).suffix.lower() not in DATA_EXT)
    refs = "; ".join(f"[{r.section}.{r.block}] из {r.file}" for r in plan.listings) or "нет"
    user = (f"Язык: {plan.language}, вид: {plan.kind}, главный файл: {plan.entry}.\n\nОшибки сборки:\n{log[-6000:]}"
            f"\n\nЛистинги отчёта: {refs}.\n\nФайлы проекта:\n{files}")
    out = llm.json([{"role": "system", "content": FIX_SYSTEM}, {"role": "user", "content": user}], FixOut,
                   max_tokens=8000)
    by_path = {f.path: f for f in plan.files}
    for f in out.files:
        if plan_rules._build_file_problem(f) is None:  # go.mod, csproj и т.п. всё равно пишет ForLabs
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


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def listing_in_file(code: str, body: str) -> str | None:
    """Листинг так, как он стоит в файле: от первой до последней совпавшей строки. Нужен, когда следующее задание
    дополнило код листинга (в main добавлен вызов, в класс — метод). None — в файле этого кода нет."""
    a = [x.strip() for x in code.splitlines()]
    lines = body.splitlines()
    sm = difflib.SequenceMatcher(lambda x: not x, a, [x.strip() for x in lines], autojunk=False)
    blocks = [m for m in sm.get_matching_blocks() if m.size and any(a[m.a:m.a + m.size])]
    if not blocks or sum(m.size for m in blocks) < 0.6 * sum(1 for x in a if x):
        return None
    solid = [m for m in blocks if any(len(x) > 3 for x in a[m.a:m.a + m.size])]  # не «}» и «{» где-то в стороне
    start, end = solid[0].b, solid[-1].b + solid[-1].size
    if HEADER.match(next((x for x in a if x), "")):  # листинг — файл целиком: и импорты, добавленные для сборки
        while start > 0 and (HEADER.match(lines[start - 1]) or not lines[start - 1].strip()):
            start -= 1
    while end < len(lines) and any(m.b == end for m in blocks):  # закрывающие скобки сразу за кодом — его часть
        end += next(m.size for m in blocks if m.b == end)
    if end - start > 2 * len(a) + 30:
        return None
    return "\n".join(lines[start:end]).strip("\n")


def shot_url(page: str, url: str) -> str:
    """Адрес страницы снимка сайта на сервере: путь из плана («form.php», «/index.html?x=1») — от корня сайта, а не
    от главной страницы (иначе вышло бы «…/order.php/form.php» — снова главная, и без стилей). Чужой адрес —
    ValueError."""
    from forlabs.runner.shots import url_problem

    if url and (why := url_problem(url)) is not None:
        raise ValueError(why)
    if not url or re.match(r"[a-z][a-z0-9+.-]*://", url, re.I) or not page.startswith("http"):
        return url or page
    root = page.split("/", 3)
    return "/".join(root[:3]) + "/" + url.lstrip("/")


def page_uri(root: Path, url: str) -> str:
    """Страница сайта без сервера — file://-адрес в папке root («about.html#cont» → …/about.html#cont). Путь вне
    папки проекта — ValueError (снимок не откроет чужой файл)."""
    from forlabs.runner.shots import url_problem

    if (why := url_problem(url)) is not None:
        raise ValueError(why)
    if re.match(r"[a-z][a-z0-9+.-]*:", url, re.I):
        return url
    m = re.match(r"([^?#]*)(.*)", url.lstrip("/"), re.S)
    page = Path(root, m[1]).resolve()
    if not page.is_relative_to(Path(root).resolve()):
        raise ValueError("путь страницы выходит за папку проекта")
    return page.as_uri() + m[2]


def _target_and_shot(plan: ProjectPlan, spec, shot, work: Path, hand_in: Path, static: bool):
    """Как запустить программу для снимка и какой адрес открыть. Адрес вне проекта — ValueError."""
    from forlabs.runner.shots import RunTarget

    target = RunTarget(cmd=[] if spec.url and plan.language == "php" else list(spec.cmd),
                       cwd=hand_in if static else Path(spec.cwd), console=spec.console,
                       url=page_uri(hand_in, plan.entry) if static else spec.url or None,
                       env=dict(spec.env or {}),
                       server=list(spec.cmd) if spec.url and plan.language == "php" else None,
                       title=_in_hand_in(spec.cmd[0], work, hand_in) if spec.cmd else None)
    if spec.url.startswith("http") and shot.url:
        shot = shot.model_copy(update={"url": shot_url(spec.url, shot.url)})
    elif static and shot.url:
        shot = shot.model_copy(update={"url": page_uri(hand_in, shot.url)})
    return target, shot


def drop_bad_shots(plan: ProjectPlan) -> ProjectPlan:
    """Снимок с ошибкой в сценарии (адрес вне проекта и т.п.) — студенту, а проект всё равно собирается."""
    shots = []
    for s in plan.shots:
        one = validate(plan.model_copy(update={"shots": [s]}))
        bad = [p for p in one if p.startswith(f"Снимок {s.section}.{s.block}")]
        shots.append(s.model_copy(update={"mode": "manual", "steps": [], "url": "",
                                          "reason": "в сценарии снимка ошибка, сними его сам"}) if bad else s)
    return plan.model_copy(update={"shots": shots})


def _in_hand_in(program: str, work: Path, hand_in: Path) -> str | None:
    """Путь программы, как у студента (в папке сдачи), для заголовка консоли. Интерпретатор (python, node) — None:
    тогда заголовок — его путь, как при обычном запуске."""
    try:
        rel = Path(program).resolve().relative_to(work.resolve())
    except (ValueError, OSError):
        return None
    return str(hand_in / rel)


def _tool_title(key: str) -> str:
    return getattr(languages, "TOOL_TITLES", {}).get(key, key)


def start_mysql(client: str):
    """Свой сервер MySQL на время снимков лабы: чистая папка данных, только 127.0.0.1, свободный порт. В Windows —
    в папке запуска с меткой Low и с пониженными правами. Сервер студента и его базы не трогаются."""
    from forlabs.runner import mysqlrun

    win = sys.platform == "win32"
    if win:
        from forlabs.runner import winapi
        from forlabs.runner.shots import run_dir

        root = run_dir()
        if not str(mysqlrun.short_path(root)).isascii():
            # имя пользователя на кириллице, а коротких имён на диске нет: такой путь понимает не каждая сборка MySQL
            shutil.rmtree(root, ignore_errors=True)
            root = run_dir(Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "ForLabs" / "runs")
        winapi.low_label_dir(str(root))  # писать сервер с пониженными правами может только сюда
    else:
        root = Path(tempfile.mkdtemp(prefix="forlabs-mysql-"))
    try:
        server = mysqlrun.start(client, root, low=win)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    server.root = root
    return server


def build_and_shoot(pipe, st: LabState, plan: ProjectPlan, llm=None, emit=None) -> LabState:
    """Сборка (с исправлениями, если есть модель) → листинги → копия в папку сдачи → снимки. Меняет st."""

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
    # опасный код не собирается вовсе: сборка и проверка синтаксиса — тоже запуск чужих инструментов над ним
    info.blocked = safety.scan(plan)
    if not info.missing and not info.blocked:
        emit("Собираю программу")
        result = languages.build(work, plan, tools)
        while not result.ok and llm is not None and info.fixes < MAX_FIXES:
            info.fixes += 1
            emit(f"Исправляю ошибки сборки ({info.fixes} из {MAX_FIXES})")
            fixed = _with_materials(fix_project(plan, result.log, llm), st)
            if validate(fixed):
                break
            plan = fixed
            shutil.rmtree(work, ignore_errors=True)
            languages.materialize(plan, work, tools)
            info.blocked = safety.scan(plan)  # исправление тоже проверяется до сборки
            if info.blocked:
                break
            result = languages.build(work, plan, tools)
        info.built = bool(result.ok) and not info.blocked
        if not result.ok:
            info.note = "Программу не собрал: ошибки остались. Проект в папке сдачи, снимки сделай сам."
    if info.blocked:
        info.note = f"Сам не собирал и не запускал: {info.blocked[0]}"
    (pdir / "plan.json").write_text(plan.model_dump_json(indent=1), encoding="utf-8")

    # листинги отчёта — как в коде проекта
    refs = {(r.section, r.block): r for r in plan.listings}
    for n, bi, b in _blocks(st):
        r = refs.get((n, bi))
        if not isinstance(b, Listing) or r is None:
            continue
        code = r.code.rstrip() if r.code.strip() else b.code
        f = plan.file(r.file)
        got = listing_in_file(code, f.content) if f is not None else None
        # задание дополнило код или для сборки добавлены импорты, а модель листинг не вернула — берём из файла
        if got and (_squash(code) not in _squash(f.content) or
                    (_squash(got) != _squash(code) and _squash(got).endswith(_squash(code)))):
            code = got
        b.code = code

    # копия проекта в папку сдачи — до снимков: на снимках пути из неё, как при запуске самим студентом
    hand_in = work
    try:
        hand_in, locked = _copy_to_hand_in(pipe, st, plan, work)
        info.folder = str(hand_in)
        if locked:
            emit("Не обновил файлы проекта, открытые в другой программе: " + ", ".join(locked[:5])
                 + ". Закрой их и нажми «Пересобрать».", "warn")
    except OSError as e:
        emit(f"Проект не скопировался в папку сдачи ({e})", "warn")

    # снимки
    shots = {(s.section, s.block): s for s in plan.shots}
    shooter = pipe.shooter
    if shooter is None and sys.platform == "win32":
        from forlabs.runner.shots import WindowsShooter

        shooter = WindowsShooter()
    spec = None
    db, db_error = None, None  # свой сервер MySQL: поднимается к первому снимку, гасится после последнего
    try:
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
                b.auto_note = (f"Для снимка нужна программа {_tool_title(info.missing[0])}: установи её и нажми "
                               "«Повторить».")
                continue
            if info.blocked:
                b.auto_note = f"Сам не запускал: {info.blocked[0]}"
                continue
            if not info.built:
                b.auto_note = "Программа не собралась, снимок сделай сам."
                continue
            if shooter is None:
                b.auto_note = "Снимки окон ForLabs делает только в Windows."
                continue
            if spec is None:
                spec = languages.run_spec(plan, result.artifact if result else None, work, tools)
            if plan.language == "mysql":
                if db is None and db_error is None:
                    emit("Запускаю отдельный сервер MySQL для снимков")
                    try:
                        db = start_mysql(tools["mysql"].path)
                        spec = dataclasses.replace(spec, cmd=db.client())
                    except Exception as e:  # noqa: BLE001 — сервер не поднялся: снимки студенту, отчёт собирается
                        db_error = str(e).rstrip(".") or "сервер MySQL не запустился"
                if db_error:
                    b.auto_note = f"Сам не снял: {db_error}."
                    continue
            static = plan.language == "web"  # страница без сервера — из папки сдачи (адресная строка как у студента)
            try:
                target, shot = _target_and_shot(plan, spec, shot, work, hand_in, static)
            except ValueError as e:
                b.auto_note = f"Сам не снял: {e}."
                continue
            emit(f"Снимаю рисунок: {b.topic}")
            rel = f"assets/shot_{n}_{bi}_{uuid.uuid4().hex[:6]}.png"
            res = shooter.shoot(target, shot, lab / rel)
            if res.ok:
                b.image_path, b.auto_note = rel, None
                pipe._sync(st, [rel])
            else:
                b.auto_note = f"Сам не снял: {res.reason}."
    finally:
        if db is not None:
            db.stop()
    try:
        pipe._sync(st, ["project/plan.json"])
    except Exception:
        pass
    st.project = info
    st.docx_sha = None
    return st


def _copy_to_hand_in(pipe, st: LabState, plan: ProjectPlan, work: Path) -> tuple[Path, list[str]]:
    """Проект в папку сдачи. Меняются только свои файлы (каждый — атомарно), свои прошлые, которых больше нет, —
    удаляются; файлы, которые студент положил туда сам, не трогаются. Список своих файлов — в папке лабы, а не у
    студента. Возвращает (папка, файлы, которые не обновились — открыты в другой программе)."""
    import os

    from forlabs.core.store import safe_name
    from forlabs.core.subjects import get_subject
    from forlabs.pipeline.run import output_root

    profile = pipe._profile(st)
    short = safe_name(get_subject(st.subject_id).short_name)
    number = safe_name(st.spec.number if st.spec else st.number)
    out = output_root(profile) / short / f"ЛР{number}" / f"Проект {safe_name(plan.name) or 'программа'}"
    skip = set(getattr(languages, "BUILD_DIRS", ())) | {"obj", "bin", "out", "__pycache__", ".vs"}
    files = sorted(f.relative_to(work).as_posix() for f in work.rglob("*")
                   if f.is_file() and not (set(f.relative_to(work).parts[:-1]) & skip) and f.name != "forlabs-run.cmd")
    manifest = pipe.lab_dir(st) / "project" / "hand_in.json"
    try:
        before = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = {}
    old = set(before.get("files", [])) if before.get("folder") == str(out) else set()
    out.mkdir(parents=True, exist_ok=True)
    locked: list[str] = []
    for rel in files:
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:6]}.tmp")
        try:
            shutil.copy2(work / rel, tmp)
            os.replace(tmp, dst)
        except OSError:
            tmp.unlink(missing_ok=True)
            locked.append(rel)
    for rel in sorted(old - set(files)):  # свои прошлые файлы, которых в проекте больше нет
        try:
            (out / rel).unlink(missing_ok=True)
            parent = (out / rel).parent
            while parent != out and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        except OSError:
            locked.append(rel)
    manifest.write_text(json.dumps({"folder": str(out), "files": files}, ensure_ascii=False), encoding="utf-8")
    return out, locked


def load_plan(pipe, st: LabState) -> ProjectPlan | None:
    f = pipe.lab_dir(st) / "project" / "plan.json"
    if not f.exists():
        return None
    try:
        return ProjectPlan.model_validate(json.loads(f.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
