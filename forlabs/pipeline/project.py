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

from pydantic import BaseModel, Field, field_validator

from forlabs.core.fs import remove_tree
from forlabs.core.llm import LLMError
from forlabs.core.models import Figure, LabState, Listing, ProjectInfo
from forlabs.runner import languages, safety
from forlabs.runner import plan as plan_rules
from forlabs.runner.plan import (  # noqa: F401 (validate — для конвейера)
    DATA_EXT,
    ListingRef,
    ProgramPlan,
    ProjectFile,
    ProjectPlan,
    ShotPlan,
    validate,
)

MAX_FIXES = 3
BUILD_LOG_LIMIT = 4000  # ошибки сборки, которые остаются в лабе: их видят студент и чат правок
CODE_LANGS = {"python", "py", "csharp", "cs", "c#", "cpp", "c++", "c", "java", "js", "javascript", "html", "css",
              "php", "go", "sql", "mysql"}

PROJECT_HEAD = """Ты собираешь из отчёта по лабораторной работе настоящий проект: студент откроет его в своей среде,
соберёт и запустит, а ForLabs сам сделает снимки окон программы для отчёта. Ответ — только JSON.

Код и файлы:
1. Все файлы исходного кода целиком, чтобы проект собирался и работал (со всеми using, import, #include). Код из
   листингов отчёта уже согласован: он входит в файлы дословно, ничего в нём не меняй — ни ради снимков (фокус,
   имена элементов, клавиши), ни «для надёжности». Чего не хватает (другие классы, файл Designer формы, App.xaml,
   разметка, главный файл), допиши сам в том же стиле. В дописанном должно быть всё, на что опирается код листингов:
   у каждого элемента формы — имя (Name), у кнопок — привязанные обработчики (Click += …), у списков — пункты,
   с которыми код сравнивает выбранное значение (Items.AddRange(…)). Программа должна работать сразу после сборки.
2. listings — по записи на КАЖДЫЙ листинг отчёта (все номера [N.M], и фрагменты, и файлы данных): из какого файла
   он взят. Если код листинга в итоговом файле всё же другой (задание продолжает прежнее и меняет его код,
   например в main добавлены строки), верни в code текст этого листинга так, как он стоит в файле; иначе code не
   пиши.
3. Файлы сборки (csproj, sln, pom.xml, go.mod, CMakeLists.txt, package.json со скриптами, .bat, .ps1) НЕ пиши — их
   сделает ForLabs. Файлы данных (txt, csv, json), которые читает программа, положи в проект; файлы от
   преподавателя ForLabs положит сам.
4. Пиши как студент колледжа: заголовки окон, надписи и сообщения — по-русски и по теме лабы; имена — как в отчёте.
   Никаких служебных пометок, «TODO», «generated», английских надписей, отладочного вывода и лишних окон.
5. Программа не трогает файлы вне своей папки, не запускает другие программы, не обращается к реестру. В интернет —
   только если этого требует задание (запрос к сайту, API, загрузка страницы): ForLabs запустит её в песочнице.
6. name — короткое название по-русски, 1–4 слова (это имя папки проекта): без двоеточий, кавычек, слэшей и
   названия языка. language — один из: csharp, cpp, python, java, javascript, web, php, go, mysql; kind — console,
   winforms, wpf, web, script, sql или gui (окно Swing/tkinter); entry — главный файл (у сайта на PHP — файл .php).
7. Несколько программ в одной лабе (у задания своя программа; библиотека классов и приложение, которое её
   использует; приложение и программа для его проверки) в один проект не складывай: две точки входа вместе не
   соберутся, и студент не сможет запустить их по отдельности. Опиши каждую в programs, а её файлы положи в папку
   с её именем: "programs": [{"name": "ClassLibrary1", "kind": "library"}, {"name": "CalculatorApp",
   "kind": "winforms", "entry": "CalculatorApp/Program.cs", "uses": ["ClassLibrary1"]}]. name — имя проекта, как в
   отчёте или методичке, одно слово без пробелов; kind — как у плана. library (библиотека классов, DLL) и uses
   (библиотеки плана, которые программа подключает) — только C#: ForLabs сам напишет каждой программе .csproj со
   ссылками и общий .sln. В других языках общий код положи в папку каждой программы. У каждого снимка укажи
   "program" — какую программу запустить. Программа одна — programs не пиши."""

