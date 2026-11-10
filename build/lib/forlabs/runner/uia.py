"""UI Automation (System.Windows.Automation через pythonnet): заполнить поля, выбрать пункт, нажать кнопку,
прочитать текст окна. Работает с WinForms, WPF, Swing (через Java Access Bridge — не всегда), tkinter (частично).
Элемент ищется по AutomationId (у WinForms это имя элемента: inputA, btnCalc), иначе по видимому тексту."""

from __future__ import annotations

import threading
import time

_loaded = False


def load() -> None:
    global _loaded
    if _loaded:
        return
    import glob

    import clr  # pythonnet: в установленной программе есть (на нём работает окно pywebview)

    for asm in ("UIAutomationClient", "UIAutomationTypes"):
        try:
            clr.AddReference(f"{asm}, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35")
        except Exception:
            found = (glob.glob(rf"C:\Windows\Microsoft.NET\assembly\GAC_MSIL\{asm}\*\{asm}.dll")
                     + glob.glob(rf"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\WPF\{asm}.dll"))
            if not found:
                raise
            clr.AddReference(found[0])
    _loaded = True


class UiaError(Exception):
    pass


def _ns():
    load()
    import System.Windows.Automation as A  # noqa: N812

    return A


def root(hwnd: int):
    A = _ns()  # сначала загрузка сборок: без неё пространства имён System ещё нет
    from System import IntPtr

    return A.AutomationElement.FromHandle(IntPtr(hwnd))


def _roots(where) -> list:
    """Корни окон, в которых искать: одно окно (hwnd) или функция, отдающая окна программы — новые первыми
    (после нажатия могла открыться вторая форма или окно сообщения)."""
    out = []
    for h in (where() if callable(where) else [where]):
        try:
            out.append(root(h))
        except Exception:  # noqa: BLE001 — окно успело закрыться
            continue
    return out


def _find_in(win, target: str):
    A = _ns()
    by_id = A.PropertyCondition(A.AutomationElement.AutomationIdProperty, target.removeprefix("id:"))
    el = win.FindFirst(A.TreeScope.Descendants, by_id)
    if el is None and not target.startswith("id:"):
        el = win.FindFirst(A.TreeScope.Descendants, A.PropertyCondition(A.AutomationElement.NameProperty, target))
    return el


def _titles(wins) -> str:
    names = []
    for w in wins:
        try:
            names.append(f"«{w.Current.Name}»")
        except Exception:  # noqa: BLE001
            continue
    return ", ".join(names)


def find(where, target: str, timeout: float = 5.0):
    """«id:inputA» — по AutomationId; «Вычислить» — по имени (видимому тексту), сначала точное совпадение.
    where — hwnd окна или функция, отдающая окна программы (новые — первыми)."""
    end = time.monotonic() + timeout
    while True:
        wins = _roots(where)
        for win in wins:
            el = _find_in(win, target)
            if el is not None:
                return el
        if time.monotonic() > end:
            many = len(wins) > 1
            raise UiaError(f"в {'окнах' if many else 'окне'} {_titles(wins) or 'программы'} нет элемента «{target}»")
        time.sleep(0.2)


def _pattern(el, pattern):
    ok, p = el.TryGetCurrentPattern(pattern, None)
    return p if ok else None


def set_value(el, value: str) -> None:
    A = _ns()
    vp = _pattern(el, A.ValuePattern.Pattern)
    if vp is not None and not vp.Current.IsReadOnly:
        vp.SetValue(value)
        return
    if el.Current.ControlType == A.ControlType.ComboBox:
        select(el, value)
        return
    raise UiaError(f"в «{el.Current.Name or el.Current.AutomationId}» нельзя ввести текст")


def loose_match(names: list[str], item: str) -> str | None:
    """Пункт, записанный в сценарии чуть иначе: другой регистр, пробелы по краям или начало пункта («Сложение» —
    «Сложение (+)»), если такой в списке один."""
    want = item.strip().casefold()
    same = [n for n in names if n.strip().casefold() == want]
    if same:
        return same[0]
    starts = [n for n in names if want and n.strip().casefold().startswith(want)]
    return starts[0] if len(starts) == 1 else None


def item_names(el, limit: int = 40) -> list[str]:
    """Пункты списка, как их видит UI Automation (у выпадающего списка — пока он раскрыт)."""
    A = _ns()
    cond = A.PropertyCondition(A.AutomationElement.ControlTypeProperty, A.ControlType.ListItem)
    out: list[str] = []
    try:
        for it in el.FindAll(A.TreeScope.Descendants, cond):
            name = str(it.Current.Name or "")
            if name:
                out.append(name)
            if len(out) >= limit:
                break
    except Exception:  # noqa: BLE001
        pass
    return out


def _label(el) -> str:
    try:
        aid = str(el.Current.AutomationId or "")
        return aid if aid and not aid.isdigit() else str(el.Current.Name or "")
    except Exception:  # noqa: BLE001
        return ""


