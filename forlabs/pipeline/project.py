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
  На снимок попадает только то, что помещается на экране (первые ~700 точек страницы). Нужное ниже — шаг
  {"action":"scroll","target":"id:slider"} (прокрутить к элементу) или {"action":"scroll","value":"600"}.
  Эффект при наведении (:hover, выпадающее меню, смена картинки) — шаг {"action":"hover","target":"…"}: без
  него на снимке будет обычный вид. target — id:имя, CSS-селектор (".btn-fill", "nav li:nth-child(2)") или
  текст элемента. Один снимок — одно состояние: два наведения сразу снять нельзя. Анимация, которая идёт сама
  (animation, @keyframes), наведения не требует. Щелчок по ссылке на страницу, которой нет, не делай.
- MySQL: ForLabs запускает консольный клиент mysql в папке проекта на своём отдельном сервере. Перед каждым
  снимком сервер чистый: баз лабы на нём нет. Результат запросов — mode "console"; input — сначала source скриптов,
  которые создают и заполняют базу («source shop.sql\\n»), затем сам запрос рисунка текстом, как его набирает
  студент («USE shop;\\nSELECT name FROM users LIMIT 5;\\n»): запрос должен быть виден на снимке вместе с
  результатом; expect — значение из результата. Все скрипты ForLabs заранее выполняет по порядку на чистом
  сервере — они должны проходить без ошибок. Не начинай скрипты с DROP DATABASE и DROP USER: у студента на
  сервере может быть своя база с таким именем; пиши CREATE DATABASE IF NOT EXISTS и сразу USE.
- mode "app" + app + goal (+ open) — окно другой программы, которое ForLabs снимет сам, работая в ней: среда
  разработки с кодом или обозревателем решений, конструктор формы, SSMS, Workbench, Блокнот. app — ключ: {apps};
  goal — что сделать (например «открыть Program.cs в редакторе, чтобы был виден метод Main»); open — файлы
  проекта, которые открыть при запуске (для Visual Studio — файл .csproj или .sln и нужный .cs); expect — что
  должно быть видно на снимке.
  Программы из рисунка нет в этом списке (Eclipse, Unity, Selenium IDE…) — не подменяй её похожей: mode "manual"
  с причиной «в программе … ForLabs работать не умеет».
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
каждой программы лежат в папке с её именем: каждый файл перенеси в папку своей программы через "move":
[{"from": "Calculator.cs", "to": "ClassLibrary1/Calculator.cs"}] — текст файла при этом повторять не нужно (если он
ещё и меняется, верни его в files уже с новым путём). Перенести нужно все файлы проекта: вне папок программ файлов
не остаётся. В "shots" скажи, какую программу запускает каждый снимок: [{"section": 1, "block": 3,
"program": "CalculatorApp"}] — сценарий снимка при этом не повторяй. Код листингов при переносе не меняй. library и
uses — только C#: файлы .csproj со ссылками и общий .sln ForLabs напишет сам."""

FIX_SYSTEM = """Ты исправляешь проект лабораторной работы: сборка не прошла. Верни только JSON с изменёнными файлами
целиком: {"files": [{"path": "…", "content": "…"}], "listings": [{"section": 1, "block": 2, "file": "…",
"code": "новый текст листинга"}], "remove": [], "move": [], "programs": [], "shots": []}. Исправляй причину ошибки, меняй как можно
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

REVIEW_SYSTEM = """Ты проверяешь, правильно ли работает программа лабораторной работы. ForLabs собрал её, запустил и
прошёл сценарий каждого снимка. Ниже — задания, что сказано в отчёте рядом с рисунком, код программы и то, что
программа на самом деле показала: экран консоли или текст окна. Для каждого рисунка реши, годится ли такой результат
в отчёт.
Не годится, если:
- вместо данных пусто или нули: пустые названия, «: 0», 0,00 там, где данные заданы в коде или введены;
- результат противоречит вводу, коду или тексту отчёта (в отчёте сказано одно, на экране другое);
- на экране ошибка, исключение, трассировка, «NaN», «Infinity», «null», «undefined», необработанный шаблон («{0}»,
  «System.String[]», «[object Object]»);