SHOT_RULES = """- mode "console": input — что набрать на клавиатуре, каждая строка с \\n, ровно столько строк, сколько программа
  прочитает (лишний Enter закроет окно консоли; программа ничего не читает — input пустой), в том формате, как
  читает код: «12 5\\n» — если два числа читаются из одной строки, «12\\n5\\n» — если по одному. expect — короткий кусок строки, которую печатает сама программа:
  подпись результата («Общая стоимость:»). Сам ничего не считай: число, которое вычисляет программа, добавляй к
  подписи, только если оно уже написано в тексте отчёта. Набранный ввод в expect не включай. C# и Java в русской
  Windows читают и печатают дробные числа с запятой (2,5): в input для них бери целые числа, в expect дробное — с
  запятой.
- mode "window", WinForms и WPF: steps — {"action":"set","target":"имя поля","value":"12"},
  {"action":"select","target":"имя списка","value":"пункт"}, {"action":"click","target":"имя кнопки"},
  {"action":"check","target":"имя флажка"}, {"action":"wait","value":"1"}; target — Name элемента (WinForms) или
  x:Name (WPF), это имя должно быть задано в коде. select выбирает только пункт, который есть в списке: список
  заполняется в коде. Сценарий начинается в окне, которое открывается при запуске; другую форму открой шагом —
  click по кнопке или пункту меню, который её открывает: следующие шаги пойдут в новом окне, и снимок будет его.
  expect — текст, который появится в окне; если после нажатия открывается окно сообщения, снимок будет его.
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
- mode "app" + app + goal (+ open) — окно другой программы, которое ForLabs снимет сам, работая в ней: среда
  разработки с кодом или обозревателем решений, конструктор формы, SSMS, Workbench, Блокнот. app — ключ: {apps};
  goal — что сделать (например «открыть Program.cs в редакторе, чтобы был виден метод Main»); open — файлы
  проекта, которые открыть при запуске (для Visual Studio — файл .csproj или .sln и нужный .cs); expect — что
  должно быть видно на снимке.
- mode "manual" + reason — только если снять может лишь студент: phpMyAdmin и сайты со входом, телефон, фото."""

_AGENT_APPS = __import__("forlabs.agent.apps", fromlist=["APPS"]).APPS
SHOT_RULES = SHOT_RULES.replace("{apps}", ", ".join(
    k for k, a in _AGENT_APPS.items() if a.kind in ("ide", "db", "editor")))
SCENARIO_RULES = SHOT_RULES[:SHOT_RULES.index('- mode "app"')].rstrip()  # то, что ForLabs проходит по шагам сам

PROJECT_SYSTEM = (PROJECT_HEAD + """

Снимки — по одному на каждый рисунок-скриншот (section и block — номера из квадратных скобок). ForLabs запускает
программу в Windows студента, проходит сценарий и проверяет, что на экране есть expect:
""" + SHOT_RULES + """

Формат:
{"language": "csharp", "kind": "winforms", "name": "Калькулятор", "entry": "Program.cs",
 "files": [{"path": "Program.cs", "content": "…"}, {"path": "Form1.cs", "content": "…"}],
 "listings": [{"section": 1, "block": 2, "file": "Form1.cs"}],
 "shots": [{"section": 1, "block": 3, "mode": "window", "steps": […], "expect": "Результат:"},
           {"section": 2, "block": 2, "mode": "app", "app": "visualstudio", "goal": "открыть Form1.cs в конструкторе",
            "open": ["Form1.cs"], "expect": "конструктор формы Form1 с кнопкой «Вычислить»"}]}
Несколько программ — добавь "programs": [{"name": "…", "kind": "…", "entry": "имя/главный файл", "uses": []}], пути
файлов начинай с имени программы, у снимков пиши "program": "имя программы".""")

PARTS_NOTE = """Прошлый ответ не поместился целиком: проект большой. Сейчас верни план без текста файлов — у каждого
файла в files только path (content не пиши); listings и shots — полностью, как обычно. Тексты файлов я попрошу
следом, по одному."""

FILE_SYSTEM = """Ты пишешь один файл проекта лабораторной работы по готовому плану. Ответ — только JSON:
{"path": "…", "content": "полный текст файла"}. Код из листингов отчёта, которые берутся из этого файла, входит в него
дословно; с уже написанными файлами проекта файл должен собираться вместе (те же имена классов, полей, функций).
Правила те же: русские надписи, без служебных пометок и отладочного вывода, без работы с файлами вне папки
программы и процессами; в интернет — только если этого требует задание."""

SPLIT_RULES = """Если в одном проекте оказалось несколько программ (ошибки «несколько точек входа», CS0017, multiple
definition of main, main redeclared, duplicate class) или приложению нужна отдельная библиотека классов, раздели
проект на программы: верни "programs" — все программы плана, например [{"name": "ClassLibrary1", "kind": "library"},
{"name": "CalculatorApp", "kind": "console", "entry": "CalculatorApp/Program.cs", "uses": ["ClassLibrary1"]}]. Файлы
каждой программы лежат в папке с её именем: перенесённый файл верни в files с новым путём, а старый путь назови в
"remove". В "shots" скажи, какую программу запускает каждый снимок: [{"section": 1, "block": 3,
"program": "CalculatorApp"}]. Код листингов при переносе не меняй. library и uses — только C#: файлы .csproj со
ссылками и общий .sln ForLabs напишет сам."""

FIX_SYSTEM = """Ты исправляешь проект лабораторной работы: сборка не прошла. Верни только JSON с изменёнными файлами
целиком: {"files": [{"path": "…", "content": "…"}], "listings": [{"section": 1, "block": 2, "file": "…",
"code": "новый текст листинга"}], "remove": [], "programs": [], "shots": []}. Исправляй причину ошибки, меняй как можно
меньше, тем же стилем. Если изменился код, который входит в листинг отчёта (список ниже), верни этот листинг в
listings — так, как он теперь стоит в файле. Файлы сборки (csproj, sln, go.mod, pom.xml, package.json, CMakeLists.txt)
делает ForLabs — не пиши и не меняй их; "remove" — пути файлов проекта, которые нужно убрать. Правила те же: русские
надписи, без служебных пометок, без работы с файлами вне папки программы и процессами; в интернет — только если этого
требует задание.
""" + SPLIT_RULES