def select(el, item: str, timeout: float = 2.0) -> None:
    A = _ns()
    ec = _pattern(el, A.ExpandCollapsePattern.Pattern)
    if ec is not None:
        try:
            ec.Expand()
        except Exception:
            pass
        time.sleep(0.2)
    end = time.monotonic() + timeout
    names: list[str] = []
    while True:  # список раскрывается не мгновенно, а пункты могут подгружаться
        it = el.FindFirst(A.TreeScope.Descendants, A.PropertyCondition(A.AutomationElement.NameProperty, item))
        if it is None:
            names = item_names(el)
            near = loose_match(names, item)
            if near is not None:
                it = el.FindFirst(A.TreeScope.Descendants,
                                  A.PropertyCondition(A.AutomationElement.NameProperty, near))
        if it is not None or time.monotonic() > end:
            break
        time.sleep(0.2)
    if it is None:
        if ec is not None:
            try:
                ec.Collapse()
            except Exception:
                pass
        have = ("в списке: " + ", ".join(names[:15])) if names else "список пуст"
        raise UiaError(f"в списке «{_label(el)}» нет пункта «{item}» ({have})")
    sp = _pattern(it, A.SelectionItemPattern.Pattern)
    if sp is None:
        raise UiaError(f"пункт «{item}» нельзя выбрать")
    sp.Select()
    if ec is not None:
        try:
            ec.Collapse()
        except Exception:
            pass


def click(el, timeout: float = 10.0) -> None:
    """Нажать кнопку/флажок/переключатель. Invoke может не вернуться, пока открыт MessageBox, — в потоке."""
    A = _ns()
    inv = _pattern(el, A.InvokePattern.Pattern)
    tog = _pattern(el, A.TogglePattern.Pattern)
    sel = _pattern(el, A.SelectionItemPattern.Pattern)
    if inv is not None:
        action = inv.Invoke
    elif tog is not None:
        action = tog.Toggle
    elif sel is not None:
        action = sel.Select
    else:
        raise UiaError(f"«{el.Current.Name or el.Current.AutomationId}» не нажимается")
    errors: list[Exception] = []

    def go():
        try:
            action()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(timeout if inv is None else 1.5)  # модальное окно держит Invoke — это нормально
    if errors:
        raise UiaError(f"нажатие не удалось: {errors[0]}")


def check(el, on: bool = True) -> None:
    A = _ns()
    tog = _pattern(el, A.TogglePattern.Pattern)
    if tog is None:
        raise UiaError("это не флажок")
    want = A.ToggleState.On if on else A.ToggleState.Off
    for _ in range(3):
        if tog.Current.ToggleState == want:
            return
        tog.Toggle()


def texts(win) -> list[str]:
    """Весь видимый текст окна: имена и значения элементов (для проверки ожидаемого результата)."""
    A = _ns()
    out: list[str] = []
    for el in win.FindAll(A.TreeScope.Subtree, A.Condition.TrueCondition):
        try:
            name = el.Current.Name
            if name:
                out.append(str(name))
            vp = _pattern(el, A.ValuePattern.Pattern)
            if vp is not None and vp.Current.Value:
                out.append(str(vp.Current.Value))
        except Exception:
            continue
    return out


def perform(where, steps: list[dict], timeout: float = 5.0) -> None:
    """Шаги сценария: {"action": "set"|"select"|"click"|"check"|"uncheck"|"wait", "target": "...", "value": "..."}.
    where — hwnd окна или функция, отдающая окна программы (новые — первыми): элемент ищется во всех."""
    for s in steps:
        action, target, value = s.get("action"), s.get("target") or "", s.get("value") or ""
        if action == "wait":
            time.sleep(min(float(value or 1), 10))
            continue
        el = find(where, target, timeout)
        if action == "set":
            set_value(el, value)
        elif action == "select":
            select(el, value)
        elif action == "click":
            click(el)
        elif action in ("check", "uncheck"):
            check(el, action == "check")
        else:
            raise UiaError(f"неизвестный шаг «{action}»")
        time.sleep(0.15)


# --- что в окне: описание для модели, которая чинит сценарий или программу
ROLES = {"Edit": "поле", "Document": "поле", "Button": "кнопка", "SplitButton": "кнопка", "ComboBox": "список",
         "List": "список", "CheckBox": "флажок", "RadioButton": "переключатель", "Text": "надпись",
         "MenuItem": "меню", "TabItem": "вкладка", "Hyperlink": "ссылка", "DataGrid": "таблица", "Table": "таблица",
         "Spinner": "счётчик", "Slider": "ползунок", "Group": "группа", "Tree": "дерево", "TreeItem": "ветка"}
CONTAINERS = {"Pane", "Group", "Tab", "TabItem", "ToolBar", "StatusBar", "MenuBar", "Menu", "Window", "Custom",
              "Tree", "Header"}
