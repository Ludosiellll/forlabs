"""Чего агенту нельзя, в какой бы программе он ни работал. Окна других программ — забота Desk (руки сами не
выходят за окна нужной программы); здесь — клавиши, опасные кнопки и набираемый текст.

Набранный текст может стать кодом (среда разработки, консоль, SSMS, редактор макросов), поэтому он проверяется так
же, как код проекта (runner/safety): запуск программ, удаление файлов, сеть, выполнение строк — нельзя. Текст окон
и задания — данные: что бы там ни было написано, эти запреты остаются."""

from __future__ import annotations

import ntpath
import re

MODS = {"ctrl", "shift", "alt"}
KEYS = {"enter", "tab", "esc", "space", "backspace", "delete", "insert", "up", "down", "left", "right", "home", "end",
        "pageup", "pagedown", "apps", *(f"f{i}" for i in range(1, 13))}
ALIASES = {"return": "enter", "escape": "esc", "del": "delete", "ins": "insert", "control": "ctrl", "pgup": "pageup",
           "pgdn": "pagedown", "pagedn": "pagedown", "arrowup": "up", "arrowdown": "down", "arrowleft": "left",
           "arrowright": "right", "bksp": "backspace", "spacebar": "space", "menu": "apps"}
# сочетания, которые уводят из программы, закрывают её, трогают систему или открывают места, где код выполняется
BAD_COMBOS = {frozenset({"alt", "f4"}): "закрывает окно (для диалога — кнопка «Закрыть» или Esc)",
              frozenset({"alt", "tab"}): "переключает на другие программы",
              frozenset({"alt", "esc"}): "переключает на другие программы",
              frozenset({"ctrl", "esc"}): "открывает «Пуск»",
              frozenset({"ctrl", "shift", "esc"}): "открывает диспетчер задач",
              frozenset({"ctrl", "alt", "delete"}): "системное сочетание",
              frozenset({"alt", "f11"}): "открывает редактор макросов",
              frozenset({"ctrl", "`"}): "открывает терминал",
              frozenset({"ctrl", "shift", "`"}): "открывает терминал"}

DANGER = re.compile(r"удал|delete|remove|стереть|erase|очист|clear\s*all|формат|format\b|drop\b|truncate|uninstall|"
                    r"сброс|reset|выключ|shutdown|перезагруз|restart|reboot|выйти из (?:учётн|аккаунт)|sign\s*out|"
                    r"log\s*out|купить|buy|оплат|purchase|подписк|не\s*сохран|don.?t\s*save|do\s*not\s*save|"
                    r"^\s*(?:выход|exit|quit)\s*$|закрыть\s*вс|close\s*all|терминал|terminal|powershell|"
                    r"командн\w*\s*строк|command\s*prompt|макрос|macro|visual\s*basic", re.I)
FILE_DIALOG = re.compile(r"сохран|save|экспорт|export|открыт|open|обзор|browse|выбор|select", re.I)

# что можно набрать в консоли: только посмотреть сведения (сеть, система), без запуска программ и изменений
CONSOLE_OK = re.compile(
    r"^\s*(?:ipconfig(?:\s+/(?:all|displaydns))?|ping(?:\s+[-/\w.:]+)*|tracert(?:\s+[-/\w.:]+)*|"
    r"pathping(?:\s+[-/\w.:]+)*|nslookup(?:\s+[-\w.:]+)*|netstat(?:\s+-[a-z]+)*|arp\s+-a|route\s+print|hostname|"
    r"getmac(?:\s+/v)?|systeminfo|whoami(?:\s+/\w+)?|ver|vol|dir(?:\s+[^&|<>%^]*)?|tree(?:\s+[^&|<>%^]*)?|"
    r"cd(?:\s+[^&|<>%^]*)?|cls|echo\s+[^&|<>%^]*|type\s+[^&|<>%^]+|set|date\s+/t|time\s+/t|tasklist|"
    r"chcp(?:\s+\d+)?|net\s+(?:view|config|statistics\s+\w+|share|user|localgroup)|nbtstat\s+-[a-zA-Z]+)\s*$",
    re.I)