- программа завершилась, не дойдя до результата, или показано не то задание;
- русский текст превратился в «????» или в коды вида «\\u041D».
Годится, если результат осмысленный и следует из кода и ввода. Мелочи оформления — лишний пробел, другой порядок
строк, запятая или точка в дробном числе — не ошибка. Сам пересчитывай только простое; не уверен — считай результат
правильным.
Ответ — только JSON: {"shots": [{"section": 1, "block": 3, "ok": true}, {"section": 2, "block": 2, "ok": false,
"problem": "что именно не так, одной фразой по-русски"}]} — по записи на каждый рисунок."""
REVIEW_FILES_LIMIT = 24_000  # знаков кода в запросе на проверку результата

REJECTED = "\n\nПрошлое исправление не принято: "
REPAIR_SYSTEM = """Ты чинишь проект лабораторной работы по просьбе студента. Ниже — что нужно исправить, ошибки
последней сборки (если она не прошла) и все файлы проекта. Найди причину и исправь её в файлах; меняй как можно
меньше и тем же стилем, то, о чём не просили, не трогай. Если изменился код, который входит в листинг отчёта (список
ниже), верни этот листинг в listings — так, как он теперь стоит в файле. Ответ — только JSON: {"files": [{"path": "…",
"content": "файл целиком"}], "listings": [{"section": 1, "block": 2, "file": "…", "code": "…"}], "remove": [],
"move": [], "programs": [], "shots": []} — только изменённые файлы. Файлы сборки (csproj, sln, go.mod, pom.xml, package.json,
CMakeLists.txt) делает ForLabs — не пиши их; "remove" — пути файлов проекта, которые нужно убрать. Правила те же:
русские надписи, без служебных пометок, без работы с файлами вне папки программы и процессами; в интернет — только
если этого требует задание.
""" + SPLIT_RULES + """
Если из-за исправления меняется то, что видно на снимке, верни в shots исправленный сценарий этого снимка целиком.
Как ForLabs проходит сценарий:
""" + SCENARIO_RULES

SHOTS_SYSTEM = """Ты дописываешь сценарии снимков к готовому проекту лабораторной работы. Программа уже написана (файлы
ниже), но для перечисленных рисунков сценария нет. Составь его для каждого: ForLabs запустит программу в Windows
студента, пройдёт сценарий и проверит, что на экране есть expect. Код не меняй. Ответ — только JSON:
{"shots": [{"section": 2, "block": 4, "mode": "console", "program": "…", "input": "…", "expect": "…"}]} — по записи на
каждый рисунок из списка, section и block — его номера из квадратных скобок.
""" + SHOT_RULES

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


class FileMove(BaseModel):
    """Перенос файла проекта в папку программы: текст остаётся прежним."""
    model_config = {"populate_by_name": True}
    src: str = Field(alias="from")
    to: str

    @field_validator("src", "to", mode="before")
    @classmethod
    def _path(cls, v):
        return plan_rules.norm_path(v)


class FixOut(BaseModel):
    files: list[ProjectFile] = Field(default_factory=list)
    listings: list[ListingRef] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)  # пути файлов, которых в проекте больше нет
    move: list[FileMove] = Field(default_factory=list)  # переносы файлов без изменения текста
    programs: list[ProgramPlan] = Field(default_factory=list)  # проект разделён на программы: все программы плана
    entry: str = ""  # новый главный файл (если прежний переименован)
    shots: list[ShotPlan] = Field(default_factory=list)

    @field_validator("files", "listings", "remove", "move", "programs", "shots", mode="before")
    @classmethod
    def _lists(cls, v):
        return v if isinstance(v, list) else []

    @field_validator("move", mode="before")
    @classmethod
    def _moves(cls, v):
        return [x for x in v if isinstance(x, dict) and x.get("from") and x.get("to")] if isinstance(v, list) else []

    @field_validator("entry", mode="before")
    @classmethod
    def _entry(cls, v):
        return plan_rules.norm_path(v) if isinstance(v, str) else ""


ShotFixOut = FixOut  # прежнее имя: ответ на исправление после снимка


class ShotVerdict(BaseModel):
    """Годится ли в отчёт то, что программа показала на снимке."""
    section: int
    block: int
    ok: bool = True
    problem: str = ""

    @field_validator("problem", mode="before")
    @classmethod
    def _problem(cls, v):
        return v if isinstance(v, str) else ""


class ReviewOut(BaseModel):
    shots: list[ShotVerdict] = Field(default_factory=list)

    @field_validator("shots", mode="before")
    @classmethod
    def _shots(cls, v):
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


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


# Код, который живёт только внутри своей среды: ForLabs его не собирает и не запускает. Раньше скрипт Unity
# превращался в проект WinForms — чужую программу в папке сдачи и чужое окно под подписью «режим Play».
ENGINES = {
    "Unity": r"\bUnityEngine\b|\bMonoBehaviour\b|\bScriptableObject\b",
    "Unreal Engine": r"#include\s+\"CoreMinimal\.h\"|\bUCLASS\s*\(|\bGENERATED_BODY\s*\(",
    "Godot": r"\busing\s+Godot\s*;|^\s*extends\s+(?:Node\w*|Control|CharacterBody\dD|Area\dD|Sprite\dD)\b",
    "Android": r"\bimport\s+androidx?\.|\bAppCompatActivity\b|\bsetContentView\s*\(",
    "Arduino": r"\bvoid\s+loop\s*\(\s*\)[\s\S]*\b(?:pinMode|digitalWrite|analogRead|Serial\.)|"
               r"\b(?:pinMode|digitalWrite|analogRead|Serial\.)[\s\S]*\bvoid\s+loop\s*\(\s*\)",
}


def engine_in(listings) -> str | None:
    """Среда, без которой код листингов не собрать (Unity, Android Studio, Arduino…), или None."""
    code = "\n".join(b.code or "" for b in listings)
    return next((name for name, pat in ENGINES.items() if re.search(pat, code, re.M)), None)


def engine_of(st: LabState) -> str | None:
    return engine_in([b for _, _, b in _blocks(st) if isinstance(b, Listing)])


def mark_engine(st: LabState, engine: str) -> bool:
    """Рисункам без снимка — причина, по которой их делает студент. → изменилось ли что-то."""
    changed = False
    for _, _, b in _blocks(st):
        if isinstance(b, Figure) and not b.image_path and not b.auto_note and b.app_task is None:
            b.auto_note = (f"Этот снимок делаешь ты: проект {engine} ForLabs не собирает и не запускает. Код из "
                           f"листингов перенеси в свой проект и сними окно сам.")
            changed = True
    return changed


# Программы, в которых ForLabs работать не умеет: рисунок про них не отдаётся «ближайшей» из списка
FOREIGN_APPS = ("Eclipse", "NetBeans", "Android Studio", "Unity", "Unreal Engine", "Qt Creator", "Sublime Text",
                "WebStorm", "PhpStorm", "CLion", "Rider", "GoLand", "DataGrip", "Figma", "Photoshop", "GIMP",
                "Blender", "AutoCAD", "КОМПАС", "MATLAB", "Wireshark", "VirtualBox", "VMware", "Postman",
                "Selenium IDE", "pgAdmin", "phpMyAdmin", "DBeaver", "HeidiSQL", "OpenServer", "Denwer", "XAMPP",
                "GitHub Desktop", "Jupyter", "Delphi", "Scratch", "Arduino IDE", "Proteus", "Multisim", "GNS3")


def foreign_app(shot: ShotPlan, fig: Figure) -> str | None:
    """Рисунок назван по программе, которой нет среди рабочих для ForLabs, а сценарий отдаёт его другой. →
    название той программы; None — рисунок про выбранную программу или программа в нём не названа."""
    text = f"{fig.topic} {fig.slot or ''}".lower()
    chosen = _AGENT_APPS.get(shot.app)
    if chosen is not None and chosen.title.lower() in text:
        return None
    return next((name for name in FOREIGN_APPS if re.search(rf"(?<!\w){re.escape(name.lower())}(?!\w)", text)), None)


def needs_project(st: LabState) -> bool:
    """Есть ли в отчёте программа, которую ForLabs соберёт сам: листинг на поддерживаемом языке и не код среды,
    которой у ForLabs нет (Unity, Android, Arduino)."""
    listings = [b for _, _, b in _blocks(st) if isinstance(b, Listing)]
    return any((b.language or "").strip().lower() in CODE_LANGS for b in listings) and engine_in(listings) is None


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
    return _assign_programs(_place_loose(plan_rules.split_programs(plan_rules.fix_dead_links(plan))))


def _place_loose(plan: ProjectPlan) -> ProjectPlan:
    """Программ несколько, а файл лежит мимо их папок. Данные нужны каждой программе, которую запускают; исходник
    C# идёт в программу с именем его namespace. Листинги и открываемые файлы — за ними. Остальное покажет проверка."""
    if not plan.programs:
        return plan
    folders = {p.name.lower(): p.name for p in plan.programs}
    runs = [p.name for p in plan.programs if p.runs]
    have = {f.path.lower() for f in plan.files}
    files: list[ProjectFile] = []
    moved: dict[str, str] = {}
    for f in plan.files:
        top, _, rest = f.path.partition("/")
        if rest and top.lower() in folders:
            files.append(f)
            continue
        name, ext = Path(f.path).name, Path(f.path).suffix.lower()
        m = re.search(r"^\s*namespace\s+([\w.]+)", f.content, re.M) if ext == ".cs" else None
        home = folders.get(m[1].split(".")[0].lower()) if m else None
        if ext in DATA_EXT and runs:
            files += [ProjectFile(path=f"{r}/{name}", content=f.content) for r in runs
                      if f"{r}/{name}".lower() not in have]
            moved[f.path.lower()] = f"{runs[0]}/{name}"
        elif home and f"{home}/{name}".lower() not in have:
            files.append(ProjectFile(path=f"{home}/{name}", content=f.content))
            moved[f.path.lower()] = f"{home}/{name}"
        else:
            files.append(f)
    if not moved:
        return plan
    data = plan.model_dump() | {"files": [f.model_dump() for f in files]}
    for ref in data["listings"]:
        ref["file"] = moved.get(ref["file"].lower(), ref["file"])
    for shot in data["shots"]:
        shot["open"] = [moved.get(x.lower(), x) for x in shot["open"]]
    try:
        return ProjectPlan.model_validate(data)
    except ValueError:
        return plan