SHOT_FIX_SYSTEM = """Ты чинишь проект лабораторной работы. ForLabs собрал программу, запустил её в Windows и
прошёл сценарий снимка, но снимок не получился. Ниже по каждому рисунку — сценарий, что именно не вышло и что на
самом деле было в окне программы (элементы с именами и текстом, пункты списков) или на экране консоли. Найди причину
и исправь её:
- ошибка в программе: список без пунктов, к кнопке не привязан обработчик, элементу не задано имя (Name), вторая
  форма ниоткуда не открывается, программа падает или считает не то — исправь файлы;
- ошибка в сценарии: не то имя элемента или пункта, не открыта вторая форма, лишний или недостающий ввод, expect не
  совпадает с тем, что программа на самом деле выводит, — исправь снимок.
Меняй как можно меньше и тем же стилем. Код из листингов отчёта трогай, только если ошибка в нём самом, и тогда
верни этот листинг в listings — так, как он теперь стоит в файле. Вид окна и надписи, которых ошибка не касается, не
меняй. expect не подгоняй под неверный результат: если программа считает неправильно, чини программу.
Ответ — только JSON: {"files": [{"path": "…", "content": "файл целиком"}], "listings": [{"section": 1, "block": 2,
"file": "…", "code": "…"}], "shots": [{"section": 1, "block": 3, "mode": "window", "steps": […], "expect": "…"}]} —
только изменённые файлы и только исправленные снимки (снимок — целиком, со всеми шагами). Файлы сборки (csproj,
go.mod, pom.xml, package.json, CMakeLists.txt) делает ForLabs — не пиши их. Правила те же: русские надписи, без
служебных пометок, без работы с файлами вне папки программы и процессами; в интернет — только если этого требует
задание. Когда программ в проекте несколько, у снимка есть "program" — какую из них ForLabs запускает: запущена не
та — исправь "program".

Как ForLabs проходит сценарий:
""" + SCENARIO_RULES

REJECTED = "\n\nПрошлое исправление не принято: "
REPAIR_SYSTEM = """Ты чинишь проект лабораторной работы по просьбе студента. Ниже — что нужно исправить, ошибки
последней сборки (если она не прошла) и все файлы проекта. Найди причину и исправь её в файлах; меняй как можно
меньше и тем же стилем, то, о чём не просили, не трогай. Если изменился код, который входит в листинг отчёта (список
ниже), верни этот листинг в listings — так, как он теперь стоит в файле. Ответ — только JSON: {"files": [{"path": "…",
"content": "файл целиком"}], "listings": [{"section": 1, "block": 2, "file": "…", "code": "…"}], "remove": [],
"programs": [], "shots": []} — только изменённые файлы. Файлы сборки (csproj, sln, go.mod, pom.xml, package.json,
CMakeLists.txt) делает ForLabs — не пиши их; "remove" — пути файлов проекта, которые нужно убрать. Правила те же:
русские надписи, без служебных пометок, без работы с файлами вне папки программы и процессами; в интернет — только
если этого требует задание.
""" + SPLIT_RULES + """
Если из-за исправления меняется то, что видно на снимке, верни в shots исправленный сценарий этого снимка целиком.
Как ForLabs проходит сценарий:
""" + SCENARIO_RULES

MAX_SHOT_FIXES = 2  # кругов «снимок не вышел → модель чинит программу или сценарий → снимаю заново»
SEEN_LIMIT = 2500  # экран консоли или окно программы в запросе на исправление
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
    remove: list[str] = Field(default_factory=list)  # пути файлов, которых в проекте больше нет (перенос, слияние)
    programs: list[ProgramPlan] = Field(default_factory=list)  # проект разделён на программы: все программы плана
    entry: str = ""  # новый главный файл (если прежний переименован)
    shots: list[ShotPlan] = Field(default_factory=list)

    @field_validator("files", "listings", "remove", "programs", "shots", mode="before")
    @classmethod
    def _lists(cls, v):
        return v if isinstance(v, list) else []

    @field_validator("entry", mode="before")
    @classmethod
    def _entry(cls, v):
        return plan_rules.norm_path(v) if isinstance(v, str) else ""


ShotFixOut = FixOut  # прежнее имя: ответ на исправление после снимка


@dataclasses.dataclass
class ShotFailure:
    """Рисунок, который ForLabs не снял из-за сценария: что делал, что не вышло, что было на экране."""
    section: int
    block: int
    topic: str
    slot: str
    shot: ShotPlan
    reason: str
    seen: str = ""
    ui: str = ""


def _blocks(st: LabState):
    for sec in st.report.sections if st.report else []:
        for bi, b in enumerate(sec.blocks, start=1):
            yield sec.n, bi, b