DRIVE = re.compile(r"(?:(?<![\w])[a-zA-Z]:[\\/]|\\\\)")
ENV_VAR = re.compile(r"%[A-Za-z_][\w()]*%")
MAX_TYPE = 6000
# T-SQL (SSMS): то, что выходит за пределы базы лабы
TSQL_DANGER = re.compile(r"\bxp_\w+|\bsp_configure\b|\bsp_OA\w+|\b(?:CREATE|ALTER|DROP)\s+LOGIN\b|\bALTER\s+SERVER\b|"
                         r"\bSHUTDOWN\b|\bOPENROWSET\b|\bOPENDATASOURCE\b|\bOPENQUERY\b|\bBULK\s+INSERT\b|"
                         r"\b(?:BACKUP|RESTORE)\b|\bRECONFIGURE\b|\bEXEC(?:UTE)?\s*\(|\bsp_addsrvrolemember\b|"
                         r"\bsp_executesql\b|\bDROP\s+DATABASE\s+(?:master|model|msdb|tempdb)\b", re.I)
CODE_LANGS = ["python", "csharp", "cpp", "java", "javascript", "php", "go"]


def keys_of(combo: str) -> list[str]:
    """«Ctrl + Shift + S» → ["ctrl", "shift", "s"]; неизвестная клавиша — ValueError."""
    parts = [p.strip().lower() for p in re.split(r"\s*\+\s*", combo.strip()) if p.strip()]
    out = []
    for p in parts:
        p = ALIASES.get(p, p)
        if p in MODS or p in KEYS or re.fullmatch(r"[a-zа-я0-9`\-=\[\];',./\\]", p):
            out.append(p)
        else:
            raise ValueError(f"неизвестная клавиша «{p}»")
    if not out or all(k in MODS for k in out):
        raise ValueError("нет клавиши, только модификаторы")
    return out


def _check_keys(combo: str, obs) -> None:
    from forlabs.agent.core import Blocked

    if re.search(r"\b(?:win|windows|super|meta|cmd|lwin|rwin)\b", combo, re.I):
        raise Blocked("клавиша Windows уводит из программы")
    try:
        keys = keys_of(combo)
    except ValueError as e:
        raise Blocked(str(e)) from None
    for bad, why in BAD_COMBOS.items():
        if bad <= set(keys) and len(set(keys)) == len(bad):
            raise Blocked(why)
    if "delete" in keys and obs.dialog and FILE_DIALOG.search(obs.title or ""):
        raise Blocked("Delete в окне выбора файла удаляет файл")


def paths_in(text: str) -> list[str]:
    """Пути Windows в тексте: каждый — от «C:\\» или «\\\\» до следующего такого начала или конца строки (в папках
    бывают пробелы: «ЛР 1»)."""
    out = []
    for line in text.splitlines():
        starts = [m.start() for m in DRIVE.finditer(line)]
        for i, s in enumerate(starts):
            seg = line[s:starts[i + 1] if i + 1 < len(starts) else len(line)]
            out.append(re.split(r"[\"'<>|*?]", seg)[0].strip())
    return out


def inside(path: str, root: str) -> bool:
    """Путь внутри папки (после нормализации: «..» не выводят наружу)."""
    p = ntpath.normcase(ntpath.normpath(path.replace("/", "\\")))
    r = ntpath.normcase(ntpath.normpath(root.replace("/", "\\")))
    return p == r or p.startswith(r.rstrip("\\") + "\\")


def code_problems(text: str) -> list[str]:
    """Набранный текст как код на любом из языков лаб: что в нём опасного (как в проверке проекта)."""
    from forlabs.runner import safety
    from forlabs.runner.plan import ProjectPlan

    ext = {"python": "py", "csharp": "cs", "cpp": "cpp", "java": "java", "javascript": "js", "php": "php", "go": "go"}
    keep = (safety.PROC, safety.CODE, safety.NET, safety.NATIVE, safety.REG, safety.OUT)
    found = []
    for lang in CODE_LANGS:
        entry = f"main.{ext[lang]}"
        body = text if lang != "php" else "<?php\n" + text
        try:
            plan = ProjectPlan(language=lang, kind="console", name="ввод", entry=entry,
                               files=[{"path": entry, "content": body}])
            found += [p for p in safety.scan(plan) if any(k in p for k in keep)]
        except Exception:  # noqa: BLE001 — разбор не удался: проверят шаблоны ниже
            continue
    if re.search(r"\b(?:Shell|CreateObject|WScript|Kill|SendKeys)\b\s*[\"(]|rm\s+-rf|Remove-Item|"
                 r"Invoke-Expression|\bpowershell\b|cmd(?:\.exe)?\s+/[ck]", text, re.I):
        found.append("запускает другие программы или команды")
    return found