def _is_scheme(b) -> bool:
    return bool(b.image_path) and Path(b.image_path).name.startswith("scheme_")


def match_shots(plan: ProjectPlan, st: LabState) -> ProjectPlan:
    """Модель ошиблась номером блока у снимка: сценарий отдаётся рисунку того же задания, у которого сценария нет
    (по порядку). Так рисунок не остаётся без съёмки из-за сдвига на единицу."""
    figs = sorted((n, bi) for n, bi, b in _blocks(st) if isinstance(b, Figure) and not _is_scheme(b))
    have = {(x.section, x.block) for x in plan.shots}
    fixed: dict[int, int] = {}  # место снимка в списке → верный номер блока
    for n in sorted({k[0] for k in figs}):
        free = [k for k in figs if k[0] == n and k not in have]
        stray = sorted((i for i, x in enumerate(plan.shots) if x.section == n and (x.section, x.block) not in figs),
                       key=lambda i: plan.shots[i].block)
        fixed.update({i: key[1] for key, i in zip(free, stray)})
    if not fixed:
        return plan
    shots = [x.model_copy(update={"block": fixed[i]}) if i in fixed else x for i, x in enumerate(plan.shots)]
    return plan.model_copy(update={"shots": shots})


def shot_gaps(plan: ProjectPlan, st: LabState) -> list[tuple[int, int, Figure]]:
    """Рисунки-скриншоты без снимка, для которых в плане нет сценария."""
    have = {(x.section, x.block) for x in plan.shots}
    return [(n, bi, b) for n, bi, b in _blocks(st)
            if isinstance(b, Figure) and not b.image_path and (n, bi) not in have]


NO_SCENARIO = ("Сам не снял: для этого рисунка не составлен сценарий съёмки. Напиши в «Правках»: «сними рисунки "
               "сам» — ForLabs составит сценарий и попробует ещё раз.")


def complete_shots(plan: ProjectPlan, st: LabState, gaps: list, llm) -> ProjectPlan:
    """Для части рисунков модель не дала сценарий: просим только его, по готовым файлам проекта."""
    wanted = {(n, bi) for n, bi, _ in gaps}
    figures = "\n".join(f"[{n}.{bi}] {b.topic}. Что в кадре: {b.slot or '—'}" for n, bi, b in gaps)
    refs = "; ".join(f"[{r.section}.{r.block}] из {r.file}" for r in plan.listings) or "нет"
    user = (f"Язык: {plan.language}, вид: {plan.kind}. {programs_text(plan)}\n\nРисунки без сценария:\n{figures}\n\n"
            f"Листинги отчёта: {refs}.\n\nФайлы проекта:\n{_files_text(plan.files)}")
    out = llm.json([{"role": "system", "content": SHOTS_SYSTEM}, {"role": "user", "content": user}], FixOut,
                   max_tokens=3000)
    added = [x for x in out.shots if (x.section, x.block) in wanted]
    if not added:
        return plan
    new = plan.model_copy(update={"shots": [*plan.shots, *added]})
    return drop_bad_shots(_quiet_console(_trusted_expects(_assign_programs(new), st)))


def mark_unmade(st: LabState, reason: str) -> None:
    """Программу собрать не получилось: причина остаётся у каждого рисунка и у проекта, а не только в журнале."""
    why = f"программу собрать не получилось ({reason.strip().rstrip('.')[:300]})"
    for _, _, b in _blocks(st):
        if isinstance(b, Figure) and not b.image_path and not b.auto_note and b.app_task is None:
            b.auto_note = f"Сам не снял: {why}."
    if st.project is None:
        st.project = ProjectInfo(name="", language="", kind="")
    if not st.project.built:
        st.project.note = why[:1].upper() + why[1:] + ". Снимки сделай сам или напиши в «Правках»: «попробуй ещё раз»."


def _assign_programs(plan: ProjectPlan) -> ProjectPlan:
    """Какую программу запускает снимок, если модель этого не сказала (или назвала библиотеку): ту, чей листинг
    стоит в отчёте над рисунком, затем любую из того же задания, иначе первую запускаемую."""
    if not plan.programs:
        return _assign_entries(plan)
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


def _assign_entries(plan: ProjectPlan) -> ProjectPlan:
    """Несколько программ одной папкой без programs (классы Java с main, скрипты): снимок запускает файл, чей
    листинг стоит в отчёте над рисунком; названный моделью файл записывается его путём. Иначе — главный файл."""
    runnable = plan_rules.entry_files(plan)
    if len(runnable) < 2:
        return plan
    refs: dict[int, list[tuple[int, str]]] = {}
    for r in plan.listings:
        if r.file in runnable:
            refs.setdefault(r.section, []).append((r.block, r.file))
    shots = []
    for sh in plan.shots:
        if sh.mode not in ("console", "window", "browser"):
            shots.append(sh)
            continue
        named = plan_rules.entry_of(plan, sh.program)
        if named is None and not sh.program.strip():
            near = sorted(refs.get(sh.section, []))
            above = [path for block, path in near if block < sh.block]
            named = above[-1] if above else near[0][1] if near else None
        shots.append(sh if named is None or sh.program == named else sh.model_copy(update={"program": named}))
    return plan.model_copy(update={"shots": shots})


shot_files = plan_rules.shot_files


def programs_text(plan: ProjectPlan) -> str:
    """Программы плана одной строкой — для запросов к модели."""
    if not plan.programs:
        runnable = plan_rules.entry_files(plan)
        if len(runnable) > 1:
            return ("Programs нет, но запускаемых файлов несколько: " + ", ".join(runnable) + f" (главный — "
                    f"{plan.entry}). Снимок называет свой файл в program.")
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
                          max_tokens=PLAN_MAX_TOKENS, think=True))
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
                       max_tokens=FILE_MAX_TOKENS, think=True)
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
        files = shot_files(plan, sh)  # своя программа и то, что она подключает
        return bool(re.search(reads, "\n".join(f.content for f in files if Path(f.path).suffix.lower() in ext)))

    shots = [sh.model_copy(update={"input": "", "steps": [x for x in sh.steps if x.action not in ("type", "key")]})
             if sh.mode == "console" and not reads_keyboard(sh) else sh for sh in plan.shots]
    return plan.model_copy(update={"shots": shots})


PREVIOUS_LIMIT = 14000  # знаков прежнего проекта в запросе на новый план


def previous_text(previous: ProjectPlan) -> str:
    """Прежний проект лабы для нового плана: после правки отчёта меняется только то, чего коснулась правка, а не
    имена файлов, структура таблиц и сценарии снимков (иначе прежние снимки расходятся с новым кодом)."""
    shots = [x.model_dump(exclude={"reason", "app", "goal", "open"}, exclude_defaults=True)
             for x in previous.shots if x.mode in AUTO_MODES]
    head = (f"Прежний проект этой лабы (отчёт с тех пор правили). Сохрани всё, чего правка не коснулась: название "
            f"«{previous.name}», язык и вид ({previous.language}, {previous.kind}), имена файлов и программ, текст "
            "нетронутых файлов, режимы и шаги снимков. Меняй только то, что изменилось в листингах и рисунках "
            f"отчёта выше.\n{programs_text(previous)}\nСценарии снимков: "
            f"{json.dumps(shots, ensure_ascii=False)}\nФайлы:\n")
    return head + _files_text(previous.files)[:PREVIOUS_LIMIT]