def report_sha(st: LabState) -> str:
    """Отпечаток того, по чему строится проект: код листингов и рисунки отчёта с их местами. План проекта ссылается
    на блоки по номерам, поэтому после правки кода или перестановки блоков он уже не от этого отчёта."""
    import hashlib

    parts = []
    for n, bi, b in _blocks(st):
        if isinstance(b, Listing):
            parts.append(f"L{n}.{bi}:{_squash(b.code)}")
        elif isinstance(b, Figure):
            parts.append(f"F{n}.{bi}:{b.topic}|{b.slot or ''}")
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


def stale(st: LabState, plan: ProjectPlan | None = None) -> bool:
    """Проект собран по другому отчёту: код или рисунки с тех пор меняли (в чате правок). У лабы, сделанной до
    появления отпечатка, сверяется сам план: код листинга отчёта должен стоять в файле, на который план ссылается."""
    if st.project is None:
        return False
    if st.project.report_sha:
        return st.project.report_sha != report_sha(st)
    if plan is None:
        return False
    blocks = {(n, bi): b for n, bi, b in _blocks(st)}
    for r in plan.listings:
        b, f = blocks.get((r.section, r.block)), plan.file(r.file)
        if not isinstance(b, Listing) or f is None or _squash(b.code) not in _squash(f.content):
            return True
    return False


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
    plan = plan.model_copy(update={"name": name, "files": files})
    return _assign_programs(plan_rules.split_programs(plan))


def _assign_programs(plan: ProjectPlan) -> ProjectPlan:
    """Какую программу запускает снимок, если модель этого не сказала (или назвала библиотеку): ту, чей листинг
    стоит в отчёте над рисунком, затем любую из того же задания, иначе первую запускаемую."""
    if not plan.programs:
        return plan
    main = plan.main_program()
    refs: dict[int, list[tuple[int, str]]] = {}
    for r in plan.listings:
        prog = plan.program(r.file.partition("/")[0])
        if prog is not None and prog.runs:
            refs.setdefault(r.section, []).append((r.block, prog.name))
    shots = []
    for sh in plan.shots:
        named = plan.program(sh.program) if sh.program.strip() else None
        if named is not None and named.runs:
            shots.append(sh if sh.program == named.name else sh.model_copy(update={"program": named.name}))
            continue
        near = sorted(refs.get(sh.section, []))
        above = [name for block, name in near if block < sh.block]
        pick = above[-1] if above else near[0][1] if near else (main.name if main else "")
        shots.append(sh.model_copy(update={"program": pick}))
    return plan.model_copy(update={"shots": shots})


