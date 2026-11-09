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


def elements(hwnd: int, timeout: float = 4.0) -> list[tuple[str, str, str, tuple[int, int, int, int], bool]]:
    """[(роль, имя, значение, (x, y, w, h) в пикселях экрана, доступен)] видимых элементов окна, сверху вниз.
    Не успели — пустой список: агент работает по картинке."""
    out: list = []

    def work():
        try:
            out.extend(_collect(hwnd))
        except Exception:  # noqa: BLE001 — элементы — подсказка, а не условие работы
            pass

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    return list(out) if not t.is_alive() else []


def _collect(hwnd: int):
    from forlabs.runner import uia

    A = uia._ns()
    win = uia.root(hwnd)
    req = A.CacheRequest()
    for prop in (A.AutomationElement.ControlTypeProperty, A.AutomationElement.NameProperty,
                 A.AutomationElement.BoundingRectangleProperty, A.AutomationElement.IsEnabledProperty,
                 A.AutomationElement.IsOffscreenProperty, A.ValuePattern.ValueProperty):
        req.Add(prop)
    req.TreeScope = A.TreeScope.Element
    ctx = req.Activate()  # IDisposable: свойства приходят одним запросом
    try:
        found = win.FindAll(A.TreeScope.Descendants, A.Condition.TrueCondition)
    finally:
        ctx.Dispose()
    items = []
    for el in found:
        try:
            ct = str(el.Cached.ControlType.ProgrammaticName).split(".")[-1]
            if ct not in CLICKABLE or el.Cached.IsOffscreen:
                continue
            r = el.Cached.BoundingRectangle
            if r.IsEmpty or r.Width < 3 or r.Height < 3:
                continue
            name = str(el.Cached.Name or "").strip()
            try:
                v = el.GetCachedPropertyValue(A.ValuePattern.ValueProperty, True)
                value = "" if v is None or v.Equals(A.AutomationElement.NotSupported) else str(v)
            except Exception:  # noqa: BLE001
                value = ""
            if ct == "Text" and not name:
                continue
            items.append((ROLES.get(ct) or ct, name.replace("\n", " ")[:120], value.replace("\n", " ")[:120],
                          (int(r.X), int(r.Y), int(r.Width), int(r.Height)), bool(el.Cached.IsEnabled)))
        except Exception:  # noqa: BLE001
            continue
        if len(items) >= LIMIT * 3:
            break
    items.sort(key=lambda it: (it[3][1] // 8, it[3][0]))
    return items[:LIMIT]