def plan_project(st: LabState, llm, memory: str = "", wish: str = "", rejected: str = "",
                 previous: ProjectPlan | None = None) -> ProjectPlan:
    user = lab_text(st) + (f"\n\nПамять предмета и требования:\n{memory[:6000]}" if memory.strip() else "")
    if previous is not None:
        user += "\n\n" + previous_text(previous)
    if wish.strip():  # просьба из чата правок: что в прежнем проекте было не так
        user += f"\n\nСтудент просит о программе: {wish.strip()}"
    if rejected.strip():  # прошлый план не прошёл проверку: модель узнаёт почему
        user += f"\n\nПрошлый план не принят: {rejected.strip()}. Составь план заново без этих ошибок."
    try:
        plan = llm.json([{"role": "system", "content": PROJECT_SYSTEM}, {"role": "user", "content": user}],
                        ProjectPlan, max_tokens=PLAN_MAX_TOKENS, think=True)
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
                   max_tokens=8000, think=True)
    return _apply_fix(plan, out, scenarios=False)


AUTO_MODES = ("window", "console", "browser")  # снимки, которые ForLabs проходит по сценарию сам


def _alike(a: str, b: str) -> bool:
    """Один и тот же файл до и после правки (а не тёзка из другой программы)."""
    a, b = _squash(a)[:6000], _squash(b)[:6000]
    return bool(a) and bool(b) and difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.6


def _fragment_of_whole(new: ListingRef, old: ListingRef | None, old_file: str, new_file: str) -> bool:
    """Модель после исправления прислала в листинге только изменённый кусок (один метод), хотя листинг отчёта —
    весь файл: без своего кода или с кодом почти всего файла. Такой обрывок вместо программы в отчёт не идёт."""
    if old is None or not new.code.strip() or not new_file.strip():
        return False
    was_whole = not old.code.strip() or (bool(old_file) and len(_squash(old.code)) >= 0.9 * len(_squash(old_file)))
    return was_whole and len(_squash(new.code)) < 0.6 * len(_squash(new_file))


def _apply_fix(plan: ProjectPlan, out: FixOut, scenarios: bool) -> ProjectPlan:
    """Ответ модели на исправление — в план: изменённые, перенесённые и убранные файлы, листинги, деление на
    программы и то, какую программу запускает снимок. scenarios — можно менять и сами сценарии снимков (исправление
    после снимка). Модель экономит слова, поэтому перенос понимается и без повторной отправки текста файла."""
    by_path = {f.path: f for f in plan.files}
    before = {f.path.lower(): f.content for f in plan.files}
    where: dict[str, str] = {}  # прежний путь (в нижнем регистре) → новый: по нему переезжают листинги

    def find(path: str) -> str | None:
        return next((x for x in by_path if x.lower() == path.lower()), None)

    def move(src: str, dst: str) -> None:
        cur = find(src)
        ok = (cur is not None and plan_rules._path_problem(dst) is None and find(dst) is None
              and plan_rules._build_file_problem(ProjectFile(path=dst)) is None
              and Path(dst).suffix.lower() == Path(cur).suffix.lower())
        if ok:
            by_path[dst] = ProjectFile(path=dst, content=by_path.pop(cur).content)
            where[cur.lower()] = dst

    for m in out.move:
        move(m.src, m.to)
    fresh: dict[str, list[str]] = {}  # имя файла → новые пути из ответа
    for f in out.files:
        if plan_rules._build_file_problem(f) is None:  # go.mod, csproj и т.п. всё равно пишет ForLabs
            old = find(f.path)
            if old is not None and old != f.path:
                del by_path[old]
            by_path[f.path] = f
            if f.path.lower() not in before:
                fresh.setdefault(Path(f.path).name.lower(), []).append(f.path)
    new_paths = {f.path.lower() for f in out.files} | {v.lower() for v in where.values()}
    for path in [x for x in by_path if x.lower() in {plan_rules.norm_path(r).lower() for r in out.remove}
                 and x.lower() not in new_paths]:
        gone = by_path.pop(path)
        # убран и прислан заново в другом месте (и, может быть, под другим именем) — тоже перенос, если это тот же
        # файл, а не тёзка из другой программы: сначала ищем точную копию, затем единственный похожий
        taken = set(where.values())
        cands = [n for names in fresh.values() for n in names if n not in taken]
        same = [n for n in cands if _squash(by_path[n].content) == _squash(gone.content)]
        same = same or [n for n in cands if _alike(gone.content, by_path[n].content)]
        if len(same) == 1:
            where[path.lower()] = same[0]
    programs = plan.programs if (not plan.programs and len(out.programs) == 1) else (out.programs or plan.programs)
    folders = {p.name.lower(): p.name for p in programs}

    def outside(path: str) -> bool:
        return path.partition("/")[0].lower() not in folders or "/" not in path

    refs = {(r.section, r.block): r for r in plan.listings}
    for r in out.listings:
        if find(r.file) is None and programs and (src := find(Path(r.file).name)) is not None and outside(src):
            move(src, r.file)  # листинг назван по новому месту, а сам файл остался на старом: переносим файл
        if find(r.file) is not None:  # листинг про файл, которого нет, — описка: остаётся прежняя ссылка
            old = refs.get((r.section, r.block))
            if _fragment_of_whole(r, old, before.get(old.file.lower(), "") if old else "", by_path[find(r.file)].content):
                r = r.model_copy(update={"code": ""})  # листинг возьмётся из исправленного файла целиком
            refs[(r.section, r.block)] = r
    if programs:
        if plan.language == "csharp":  # файл остался вне программ: его место — программа с именем его namespace
            for path in [x for x in by_path if outside(x) and x.lower().endswith(".cs")]:
                m = re.search(r"^\s*namespace\s+([\w.]+)", by_path[path].content, re.M)
                home = folders.get((m[1].split(".")[0] if m else "").lower())
                if home:
                    move(path, f"{home}/{Path(path).name}")
        runs = [p.name for p in programs if p.runs]
        for path in [x for x in by_path if outside(x) and "/" not in x and Path(x).suffix.lower() in DATA_EXT]:
            data = by_path.pop(path)  # данные из корня нужны каждой программе, которую запускают
            for name in runs:
                if find(f"{name}/{path}") is None:
                    by_path[f"{name}/{path}"] = ProjectFile(path=f"{name}/{path}", content=data.content)
            if runs:
                where[path.lower()] = f"{runs[0]}/{path}"
    listings = []
    for r in refs.values():
        if find(r.file) is None:  # файл листинга переехал: листинг — по новому пути; пропал совсем — ссылки нет
            new = where.get(r.file.lower())
            if new is None or find(new) is None:
                continue
            r = r.model_copy(update={"file": new})
        listings.append(r)
    given = {(x.section, x.block): x for x in out.shots}
    shots = []
    for x in plan.shots:
        new = given.get((x.section, x.block))
        opened = [where.get(o.lower(), o) for o in x.open]
        if opened != x.open:
            x = x.model_copy(update={"open": opened})
        if new is None or x.mode not in AUTO_MODES:
            shots.append(x)
            continue
        whole = bool(new.steps or new.input or new.expect.strip() or new.url.strip())  # сценарий, а не одно имя
        if scenarios and whole and new.mode in AUTO_MODES:  # «сними сам» — не починка
            shots.append(new if new.program.strip() else new.model_copy(update={"program": x.program}))
        else:
            shots.append(x.model_copy(update={"program": new.program}) if new.program.strip() else x)
    data = plan.model_dump() | {"files": [f.model_dump() for f in by_path.values()],
                                "listings": [r.model_dump() for r in listings],
                                "shots": [x.model_dump() for x in shots]}
    alone = not plan.programs and len(out.programs) == 1  # одна программа — это обычный проект, папки не нужны
    if out.programs and not alone:
        data["programs"] = [x.model_dump() for x in out.programs]
    if out.entry and not data.get("programs"):
        data["entry"] = out.entry
    elif not data.get("programs") and plan.entry.lower() in where:
        data["entry"] = where[plan.entry.lower()]
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
                   max_tokens=PLAN_MAX_TOKENS, think=True)
    fixed = _apply_fix(plan, out, scenarios=True)
    return _quiet_console(_trusted_expects(_tidy(fixed), st)) if st is not None else fixed