def programs_text(plan: ProjectPlan) -> str:
    """Программы плана одной строкой — для запросов к модели."""
    if not plan.programs:
        return f"Программа одна (programs нет), главный файл: {plan.entry}."
    parts = []
    for prog in plan.programs:
        bits = [prog.kind] + ([f"главный файл {prog.entry}"] if prog.runs else [])
        bits += [f"подключает {', '.join(prog.uses)}"] if prog.uses else []
        parts.append(f"{prog.name} ({'; '.join(bits)})")
    return "Программы (у каждой своя папка): " + ", ".join(parts) + "."


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
    manifest = (f"План проекта: язык {head.language}, вид {head.kind}. {programs_text(head)}\n"
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
    if reads is None:
        return plan
    ext = plan_rules.CODE_EXT[plan.language]

    def reads_keyboard(sh: ShotPlan) -> bool:
        prog = plan.program_of(sh)
        files = plan.files
        if prog is not None:  # своя программа и библиотеки, которые она подключает
            files = [f for p in (prog, *(plan.program(u) for u in prog.uses)) if p is not None for f in plan.files_of(p)]
        return bool(re.search(reads, "\n".join(f.content for f in files if Path(f.path).suffix.lower() in ext)))

    shots = [sh.model_copy(update={"input": "", "steps": [x for x in sh.steps if x.action not in ("type", "key")]})
             if sh.mode == "console" and not reads_keyboard(sh) else sh for sh in plan.shots]
    return plan.model_copy(update={"shots": shots})


def plan_project(st: LabState, llm, memory: str = "", wish: str = "") -> ProjectPlan:
    user = lab_text(st) + (f"\n\nПамять предмета и требования:\n{memory[:6000]}" if memory.strip() else "")
    if wish.strip():  # просьба из чата правок: что в прежнем проекте было не так
        user += f"\n\nСтудент просит о программе: {wish.strip()}"
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
    shots = "; ".join(f"[{x.section}.{x.block}]" + (f" запускает {x.program}" if x.program else "")
                      for x in plan.shots if x.mode in AUTO_MODES) or "нет"
    user = (f"Язык: {plan.language}, вид: {plan.kind}. {programs_text(plan)}\n\nОшибки сборки:\n{log[-6000:]}"
            f"\n\nЛистинги отчёта: {refs}.\nСнимки, которые ForLabs делает сам: {shots}.\n\nФайлы проекта:\n{files}")
    out = llm.json([{"role": "system", "content": FIX_SYSTEM}, {"role": "user", "content": user}], FixOut,
                   max_tokens=8000)
    return _apply_fix(plan, out, scenarios=False)


AUTO_MODES = ("window", "console", "browser")  # снимки, которые ForLabs проходит по сценарию сам


def _apply_fix(plan: ProjectPlan, out: FixOut, scenarios: bool) -> ProjectPlan:
    """Ответ модели на исправление — в план: изменённые и убранные файлы, листинги, деление на программы и то, какую
    программу запускает снимок. scenarios — можно менять и сами сценарии снимков (исправление после снимка)."""
    by_path = {f.path: f for f in plan.files}
    gone = {plan_rules.norm_path(x).lower() for x in out.remove}
    moved: dict[str, list[str]] = {}  # имя убранного файла → куда он переехал
    for f in out.files:
        if plan_rules._build_file_problem(f) is None:  # go.mod, csproj и т.п. всё равно пишет ForLabs
            by_path[f.path] = f
            moved.setdefault(Path(f.path).name.lower(), []).append(f.path)
    new_paths = {f.path.lower() for f in out.files}
    for path in [x for x in by_path if x.lower() in gone and x.lower() not in new_paths]:
        del by_path[path]
    refs = {(r.section, r.block): r for r in plan.listings}
    for r in out.listings:
        refs[(r.section, r.block)] = r
    listings = []
    for r in refs.values():
        if r.file not in by_path:  # файл листинга переехал в папку программы: листинг — по новому пути
            new = moved.get(Path(r.file).name.lower(), [])
            if len(new) != 1:
                continue
            r = r.model_copy(update={"file": new[0]})
        listings.append(r)
    given = {(x.section, x.block): x for x in out.shots}
    shots = []
    for x in plan.shots:
        new = given.get((x.section, x.block))
        if new is None or x.mode not in AUTO_MODES:
            shots.append(x)
        elif scenarios and new.mode in AUTO_MODES:  # сценарий целиком; «сними сам» — не починка
            shots.append(new if new.program.strip() else new.model_copy(update={"program": x.program}))
        else:
            shots.append(x.model_copy(update={"program": new.program}) if new.program.strip() else x)
    data = plan.model_dump() | {"files": [f.model_dump() for f in by_path.values()],
                                "listings": [r.model_dump() for r in listings],
                                "shots": [x.model_dump() for x in shots]}
    if out.programs:
        data["programs"] = [x.model_dump() for x in out.programs]
    if out.entry and not data.get("programs"):
        data["entry"] = out.entry
    try:
        return _assign_programs(ProjectPlan.model_validate(data))
    except ValueError:
        return plan  # ответ не складывается в план: остаётся прежний, причину покажет сборка


def repair_project(plan: ProjectPlan, wish: str, log: str, llm, st: LabState | None = None) -> ProjectPlan:
    """Просьба из чата правок («исправь ошибки сборки», «разнеси по проектам»): модель видит просьбу, ошибки
    последней сборки и все файлы и возвращает исправление. Собирает и проверяет его уже конвейер."""
    files = "\n\n".join(f"Файл {f.path}:\n<<<\n{f.content}\n>>>" for f in plan.files
                        if Path(f.path).suffix.lower() not in DATA_EXT)
    refs = "; ".join(f"[{r.section}.{r.block}] из {r.file}" for r in plan.listings) or "нет"
    shots = "\n".join(f"[{x.section}.{x.block}] " + x.model_dump_json(
        exclude={"section", "block", "reason", "app", "goal", "open"}, exclude_defaults=True)
        for x in plan.shots if x.mode in AUTO_MODES) or "нет"
    errors = f"Ошибки последней сборки:\n{log[-6000:]}" if log.strip() else "Последняя сборка прошла без ошибок."
    user = (f"Что исправить: {wish.strip()}\n\nЯзык: {plan.language}, вид: {plan.kind}. {programs_text(plan)}\n\n"
            f"{errors}\n\nЛистинги отчёта: {refs}.\nСнимки, которые ForLabs делает сам:\n{shots}\n\n"
            f"Файлы проекта:\n{files}")
    out = llm.json([{"role": "system", "content": REPAIR_SYSTEM}, {"role": "user", "content": user}], FixOut,
                   max_tokens=PLAN_MAX_TOKENS)
    fixed = _apply_fix(plan, out, scenarios=True)
    return _quiet_console(_trusted_expects(_tidy(fixed), st)) if st is not None else fixed


def first_error(log: str, limit: int = 300) -> str:
    """Главная строка лога сборки — та, что с ошибкой (иначе первая непустая): её чат показывает студенту."""
    lines = [ln.strip() for ln in (log or "").splitlines() if ln.strip()]
    hit = next((ln for ln in lines if re.search(r"\berror\b|Error\b|ошибк", ln, re.I)), lines[0] if lines else "")
    return hit if len(hit) <= limit else hit[:limit].rstrip() + "…"


def _with_materials(plan: ProjectPlan, st: LabState) -> ProjectPlan:
    """Файлы данных от преподавателя — рядом с программой (модель их не переписывает)."""
    have = {f.path.lower() for f in plan.files}
    folders = [f"{p.name}/" for p in plan.programs if p.runs] or [""]  # у каждой программы своя рабочая папка
    extra = [ProjectFile(path=d + m.name, content=m.text) for m in st.materials for d in folders
             if Path(m.name).suffix.lower() in DATA_EXT and (d + m.name).lower() not in have and "/" not in m.name]
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
    # первую или последнюю строку листинга в файле заменили (исправление после снимка): она тоже часть листинга
    lead = sum(1 for x in a[:solid[0].a] if x)
    trail = sum(1 for x in a[solid[-1].a + solid[-1].size:] if x)
    while 0 < lead <= 3 and start > 0:
        start -= 1
        lead -= 1 if lines[start].strip() else 0
    while 0 < trail <= 3 and end < len(lines):
        trail -= 1 if lines[end].strip() else 0
        end += 1
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


def sandbox_has_internet(pipe) -> bool:
    """Снимки идут в песочнице AppContainer (Windows): там программе можно дать интернет без доступа к файлам."""
    shooter = getattr(pipe, "shooter", None)
    if shooter is not None:
        return bool(getattr(shooter, "container", False))
    return sys.platform == "win32"


def _blocked(pipe, plan: ProjectPlan) -> tuple[list[str], bool]:
    """(почему не запускать, нужен ли программе интернет)."""
    found = safety.scan(plan)
    network = any(safety.NET in r for r in found)
    if network and sandbox_has_internet(pipe):
        found = [r for r in found if safety.NET not in r]
    return found, network


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
            remove_tree(root)
            root = run_dir(Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "ForLabs" / "runs")
        winapi.low_label_dir(str(root))  # писать сервер с пониженными правами может только сюда
    else:
        root = Path(tempfile.mkdtemp(prefix="forlabs-mysql-"))
    try:
        server = mysqlrun.start(client, root, low=win)
    except BaseException:
        remove_tree(root, tries=3)
        raise
    server.root = root
    return server


def fix_shots(plan: ProjectPlan, failures: list[ShotFailure], llm, st: LabState | None = None) -> ProjectPlan:
    """Снимки не получились из-за сценария: модель видит, что было в окне, и возвращает исправленные файлы и шаги."""
    def one(f: ShotFailure) -> str:
        shot = f.shot.model_dump_json(exclude={"section", "block", "reason", "app", "goal", "open"},
                                      exclude_defaults=True)
        parts = [f"[{f.section}.{f.block}] {f.topic}. Что в кадре: {f.slot or '—'}", f"Сценарий: {shot}",
                 f"Не вышло: {f.reason}"]
        if f.ui.strip():
            parts.append(f"В окнах программы было:\n<<<\n{f.ui.strip()[:SEEN_LIMIT]}\n>>>")
        elif f.seen.strip():
            parts.append(f"На экране было:\n<<<\n{f.seen.strip()[-SEEN_LIMIT:]}\n>>>")
        return "\n".join(parts)

    files = "\n\n".join(f"Файл {f.path}:\n<<<\n{f.content}\n>>>" for f in plan.files
                        if Path(f.path).suffix.lower() not in DATA_EXT)
    refs = "; ".join(f"[{r.section}.{r.block}] из {r.file}" for r in plan.listings) or "нет"
    user = (f"Язык: {plan.language}, вид: {plan.kind}. {programs_text(plan)}\n\n"
            "Рисунки, которые не получилось снять:\n\n" + "\n\n".join(one(f) for f in failures)
            + f"\n\nЛистинги отчёта: {refs}.\n\nФайлы проекта:\n{files}")
    out = llm.json([{"role": "system", "content": SHOT_FIX_SYSTEM}, {"role": "user", "content": user}], ShotFixOut,
                   max_tokens=PLAN_MAX_TOKENS)
    # сценарий меняется только у рисунков, которые ForLabs снимает сам, и остаётся таким: «сними сам» — не починка
    fixed = _apply_fix(plan, out, scenarios=True)
    return _quiet_console(_trusted_expects(_tidy(fixed), st)) if st is not None else fixed


def build_and_shoot(pipe, st: LabState, plan: ProjectPlan, llm=None, emit=None, retake: bool = False) -> LabState:
    """Сборка (с исправлениями, если есть модель) → листинги → копия в папку сдачи → снимки. Снимок не прошёл из-за
    сценария — модель чинит программу или шаги, и всё повторяется (до MAX_SHOT_FIXES кругов). Меняет st."""

    emit = emit or (lambda text, kind="info": None)
    lab = pipe.lab_dir(st)
    pdir = lab / "project"
    pdir.mkdir(parents=True, exist_ok=True)
    # план мог быть составлен до того, как ForLabs научился нескольким программам: папки со своей точкой входа
    # становятся программами — иначе такая лаба не соберётся никогда
    plan = _with_materials(_tidy(plan), st)
    info = ProjectInfo(name=plan.name, language=plan.language, kind=plan.kind)
    tools = languages.detect()
    info.missing = languages.missing(plan, tools)
    work = pdir / "src"
    shooter = pipe.shooter
    if shooter is None and sys.platform == "win32":
        from forlabs.runner.shots import WindowsShooter

        shooter = WindowsShooter()

    def build(candidate: ProjectPlan, fix_builds: bool):
        """Проект в папку work и сборка. → (план после исправлений сборки, итог сборки, почему нельзя запускать,
        нужна ли сеть). Опасный код не собирается вовсе: сборка и проверка синтаксиса — тоже запуск чужих
        инструментов над ним. Сеть — не опасность, если программа работает в песочнице."""
        shutil.rmtree(work, ignore_errors=True)
        languages.materialize(candidate, work, tools)
        blocked, net = _blocked(pipe, candidate)
        res = None
        if not info.missing and not blocked:
            emit("Собираю программу")
            res = languages.build(work, candidate, tools)
            while not res.ok and fix_builds and llm is not None and info.fixes < MAX_FIXES:
                info.fixes += 1
                emit(f"Исправляю ошибки сборки ({info.fixes} из {MAX_FIXES})")
                fixed = _with_materials(fix_project(candidate, res.log, llm), st)
                if problems := validate(fixed):  # исправление не годится: в следующем круге модель узнает почему
                    res = dataclasses.replace(res, log=res.log.split(REJECTED)[0].rstrip() + REJECTED
                                              + "; ".join(problems[:5]))
                    continue
                candidate = fixed
                shutil.rmtree(work, ignore_errors=True)
                languages.materialize(candidate, work, tools)
                blocked, net = _blocked(pipe, candidate)  # исправление тоже проверяется до сборки
                if blocked:
                    break
                res = languages.build(work, candidate, tools)
        return candidate, res, blocked, net

    def settle() -> Path:
        """Листинги отчёта — как в коде проекта; копия проекта в папку сдачи (до снимков: на снимках пути из неё,
        как при запуске самим студентом)."""
        (pdir / "plan.json").write_text(plan.model_dump_json(indent=1), encoding="utf-8")
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
        folder = work
        try:
            folder, locked = _copy_to_hand_in(pipe, st, plan, work)
            info.folder = str(folder)
            if locked:
                emit("Не обновил файлы проекта, открытые в другой программе: " + ", ".join(locked[:5])
                     + ". Закрой их и нажми «Пересобрать».", "warn")
        except OSError as e:
            emit(f"Проект не скопировался в папку сдачи ({e})", "warn")
        return folder

    mine: set[tuple[int, int]] = set()  # рисунки, снятые в этом запуске (после правки программы снимаются заново)
    kept: dict[tuple[int, int], str] = {}  # их прежние снимки: вернутся, если пересъёмка не выйдет

    def shoot_all(hand_in: Path) -> list[ShotFailure]:
        shots = {(s.section, s.block): s for s in plan.shots}
        failures: list[ShotFailure] = []
        specs: dict[str, object] = {}  # чем запускать каждую программу плана
        db, db_error = None, None  # свой сервер MySQL: поднимается к первому снимку, гасится после последнего
        try:
            for n, bi, b in list(_blocks(st)):
                if not isinstance(b, Figure) or b.image_path:
                    continue
                b.need_tool = None
                shot = shots.get((n, bi))
                if shot is not None and shot.mode == "app":  # окно среды разработки и т.п. — агент, по кнопке
                    from forlabs.agent import apps as agent_apps
                    from forlabs.core.models import AppTask
                    from forlabs.pipeline import appshots

                    project = hand_in.name if hand_in != work else ""
                    files = list(shot.open)
                    if shot.app == "visualstudio":  # файл проекта создаёт ForLabs — модель его не знает
                        files = [f.name for f in sorted(hand_in.glob("*"))
                                 if f.suffix in (".sln", ".slnx", ".csproj", ".vcxproj")][:1] + files
                    opens = [f"{project}/{f}" if project else f for f in files]
                    b.app_task = AppTask(app=shot.app, goal=shot.goal, shot=shot.expect, open=opens)
                    b.auto_note = appshots.note_for(b, agent_apps.installed(getattr(pipe, "app_paths", {}))
                                                    if sys.platform == "win32" else {})
                    continue
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
                prog = plan.program_of(shot)
                which = prog.name if prog is not None else ""
                if which not in specs:
                    specs[which] = languages.run_spec_for(plan, result, work, tools, which)
                spec = specs[which]
                if plan.language == "mysql":
                    if db is None and db_error is None:
                        emit("Запускаю отдельный сервер MySQL для снимков")
                        try:
                            db = start_mysql(tools["mysql"].path)
                            spec = specs[which] = dataclasses.replace(spec, cmd=db.client())
                        except Exception as e:  # noqa: BLE001 — сервер не поднялся: снимки студенту, отчёт собирается
                            db_error = str(e).rstrip(".") or "сервер MySQL не запустился"
                    if db_error:
                        b.auto_note = f"Сам не снял: {db_error}."
                        continue
                static = plan.language == "web"  # страница без сервера — из папки сдачи (адрес как у студента)
                try:
                    target, shot = _target_and_shot(plan, spec, shot, work, hand_in, static)
                    target.network = network
                    target.loopback = plan.language == "mysql"  # консоль mysql идёт на свой сервер по localhost
                except ValueError as e:
                    b.auto_note = f"Сам не снял: {e}."
                    continue
                emit(f"Снимаю рисунок: {b.topic}")
                rel = f"assets/shot_{n}_{bi}_{uuid.uuid4().hex[:6]}.png"
                res = shooter.shoot(target, shot, lab / rel)
                if res.ok:
                    b.image_path, b.auto_note = rel, None
                    mine.add((n, bi))
                    old = kept.pop((n, bi), None)
                    if old and old != rel:  # снимок прежней программы больше не нужен
                        (lab / old).unlink(missing_ok=True)
                    pipe._sync(st, [rel])
                elif (n, bi) in kept:  # программа изменилась, а переснять не вышло: остаётся прежний снимок
                    b.image_path, b.auto_note = kept.pop((n, bi)), None
                else:
                    b.auto_note = f"Сам не снял: {res.reason.rstrip('.')}."
                    if getattr(res, "kind", ""):
                        failures.append(ShotFailure(n, bi, b.topic, b.slot or "", shots[(n, bi)], res.reason,
                                                    res.seen, getattr(res, "ui", "")))
        finally:
            if db is not None:
                db.stop()
        return failures

    def restore_kept() -> None:
        for n, bi, b in _blocks(st):
            if isinstance(b, Figure) and not b.image_path and (n, bi) in kept:
                b.image_path, b.auto_note = kept.pop((n, bi)), None

    plan, result, info.blocked, network = build(plan, fix_builds=True)
    if retake:  # «Пересобрать» или просьба в чате: снимки, сделанные ForLabs, — заново; не выйдет — останется прежний
        auto = {(x.section, x.block) for x in plan.shots if x.mode in ("window", "console", "browser")}
        for n, bi, b in _blocks(st):
            if (isinstance(b, Figure) and b.image_path and (n, bi) in auto
                    and Path(b.image_path).name.startswith("shot_")):  # вставленное студентом не трогаем
                kept[(n, bi)] = b.image_path
                b.image_path = None
    while True:
        info.built = bool(result is not None and result.ok) and not info.blocked
        info.programs = [x.name for x in plan.programs]
        info.build_log = ""
        if result is not None and not result.ok:
            info.note = "Программу не собрал: ошибки остались. Проект в папке сдачи, снимки сделай сам."
            info.build_log = result.log.split(REJECTED)[0].strip()[:BUILD_LOG_LIMIT]
        if info.blocked:
            info.note = f"Сам не собирал и не запускал: {info.blocked[0]}"
        try:
            failures = shoot_all(settle())
        finally:
            restore_kept()
        if not failures or llm is None or info.shot_fixes >= MAX_SHOT_FIXES:
            break
        info.shot_fixes += 1
        emit(f"Снимок не получился ({failures[0].reason.rstrip('.')}) — исправляю ({info.shot_fixes} из "
             f"{MAX_SHOT_FIXES})")
        try:
            fixed = _with_materials(fix_shots(plan, failures, llm, st), st)
        except Exception as e:  # noqa: BLE001 — нет связи, лимит, кривой ответ: рисунок остаётся студенту с причиной
            emit(f"Исправить не получилось ({getattr(e, 'message_ru', None) or e})", "warn")
            break
        # исправление проверяется до любых действий: опасным может быть и код, и ввод в сценарии (команды в консоли)
        if fixed == plan or validate(fixed) or _blocked(pipe, fixed)[0]:
            break
        if fixed.files == plan.files:  # поменялся только сценарий
            plan = fixed
            continue
        # программа меняется: исправленная должна собраться; если нет — возвращается прежняя, рабочая
        try:
            cand, cand_result, cand_blocked, cand_network = build(fixed, fix_builds=False)
            accepted = not cand_blocked and cand_result is not None and cand_result.ok
        except Exception as e:  # noqa: BLE001 — сборка упала не ошибкой кода (диск, инструмент): проект не теряется
            emit(f"Исправление не собралось ({e})", "warn")
            accepted = False
        if not accepted:
            emit("Исправление не собралось — оставляю прежнюю программу", "warn")
            try:
                plan, result, info.blocked, network = build(plan, fix_builds=False)
                info.built = bool(result is not None and result.ok) and not info.blocked
                settle()
            except Exception as e:  # noqa: BLE001
                emit(f"Прежняя программа не пересобралась ({e}) — нажми «Пересобрать»", "warn")
            break
        plan, result, network = cand, cand_result, cand_network
        auto = {(x.section, x.block) for x in plan.shots if x.mode in ("window", "console", "browser")}
        for n, bi, b in _blocks(st):  # снятое ForLabs (и в прошлых запусках) — заново, уже с исправленной программой
            if (isinstance(b, Figure) and b.image_path and (n, bi) in auto
                    and ((n, bi) in mine or Path(b.image_path).name.startswith("shot_"))):
                kept[(n, bi)] = b.image_path
                b.image_path = None
    try:
        pipe._sync(st, ["project/plan.json"])
    except Exception:
        pass
    info.report_sha = report_sha(st)
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
