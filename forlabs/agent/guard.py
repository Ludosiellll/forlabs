"""Чего агенту нельзя, в какой бы программе он ни работал. Окна других программ — забота Desk (руки сами не
выходят за окна нужной программы); здесь — клавиши, опасные кнопки и набираемый текст."""

from __future__ import annotations

import re

MODS = {"ctrl", "shift", "alt"}
KEYS = {"enter", "tab", "esc", "space", "backspace", "delete", "insert", "up", "down", "left", "right", "home", "end",
        "pageup", "pagedown", "apps", *(f"f{i}" for i in range(1, 13))}
ALIASES = {"return": "enter", "escape": "esc", "del": "delete", "ins": "insert", "control": "ctrl", "pgup": "pageup",
           "pgdn": "pagedown", "pagedn": "pagedown", "arrowup": "up", "arrowdown": "down", "arrowleft": "left",
           "arrowright": "right", "bksp": "backspace", "spacebar": "space", "menu": "apps"}
# сочетания, которые уводят из программы, закрывают её или трогают систему
BAD_COMBOS = {frozenset({"alt", "f4"}): "закрывает окно (для диалога — кнопка «Закрыть» или Esc)",
              frozenset({"alt", "tab"}): "переключает на другие программы",
              frozenset({"alt", "esc"}): "переключает на другие программы",
              frozenset({"ctrl", "esc"}): "открывает «Пуск»",
              frozenset({"ctrl", "shift", "esc"}): "открывает диспетчер задач",
              frozenset({"ctrl", "alt", "delete"}): "системное сочетание"}

DANGER = re.compile(r"удал|delete|remove|стереть|erase|очист|clear\s*all|формат|format\b|drop\b|truncate|uninstall|"
                    r"сброс|reset|выключ|shutdown|перезагруз|restart|reboot|выйти из (?:учётн|аккаунт)|sign\s*out|"
                    r"log\s*out|купить|buy|оплат|purchase|подписк", re.I)

# что агенту можно запускать в консоли: смотреть, а не менять
CONSOLE_OK = re.compile(
    r"^\s*(?:ipconfig|ping|tracert|pathping|nslookup|netstat|arp|route\s+print|hostname|getmac|systeminfo|whoami|"
    r"ver|vol|dir|tree|cd|chdir|cls|echo|type|set|date\s+/t|time\s+/t|tasklist|chcp|net\s+(?:view|config|"
    r"statistics|user|localgroup|share)\b(?!.*\s/(?:add|delete|del)\b)|nbtstat|telnet|ssh|ftp|mysql|psql|sqlcmd|"
    r"python|py|java|node|php|go\s+run|dotnet\s+run)\b[^&|><]*$", re.I)

# путь — до конца строки или кавычки: в папках бывают пробелы («ЛР 1»)
WIN_PATH = re.compile(r"(?:[a-zA-Z]:[\\/]|\\\\)[^\r\n\"'<>|*?]*")
MAX_TYPE = 6000



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


def _check_keys(combo: str) -> None:
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


def _check_text(text: str, task) -> None:
    from forlabs.agent.core import Blocked
    from forlabs.runner.safety import sql_problems

    if len(text) > MAX_TYPE:
        raise Blocked(f"слишком длинный текст ({len(text)} знаков) — набирай по частям")
    if task.folder:
        root = task.folder.replace("/", "\\").rstrip("\\").lower()
        for m in WIN_PATH.finditer(text):
            path = m[0].strip().replace("/", "\\").rstrip("\\").lower()
            if not (path == root or path.startswith(root + "\\")):
                raise Blocked(f"путь вне папки лабы: {m[0]} (сохраняй в {task.folder})")
    if task.kind == "db":
        probs = sql_problems(text)
        if probs:
            raise Blocked(f"опасный запрос: {probs[0]}")
    if task.kind == "console":
        for line in text.splitlines():
            if line.strip() and not CONSOLE_OK.match(line):
                raise Blocked(f"команда не из разрешённых (только посмотреть сведения): {line.strip()[:60]}")


def check(action, obs, task) -> None:
    """Можно ли это действие. Нельзя — core.Blocked с причиной по-русски (её увидит модель)."""
    from forlabs.agent.core import Blocked

    if action.do == "zoom" and (action.x is None or action.y is None):
        raise Blocked("для приближения нужны x и y")
    if action.do in ("click", "double_click", "right_click", "drag", "scroll", "zoom"):
        if action.el is None and (action.x is None or action.y is None):
            raise Blocked("нужен номер элемента или координаты x, y")
        if action.do == "drag" and action.to_el is None and (action.x2 is None or action.y2 is None):
            raise Blocked("для перетаскивания нужен to_el или x2, y2")
        for eid in (action.el, action.to_el):
            el = obs.element(eid)
            if eid is not None and el is None:
                raise Blocked(f"элемента [{eid}] нет в списке — посмотри список заново")
            if el is not None and action.do in ("click", "double_click") and DANGER.search(el.name):
                allowed = any(w.lower() in el.name.lower() for w in task.allow)
                if not allowed:
                    raise Blocked(f"кнопка «{el.name}» может удалить или сбросить данные — в этой задаче нельзя")
        if action.x is not None and action.y is not None:
            w, h = obs.image.size
            sw, sh = round(w * obs.scale), round(h * obs.scale)
            if not (0 <= action.x < sw and 0 <= action.y < sh):
                raise Blocked(f"точка ({action.x},{action.y}) вне снимка {sw}×{sh}")
    elif action.do == "key":
        _check_keys(action.keys)
    elif action.do == "type":
        if not action.text:
            raise Blocked("пустой текст")
        _check_text(action.text, task)
    elif action.do == "wait":
        if not 0 <= action.seconds <= 30:
            raise Blocked("ждать можно до 30 секунд")