def review_results(plan: ProjectPlan, st: LabState, shown: list, llm) -> dict[tuple[int, int], str]:
    """Что программа на самом деле показала на снятых рисунках — годится ли это в отчёт. shown — записи
    (раздел, блок, рисунок, сценарий, текст экрана). → {(раздел, блок): что не так} только для неверных."""
    sections = {sec.n: sec for sec in st.report.sections} if st.report else {}

    def near(n: int) -> str:
        sec = sections.get(n)
        parts = [getattr(b, "text", "") or " ".join(getattr(b, "items", None) or []) for b in (sec.blocks if sec else [])]
        return " ".join(x for x in parts if x)[:1500] or "—"

    def one(n: int, bi: int, b, shot, screen: str) -> str:
        how = shot.model_dump_json(exclude={"section", "block", "reason", "app", "goal", "open"}, exclude_defaults=True)
        return (f"[{n}.{bi}] {b.topic}. Что должно быть в кадре: {b.slot or '—'}\nСценарий: {how}\n"
                f"В отчёте рядом с рисунком сказано: {near(n)}\nНа экране было:\n<<<\n{screen.strip()[-SEEN_LIMIT:]}\n>>>")

    spec = st.spec
    tasks = "\n".join(f"{t.n}. {t.text}" for t in spec.tasks) if spec else ""
    files = _files_text(plan.files)[:REVIEW_FILES_LIMIT]
    user = (f"Тема: {spec.topic if spec else ''}\nЗадания:\n{tasks}\n\nРисунки:\n\n"
            + "\n\n".join(one(*x) for x in shown) + f"\n\nФайлы проекта:\n{files}")
    out = llm.json([{"role": "system", "content": REVIEW_SYSTEM}, {"role": "user", "content": user}], ReviewOut,
                   max_tokens=1500, think=True)
    known = {(n, bi) for n, bi, *_ in shown}
    return {(v.section, v.block): (" ".join(v.problem.split()) or "результат на экране неверный")
            for v in out.shots if not v.ok and (v.section, v.block) in known}


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


_SOURCE = re.compile(r"^\s*(?:source|\\\.)\s+([^\s;]+)", re.IGNORECASE | re.MULTILINE)


def sql_order(plan: ProjectPlan) -> list[str]:
    """В каком порядке выполнять скрипты лабы при проверке: как их подключают снимки (source create.sql, затем
    fill.sql…), остальные — как в плане, главный первым."""
    have = {f.path.lower(): f.path for f in plan.files if Path(f.path).suffix.lower() == ".sql"}
    out: list[str] = []
    for sh in sorted(plan.shots, key=lambda x: (x.section, x.block)):
        for m in _SOURCE.finditer(sh.input or ""):
            path = have.get(m.group(1).strip("'\"").replace("\\", "/").removeprefix("./").lower())
            if path and path not in out:
                out.append(path)
    rest = [p for p in have.values() if p not in out]
    return out + sorted(rest, key=lambda p: p != plan.entry)


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
    servers: list = []  # свой сервер MySQL лабы: гасится, чем бы этап ни кончился
    try:
        return _build_and_shoot(pipe, st, plan, llm, emit, retake, servers)
    finally:
        for srv in servers:
            try:
                srv.stop()
            except Exception:  # noqa: BLE001
                pass