SKIP = {"TitleBar", "ScrollBar", "Thumb", "Image", "Separator", "ToolTip", "ProgressBar"}


def format_outline(title: str, rows: list[tuple]) -> str:
    """Строки (тип, AutomationId, имя, значение, доступен ли, пункты списка или None) — текстом для модели."""
    lines = [f"Окно «{title}»:"]
    for kind, aid, name, value, usable, items in rows:
        role = ROLES.get(kind)
        if role is None:
            continue
        ident = aid if aid and not aid.isdigit() else ""
        head = f"  [{role}]" + (f" {ident}" if ident else "")
        if role == "поле":
            if value:
                line = f"{head} = «{value}»" + ("" if usable else " (только чтение)")
            else:
                line = f"{head} — пусто" + ("" if usable else ", только чтение")
        elif role == "список":
            line = head + (f" = «{value}»" if value else "")
            if items is not None:
                tail = ("пункты: " + ", ".join(items)) if items else "пунктов нет"
                line += ("; " if value else " — ") + tail
            elif not value:
                line += " — ничего не выбрано"
        else:
            line = head + (f" «{name}»" if name else "") + (f" — {value}" if value else "")
            if not usable:
                line += " (недоступно)"
        if line.strip() != f"[{role}]":
            lines.append(line)
    if len(lines) == 1:
        lines.append("  элементов не видно")
    return "\n".join(lines)


def _row(el, kind: str) -> tuple | None:
    A = _ns()
    cur = el.Current
    aid, name = str(cur.AutomationId or ""), " ".join(str(cur.Name or "").split())[:80]
    usable, value, items = bool(cur.IsEnabled), "", None
    if kind in ("Edit", "Document", "Spinner"):
        vp = _pattern(el, A.ValuePattern.Pattern)
        if vp is not None:
            value = "" if cur.IsPassword else " ".join(str(vp.Current.Value or "").split())[:120]
            usable = usable and not vp.Current.IsReadOnly
    elif kind in ("ComboBox", "List"):
        sel = _pattern(el, A.SelectionPattern.Pattern)
        if sel is not None:
            chosen = [str(x.Current.Name or "") for x in sel.Current.GetSelection()]
            value = ", ".join(c for c in chosen if c)[:120]
        if not value:
            vp = _pattern(el, A.ValuePattern.Pattern)
            value = " ".join(str(vp.Current.Value or "").split())[:120] if vp is not None else ""
        ec = _pattern(el, A.ExpandCollapsePattern.Pattern) if kind == "ComboBox" else None
        if ec is not None:  # пункты выпадающего списка видны, только пока он раскрыт
            try:
                ec.Expand()
                time.sleep(0.25)
            except Exception:  # noqa: BLE001
                pass
        items = item_names(el, 25)
        if ec is not None:
            try:
                ec.Collapse()
            except Exception:  # noqa: BLE001
                pass
    elif kind == "CheckBox":
        tog = _pattern(el, A.TogglePattern.Pattern)
        if tog is not None:
            value = "отмечен" if tog.Current.ToggleState == A.ToggleState.On else "не отмечен"
    elif kind == "RadioButton":
        sp = _pattern(el, A.SelectionItemPattern.Pattern)
        if sp is not None:
            value = "выбран" if sp.Current.IsSelected else "не выбран"
    elif kind in ("DataGrid", "Table"):
        gp = _pattern(el, A.GridPattern.Pattern)
        if gp is not None:
            value = f"строк: {gp.Current.RowCount}, столбцов: {gp.Current.ColumnCount}"
    return kind, aid, name, value, usable, items


def rows(win, budget: int = 150, seconds: float = 6.0) -> list[tuple]:
    A = _ns()
    walker = A.TreeWalker.ControlViewWalker
    end = time.monotonic() + seconds
    out: list[tuple] = []

    def visit(el, depth: int) -> None:
        child = walker.GetFirstChild(el)
        while child is not None and len(out) < budget and time.monotonic() < end:
            try:
                kind = str(child.Current.ControlType.ProgrammaticName).split(".")[-1]
                if kind not in SKIP:
                    if kind in ROLES:
                        out.append(_row(child, kind))
                    if kind in CONTAINERS and depth < 8:
                        visit(child, depth + 1)
            except Exception:  # noqa: BLE001 — элемент исчез, пока читали
                pass
            child = walker.GetNextSibling(child)

    visit(win, 0)
    return [r for r in out if r]


def outline(hwnds: list[int], limit: int = 3500) -> str:
    """Что сейчас в окнах программы: элементы с именами (их пишут в target), текстом, пунктами списков."""
    parts = []
    for h in hwnds:
        try:
            win = root(h)
            parts.append(format_outline(str(win.Current.Name or ""), rows(win)))
        except Exception:  # noqa: BLE001
            continue
    return "\n".join(parts)[:limit]