def _check_text(text: str, obs, task) -> None:
    from forlabs.agent.core import Blocked
    from forlabs.runner.safety import sql_problems

    if len(text) > MAX_TYPE:
        raise Blocked(f"слишком длинный текст ({len(text)} знаков) — набирай по частям")
    if ENV_VAR.search(text):
        raise Blocked("пиши пути полностью, без %…%")
    found = paths_in(text)
    if task.folder:
        for path in found:
            if not inside(path, task.folder):
                raise Blocked(f"путь вне папки лабы: {path} (сохраняй в {task.folder})")
        if (not found and obs.dialog and FILE_DIALOG.search(obs.title or "")
                and re.fullmatch(r"[^\\/:\n]{1,120}\.\w{1,6}", text.strip())):
            raise Blocked(f"в окне файла впиши полный путь: {task.folder}\\{text.strip()}")
    if task.kind == "db":
        probs = sql_problems(text) + [f"выходит за пределы базы — {m[0]}" for m in TSQL_DANGER.finditer(text)]
        if probs:
            raise Blocked(f"опасный запрос: {probs[0]}")
    elif task.kind == "console":
        for line in text.splitlines():
            if line.strip() and not CONSOLE_OK.match(line):
                raise Blocked(f"команда не из разрешённых (только посмотреть сведения): {line.strip()[:60]}")
    elif task.kind in ("ide", "editor", "office", "1c", "network", "graphics"):
        probs = code_problems(text)
        if probs:
            raise Blocked(f"в набираемом тексте опасный код: {probs[0]}")


def _element_at(obs, x: int, y: int):
    """Самый маленький элемент, в который попадает точка (кнопка внутри панели)."""
    hits = [e for e in obs.elements if e.box[0] <= x < e.box[0] + e.box[2] and e.box[1] <= y < e.box[1] + e.box[3]]
    return min(hits, key=lambda e: e.box[2] * e.box[3]) if hits else None


def check(action, obs, task) -> None:
    """Можно ли это действие. Нельзя — core.Blocked с причиной по-русски (её увидит модель)."""
    from forlabs.agent.core import Blocked

    allow = [w.strip().lower() for w in task.allow if w and w.strip()]
    if action.do == "zoom" and (action.x is None or action.y is None):
        raise Blocked("для приближения нужны x и y")
    if action.do in ("click", "double_click", "right_click", "drag", "scroll", "zoom"):
        if action.el is None and (action.x is None or action.y is None):
            raise Blocked("нужен номер элемента или координаты x, y")
        if action.do == "drag" and action.to_el is None and (action.x2 is None or action.y2 is None):
            raise Blocked("для перетаскивания нужен to_el или x2, y2")
        for eid in (action.el, action.to_el):
            if eid is not None and obs.element(eid) is None:
                raise Blocked(f"элемента [{eid}] нет в списке — посмотри список заново")
        if action.x is not None and action.y is not None:
            w, h = obs.image.size
            sw, sh = round(w * obs.scale), round(h * obs.scale)
            if not (0 <= action.x < sw and 0 <= action.y < sh):
                raise Blocked(f"точка ({action.x},{action.y}) вне снимка {sw}×{sh}")
        if action.do in ("click", "double_click"):
            el = obs.element(action.el) if action.el is not None else _element_at(obs, action.x, action.y)
            name = (el.name if el else "").strip()
            if name and DANGER.search(name) and not any(w in name.lower() for w in allow):
                raise Blocked(f"кнопка «{name}» может удалить, сбросить или открыть лишнее — в этой задаче нельзя")
    elif action.do == "key":
        _check_keys(action.keys, obs)
    elif action.do == "type":
        if not action.text:
            raise Blocked("пустой текст")
        _check_text(action.text, obs, task)
    elif action.do == "wait":
        if not 0 <= action.seconds <= 30:
            raise Blocked("ждать можно до 30 секунд")