def _build_and_shoot(pipe, st: LabState, plan: ProjectPlan, llm, emit, retake: bool, servers: list) -> LabState:

    emit = emit or (lambda text, kind="info": None)
    lab = pipe.lab_dir(st)
    pdir = lab / "project"
    pdir.mkdir(parents=True, exist_ok=True)
    # план мог быть составлен до того, как ForLabs научился нескольким программам: папки со своей точкой входа
    # становятся программами — иначе такая лаба не соберётся никогда
    plan = match_shots(_with_materials(_tidy(plan), st), st)
    if llm is not None and (gaps := shot_gaps(plan, st)):
        emit("Составляю сценарии для рисунков, у которых их нет")
        try:
            plan = complete_shots(plan, st, gaps, llm)
        except Exception as e:  # noqa: BLE001 — нет связи, кривой ответ: рисунки останутся студенту с причиной
            emit(f"Сценарии составить не получилось ({getattr(e, 'message_ru', None) or e})", "warn")
    info = ProjectInfo(name=plan.name, language=plan.language, kind=plan.kind)
    tools = languages.detect()
    info.missing = languages.missing(plan, tools)
    work = pdir / "src"
    shooter = pipe.shooter
    if shooter is None and sys.platform == "win32":
        from forlabs.runner.shots import WindowsShooter

        shooter = WindowsShooter()

    mysql: dict = {"srv": None, "error": None}  # свой сервер MySQL лабы: один на проверку скриптов и снимки

    def mysql_server():
        if mysql["srv"] is None and mysql["error"] is None:
            emit("Запускаю отдельный сервер MySQL: на нём проверяю скрипты и делаю снимки")
            try:
                mysql["srv"] = start_mysql(tools["mysql"].path)
                servers.append(mysql["srv"])
            except Exception as e:  # noqa: BLE001 — сервер не поднялся: снимки студенту, отчёт собирается
                mysql["error"] = str(e).rstrip(".") or "сервер MySQL не запустился"
        return mysql["srv"]

    def checked_sql(candidate: ProjectPlan, res):
        """Скрипты SQL выполняются на чистом сервере: ошибка в запросе — та же ошибка сборки (её чинит модель,
        а студент видит в «Ошибках сборки»). Раньше скрипты не запускались вовсе, пока не дойдёт до снимка."""
        if candidate.language != "mysql" or res is None or not res.ok or "mysql" not in tools:
            return res
        srv = mysql_server()
        if srv is None or not hasattr(srv, "run_file"):
            return res
        from forlabs.runner import mysqlrun

        emit("Проверяю скрипты SQL на отдельном сервере")
        try:
            log = mysqlrun.check_scripts(srv, work, sql_order(candidate))
        except Exception as e:  # noqa: BLE001 — сервер упал посреди проверки: скрипты остаются непроверенными
            emit(f"Скрипты SQL проверить не получилось ({e})", "warn")
            return res
        return res if not log else dataclasses.replace(res, ok=False, log=log)

    def build(candidate: ProjectPlan, fix_builds: bool):
        """Проект в папку work и сборка. → (план после исправлений сборки, итог сборки, почему нельзя запускать,
        нужна ли сеть). Опасный код не собирается вовсе: сборка и проверка синтаксиса — тоже запуск чужих
        инструментов над ним. Сеть — не опасность, если программа работает в песочнице."""
        shutil.rmtree(work, ignore_errors=True)
        languages.materialize(candidate, work, tools)
        _put_assets(pipe, st, candidate, work)
        info.placeholders = _own_pictures(pipe, st, candidate, work)
        blocked, net = _blocked(pipe, candidate)
        res = None
        if not info.missing and not blocked:
            sql = candidate.language == "mysql"
            if not sql:
                emit("Собираю программу")
            res = checked_sql(candidate, languages.build(work, candidate, tools))
            while not res.ok and fix_builds and llm is not None and info.fixes < MAX_FIXES:
                info.fixes += 1
                emit(f"Исправляю ошибки {'в скриптах SQL' if sql else 'сборки'} ({info.fixes} из {MAX_FIXES})")
                fixed = _with_materials(fix_project(candidate, res.log, llm), st)
                if problems := validate(fixed):  # исправление не годится: в следующем круге модель узнает почему
                    res = dataclasses.replace(res, log=res.log.split(REJECTED)[0].rstrip() + REJECTED
                                              + "; ".join(problems[:5]))
                    continue
                candidate = fixed
                shutil.rmtree(work, ignore_errors=True)
                languages.materialize(candidate, work, tools)
                _put_assets(pipe, st, candidate, work)
                info.placeholders = _own_pictures(pipe, st, candidate, work)
                blocked, net = _blocked(pipe, candidate)  # исправление тоже проверяется до сборки
                if blocked:
                    break
                res = checked_sql(candidate, languages.build(work, candidate, tools))
        return candidate, res, blocked, net

    told: list[bool] = []  # про заглушки сказано

    def settle() -> Path:
        """Листинги отчёта — как в коде проекта; копия проекта в папку сдачи (до снимков: на снимках пути из неё,
        как при запуске самим студентом)."""
        (pdir / "plan.json").write_text(plan.model_dump_json(indent=1), encoding="utf-8")
        sync_listings(st, plan)
        folder = work
        try:
            folder, locked, saved = _copy_to_hand_in(pipe, st, plan, work)
            info.folder = str(folder)
            if locked:
                emit("Не обновил файлы проекта, открытые в другой программе: " + ", ".join(locked[:5])
                     + ". Закрой их и нажми «Пересобрать».", "warn")
            if saved:
                emit("Эти файлы ты менял сам, а проект их заменил. Твои версии лежат рядом с пометкой .bak: "
                     + ", ".join(saved[:5]), "warn")
            if info.placeholders and not told:
                told.append(True)
                emit("Картинок для сайта у меня нет — положил заглушки: " + ", ".join(info.placeholders[:8])
                     + ". Замени их своими с теми же именами в папке проекта", "warn")
        except OSError as e:
            emit(f"Проект не скопировался в папку сдачи ({e})", "warn")
        return folder

    mine: set[tuple[int, int]] = set()  # рисунки, снятые в этом запуске (после правки программы снимаются заново)
    shown: list = []  # снятое в этом круге: (раздел, блок, рисунок, сценарий, что было на экране)
    kept: dict[tuple[int, int], str] = {}  # их прежние снимки: вернутся, если пересъёмка не выйдет

    def shoot_all(hand_in: Path) -> list[ShotFailure]:
        shots = {(s.section, s.block): s for s in plan.shots}
        failures: list[ShotFailure] = []
        specs: dict[str, object] = {}  # чем запускать каждую программу плана
        for n, bi, b in list(_blocks(st)):
            if not isinstance(b, Figure) or b.image_path:
                continue
            b.need_tool = None
            shot = shots.get((n, bi))
            if shot is not None and shot.mode == "app" and (other := foreign_app(shot, b)):
                b.app_task = None
                b.auto_note = (f"Этот снимок делаешь ты: в программе {other} ForLabs работать не умеет.")
                continue
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
            if shot is None:  # сценария нет: не молчим — причина видна студенту и чату
                b.auto_note = NO_SCENARIO
                continue
            if shot.mode == "manual":
                why = shot.reason.strip().rstrip(".")
                if why[:1].isupper() and why[1:2].islower():  # после двоеточия — со строчной («JDK» не трогаем)
                    why = why[:1].lower() + why[1:]
                b.auto_note = f"Этот снимок делаешь ты: {why}." if why else "Этот снимок делаешь ты."
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
                b.auto_note = ("В скриптах SQL осталась ошибка, снимок сделай сам." if plan.language == "mysql"
                               else "Программа не собралась, снимок сделай сам.")
                continue
            if shooter is None:
                b.auto_note = "Снимки окон ForLabs делает только в Windows."
                continue
            prog = plan.program_of(shot)
            which = prog.name if prog is not None else (plan_rules.entry_of(plan, shot.program) or "")
            if which not in specs:
                specs[which] = languages.run_spec_for(plan, result, work, tools, which)
            spec = specs[which]
            if plan.language == "mysql":
                db = mysql_server()
                if db is None:
                    b.auto_note = f"Сам не снял: {mysql['error']}."
                    continue
                spec = specs[which] = dataclasses.replace(spec, cmd=db.client())
                if hasattr(db, "reset"):
                    try:
                        db.reset()  # каждый снимок — с чистого сервера: скриптам не нужен DROP DATABASE
                    except Exception:  # noqa: BLE001
                        pass
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
                screen = (res.seen or getattr(res, "ui", "") or "").strip()
                if screen:
                    shown.append((n, bi, b, shots[(n, bi)], screen))
                old = kept.pop((n, bi), None)
                if old and old != rel and not in_versions(old):  # снимок прежней программы больше не нужен
                    (lab / old).unlink(missing_ok=True)
                pipe._sync(st, [rel])
            elif (n, bi) in kept:  # программа изменилась, а переснять не вышло: остаётся прежний снимок
                b.image_path, b.auto_note = kept.pop((n, bi)), None
            else:
                b.auto_note = f"Сам не снял: {res.reason.rstrip('.')}."
                if getattr(res, "kind", ""):
                    failures.append(ShotFailure(n, bi, b.topic, b.slot or "", shots[(n, bi)], res.reason,
                                                res.seen, getattr(res, "ui", "")))
        return failures

    def in_versions(rel: str) -> bool:
        """Снимок ещё нужен: на него ссылается сохранённая версия отчёта (к ней можно откатиться)."""
        return any(isinstance(b, Figure) and b.image_path == rel
                   for v in st.versions for sec in v.report.sections for b in sec.blocks)

    def judge() -> list[ShotFailure]:
        """Снимок получился — но что на нём? Неверный результат (нули вместо данных, ошибка на экране) в отчёт не
        идёт: рисунок снимается с отчёта с причиной, а дальше программу чинит модель."""
        if llm is None or not shown:
            return []
        emit("Проверяю, что программа показала на снимках")
        try:
            wrong = review_results(plan, st, shown, llm)
        except Exception as e:  # noqa: BLE001 — нет связи, кривой ответ: снимки остаются, как без проверки
            emit(f"Результат на снимках проверить не получилось ({getattr(e, 'message_ru', None) or e})", "warn")
            return []
        out = []
        for n, bi, b, shot, screen in shown:
            problem = wrong.get((n, bi))
            if not problem:
                continue
            rel, b.image_path = b.image_path, None
            b.auto_note = f"Сам не снял: программа выводит неверный результат — {problem.rstrip('.')}."
            if rel and not in_versions(rel):
                (lab / rel).unlink(missing_ok=True)
            mine.discard((n, bi))
            out.append(ShotFailure(n, bi, b.topic, b.slot or "", shot,
                                   f"программа выводит неверный результат: {problem}", screen))
        return out

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
            info.note = ("В скриптах SQL осталась ошибка: запросы с ней не выполняются. Скрипты в папке сдачи, "
                         "снимки сделай сам." if plan.language == "mysql" else
                         "Программу не собрал: ошибки остались. Проект в папке сдачи, снимки сделай сам.")
            info.build_log = result.log.split(REJECTED)[0].strip()[:BUILD_LOG_LIMIT]
        if info.blocked:
            info.note = f"Сам не собирал и не запускал: {info.blocked[0]}"
        shown.clear()
        try:
            failures = shoot_all(settle())
        finally:
            restore_kept()
        failures += judge()
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
        if fixed == plan:
            emit("Исправить не получилось: модель не предложила изменений — снимок остаётся за тобой", "warn")
            break
        if (bad := validate(fixed)) or (bad := _blocked(pipe, fixed)[0]):
            emit(f"Исправление не принято ({str(bad[0]).rstrip('.')[:200]}) — снимок остаётся за тобой", "warn")
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


