"""Элементы окна для агента через UI Automation: одним запросом с кешем свойств (Visual Studio отдаёт тысячи
элементов — по одному это секунды), с ограничением по времени (окно с модальным диалогом может не отвечать)."""

from __future__ import annotations

import threading

ROLES = {"Button": "Кнопка", "MenuItem": "Пункт меню", "Edit": "Поле", "ComboBox": "Список", "ListItem": "Строка",
         "TreeItem": "Узел", "TabItem": "Вкладка", "CheckBox": "Флажок", "RadioButton": "Переключатель",
         "Hyperlink": "Ссылка", "DataItem": "Ячейка", "Document": "Документ", "MenuBar": "", "Menu": "",
         "SplitButton": "Кнопка", "Text": "Текст", "HeaderItem": "Заголовок", "Spinner": "Счётчик",
         "Slider": "Ползунок", "Custom": ""}
CLICKABLE = {"Button", "MenuItem", "Edit", "ComboBox", "ListItem", "TreeItem", "TabItem", "CheckBox", "RadioButton",
             "Hyperlink", "DataItem", "Document", "SplitButton", "HeaderItem", "Spinner", "Slider", "Text"}
LIMIT = 220


def elements(hwnd: int, timeout: float = 6.0, budget: float = 3.5):
    """[(роль, имя, значение, (x, y, w, h) в пикселях экрана, доступен)] видимых элементов окна, сверху вниз.
    Обход по уровням с бюджетом времени: у Visual Studio тысячи элементов — берётся то, что ближе к корню (меню,
    панели, вкладки, дерево решения). Не успели совсем — пустой список: агент работает по картинке."""
    out: list = []

    def work():
        try:
            out.extend(_collect(hwnd, budget))
        except Exception:  # noqa: BLE001 — элементы — подсказка, а не условие работы
            pass

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    return list(out) if not t.is_alive() else []


def _collect(hwnd: int, budget: float):
    import time

    from forlabs.runner import uia, winapi

    winapi.dpi_aware()  # осведомлённость о масштабе — у каждого потока своя: иначе рамки в «логических» пикселях
    A = uia._ns()
    win = uia.root(hwnd)
    req = A.CacheRequest()
    for prop in (A.AutomationElement.ControlTypeProperty, A.AutomationElement.NameProperty,
                 A.AutomationElement.BoundingRectangleProperty, A.AutomationElement.IsEnabledProperty,
                 A.AutomationElement.IsOffscreenProperty, A.ValuePattern.ValueProperty):
        req.Add(prop)
    req.TreeScope = A.TreeScope.Element
    end = time.monotonic() + budget
    items, queue, seen = [], [win], 0
    ctx = req.Activate()  # IDisposable: свойства детей приходят вместе с ними
    try:
        while queue and time.monotonic() < end and seen < 4000:
            node = queue.pop(0)
            try:
                kids = node.FindAll(A.TreeScope.Children, A.Condition.TrueCondition)
            except Exception:  # noqa: BLE001
                continue
            for el in kids:
                seen += 1
                try:
                    if el.Cached.IsOffscreen:
                        continue
                    r = el.Cached.BoundingRectangle
                    if r.IsEmpty or r.Width < 3 or r.Height < 3:
                        continue
                    queue.append(el)
                    ct = str(el.Cached.ControlType.ProgrammaticName).split(".")[-1]
                    if ct not in CLICKABLE:
                        continue
                    name = str(el.Cached.Name or "").strip()
                    if ct == "Text" and not name:
                        continue
                    try:
                        v = el.GetCachedPropertyValue(A.ValuePattern.ValueProperty, True)
                        value = "" if v is None or v.Equals(A.AutomationElement.NotSupported) else str(v)
                    except Exception:  # noqa: BLE001
                        value = ""
                    items.append((ROLES.get(ct) or ct, name.replace("\n", " ")[:120], value.replace("\n", " ")[:120],
                                  (int(r.X), int(r.Y), int(r.Width), int(r.Height)), bool(el.Cached.IsEnabled)))
                except Exception:  # noqa: BLE001
                    continue
    finally:
        ctx.Dispose()
    items.sort(key=lambda it: (it[3][1] // 8, it[3][0]))
    return items[:LIMIT]