BACKUP = ".bak"  # прежняя версия файла студента рядом с новой: «main.py.bak» (компилятор её не берёт)


def _put_assets(pipe, st: LabState, plan: ProjectPlan, work: Path) -> int:
    """Картинки и другие файлы из архивов преподавателя — в папку программы, по путям из архива (на снимки и в
    папку сдачи). Файл проекта с тем же путём не заменяется. → сколько файлов положено."""
    from forlabs.core.store import safe_file_name

    lab = pipe.lab_dir(st)
    folders = [p.name for p in plan.programs if p.runs] or [""]
    placed = 0
    for m in st.materials:
        root = lab / "assets_in" / safe_file_name(Path(m.name).stem)
        for rel in m.assets:
            src = lab / rel
            try:
                inner = src.relative_to(root)
            except ValueError:
                continue
            for folder in folders:
                dst = work / folder / inner
                if dst.exists() or not src.is_file():
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                placed += 1
    return placed


def _own_pictures(pipe, st: LabState, plan: ProjectPlan, work: Path) -> list[str]:
    """Картинки сайта, которых нет в плане: если студент уже положил свою на это место в папке сдачи, в сборку
    идёт она (и на снимки, и обратно в папку — без замены и «.bak»). → картинки, вместо которых остались заглушки."""
    wanted = plan_rules.missing_images(plan)
    if not wanted:
        return []
    try:
        before = json.loads((pipe.lab_dir(st) / "project" / "hand_in.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        before = {}
    root = Path(before.get("folder") or "-")
    sha = before.get("sha") or {}
    left = []
    for rel in wanted:
        theirs = root.joinpath(*rel.split("/"))
        try:
            if theirs.is_file() and not theirs.is_symlink() and theirs.stat().st_size <= 20 * 1024 * 1024 \
                    and rel in sha and text_sha(theirs.read_bytes()) != sha[rel]:
                shutil.copy2(theirs, work.joinpath(*rel.split("/")))  # это уже картинка студента
                continue
        except OSError:
            pass
        left.append(rel)
    return left


def _copy_to_hand_in(pipe, st: LabState, plan: ProjectPlan, work: Path) -> tuple[Path, list[str], list[str]]:
    """Проект в папку сдачи. Меняются только свои файлы (каждый — атомарно), свои прошлые, которых больше нет, —
    удаляются; файлы, которые студент положил туда сам, не трогаются. Файл, который студент правил (или положил
    сам на то же место), перед заменой сохраняется рядом как «имя.bak». Список своих файлов и отпечатки записанного —
    в папке лабы, а не у студента. → (папка, что не обновилось — открыто в другой программе, чьи копии сохранены)."""
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
    same = before.get("folder") == str(out)
    old = set(before.get("files", [])) if same else set()
    sha = dict(before.get("sha") or {}) if same else {}  # что ForLabs записал: по нему видно правки студента
    theirs = set(before.get("mine") or []) if same else set()  # файлы, чей текст взят у студента
    tracked = bool(sha) or not old  # прежние версии отпечатков не вели: чьи там правки, по ним не понять
    out.mkdir(parents=True, exist_ok=True)
    locked: list[str] = []
    saved: list[str] = []
    for rel in files:
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:6]}.tmp")
        try:
            new = (work / rel).read_bytes()
            if dst.is_file() and not dst.is_symlink():
                cur = dst.read_bytes()
                changed = text_sha(cur) != text_sha(new)
                his = rel in theirs or (text_sha(cur) != sha[rel] if rel in sha else tracked and rel not in old)
                if changed and his:  # не затираем молча: версия студента остаётся рядом
                    shutil.copy2(dst, dst.with_name(dst.name + BACKUP))
                    saved.append(rel)
            shutil.copy2(work / rel, tmp)
            os.replace(tmp, dst)
            sha[rel] = text_sha(new)
            theirs.discard(rel)
        except OSError:
            tmp.unlink(missing_ok=True)
            locked.append(rel)
    sha = {rel: v for rel, v in sha.items() if rel in files}
    now = {rel.lower() for rel in files}
    kept_his = theirs - set(files)  # файлы, которые положил студент: в новом проекте их нет, но они его
    for rel in sorted(old - set(files)):  # свои прошлые файлы, которых в проекте больше нет
        if rel.lower() in now or rel in kept_his:
            continue  # тот же файл с другим регистром букв: в Windows удаление стёрло бы только что записанный
        try:
            (out / rel).unlink(missing_ok=True)
            parent = (out / rel).parent
            while parent != out and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        except OSError:
            locked.append(rel)
    manifest.write_text(json.dumps({"folder": str(out), "files": files, "sha": sha,
                                    "mine": sorted(theirs & set(files))}, ensure_ascii=False), encoding="utf-8")
    return out, locked, saved


def decode_text(raw: bytes) -> str | None:
    """Текст файла студента, как бы его ни сохранил редактор: UTF-8 (с меткой и без), UTF-16 с меткой, Windows-1251.
    None — это не текст (или кодировка неизвестна): такой файл в проект не берётся."""
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
    if "\x00" in text or sum(1 for ch in text if ch < " " and ch not in "\n\r\t\f") > 0:
        return None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _plain(raw: bytes) -> str:
    """Текст файла без метки кодировки и с одинаковыми концами строк: редактор пересохраняет файл по-своему."""
    text = decode_text(raw)
    return text if text is not None else raw.decode("utf-8", "replace")


def text_sha(raw: bytes) -> str:
    import hashlib

    return hashlib.sha1(_plain(raw).encode("utf-8", "replace")).hexdigest()


def sync_listings(st: LabState, plan: ProjectPlan) -> None:
    """Листинги отчёта — как в коде проекта: задание дополнило код, для сборки добавлены импорты, студент поправил
    файл — листинг берётся из файла."""
    refs = {(r.section, r.block): r for r in plan.listings}
    for n, bi, b in _blocks(st):
        r = refs.get((n, bi))
        if not isinstance(b, Listing) or r is None:
            continue
        code = r.code.rstrip() if r.code.strip() else b.code
        f = plan.file(r.file)
        got = listing_in_file(code, f.content) if f is not None else None
        if got and (_squash(code) not in _squash(f.content) or
                    (_squash(got) != _squash(code) and _squash(got).endswith(_squash(code)))):
            code = got
        b.code = code


def follow_listing(code: str, old: str, new: str) -> str | None:
    """Где листинг оказался после правки файла: его место в прежнем тексте переносится на новый по построчному
    сравнению. Так листинг идёт за любой правкой — и когда студент переписал его почти целиком. None — в прежнем
    файле этого листинга не было."""
    was = listing_in_file(code, old)
    if was is None:
        return None
    a_lines, b_lines, part = old.splitlines(), new.splitlines(), [x.rstrip() for x in was.splitlines()]
    start = next((i for i in range(len(a_lines) - len(part) + 1)
                  if [x.rstrip() for x in a_lines[i:i + len(part)]] == part), None)
    if start is None or not part:
        return None
    end = start + len(part)
    lo, hi = None, None
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=False).get_opcodes():
        if lo is None and i1 <= start < i2:
            lo = j1 + (start - i1) if tag == "equal" else j1
        if hi is None and i1 < end <= i2:
            hi = j1 + (end - i1) if tag == "equal" else j2
    if lo is None or hi is None or hi <= lo:
        return None
    return "\n".join(b_lines[lo:hi]).strip("\n") or None


NEW_FILES_LIMIT = 20  # столько новых файлов студента берётся в проект за раз


def import_student_edits(pipe, st: LabState, emit=None) -> list[str]:
    """Студент поправил код проекта у себя в папке (в Visual Studio, в редакторе): его текст становится текстом
    проекта и листингов отчёта — пересборка его не затрёт, а чат правит уже его код. Берутся исходники, которые
    отличаются от записанного ForLabs в прошлый раз, и новые исходники студента; данные программы не берутся (их
    меняет сама программа при запуске). Если отчёт с тех пор уже меняли, проект всё равно составляется заново —
    тогда не берётся ничего, а версии студента сохранятся рядом как «.bak». → какие файлы взяты."""
    pdir = pipe.lab_dir(st) / "project"
    plan = load_plan(pipe, st)
    try:
        before = json.loads((pdir / "hand_in.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    sha, root = dict(before.get("sha") or {}), Path(before.get("folder") or "-")
    if plan is None or not sha or not root.is_dir() or stale(st, plan):
        return []
    code_ext = plan_rules.CODE_EXT[plan.language] | plan_rules.EXTRA_EXT[plan.language]
    skip = set(languages.BUILD_DIRS) | {"__pycache__", ".vs", ".git", ".idea", "node_modules"}

    def text_of(disk: Path) -> str | None:
        try:
            if disk.is_symlink() or not disk.is_file() or disk.stat().st_size > plan_rules.MAX_FILE:
                return None
            return decode_text(disk.read_bytes())
        except OSError:
            return None

    mine: dict[str, str] = {}
    for f in plan.files:
        if f.path not in sha or Path(f.path).suffix.lower() not in code_ext:
            continue
        text = text_of(root.joinpath(*f.path.split("/")))
        if text is not None and text_sha(text.encode("utf-8")) != sha[f.path] and text != _plain(f.content.encode("utf-8")):
            mine[f.path] = text
    added: dict[str, str] = {}
    known = {x.lower() for x in before.get("files", [])} | {f.path.lower() for f in plan.files}
    folders = [p.name.lower() + "/" for p in plan.programs]
    try:
        for disk in sorted(root.rglob("*")):
            rel = disk.relative_to(root).as_posix()
            if (rel.lower() in known or disk.suffix.lower() not in code_ext or len(added) >= NEW_FILES_LIMIT
                    or {x.lower() for x in Path(rel).parts[:-1]} & skip
                    or (folders and not any(rel.lower().startswith(d) for d in folders))):
                continue
            text = text_of(disk)
            if text is not None and plan_rules._path_problem(rel) is None:
                added[rel] = text
    except OSError:
        pass
    if not mine and not added:
        return []

    def with_files(extra: dict[str, str]) -> ProjectPlan:
        files = [ProjectFile(path=f.path, content=mine.get(f.path, f.content)) for f in plan.files]
        files += [ProjectFile(path=path, content=text) for path, text in extra.items()]
        # листинг, который модель когда-то вернула своим текстом, больше не указ: теперь он берётся из файла студента
        listings = [r.model_copy(update={"code": ""}) if r.file in mine else r for r in plan.listings]
        return plan.model_copy(update={"files": files, "listings": listings})

    new = with_files(added)
    if validate(new):  # новые файлы не годятся (слишком много, не те имена): берём только правки
        added, new = {}, with_files({})
    if validate(new) or not (mine or added):
        return []
    blocks = {(n, bi): b for n, bi, b in _blocks(st)}
    follows: dict[tuple[int, int], str] = {}
    for r in plan.listings:  # где каждый листинг оказался после правки
        b, f = blocks.get((r.section, r.block)), plan.file(r.file)
        if r.file in mine and isinstance(b, Listing) and f is not None:
            code = follow_listing(r.code.rstrip() if r.code.strip() else b.code, f.content, mine[r.file])
            if code is None:
                return []  # листинг в прежнем файле не найден: отчёт и проект уже расходятся, проект составят заново
            follows[(r.section, r.block)] = code
    taken = [*mine, *added]
    try:
        (pdir / "plan.json").write_text(new.model_dump_json(indent=1), encoding="utf-8")
        sha.update({path: text_sha(text.encode("utf-8")) for path, text in (mine | added).items()})
        marks = sorted(set(before.get("mine") or []) | set(taken))
        (pdir / "hand_in.json").write_text(json.dumps(before | {"sha": sha, "mine": marks, "files": sorted(
            set(before.get("files", [])) | set(added))}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return []
    for key, code in follows.items():
        blocks[key].code = code
    if st.project is not None:
        st.project.report_sha = report_sha(st)  # проект по-прежнему от этого отчёта: заново его составлять не нужно
    st.docx_sha = None
    if emit is not None:
        emit("Взял твои правки из папки проекта: " + ", ".join(taken[:6]))
    return taken


def load_plan(pipe, st: LabState) -> ProjectPlan | None:
    f = pipe.lab_dir(st) / "project" / "plan.json"
    if not f.exists():
        return None
    try:
        return ProjectPlan.model_validate(json.loads(f.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
